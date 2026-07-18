"""组件构造工厂 — _complete_initialization 的核心逻辑"""

import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.core.provider.provider import Provider

from ..base.exceptions import ProviderNotReadyError
from ..injection.recorder import InjectionDecisionRecorder
from ..managers.backup_manager import BackupManager
from ..managers.conversation_manager import ConversationManager
from ..managers.memory_engine import MemoryEngine
from ..managers.memory_evolution_gate import MemoryEvolutionGate
from ..managers.memory_evolution_manager import MemoryEvolutionManager
from ..processors.memory_consolidator import MemoryConsolidator
from ..processors.memory_processor import MemoryProcessor
from ..retrieval.derived_relation_expander import DerivedRelationExpander
from ..schedulers.decay_scheduler import DecayScheduler
from ..storage.conversation_store import ConversationStore
from ..storage.injection_decision_store import InjectionDecisionStore
from ..storage.memory_evolution_store import MemoryEvolutionStore
from ..validators.index_validator import IndexValidator


class ComponentFactory:
    """创建并初始化所有核心组件"""

    def __init__(self, context, config_manager, data_dir: str):
        self.context = context
        self.config_manager = config_manager
        self.data_dir = data_dir

    async def build_all(
        self,
        embedding_provider,
        llm_provider,
        faiss_vec_db_cls,
        faiss_checker,
        db_setup,
    ) -> dict:
        """返回初始化的组件字典"""
        data_dir_path = Path(self.data_dir)

        db_path = data_dir_path / "memora.db"
        index_path = data_dir_path / "memora.index"
        graph_doc_path = data_dir_path / "memora_graph_documents.db"
        graph_index_path = data_dir_path / "memora_graph.index"
        graph_memory_enabled = self.config_manager.get("graph_memory.enabled", True)
        evolution_config = self.config_manager.get_section("memory_evolution")
        if not isinstance(evolution_config, dict):
            evolution_config = {}

        if not embedding_provider:
            raise ProviderNotReadyError("Embedding Provider 未初始化")
        if not llm_provider or not isinstance(llm_provider, Provider):
            raise ProviderNotReadyError("LLM Provider 未初始化或类型不正确")

        await faiss_checker.check_and_fix_dimension_mismatch(
            str(index_path), embedding_provider
        )
        if graph_memory_enabled:
            await faiss_checker.check_and_fix_dimension_mismatch(
                str(graph_index_path), embedding_provider
            )

        # 并行初始化主 DB 和图 DB（两者完全独立，不同的文件）
        db = faiss_vec_db_cls(str(db_path), str(index_path), embedding_provider)

        graph_db = None
        if graph_memory_enabled:
            graph_db = faiss_vec_db_cls(
                str(graph_doc_path), str(graph_index_path), embedding_provider
            )
            await asyncio.gather(db.initialize(), graph_db.initialize())
        else:
            await db.initialize()

        memory_evolution_store = MemoryEvolutionStore(str(db_path))
        await memory_evolution_store.initialize()
        derived_expander = None
        if (
            bool(evolution_config.get("enabled", False))
            and str(evolution_config.get("mode", "disabled")) != "disabled"
        ):
            derived_expander = DerivedRelationExpander(
                memory_evolution_store,
                per_seed_limit=max(
                    1,
                    int(evolution_config.get("candidate_limit", 4)),
                ),
                global_limit=max(
                    0,
                    int(evolution_config.get("max_query_expansions", 8)),
                ),
            )

        logger.info(f"数据库已初始化。数据目录: {self.data_dir}")

        backup_manager = BackupManager(self.data_dir)

        stopwords_dir = data_dir_path / "stopwords"
        stopwords_dir.mkdir(parents=True, exist_ok=True)

        engine_config = self._build_engine_config(stopwords_dir, graph_memory_enabled)
        engine_config["memory_evolution"] = evolution_config
        engine_config["derived_expander"] = derived_expander
        memory_engine = MemoryEngine(
            db_path=str(db_path),
            faiss_db=db,
            graph_vector_db=graph_db,
            llm_provider=llm_provider,
            config=engine_config,
        )
        await memory_engine.initialize()
        logger.info("MemoryEngine 已初始化")

        conversation_db_path = data_dir_path / "conversations.db"
        conversation_store = ConversationStore(str(conversation_db_path))
        await conversation_store.initialize()

        session_config = self.config_manager.session_manager
        conversation_manager = ConversationManager(
            store=conversation_store,
            max_cache_size=session_config.get("max_sessions", 100),
            context_window_size=session_config.get("context_window_size", 50),
            session_ttl=session_config.get("session_ttl", 3600),
        )
        logger.info("ConversationManager 已初始化")

        await db_setup.repair_message_counts(conversation_store)

        llm_id = self.config_manager.get("provider_settings.llm_provider_id")
        memory_processor = MemoryProcessor(
            self.context,
            llm_provider=llm_id if llm_id else None,
            config={
                "atom_enabled": engine_config["atom_enabled"],
                "group_chat_template": self.config_manager.get(
                    "prompt_templates.group_chat_template", ""
                ),
                "private_chat_template": self.config_manager.get(
                    "prompt_templates.private_chat_template", ""
                ),
                "topic_segmentation.enabled": self.config_manager.get(
                    "topic_segmentation.enabled", True
                ),
            },
        )
        logger.info("MemoryProcessor 已初始化")

        memory_evolution_gate = MemoryEvolutionGate(evolution_config)
        memory_evolution_consolidator = MemoryConsolidator(
            memory_processor.llm_client.call_llm_with_retry,
            evolution_config,
        )
        memory_evolution_manager = MemoryEvolutionManager(
            memory_evolution_store,
            memory_evolution_gate,
            memory_evolution_consolidator,
            evolution_config,
        )
        if memory_evolution_manager.mode != "disabled":
            await memory_evolution_manager.start()

        index_validator = IndexValidator(str(db_path), db)
        await db_setup.auto_rebuild_index_if_needed(index_validator, memory_engine)

        if memory_engine and hasattr(memory_engine, "text_processor"):
            tp = memory_engine.text_processor
            if tp and hasattr(tp, "async_init"):
                await tp.async_init()
                logger.info("TextProcessor 停用词已加载")

        decay_rate = self.config_manager.get("importance_decay.decay_rate", 0.01)
        auto_cleanup = self.config_manager.get(
            "forgetting_agent.auto_cleanup_enabled", True
        )
        decay_scheduler = None
        if memory_engine and (decay_rate > 0 or auto_cleanup):
            backup_enabled = self.config_manager.get("backup_settings.enabled", True)
            backup_keep_days = self.config_manager.get("backup_settings.keep_days", 7)
            scheduler = DecayScheduler(
                memory_engine=memory_engine,
                decay_rate=decay_rate,
                data_dir=self.data_dir,
                backup_manager=backup_manager,
                backup_enabled=backup_enabled,
                backup_keep_days=backup_keep_days,
            )
            await scheduler.start()
            decay_scheduler = scheduler
            logger.info("DecayScheduler 已启动")

        try:
            injection_components = await self._build_injection_components(db_path)
        except BaseException:
            await self._rollback_build_components(
                decay_scheduler,
                conversation_store,
                memory_engine,
                graph_db,
                db,
                memory_evolution_manager,
                memory_evolution_store,
            )
            raise

        return {
            "db": db,
            "graph_db": graph_db,
            "memory_engine": memory_engine,
            "memory_processor": memory_processor,
            "backup_manager": backup_manager,
            "conversation_manager": conversation_manager,
            "index_validator": index_validator,
            "decay_scheduler": decay_scheduler,
            "memory_evolution_store": memory_evolution_store,
            "memory_evolution_manager": memory_evolution_manager,
            **injection_components,
        }

    @staticmethod
    async def _rollback_build_components(
        decay_scheduler,
        conversation_store,
        memory_engine,
        graph_db,
        db,
        memory_evolution_manager=None,
        memory_evolution_store=None,
    ) -> None:
        cleanup_steps = (
            ("MemoryEvolutionManager", memory_evolution_manager, "stop"),
            ("MemoryEvolutionStore", memory_evolution_store, "close"),
            ("DecayScheduler", decay_scheduler, "stop"),
            ("ConversationStore", conversation_store, "close"),
            ("MemoryEngine", memory_engine, "close"),
            ("GraphDB", graph_db, "close"),
            ("DB", db, "close"),
        )
        for label, component, method_name in cleanup_steps:
            if component is None:
                continue
            try:
                await getattr(component, method_name)()
            except BaseException:
                logger.error("回滚组件 %s 失败", label, exc_info=True)

    async def _build_injection_components(self, db_path: Path) -> dict[str, object]:
        decision_store = InjectionDecisionStore(db_path)
        decision_recorder: InjectionDecisionRecorder | None = None
        try:
            await decision_store.initialize()
            decision_recorder = InjectionDecisionRecorder(
                decision_store,
                retention_days=int(
                    self.config_manager.get(
                        "recall_engine.injection_decision_retention_days", 30
                    )
                ),
                max_rows=int(
                    self.config_manager.get(
                        "recall_engine.injection_decision_max_rows", 100_000
                    )
                ),
            )
            await decision_recorder.start()
            decision_recorder.schedule_cleanup()
            return {
                "injection_decision_store": decision_store,
                "injection_decision_recorder": decision_recorder,
            }
        except BaseException:
            try:
                if decision_recorder is not None:
                    try:
                        await decision_recorder.close(timeout=5.0)
                    except Exception:
                        logger.error(
                            "关闭注入决策记录器失败",
                            exc_info=True,
                        )
            finally:
                try:
                    await decision_store.close()
                except Exception:
                    logger.error(
                        "关闭注入决策存储失败",
                        exc_info=True,
                    )
            raise

    def _build_engine_config(
        self, stopwords_dir: Path, graph_memory_enabled: bool
    ) -> dict:
        cm = self.config_manager
        return {
            "data_dir": self.data_dir,
            "rrf_k": cm.get("fusion_strategy.rrf_k", 60),
            "decay_rate": cm.get("importance_decay.decay_rate", 0.01),
            "access_decay_window_days": cm.get(
                "importance_decay.access_decay_window_days", 30.0
            ),
            "access_decay_max_count": cm.get(
                "importance_decay.access_decay_max_count", 10
            ),
            "access_count_decay_multiplier": cm.get(
                "importance_decay.access_count_decay_multiplier", 0.5
            ),
            "importance_weight": cm.get("recall_engine.importance_weight", 1.0),
            "search_cache_enabled": cm.get("recall_engine.search_cache_enabled", True),
            "search_cache_ttl_seconds": cm.get(
                "recall_engine.search_cache_ttl_seconds", 45.0
            ),
            "search_cache_max_size": cm.get("recall_engine.search_cache_max_size", 256),
            "fallback_enabled": cm.get("recall_engine.fallback_to_vector", True),
            "cleanup_days_threshold": cm.get(
                "forgetting_agent.cleanup_days_threshold", 30
            ),
            "cleanup_importance_threshold": cm.get(
                "forgetting_agent.cleanup_importance_threshold", 0.3
            ),
            "auto_cleanup_enabled": cm.get(
                "forgetting_agent.auto_cleanup_enabled", True
            ),
            "stopwords_path": str(stopwords_dir),
            "graph_memory_enabled": graph_memory_enabled,
            "document_route_weight": cm.get("graph_memory.document_route_weight", 0.65),
            "graph_route_weight": cm.get("graph_memory.graph_route_weight", 0.35),
            "cross_route_bonus": cm.get("graph_memory.cross_route_bonus", 0.08),
            "graph_expansion_limit": cm.get("graph_memory.expansion_limit", 24),
            "graph_expansion_hops": cm.get("graph_memory.expansion_hops", 1),
            "graph_second_hop_weight": cm.get("graph_memory.second_hop_weight", 0.4),
            "dynamic_route_weighting": cm.get(
                "graph_memory.dynamic_route_weighting", True
            ),
            "graph_max_topics": cm.get("graph_memory.max_topics_per_memory", 6),
            "graph_max_participants": cm.get(
                "graph_memory.max_participants_per_memory", 8
            ),
            "graph_max_facts": cm.get("graph_memory.max_facts_per_memory", 8),
            "atom_enabled": cm.get("graph_memory.atom_enabled", True),
            "atom_maintenance_interval_hours": cm.get(
                "graph_memory.atom_maintenance_interval_hours", 24.0
            ),
            "atom_forget_delay_days": cm.get(
                "graph_memory.atom_forget_delay_days", 7.0
            ),
            "atom_purge_delay_days": cm.get("graph_memory.atom_purge_delay_days", 30.0),
            "index_rebuild_batch_size": cm.get("index_rebuild_settings.batch_size", 50),
            "index_rebuild_embedding_batch_size": cm.get(
                "index_rebuild_settings.embedding_batch_size", 8
            ),
            "index_rebuild_tasks_limit": cm.get(
                "index_rebuild_settings.tasks_limit", 1
            ),
            "index_rebuild_max_retries": cm.get(
                "index_rebuild_settings.max_retries", 5
            ),
            "index_rebuild_retry_base_delay": cm.get(
                "index_rebuild_settings.retry_base_delay", 30.0
            ),
            "index_rebuild_batch_delay": cm.get(
                "index_rebuild_settings.batch_delay", 5.0
            ),
            "index_rebuild_request_delay": cm.get(
                "index_rebuild_settings.request_delay", 5.0
            ),
            "index_rebuild_max_failure_ratio": cm.get(
                "index_rebuild_settings.max_failure_ratio", 0.02
            ),
            # === 请求级会话缓存（消除 Bridge→RecallHandler 重复检索） ===
            "session_cache_enabled": cm.get(
                "recall_engine.session_cache_enabled", True
            ),
            "session_cache_ttl_seconds": cm.get(
                "recall_engine.session_cache_ttl_seconds", 10.0
            ),
            # === 链式扩展（R2 多跳图/话题扩展） ===
            "recall_engine.max_chain_hops": cm.get(
                "recall_engine.max_chain_hops", 1
            ),
            "recall_engine.chain_hop_decay": cm.get(
                "recall_engine.chain_hop_decay", 0.65
            ),
            "recall_engine.chain_graph_expansion_enabled": cm.get(
                "recall_engine.chain_graph_expansion_enabled", True
            ),
            "recall_engine.chain_topic_expansion_enabled": cm.get(
                "recall_engine.chain_topic_expansion_enabled", True
            ),
            # === 测试效应（召回成功后的访问时间强化） ===
            "testing_effect_async": cm.get(
                "recall_engine.testing_effect_async", True
            ),
            "testing_effect_top_k": cm.get(
                "recall_engine.testing_effect_top_k", 5
            ),
            # === 重排序器 ===
            "reranker.enabled": cm.get("reranker.enabled", True),
            "reranker.strategy": cm.get("reranker.strategy", "mmr"),
            "reranker.llm_batch_size": cm.get("reranker.llm_batch_size", 10),
            "reranker.cross_encoder_lambda": cm.get(
                "reranker.cross_encoder_lambda", 0.7
            ),
            "reranker.mmr_lambda": cm.get("reranker.mmr_lambda", 0.7),
            # === 成本控制 ===
            "cost_control.mode": cm.get("cost_control.mode", "balanced"),
            "cost_control.max_extra_llm_calls_per_turn": cm.get(
                "cost_control.max_extra_llm_calls_per_turn", 0
            ),
            "cost_control.allow_llm_reranker_in_passive_recall": cm.get(
                "cost_control.allow_llm_reranker_in_passive_recall", False
            ),
            "cost_control.allow_llm_topic_strategy_d": cm.get(
                "cost_control.allow_llm_topic_strategy_d", False
            ),
            "cost_control.max_reflection_parallel_llm_calls": cm.get(
                "cost_control.max_reflection_parallel_llm_calls", 2
            ),
            "cost_control.llm_reranker_min_candidates": cm.get(
                "cost_control.llm_reranker_min_candidates", 12
            ),
            "cost_control.llm_reranker_prompt_chars": cm.get(
                "cost_control.llm_reranker_prompt_chars", 3000
            ),
        }
