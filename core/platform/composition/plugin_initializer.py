"""平台组合根的插件初始化编排器。"""

import asyncio
import inspect
import time
from pathlib import Path
from typing import Any, cast

from astrbot.api import logger
from astrbot.api.provider import Provider
from astrbot.api.star import Context

from ...features.backfill.application import BackfillScheduler
from ...features.conversation.application.conversation_manager import (
    ConversationManager,
)
from ...features.decay.application import DecayScheduler
from ...features.identity.application.runtime import ProtocolIdentityRuntime
from ...features.injection.infrastructure.injection_decision_store import (
    InjectionDecisionStore,
)
from ...features.injection.infrastructure.recorder import InjectionDecisionRecorder
from ...features.memory.application.memory_engine import MemoryEngine
from ...features.memory.infrastructure.validators import IndexValidator
from ...features.observability.application import MemoryQualityScorer
from ...features.observability.infrastructure.debug_reporter import (
    report_debug_event,
    report_debug_exception,
)
from ...features.quality.application.gate_runtime import GateRuntime
from ...features.quality.application.memory_quality_gate import MemoryQualityGate
from ...features.quality.infrastructure.quarantine_store import (
    MemoryQuarantineStore,
)
from ...features.recall.processors.memory_processor import MemoryProcessor
from ...shared.contracts import PromptProtectionPort
from ...shared.errors import InitializationError
from ..config.feature_config import is_jargon_discovery_enabled
from ..config.manager import ConfigManager
from ..security import build_prompt_protection_port
from ..transport.realtime_hub import RealtimeHub
from .component_factory import ComponentFactory
from .db_setup import DatabaseSetup
from .faiss_checker import FaissChecker
from .identity_lifecycle import close_identity_runtime_after_failure
from .provider_loader import ProviderLoader
from .provider_waiter import ProviderWaiter
from .readiness import InitializerReadinessMixin


