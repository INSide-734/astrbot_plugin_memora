"""组件构造工厂 — _complete_initialization 的核心逻辑"""

import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.core.provider.provider import Provider

from ..base.config_validator import CostControlConfig
from ..base.cost_control import build_cost_control_from_config
from ..base.exceptions import ProviderNotReadyError
from ..evaluation.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
)
from ..features.identity import ProtocolIdentityStore
from ..identity.conversation_sync import ConversationIdentitySynchronizer
from ..identity.memory import MemoryIdentityEnricher
from ..identity.resolver import ProtocolIdentityResolver
from ..identity.runtime import ProtocolIdentityRuntime
from ..identity.service import ProtocolIdentityService
from ..injection.recorder import InjectionDecisionRecorder
from ..managers.auto_learning_actions import aggregation_revision_for
from ..managers.backup_manager import BackupManager
from ..managers.conversation_manager import ConversationManager
from ..managers.knowledge_proposal_pipeline import KnowledgeProposalPipeline
from ..managers.memory_engine import MemoryEngine
from ..managers.memory_evolution_gate import MemoryEvolutionGate
from ..managers.memory_evolution_manager import MemoryEvolutionManager
from ..managers.note_proposal_pipeline import NoteProposalPipeline
from ..managers.profile_proposal_pipeline import ProfileProposalPipeline
from ..managers.semantic_compressor import SemanticCompressor
from ..platform.transport.realtime_hub import RealtimeHub
from ..processors.knowledge_extractor import KnowledgeExtractor
from ..processors.memory_consolidator import MemoryConsolidator
from ..processors.memory_evolution_candidates import MemoryEvolutionCandidateGenerator
from ..processors.memory_processor import MemoryProcessor
from ..processors.note_generator import NoteGenerator
from ..processors.profile_extractor import ProfileExtractor
from ..provider_adapters import EmbeddingProviderAdapter, LLMProviderAdapter
from ..retrieval.derived_relation_expander import DerivedRelationExpander
from ..retrieval.embedding_singleflight import InFlightEmbeddingProviderProxy
from ..retrieval.projection_reader import ProjectionReader
from ..review.memory_quality_gate import MemoryQualityGate
from ..review.quarantine_store import MemoryQuarantineStore
from ..schedulers.decay_scheduler import DecayScheduler
from ..storage.conversation_store import ConversationStore
from ..storage.injection_decision_store import InjectionDecisionStore
from ..storage.memory_evolution_store import MemoryEvolutionStore
from ..validators.index_validator import IndexValidator
from .derived_rebuild_coordinator import DerivedRebuildCoordinator
from .engine_runtime_config import build_engine_runtime_config


