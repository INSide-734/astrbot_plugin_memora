"""组合根的共享运行时组件构造工厂。"""

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from astrbot.api import logger
from astrbot.api.provider import Provider

from ...features.backup.application import BackupManager
from ...features.conversation.application.conversation_manager import (
    ConversationManager,
)
from ...features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from ...features.decay.application import DecayScheduler
from ...features.evolution.application import (
    DerivedRelationExpander,
    MemoryConsolidator,
    MemoryEvolutionCandidateGenerator,
    MemoryEvolutionGate,
    MemoryEvolutionManager,
    ProjectionReader,
    SemanticCompressor,
)
from ...features.evolution.infrastructure import MemoryEvolutionStore
from ...features.identity.application.scope_resolver import CanonicalScopeResolver
from ...features.injection.infrastructure.injection_decision_store import (
    InjectionDecisionStore,
)
from ...features.injection.infrastructure.recorder import InjectionDecisionRecorder
from ...features.knowledge.application import KnowledgeProposalPipeline
from ...features.knowledge.infrastructure import KnowledgeExtractor
from ...features.learning.domain.auto_learning_actions import aggregation_revision_for
from ...features.learning.infrastructure.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
    FeedbackLearningEvidenceProvider,
)
from ...features.memory.application.catalog_reconcile_scheduler import (
    TopicCatalogReconcileScheduler,
)
from ...features.memory.application.memory_engine import MemoryEngine
from ...features.memory.infrastructure.topic_metrics import build_metrics_recorder
from ...features.notes.application import NoteProposalPipeline
from ...features.notes.infrastructure import NoteGenerator
from ...features.profiles.application import ProfileProposalPipeline
from ...features.profiles.infrastructure import ProfileExtractor
from ...features.quality.application.gate_runtime import (
    GateRuntime,
    build_gate_snapshot,
    gate_snapshot_to_json,
)
from ...features.quality.application.memory_quality_gate import MemoryQualityGate
from ...features.quality.domain.gate_config import GateConfig
from ...features.quality.infrastructure.quarantine_store import (
    MemoryQuarantineStore,
)
from ...features.recall.processors.llm_client import LLMClient
from ...features.recall.processors.memory_processor import MemoryProcessor
from ...features.reflection.application import (
    SummaryScheduler,
    TopicBatchPreparer,
    TopicCandidateSelector,
)
from ...features.reflection.domain.summary_models import SummaryWindowContext
from ...features.retrieval.embedding_singleflight import InFlightEmbeddingProviderProxy
from ...shared.cost_control import CostControlConfig
from ...shared.errors import ProviderNotReadyError
from ...shared.summary_llm_limiter import SummaryLlmLimiter
from ..config.cost_control import build_cost_control_from_config
from ..provider.adapters import EmbeddingProviderAdapter, LLMProviderAdapter
from ..transport.realtime_hub import RealtimeHub
from .catalog_lifecycle import build_catalog_components, finalize_catalog_lifecycle
from .engine_runtime_config import build_engine_runtime_config
from .identity_component_factory import build_identity_runtime