class PluginInitializer(InitializerReadinessMixin):
    """插件初始化器"""

    # 关停超时配置（秒）
    SHUTDOWN_STEP_TIMEOUT: float = 8.0
    TASK_CANCEL_TIMEOUT: float = 3.0

    def __init__(self, context: Context, config_manager: ConfigManager, data_dir: str):
        """初始化插件共享组件的占位引用和质量评分器。

        参数:
            context: AstrBot 运行时上下文。
            config_manager: 已加载的插件配置管理器。
            data_dir: 插件持久化数据目录。
        """

        self.context = context
        self.config_manager = config_manager
        self.data_dir = data_dir
        # AstrBot 4.27.2 未公开 EmbeddingProvider 类型，能力由下游适配器验证。
        self.embedding_provider: Any | None = None
        self.llm_provider: Provider | None = None
        self.db: Any | None = None
        self.graph_db: Any | None = None
        self.memory_engine: MemoryEngine | None = None
        self.quality_scorer: MemoryQualityScorer = MemoryQualityScorer(window_size=100)
        self.memory_processor: MemoryProcessor | None = None
        self.memory_quarantine_store: MemoryQuarantineStore | None = None
        self.memory_quality_gate: MemoryQualityGate | None = None
        self.gate_runtime: GateRuntime | None = None
        self.conversation_manager: ConversationManager | None = None
        self.identity_runtime: ProtocolIdentityRuntime | None = None
        self.index_validator: IndexValidator | None = None
        self.decay_scheduler: DecayScheduler | None = None
        self.backfill_scheduler: BackfillScheduler | None = None
        self.injection_decision_store: InjectionDecisionStore | None = None
        self.injection_decision_recorder: InjectionDecisionRecorder | None = None
        self.memory_evolution_store: Any | None = None
        self.memory_evolution_manager: Any | None = None
        self.affection_store: Any | None = None
        self.affection_manager: Any | None = None
        self.expression_store: Any | None = None
        self.expression_learner: Any | None = None
        self.jargon_filter: Any | None = None
        self.jargon_store: Any | None = None
        self.jargon_miner: Any | None = None
        self.jargon_query_service: Any | None = None
        self.relation_store: Any | None = None
        self.relation_manager: Any | None = None
        self.prompt_protection: PromptProtectionPort | None = None
        self.realtime_hub: RealtimeHub | None = None
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()
        self._injection_close_lock = asyncio.Lock()
        self._evolution_close_lock = asyncio.Lock()
        self._initialization_failed = False
        self._initialization_error: str | None = None
        self._provider_loader = ProviderLoader(context, config_manager)
        self._provider_waiter = ProviderWaiter(max_attempts=60)
        self._faiss_checker = FaissChecker()
        self._db_setup = DatabaseSetup(config_manager)
        self._component_factory = ComponentFactory(context, config_manager, data_dir)

        # 重试回调：Provider 后台重试结束后提交唯一终态
        self._provider_waiter.on_terminal_callback = self._on_providers_ready

    async def initialize(self) -> bool:
        """在互斥门内初始化插件，并返回最终就绪状态。"""

        async with self._initialization_lock:
            if self._initialization_complete or self._initialization_failed:
                return self._initialization_complete
            return await self._initialize_once()

    async def _initialize_once(self) -> bool:
        """在初始化门内只执行一次 Provider 等待与组件构建。"""
        logger.info("记忆插件开始后台初始化...")
        provider_wait_started = time.perf_counter()
        report_debug_event(
            "provider_state",
            component="initializer",
            stage="provider_wait",
            status="started",
            reason_code="provider_wait_started",
        )

        try:
            emb, llm, ready = await self._provider_waiter.wait_non_blocking(
                self._provider_loader,
                self.embedding_provider,
                self.llm_provider,
            )
            self.embedding_provider, self.llm_provider = emb, llm

            report_debug_event(
                "provider_state",
                component="initializer",
                stage="provider_wait",
                status="completed" if ready else "degraded",
                reason_code="provider_ready" if ready else "provider_retry_scheduled",
                duration_ms=max(
                    0.0, (time.perf_counter() - provider_wait_started) * 1000.0
                ),
                attempt_count=max(0, int(self._provider_waiter.attempts)),
                capability="embedding_and_llm_ready" if ready else "provider_waiting",
            )

            if not ready:
                missing = []
                if not self.embedding_provider:
                    missing.append("向量嵌入提供器（请在 AstrBot 中配置向量嵌入模型）")
                if not self.llm_provider:
                    missing.append("LLM 提供器（请在 AstrBot 中配置语言模型）")
                logger.warning(
                    f"以下提供器暂时不可用，将在后台继续尝试: {', '.join(missing)}"
                )
                self._provider_waiter.start_retry_if_needed(
                    self._provider_loader,
                    self.embedding_provider,
                    self.llm_provider,
                )
                return False

            await self._run_full_init()
            return True

        except asyncio.CancelledError:
            report_debug_event(
                "provider_state",
                component="initializer",
                stage="provider_wait",
                status="cancelled",
                reason_code="initialization_cancelled",
                duration_ms=max(
                    0.0, (time.perf_counter() - provider_wait_started) * 1000.0
                ),
            )
            raise
        except Exception as e:
            report_debug_exception(
                "plugin_failed",
                e,
                component="initializer",
                stage="startup",
                status="failed",
                reason_code="initialization_error",
                duration_ms=max(
                    0.0, (time.perf_counter() - provider_wait_started) * 1000.0
                ),
            )
            logger.error(f"记忆插件初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            return False

    async def _on_providers_ready(self, emb, llm, *, exhausted: bool = False):
        """在初始化门内提交 Provider 重试的就绪或耗尽终态。"""
        try:
            async with self._initialization_lock:
                if self.is_initialized or self.is_failed:
                    return
                self.embedding_provider, self.llm_provider = emb, llm
                if exhausted:
                    self._initialization_failed = True
                    self._initialization_error = "Provider 重试预算耗尽"
                    return
                report_debug_event(
                    "provider_state",
                    component="initializer",
                    stage="provider_retry",
                    status="completed",
                    reason_code="providers_available_after_retry",
                    attempt_count=max(0, int(self._provider_waiter.attempts)),
                    capability="embedding_and_llm_ready",
                )
                await self._run_full_init()
        except Exception as e:
            logger.error(f"重试初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)

    async def _run_full_init(self):
        """构建组件并发布共享运行时引用。

        质量评分器在此与 ``MemoryEngine`` 绑定，确保写入链、质量接口和
        指标摘要读取同一个进程内历史窗口。
        """
        logger.info("开始完整初始化流程...")
        initialization_started = time.perf_counter()
        current_stage = "component_build"
        owns_injection_components = False
        owns_evolution_components = False
        report_debug_event(
            "plugin_initialized",
            component="initializer",
            stage="full_initialization",
            status="started",
            reason_code="full_initialization_started",
        )
        try:
            component_started = time.perf_counter()
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="component_build",
                status="started",
                reason_code="component_build_started",
            )
            faiss_cls = self._faiss_checker.load_vec_db_class()
            components = await self._component_factory.build_all(
                self.embedding_provider,
                self.llm_provider,
                faiss_cls,
                self._faiss_checker,
                self._db_setup,
            )
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="component_build",
                status="completed",
                reason_code="component_build_completed",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
                count=len(components),
            )

            current_stage = "runtime_publish"
            publish_started = time.perf_counter()
            self.db = components["db"]
            self.graph_db = components["graph_db"]
            self.memory_engine = components["memory_engine"]
            # MemoryEngine 是动态 facade，质量评分器由组合根在发布阶段挂载。
            cast(Any, self.memory_engine)._quality_scorer = self.quality_scorer
            self.memory_processor = components["memory_processor"]
            self.memory_quarantine_store = components["memory_quarantine_store"]
            self.memory_quality_gate = components["memory_quality_gate"]
            self.gate_runtime = components["gate_runtime"]
            self.identity_runtime = components["identity_runtime"]
            self.index_validator = components["index_validator"]
            self.decay_scheduler = components["decay_scheduler"]
            self.injection_decision_store = components["injection_decision_store"]
            self.injection_decision_recorder = components["injection_decision_recorder"]
            self.memory_evolution_store = components.get("memory_evolution_store")
            self.memory_evolution_manager = components.get("memory_evolution_manager")
            self.realtime_hub = components.get("realtime_hub")
            owns_injection_components = True
            owns_evolution_components = bool(
                self.memory_evolution_store or self.memory_evolution_manager
            )
            self.prompt_protection = self._create_prompt_protection_service()
            assert self.memory_processor is not None
            self.memory_processor.prompt_protection_service = self.prompt_protection

            for capability, instance in (
                ("database", self.db),
                ("graph_database", self.graph_db),
                ("memory_engine", self.memory_engine),
                ("memory_processor", self.memory_processor),
                ("memory_quarantine_store", self.memory_quarantine_store),
                ("memory_quality_gate", self.memory_quality_gate),
                ("gate_runtime", self.gate_runtime),
                ("conversation_manager", self.conversation_manager),
                ("identity_runtime", self.identity_runtime),
                ("index_validator", self.index_validator),
                ("decay_scheduler", self.decay_scheduler),
                ("injection_store", self.injection_decision_store),
                ("injection_recorder", self.injection_decision_recorder),
                ("memory_evolution_store", self.memory_evolution_store),
                ("memory_evolution_manager", self.memory_evolution_manager),
                ("prompt_protection", self.prompt_protection),
                ("realtime_hub", self.realtime_hub),
            ):
                is_ready = instance is not None
                report_debug_event(
                    "plugin_initialized",
                    component="initializer",
                    stage="component_readiness",
                    status="ready" if is_ready else "disabled",
                    reason_code="component_ready" if is_ready else "component_inactive",
                    capability=capability,
                )

            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="runtime_publish",
                status="completed",
                reason_code="core_components_published",
                duration_ms=max(0.0, (time.perf_counter() - publish_started) * 1000.0),
                success_count=sum(
                    component is not None
                    for component in (
                        self.db,
                        self.memory_engine,
                        self.memory_processor,
                        self.conversation_manager,
                        self.index_validator,
                    )
                ),
            )

            current_stage = "cognitive_components"
            await self._initialize_cognitive_components()

            current_stage = "scheduler_build"
            self.backfill_scheduler = BackfillScheduler(
                memory_engine=self.memory_engine,
                config={
                    "enabled": self.config_manager.get(
                        "topic_segmentation.legacy_backfill.enabled"
                    ),
                    "batch_size": self.config_manager.get(
                        "topic_segmentation.legacy_backfill.batch_size"
                    ),
                    "max_backfill_per_run": self.config_manager.get(
                        "topic_segmentation.legacy_backfill.max_backfill_per_run"
                    ),
                },
                embed_fn=getattr(self.embedding_provider, "get_embeddings", None),
            )
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="component_readiness",
                status="ready",
                reason_code="component_ready",
                capability="backfill_scheduler",
            )

            self._initialization_complete = True
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="full_initialization",
                status="completed",
                reason_code="full_initialization_completed",
                duration_ms=max(
                    0.0, (time.perf_counter() - initialization_started) * 1000.0
                ),
            )
            logger.info("记忆插件初始化成功。")
        except BaseException as e:
            duration_ms = max(
                0.0, (time.perf_counter() - initialization_started) * 1000.0
            )
            if isinstance(e, asyncio.CancelledError):
                report_debug_event(
                    "plugin_failed",
                    component="initializer",
                    stage=current_stage,
                    status="cancelled",
                    reason_code="full_initialization_cancelled",
                    duration_ms=duration_ms,
                )
            else:
                report_debug_exception(
                    "plugin_failed",
                    e,
                    component="initializer",
                    stage=current_stage,
                    status="failed",
                    reason_code="full_initialization_error",
                    duration_ms=duration_ms,
                )
            try:
                await self.stop_memory_engine_tasks()
            except BaseException:
                logger.error(
                    "初始化失败后收敛记忆引擎后台任务失败",
                    exc_info=True,
                )
            try:
                await self.close_realtime_hub()
            except BaseException:
                logger.error(
                    "初始化失败后关闭实时事件 Hub 失败",
                    exc_info=True,
                )
            if owns_evolution_components:
                try:
                    await self.close_memory_evolution_components()
                except BaseException:
                    logger.error(
                        "初始化失败后关闭记忆演化组件失败",
                        exc_info=True,
                    )
            if owns_injection_components:
                try:
                    await self.close_injection_components()
                except BaseException:
                    logger.error(
                        "初始化失败后关闭注入决策组件失败",
                        exc_info=True,
                    )
            await close_identity_runtime_after_failure(self.identity_runtime)
            if isinstance(e, asyncio.CancelledError):
                raise
            if not isinstance(e, Exception):
                raise
            logger.error(f"完整初始化流程失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            raise InitializationError(f"初始化失败: {e}") from e

    def _create_prompt_protection_service(self) -> PromptProtectionPort | None:
        """按安全配置为 LLM 数据流创建共享的提示词保护服务。"""
        if not self.config_manager.get("security.prompt_protection_enabled", True):
            logger.info("提示词保护服务已关闭")
            return None
        template_index = int(
            self.config_manager.get("security.wrapper_template_index", 0)
        )
        enable_double_check = bool(
            self.config_manager.get("security.double_check_enabled", True)
        )
        service = build_prompt_protection_port(
            wrapper_template_index=template_index,
            enable_double_check=enable_double_check,
        )
        logger.info("提示词保护服务已初始化")
        return service

    async def _initialize_cognitive_components(self) -> None:
        """创建共享的 v1.0+ 认知组件实例。"""
        db_path = str(Path(self.data_dir) / "memora.db")
        initialization_started = time.perf_counter()
        success_count = 0
        failed_count = 0

        component_started = time.perf_counter()
        try:
            from ...features.cognition.affection import AffectionManager, AffectionStore

            self.affection_store = AffectionStore(db_path)
            await self.affection_store.initialize()
            self.affection_manager = AffectionManager(
                self.affection_store,
                # 宿主 Provider 的运行时能力由既有认知组件边界验证。
                llm_adapter=cast(Any, self.llm_provider),
            )
            success_count += 1
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="cognitive_components",
                status="completed",
                reason_code="cognitive_component_ready",
                capability="affection",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.info("好感度管理器已初始化")
        except Exception as exc:
            failed_count += 1
            report_debug_exception(
                "plugin_initialized",
                exc,
                component="initializer",
                stage="cognitive_components",
                status="degraded",
                reason_code="cognitive_component_unavailable",
                capability="affection",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.warning("好感度管理器初始化失败，已跳过: %s", exc, exc_info=True)
            self.affection_store = None
            self.affection_manager = None

        component_started = time.perf_counter()
        try:
            from ...features.cognition.expression import (
                ExpressionPatternLearner,
                ExpressionPatternStore,
            )

            self.expression_store = ExpressionPatternStore(db_path)
            await self.expression_store.initialize()
            self.expression_learner = ExpressionPatternLearner(self.expression_store)
            success_count += 1
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="cognitive_components",
                status="completed",
                reason_code="cognitive_component_ready",
                capability="expression",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.info("表达模式学习器已初始化")
        except Exception as exc:
            failed_count += 1
            report_debug_exception(
                "plugin_initialized",
                exc,
                component="initializer",
                stage="cognitive_components",
                status="degraded",
                reason_code="cognitive_component_unavailable",
                capability="expression",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.warning("表达模式学习器初始化失败，已跳过: %s", exc, exc_info=True)
            self.expression_store = None
            self.expression_learner = None

        if not is_jargon_discovery_enabled(self.config_manager):
            self.jargon_filter = None
            self.jargon_store = None
            self.jargon_query_service = None
            self.jargon_miner = None
            logger.info("黑话自动发现功能已禁用")
        else:
            component_started = time.perf_counter()
            try:
                from ...features.cognition.jargon import (
                    JargonMiner,
                    JargonQueryService,
                    JargonStatisticalFilter,
                    JargonStore,
                )

                self.jargon_filter = JargonStatisticalFilter()
                self.jargon_store = JargonStore(db_path)
                assert self.jargon_store is not None
                await self.jargon_store.initialize()
                self.jargon_query_service = JargonQueryService(self.jargon_store)
                self.jargon_miner = JargonMiner(
                    self.llm_provider,
                    self.jargon_filter,
                    self.jargon_store,
                )
                success_count += 1
                report_debug_event(
                    "plugin_initialized",
                    component="initializer",
                    stage="cognitive_components",
                    status="completed",
                    reason_code="cognitive_component_ready",
                    capability="jargon",
                    duration_ms=max(
                        0.0, (time.perf_counter() - component_started) * 1000.0
                    ),
                )
                logger.info("黑话组件已初始化")
            except Exception as exc:
                failed_count += 1
                report_debug_exception(
                    "plugin_initialized",
                    exc,
                    component="initializer",
                    stage="cognitive_components",
                    status="degraded",
                    reason_code="cognitive_component_unavailable",
                    capability="jargon",
                    duration_ms=max(
                        0.0, (time.perf_counter() - component_started) * 1000.0
                    ),
                )
                logger.warning("黑话组件初始化失败，已跳过: %s", exc, exc_info=True)
                self.jargon_filter = None
                self.jargon_store = None
                self.jargon_query_service = None
                self.jargon_miner = None

        component_started = time.perf_counter()
        try:
            from ...features.cognition.social import RelationManager, RelationStore

            self.relation_store = RelationStore(db_path)
            await self.relation_store.initialize()
            self.relation_manager = RelationManager(self.relation_store)
            success_count += 1
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="cognitive_components",
                status="completed",
                reason_code="cognitive_component_ready",
                capability="social",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.info("关系管理器已初始化")
        except Exception as exc:
            failed_count += 1
            report_debug_exception(
                "plugin_initialized",
                exc,
                component="initializer",
                stage="cognitive_components",
                status="degraded",
                reason_code="cognitive_component_unavailable",
                capability="social",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.warning("关系管理器初始化失败，已跳过: %s", exc, exc_info=True)
            self.relation_store = None
            self.relation_manager = None

        report_debug_event(
            "plugin_initialized",
            component="initializer",
            stage="cognitive_components",
            status="completed" if failed_count == 0 else "degraded",
            reason_code=(
                "cognitive_components_ready"
                if failed_count == 0
                else "cognitive_components_partial"
            ),
            duration_ms=max(
                0.0, (time.perf_counter() - initialization_started) * 1000.0
            ),
            success_count=success_count,
            failed_count=failed_count,
        )

    # ---- 关停 ----

    async def _safe_step(self, label: str, coro, timeout: float | None = None) -> None:
        """执行关停步骤，带超时保护。"""
        if timeout is None:
            timeout = self.SHUTDOWN_STEP_TIMEOUT
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            logger.info(f"关停步骤 '{label}' 完成")
        except asyncio.TimeoutError:
            logger.warning(f"关停步骤 '{label}' 超时 ({timeout}s)，跳过")
        except Exception as e:
            logger.error(f"关停步骤 '{label}' 失败: {e}")

    async def stop_scheduler(self) -> None:
        """依次停止存量回填与衰减调度器。"""

        if self.backfill_scheduler:
            await self._safe_step("停止存量回填调度器", self.backfill_scheduler.stop())
            self.backfill_scheduler = None
        if self.decay_scheduler:
            await self._safe_step("停止衰减调度器", self.decay_scheduler.stop())
            self.decay_scheduler = None

    async def stop_background_tasks(self) -> None:
        """取消尚未结束的 Provider 后台等待任务。"""

        await self._safe_step("取消Provider等待", self._provider_waiter.cancel())

    async def stop_memory_engine_tasks(self) -> None:
        """在共享演化消费者关闭前收敛引擎持有的生产者任务。"""

        engine = getattr(self, "memory_engine", None)
        stopper = getattr(engine, "stop_pending_tasks", None)
        if not callable(stopper):
            return
        result = stopper()
        if inspect.isawaitable(result):
            await result

    async def close_realtime_hub(self) -> None:
        """先于共享 Store 关闭实时 Hub，唤醒旧 SSE 客户端并拒绝新订阅。"""

        hub = self.realtime_hub
        if hub is None:
            return
        await hub.drain()
        if self.realtime_hub is hub:
            self.realtime_hub = None

    async def close_injection_components(self) -> None:
        """按记录器后存储的顺序幂等关闭注入观测组件。"""

        async with self._injection_close_lock:
            first_error: BaseException | None = None
            recorder = self.injection_decision_recorder
            if recorder is not None:
                try:
                    await recorder.close(timeout=5.0)
                except BaseException as exc:
                    first_error = exc
                else:
                    if self.injection_decision_recorder is recorder:
                        self.injection_decision_recorder = None

            store = self.injection_decision_store
            if store is not None:
                try:
                    await store.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    if self.injection_decision_store is store:
                        self.injection_decision_store = None

            if first_error is not None:
                raise first_error

    async def close_memory_evolution_components(self) -> None:
        """按 manager 后 Store 的顺序关闭记忆演化组件。"""

        async with self._evolution_close_lock:
            first_error: BaseException | None = None
            manager = self.memory_evolution_manager
            if manager is not None:
                try:
                    await manager.stop()
                except BaseException as exc:
                    first_error = exc
                else:
                    if self.memory_evolution_manager is manager:
                        self.memory_evolution_manager = None

            store = self.memory_evolution_store
            if store is not None:
                try:
                    await store.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    if self.memory_evolution_store is store:
                        self.memory_evolution_store = None

            if first_error is not None:
                raise first_error

    async def close_extension_components(self) -> None:
        """关闭可选认知组件及组合根拥有的身份运行时。"""

        for label, obj in (
            ("AffectionStore", self.affection_store),
            ("JargonStore", self.jargon_store),
        ):
            if obj and hasattr(obj, "close"):
                await self._safe_step(f"关闭{label}", obj.close())
        identity_runtime = self.identity_runtime
        if identity_runtime is not None:
            await self._safe_step("关闭协议身份运行时", identity_runtime.close())


__all__ = ["PluginInitializer"]