class ComponentFactory:
    """创建并初始化所有核心组件"""

    def __init__(self, context, config_manager, data_dir: str):
        """保存共享上下文、已解析配置和插件数据目录。

        参数:
            context: AstrBot 运行时上下文。
            config_manager: 已完成默认合并和校验的配置管理器。
            data_dir: Memora 持久化数据目录。
        """

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
        """验证 Provider 能力并按固定顺序构造全部共享组件。

        参数:
            embedding_provider: 当前 Embedding Provider。
            llm_provider: 当前聊天 Provider。
            faiss_vec_db_cls: AstrBot FAISS 数据库构造器。
            faiss_checker: 索引维度检查与修复协作对象。
            db_setup: 索引重建和会话计数修复协作对象。

        返回:
            供初始化器发布的共享组件字典。

        异常:
            ProviderNotReadyError: Provider 缺失、类型错误或能力不足。
            asyncio.CancelledError: 初始化或失败回滚被取消。
            Exception: 组件初始化失败；已创建组件会按既有顺序尽力回滚。
        """

        data_dir_path = Path(self.data_dir)

        db_path = data_dir_path / "memora.db"
        index_path = data_dir_path / "memora.index"
        graph_doc_path = data_dir_path / "memora_graph_documents.db"
        graph_index_path = data_dir_path / "memora_graph.index"
        graph_memory_enabled = self.config_manager.get("graph_memory.enabled", True)
        semantic_compression_enabled = bool(
            self.config_manager.get("semantic_compression.enabled", False)
        )
        evolution_config = self.config_manager.get_section("memory_evolution")
        if not isinstance(evolution_config, dict):
            evolution_config = {}
        episode_config = self.config_manager.get_section("episode_clustering")
        if not isinstance(episode_config, dict):
            episode_config = {}
        cost_control_section = self.config_manager.get_section("cost_control")
        if not isinstance(cost_control_section, dict):
            cost_control_section = {}
        cost_control_config = CostControlConfig.model_validate(cost_control_section)
        cost_control = build_cost_control_from_config(cost_control_config)

        if not embedding_provider:
            raise ProviderNotReadyError("Embedding Provider 未初始化")
        if not llm_provider or not isinstance(llm_provider, Provider):
            raise ProviderNotReadyError("LLM Provider 未初始化或类型不正确")
        try:
            EmbeddingProviderAdapter.from_provider(embedding_provider)
            LLMProviderAdapter.from_provider(llm_provider)
        except RuntimeError as exc:
            raise ProviderNotReadyError("Provider 缺少 Memora 必需能力") from exc

        await faiss_checker.check_and_fix_dimension_mismatch(
            str(index_path), embedding_provider
        )
        if graph_memory_enabled:
            await faiss_checker.check_and_fix_dimension_mismatch(
                str(graph_index_path), embedding_provider
            )

        shared_embedding_provider = InFlightEmbeddingProviderProxy(embedding_provider)
        topic_embedding_adapter = EmbeddingProviderAdapter.from_provider(
            shared_embedding_provider
        )

        # 只构造适配器；持久连接必须等待 canonical Schema 迁移完成。
        db = faiss_vec_db_cls(
            str(db_path),
            str(index_path),
            shared_embedding_provider,
        )

        graph_db = None
        if graph_memory_enabled:
            graph_db = faiss_vec_db_cls(
                str(graph_doc_path),
                str(graph_index_path),
                shared_embedding_provider,
            )

        memory_evolution_store = MemoryEvolutionStore(str(db_path))
        derived_expander = None
        projection_reader = None
        if bool(evolution_config.get("enabled", False)) and str(
            evolution_config.get("mode", "disabled")
        ) in {"readonly", "active"}:
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
            projection_reader = ProjectionReader(
                memory_evolution_store,
                projection_limit=max(
                    0,
                    int(evolution_config.get("max_query_expansions", 8)),
                ),
                disabled_types=(
                    () if semantic_compression_enabled else ("semantic_summary",)
                ),
            )

        backup_manager = BackupManager(self.data_dir)

        stopwords_dir = data_dir_path / "stopwords"
        stopwords_dir.mkdir(parents=True, exist_ok=True)

        engine_config = self._build_engine_config(stopwords_dir, graph_memory_enabled)
        # Hub 由组合根创建，运行时关闭权移交给 PluginInitializer。
        # 延后到 Provider、配置和索引构造均通过后，缩小未登记资源的回滚窗口。
        realtime_hub = RealtimeHub(client_prefix="sse")
        engine_config["memory_evolution"] = evolution_config
        engine_config["cost_control_runtime"] = cost_control
        engine_config["derived_expander"] = derived_expander
        engine_config["projection_reader"] = projection_reader
        engine_config["backup_manager"] = backup_manager
        engine_config["realtime_hub"] = realtime_hub
        engine_config["auto_learning_evidence_provider"] = (
            FeedbackLearningEvidenceProvider(
                FeedbackLearningEvidenceInbox(self.data_dir),
                aggregation_revision_provider=aggregation_revision_for,
                source_config_revision_provider=self._get_current_config_revision,
                quality_gate_version="quality-gate-v1",
            )
        )
        memory_engine = None
        try:
            memory_engine = MemoryEngine(
                db_path=str(db_path),
                faiss_db=db,
                graph_vector_db=graph_db,
                llm_provider=llm_provider,
                config=engine_config,
            )
            # canonical Schema 迁移必须早于任何其他 memora.db 持久连接。
            await memory_engine.initialize()
            if graph_memory_enabled:
                await asyncio.gather(db.initialize(), graph_db.initialize())
            else:
                await db.initialize()
            await memory_evolution_store.initialize()
        except BaseException:
            await self._rollback_build_components(
                None,
                None,
                memory_engine,
                graph_db,
                db,
                None,
                memory_evolution_store,
                realtime_hub,
            )
            raise
        logger.info("数据库与索引组件已初始化")
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
                "atom_classifier.negation_detection_enabled": engine_config[
                    "atom_classifier.negation_detection_enabled"
                ],
                **{
                    key: engine_config[key]
                    for key in (
                        "atom_quality_filter_enabled",
                        "atom_min_confidence",
                        "atom_min_importance",
                        "atom_min_content_length",
                        "atom_info_check_enabled",
                        "atom_probationary_enabled",
                        "atom_probationary_ttl_days",
                        "atom_dedup_enabled",
                        "atom_dedup_threshold",
                    )
                },
                "group_chat_template": self.config_manager.get(
                    "prompt_templates.group_chat_template", ""
                ),
                "private_chat_template": self.config_manager.get(
                    "prompt_templates.private_chat_template", ""
                ),
                "topic_segmentation.enabled": self.config_manager.get(
                    "topic_segmentation.enabled", True
                ),
                "topic_segmentation.strategy": self.config_manager.get(
                    "topic_segmentation.strategy", "a_b_hybrid"
                ),
                "topic_segmentation.strategy_b.similarity_threshold": (
                    self.config_manager.get(
                        "topic_segmentation.strategy_b.similarity_threshold",
                        0.5,
                    )
                ),
                "topic_segmentation.strategy_b.min_cluster_size": (
                    self.config_manager.get(
                        "topic_segmentation.strategy_b.min_cluster_size",
                        1,
                    )
                ),
                "topic_segmentation.strategy_b.max_clusters": self.config_manager.get(
                    "topic_segmentation.strategy_b.max_clusters",
                    5,
                ),
                "topic_segmentation.hybrid_fallback_fact_threshold": (
                    self.config_manager.get(
                        "topic_segmentation.hybrid_fallback_fact_threshold",
                        3,
                    )
                ),
                "persona_interpretation.enabled": self.config_manager.get(
                    "persona_interpretation.enabled", False
                ),
            },
            cost_control=cost_control,
            topic_embed_fn=topic_embedding_adapter.embed,
        )
        logger.info("MemoryProcessor 已初始化")

        memory_quarantine_store = MemoryQuarantineStore(
            data_dir_path / "memory_quarantine.sqlite3"
        )
        memory_quality_gate = None

        memory_evolution_gate = MemoryEvolutionGate(evolution_config)
        memory_evolution_consolidator = MemoryConsolidator(
            memory_processor.llm_client.call_llm_with_retry,
            evolution_config,
        )
        memory_evolution_candidate_generator = MemoryEvolutionCandidateGenerator(
            episode_config=episode_config,
        )
        memory_evolution_manager = MemoryEvolutionManager(
            memory_evolution_store,
            memory_evolution_gate,
            memory_evolution_consolidator,
            evolution_config,
            candidate_generator=memory_evolution_candidate_generator,
        )
        # CRUD 提交后只注入同一 SQLite 上的派生 Store；canonical 仍由
        # MemoryEngine/DocumentStorage 唯一写入，避免形成第二套正文权威。
        memory_engine.memory_evolution_store = memory_evolution_store
        memory_engine.memory_evolution_manager = memory_evolution_manager
        if semantic_compression_enabled and memory_evolution_manager.mode in {
            "shadow",
            "readonly",
            "active",
        }:
            memory_engine.semantic_compressor = SemanticCompressor(
                source_store=memory_evolution_store,
                proposal_applier=memory_evolution_manager.apply_projection_proposal,
                enabled=True,
                age_days=float(
                    engine_config.get("semantic_compression.age_days", 60.0)
                ),
                similarity_threshold=float(
                    engine_config.get(
                        "semantic_compression.similarity_threshold",
                        0.85,
                    )
                ),
            )
        if memory_engine.profile_manager is not None:
            memory_engine.profile_proposal_pipeline = ProfileProposalPipeline(
                profile_manager=memory_engine.profile_manager,
                source_store=memory_evolution_store,
                get_memory=memory_engine.get_memory,
                extractor=ProfileExtractor(memory_processor.llm_client),
                cost_control=cost_control,
                min_tag_confidence=float(
                    engine_config.get("user_profile.min_tag_confidence", 0.1)
                ),
            )
        if memory_engine.knowledge_manager is not None:
            memory_engine.knowledge_proposal_pipeline = KnowledgeProposalPipeline(
                knowledge_manager=memory_engine.knowledge_manager,
                source_store=memory_evolution_store,
                get_memory=memory_engine.get_memory,
                extractor=KnowledgeExtractor(memory_processor.llm_client),
                cost_control=cost_control,
                expire_days=int(engine_config.get("knowledge_base.expire_days", 365)),
            )
        if memory_engine.note_manager is not None:
            note_min_length = int(engine_config.get("notes.auto_create_min_length", 50))
            memory_engine.note_proposal_pipeline = NoteProposalPipeline(
                note_manager=memory_engine.note_manager,
                source_store=memory_evolution_store,
                generator=NoteGenerator(
                    memory_processor.llm_client,
                    min_length=note_min_length,
                ),
                cost_control=cost_control,
                auto_create_min_length=note_min_length,
                max_tags=int(engine_config.get("notes.max_tags", 10)),
            )
        index_validator = IndexValidator(str(db_path), db)
        derived_rebuild_coordinator = DerivedRebuildCoordinator(
            index_validator,
            memory_engine,
            memory_evolution_manager,
        )
        try:
            await memory_quarantine_store.initialize()
            memory_quality_gate = MemoryQualityGate(
                memory_quarantine_store,
                memory_engine=memory_engine,
                memory_processor=memory_processor,
                conversation_manager=conversation_manager,
            )
            logger.info("记忆质量隔离门已初始化")
            await db_setup.auto_rebuild_index_if_needed(
                index_validator,
                memory_engine,
                derived_rebuild_coordinator,
            )

            # 统一重建完成或安全降级后再启动 worker，避免 worker 与全量派生失效
            # 同时修改同一批 relation/projection。
            if memory_evolution_manager.mode != "disabled":
                await memory_evolution_manager.start()
        except BaseException:
            await self._rollback_build_components(
                None,
                conversation_store,
                memory_engine,
                graph_db,
                db,
                memory_evolution_manager,
                memory_evolution_store,
                realtime_hub,
            )
            raise

        if memory_engine and hasattr(memory_engine, "text_processor"):
            tp = memory_engine.text_processor
            if tp and hasattr(tp, "async_init"):
                await tp.async_init()
                logger.info("TextProcessor 停用词已加载")

        decay_rate = self.config_manager.get("importance_decay.decay_rate", 0.01)
        auto_cleanup = self.config_manager.get(
            "forgetting_agent.auto_cleanup_enabled", True
        )
        backup_enabled = bool(self.config_manager.get("backup_settings.enabled", True))
        decay_scheduler = None
        should_start_decay_scheduler = bool(
            memory_engine
            and (
                decay_rate > 0
                or auto_cleanup
                or backup_enabled
                or bool(engine_config.get("auto_learning.enabled", False))
                or memory_engine.semantic_compressor is not None
                or memory_engine.anomaly_detector is not None
            )
        )
        if should_start_decay_scheduler:
            backup_keep_days = int(
                self.config_manager.get("backup_settings.keep_days", 7)
            )
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
            identity_runtime = await self._build_identity_runtime(conversation_manager)
        except BaseException:
            await self._rollback_build_components(
                decay_scheduler,
                conversation_store,
                memory_engine,
                graph_db,
                db,
                memory_evolution_manager,
                memory_evolution_store,
                realtime_hub=realtime_hub,
            )
            raise
        conversation_manager.identity_runtime = identity_runtime

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
                identity_runtime,
                realtime_hub,
            )
            raise

        return {
            "db": db,
            "graph_db": graph_db,
            "memory_engine": memory_engine,
            "memory_processor": memory_processor,
            "memory_quarantine_store": memory_quarantine_store,
            "memory_quality_gate": memory_quality_gate,
            "backup_manager": backup_manager,
            "conversation_manager": conversation_manager,
            "identity_runtime": identity_runtime,
            "index_validator": index_validator,
            "decay_scheduler": decay_scheduler,
            "memory_evolution_store": memory_evolution_store,
            "memory_evolution_manager": memory_evolution_manager,
            "realtime_hub": realtime_hub,
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
        identity_runtime=None,
        realtime_hub=None,
    ) -> None:
        """按依赖顺序尽力关闭工厂已经拥有的组件。"""

        cleanup_steps = (
            ("MemoryEvolutionManager", memory_evolution_manager, "stop"),
            ("MemoryEvolutionStore", memory_evolution_store, "close"),
            ("DecayScheduler", decay_scheduler, "stop"),
            ("ProtocolIdentityRuntime", identity_runtime, "close"),
            ("RealtimeHub", realtime_hub, "close"),
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

    async def _build_identity_runtime(
        self,
        conversation_manager: ConversationManager,
    ) -> ProtocolIdentityRuntime:
        """构建身份目录运行时，普通初始化失败时降级为仅解析模式。"""

        resolver = ProtocolIdentityResolver.default()
        store = ProtocolIdentityStore(str(Path(self.data_dir) / "memora.db"))
        try:
            await store.initialize()
        except asyncio.CancelledError:
            try:
                await store.close()
            except BaseException:
                pass
            raise
        except Exception:
            try:
                await store.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            logger.warning("协议身份目录初始化失败，已降级为仅解析模式")
            return ProtocolIdentityRuntime(resolver)

        service = ProtocolIdentityService(store)
        synchronizer = ConversationIdentitySynchronizer(
            conversation_manager.store,
            service,
            conversation_manager.invalidate_cache,
        )
        return ProtocolIdentityRuntime(
            resolver,
            service=service,
            synchronizer=synchronizer,
            store=store,
            enricher=MemoryIdentityEnricher(store),
        )

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
        """把已校验配置投影为 MemoryEngine 使用的运行时白名单快照。

        参数:
            stopwords_dir: 文本处理器加载停用词的目录。
            graph_memory_enabled: 本次装配是否启用图记忆能力。

        返回:
            包含显式字段所有权与生效语义的引擎配置副本。
        """

        return build_engine_runtime_config(
            self.config_manager,
            data_dir=self.data_dir,
            stopwords_dir=stopwords_dir,
            graph_memory_enabled=graph_memory_enabled,
        )

    async def _get_current_config_revision(self) -> str:
        """从 ConfigManager 权威快照取得当前配置 revision。"""

        _snapshot, revision = await self.config_manager.get_config_snapshot_async()
        return revision