class ComponentFactory:
    """创建并初始化所有核心组件"""

    def __init__(
        self,
        context,
        config_manager,
        data_dir: str,
        backup_manager: BackupManager | None = None,
    ):
        """保存上下文、配置、数据目录和唯一备份管理器。"""
        self.context = context
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.backup_manager = backup_manager

    async def build_all(
        self,
        embedding_provider,
        llm_provider,
        faiss_vec_db_cls,
        faiss_checker,
        db_setup,
    ) -> dict:
        """构造共享组件，并在失败时回滚本次已拥有的资源。"""
        cleanup_state: dict[str, object] = {}
        try:
            return await self._build_all_impl(
                embedding_provider,
                llm_provider,
                faiss_vec_db_cls,
                faiss_checker,
                db_setup,
                cleanup_state,
            )
        except BaseException:
            try:
                await self._rollback_cleanup_state(cleanup_state)
            except BaseException:
                logger.error("构造失败后的组件回滚异常", exc_info=True)
            raise
        finally:
            cleanup_state.clear()

    async def _build_all_impl(
        self,
        embedding_provider,
        llm_provider,
        faiss_vec_db_cls,
        faiss_checker,
        db_setup,
        cleanup_state: dict[str, object],
    ) -> dict:
        """按固定顺序构造全部共享组件。"""

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
        gate_section = self.config_manager.get_section("quality")
        if not isinstance(gate_section, dict):
            gate_section = {}
        gate_runtime = GateRuntime(
            build_gate_snapshot(
                GateConfig.model_validate(gate_section.get("gate") or {})
            )
        )
        cost_control_section = self.config_manager.get_section("cost_control")
        if not isinstance(cost_control_section, dict):
            cost_control_section = {}
        cost_control_config = CostControlConfig.model_validate(cost_control_section)
        cost_control = build_cost_control_from_config(cost_control_config)
        summary_llm_limiter = SummaryLlmLimiter(
            cost_control.max_reflection_parallel_llm_calls
        )
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
        cleanup_state["db"] = db

        graph_db = None
        if graph_memory_enabled:
            graph_db = faiss_vec_db_cls(
                str(graph_doc_path),
                str(graph_index_path),
                shared_embedding_provider,
            )
            cleanup_state["graph_db"] = graph_db

        memory_evolution_store = MemoryEvolutionStore(str(db_path))
        cleanup_state["memory_evolution_store"] = memory_evolution_store
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

        backup_manager = self.backup_manager or BackupManager(self.data_dir)

        stopwords_dir = data_dir_path / "stopwords"
        stopwords_dir.mkdir(parents=True, exist_ok=True)

        engine_config = self._build_engine_config(stopwords_dir, graph_memory_enabled)
        # Hub 由组合根创建，运行时关闭权移交给 PluginInitializer。
        # 延后到 Provider、配置和索引构造均通过后，缩小未登记资源的回滚窗口。
        realtime_hub = RealtimeHub(client_prefix="sse")
        cleanup_state["realtime_hub"] = realtime_hub
        engine_config["memory_evolution"] = evolution_config
        engine_config["cost_control_runtime"] = cost_control
        engine_config["derived_expander"] = derived_expander
        engine_config["projection_reader"] = projection_reader
        engine_config["backup_manager"] = backup_manager
        engine_config["realtime_hub"] = realtime_hub
        engine_config["auto_learning_evidence_provider"] = (
            FeedbackLearningEvidenceProvider(
                FeedbackLearningEvidenceInbox(self.data_dir),
                # 通用 evidence 端口隐藏了实际聚合 DTO，组合根在注入时恢复类型。
                aggregation_revision_provider=cast(
                    Callable[[Sequence[object]], str],
                    aggregation_revision_for,
                ),
                source_config_revision_provider=self._get_current_config_revision,
                quality_gate_version="quality-gate-v1",
            )
        )
        # MemoryEngine 仍是动态 facade，具体 feature 端口在初始化后按配置挂载。
        memory_engine: Any = MemoryEngine(
            db_path=str(db_path),
            faiss_db=db,
            graph_vector_db=graph_db,
            llm_provider=llm_provider,
            config=engine_config,
        )
        cleanup_state["memory_engine"] = memory_engine
        # graph_db 单独追踪；MemoryEngine 清理失败时仍需继续关闭它。
        # canonical Schema 迁移必须早于任何其他 memora.db 持久连接。
        await memory_engine.initialize()
        if graph_memory_enabled:
            assert graph_db is not None
            await asyncio.gather(db.initialize(), graph_db.initialize())
        else:
            await db.initialize()
        await memory_evolution_store.initialize()
        logger.info("数据库与索引组件已初始化")
        logger.info("MemoryEngine 已初始化")

        conversation_db_path = data_dir_path / "conversations.db"
        conversation_store = ConversationStore(str(conversation_db_path))
        cleanup_state["conversation_store"] = conversation_store
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
            },
            limiter=summary_llm_limiter,
            cost_control=cost_control,
            gate_runtime=gate_runtime,
            topic_embed_fn=topic_embedding_adapter.embed,
        )
        auxiliary_llm_client = LLMClient(
            self.context,
            llm_provider=llm_id if llm_id else None,
        )
        logger.info("MemoryProcessor 已初始化")

        memory_quarantine_store = MemoryQuarantineStore(
            data_dir_path / "memory_quarantine.sqlite3"
        )
        memory_quality_gate = None

        memory_evolution_gate = MemoryEvolutionGate(evolution_config)
        memory_evolution_consolidator = MemoryConsolidator(
            auxiliary_llm_client.call_llm_with_retry,
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
        cleanup_state["memory_evolution_manager"] = memory_evolution_manager
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
                extractor=ProfileExtractor(auxiliary_llm_client),
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
                extractor=KnowledgeExtractor(auxiliary_llm_client),
                cost_control=cost_control,
                expire_days=int(engine_config.get("knowledge_base.expire_days", 365)),
            )
        if memory_engine.note_manager is not None:
            note_min_length = int(engine_config.get("notes.auto_create_min_length", 50))
            memory_engine.note_proposal_pipeline = NoteProposalPipeline(
                note_manager=memory_engine.note_manager,
                source_store=memory_evolution_store,
                generator=NoteGenerator(
                    auxiliary_llm_client,
                    min_length=note_min_length,
                ),
                cost_control=cost_control,
                auto_create_min_length=note_min_length,
                max_tags=int(engine_config.get("notes.max_tags", 10)),
            )
        index_validator, derived_rebuild_coordinator = build_catalog_components(
            db_path, db, memory_engine, memory_evolution_manager
        )

        await memory_quarantine_store.initialize()
        memory_quality_gate = MemoryQualityGate(
            memory_quarantine_store,
            memory_engine=memory_engine,
            memory_processor=memory_processor,
            conversation_manager=conversation_manager,
            gate_runtime=gate_runtime,
        )
        conversation_store.quarantine_store = memory_quarantine_store
        logger.info("记忆质量隔离门已初始化")
        memory_engine.set_summary_source_validator(
            conversation_store.summary_source_fence_is_active
        )
        conversation_store.set_summary_canonical_owner_lookup(
            memory_engine.find_memory_id_by_idempotency_key
        )
        catalog_maintenance_result = await finalize_catalog_lifecycle(
            db_setup, index_validator, memory_engine, derived_rebuild_coordinator
        )

        summary_batch_preparer = TopicBatchPreparer(
            config_manager=self.config_manager,
            memory_engine=memory_engine,
            memory_processor=memory_processor,
            cost_control=cost_control,
        )
        scope_resolver = CanonicalScopeResolver()

        async def startup_context_factory(
            session_id: str, epoch: int, cursor: int
        ) -> SummaryWindowContext:
            """从持久化作用域构造启动扫描的固定上下文。"""
            (
                chat_type,
                group_id,
                scope_id,
                persona_id,
            ) = await conversation_store.get_summary_scope(session_id)
            persisted_reader = getattr(
                conversation_store, "get_summary_scope_snapshot", None
            )
            reader = cast(Callable[[str], Any], persisted_reader)
            persisted_snapshot = await reader(session_id) if callable(reader) else None
            scope_resolution = (
                scope_resolver.resolve_persisted(persisted_snapshot)
                if isinstance(persisted_snapshot, dict)
                else scope_resolver.resolve(None)
            )
            snapshot = gate_runtime.snapshot()
            return SummaryWindowContext(
                session_id=session_id,
                session_epoch=epoch,
                start_seq=cursor,
                end_seq=cursor,
                persona_id=persona_id,
                chat_type=chat_type,
                group_id=group_id,
                scope_id=scope_id,
                triggered_by="startup",
                gate_revision=snapshot.revision,
                gate_snapshot_json=gate_snapshot_to_json(snapshot),
                window_size=max(
                    2,
                    int(
                        self.config_manager.get(
                            "reflection_engine.summary_trigger_rounds", 10
                        )
                    )
                    * 2,
                ),
                scope_key=scope_resolution.scope_key,
                privacy_level=scope_resolution.privacy_level,
                resolver_revision=scope_resolution.resolver_revision,
                scope_reason_code=(
                    "scope_resolved"
                    if scope_resolution.available
                    else "scope_unavailable"
                ),
                scope_provenance_complete=(
                    True if scope_resolution.available else None
                ),
            )

        candidate_selector = TopicCandidateSelector(
            catalog_store=memory_engine.topic_catalog_store,
            text_processor=memory_engine.text_processor,
            conversation_formatter=memory_processor.formatter,
        )

        summary_scheduler = SummaryScheduler(
            cast(Any, conversation_store),
            memory_processor,
            memory_quality_gate,
            cast(Any, memory_engine),
            summary_batch_preparer,
            candidate_selector,
            metrics_recorder=await build_metrics_recorder(memory_engine, data_dir_path),
            config_manager=self.config_manager,
            max_parallel_summary_tasks=int(
                self.config_manager.get(
                    "reflection_engine.max_parallel_summary_tasks", 4
                )
            ),
            max_parallel_summary_tasks_per_session=int(
                self.config_manager.get(
                    "reflection_engine.max_parallel_summary_tasks_per_session", 2
                )
            ),
            limiter=summary_llm_limiter,
            startup_context_factory=startup_context_factory,
        )
        cleanup_state["summary_scheduler"] = summary_scheduler
        # 统一重建完成或安全降级后再启动 worker，避免 worker 与全量派生失效
        # 同时修改同一批 relation/projection。
        if memory_evolution_manager.mode != "disabled":
            await memory_evolution_manager.start()

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
            cleanup_state["decay_scheduler"] = scheduler
            await scheduler.start()
            decay_scheduler = scheduler
            logger.info("DecayScheduler 已启动")

        identity_runtime = await build_identity_runtime(
            self.data_dir, conversation_manager
        )
        cleanup_state["identity_runtime"] = identity_runtime
        conversation_manager.identity_runtime = identity_runtime

        catalog_reconcile_scheduler: TopicCatalogReconcileScheduler | None = None
        if memory_engine.topic_catalog_store is not None:
            catalog_reconcile_scheduler = TopicCatalogReconcileScheduler(
                catalog_store=memory_engine.topic_catalog_store,
                config_manager=self.config_manager,
            )
            cleanup_state["catalog_reconcile_scheduler"] = catalog_reconcile_scheduler
            await catalog_reconcile_scheduler.start()
            logger.info("TopicCatalogReconcileScheduler 已启动")

        injection_components = await self._build_injection_components(db_path)
        cleanup_state["injection_decision_store"] = injection_components.get(
            "injection_decision_store"
        )
        cleanup_state["injection_decision_recorder"] = injection_components.get(
            "injection_decision_recorder"
        )

        return {
            "db": db,
            "graph_db": graph_db,
            "memory_engine": memory_engine,
            "memory_processor": memory_processor,
            "memory_quarantine_store": memory_quarantine_store,
            "memory_quality_gate": memory_quality_gate,
            "gate_runtime": gate_runtime,
            "backup_manager": backup_manager,
            "conversation_manager": conversation_manager,
            "identity_runtime": identity_runtime,
            "index_validator": index_validator,
            "decay_scheduler": decay_scheduler,
            "catalog_reconcile_scheduler": catalog_reconcile_scheduler,
            "memory_evolution_store": memory_evolution_store,
            "memory_evolution_manager": memory_evolution_manager,
            "realtime_hub": realtime_hub,
            "summary_scheduler": summary_scheduler,
            "summary_llm_limiter": summary_llm_limiter,
            "catalog_maintenance_result": catalog_maintenance_result,
            "derived_rebuild_coordinator": derived_rebuild_coordinator,
            **injection_components,
        }

    async def _rollback_cleanup_state(self, cleanup_state: dict[str, object]) -> None:
        """回滚当前构造调用已取得的资源，并吞掉清理异常。"""
        await self._rollback_build_components(
            cleanup_state.get("decay_scheduler"),
            cleanup_state.get("conversation_store"),
            cleanup_state.get("memory_engine"),
            cleanup_state.get("graph_db"),
            cleanup_state.get("db"),
            cleanup_state.get("memory_evolution_manager"),
            cleanup_state.get("memory_evolution_store"),
            cleanup_state.get("identity_runtime"),
            cleanup_state.get("realtime_hub"),
            cleanup_state.get("summary_scheduler"),
            cleanup_state.get("catalog_reconcile_scheduler"),
            injection_decision_recorder=cleanup_state.get(
                "injection_decision_recorder"
            ),
            injection_decision_store=cleanup_state.get("injection_decision_store"),
        )

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
        summary_scheduler=None,
        catalog_reconcile_scheduler=None,
        injection_decision_recorder=None,
        injection_decision_store=None,
    ) -> None:
        if memory_engine is not None and graph_db is not None:
            if getattr(memory_engine, "graph_vector_db", None) is graph_db:
                memory_engine.graph_vector_db = None
        cleanup_steps = (
            ("SummaryScheduler", summary_scheduler, "close"),
            ("DecayScheduler", decay_scheduler, "stop"),
            ("TopicCatalogReconcileScheduler", catalog_reconcile_scheduler, "stop"),
            ("MemoryEngine.stop_pending_tasks", memory_engine, "stop_pending_tasks"),
            ("MemoryEvolutionManager", memory_evolution_manager, "stop"),
            ("MemoryEvolutionStore", memory_evolution_store, "close"),
            ("ProtocolIdentityRuntime", identity_runtime, "close"),
            ("InjectionDecisionRecorder", injection_decision_recorder, "close"),
            ("InjectionDecisionStore", injection_decision_store, "close"),
            ("RealtimeHub", realtime_hub, "close"),
            ("ConversationStore", conversation_store, "close"),
            ("MemoryEngine", memory_engine, "close"),
            ("GraphDB", graph_db, "close"),
            ("DB", db, "close"),
        )
        cancellation: asyncio.CancelledError | None = None
        closed_ids: set[int] = set()
        for label, component, method_name in cleanup_steps:
            if component is None or id(component) in closed_ids:
                continue
            closed_ids.add(id(component))
            try:
                await getattr(component, method_name)()
            except asyncio.CancelledError as cleanup_error:
                cancellation = cancellation or cleanup_error
                logger.error("回滚组件 %s 被取消", label)
            except BaseException as cleanup_error:
                # 回滚必须继续尝试后续资源；build_all 重新抛出原始异常。
                logger.error(
                    "回滚组件 %s 失败: %s",
                    label,
                    type(cleanup_error).__name__,
                )
        if cancellation is not None:
            raise cancellation

    async def _build_injection_components(self, db_path: Path) -> dict[str, object]:
        """初始化注入决策存储与异步记录器。"""
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
            cancellation: asyncio.CancelledError | None = None
            try:
                if decision_recorder is not None:
                    await decision_recorder.close(timeout=5.0)
            except asyncio.CancelledError as error:
                cancellation = error
            except BaseException:
                logger.error("关闭注入决策记录器失败")
            try:
                await decision_store.close()
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except BaseException:
                logger.error("关闭注入决策存储失败")
            if cancellation is not None:
                raise cancellation
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


__all__ = ["ComponentFactory"]
