"""
插件初始化器，负责整个插件的初始化编排逻辑。
"""

import asyncio
import time
from typing import Any
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.provider.provider import EmbeddingProvider, Provider

from .base.config_manager import ConfigManager
from .base.exceptions import InitializationError
from .initializer.component_factory import ComponentFactory
from .initializer.db_setup import DatabaseSetup
from .initializer.faiss_checker import FaissChecker
from .initializer.provider_loader import ProviderLoader
from .initializer.provider_waiter import ProviderWaiter
from .injection.recorder import InjectionDecisionRecorder
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .processors.memory_processor import MemoryProcessor
from .schedulers.backfill_scheduler import BackfillScheduler
from .schedulers.decay_scheduler import DecayScheduler
from .storage.injection_decision_store import InjectionDecisionStore
from .security import PromptProtectionService
from .validators.index_validator import IndexValidator


class PluginInitializer:
    """插件初始化器"""

    # 关停超时配置（秒）
    SHUTDOWN_STEP_TIMEOUT: float = 8.0
    TASK_CANCEL_TIMEOUT: float = 3.0

    def __init__(self, context: Context, config_manager: ConfigManager, data_dir: str):
        self.context = context
        self.config_manager = config_manager
        self.data_dir = data_dir

        # 组件实例
        self.embedding_provider: EmbeddingProvider | None = None
        self.llm_provider: Provider | None = None
        self.db: Any | None = None
        self.graph_db: Any | None = None
        self.memory_engine: MemoryEngine | None = None
        self.memory_processor: MemoryProcessor | None = None
        self.conversation_manager: ConversationManager | None = None
        self.index_validator: IndexValidator | None = None
        self.decay_scheduler: DecayScheduler | None = None
        self.backfill_scheduler: BackfillScheduler | None = None
        self.injection_decision_store: InjectionDecisionStore | None = None
        self.injection_decision_recorder: InjectionDecisionRecorder | None = None
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
        self.prompt_protection: PromptProtectionService | None = None

        # 初始化状态
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()
        self._injection_close_lock = asyncio.Lock()
        self._initialization_failed = False
        self._initialization_error: str | None = None

        # 子模块
        self._provider_loader = ProviderLoader(context, config_manager)
        self._provider_waiter = ProviderWaiter(max_attempts=60)
        self._faiss_checker = FaissChecker()
        self._db_setup = DatabaseSetup(config_manager)
        self._component_factory = ComponentFactory(context, config_manager, data_dir)

        # 重试回调：Provider 后台就绪后自动完成初始化
        self._provider_waiter.on_ready_callback = self._on_providers_ready

    async def initialize(self) -> bool:
        async with self._initialization_lock:
            if self._initialization_complete or self._initialization_failed:
                return self._initialization_complete

        logger.info("记忆插件开始后台初始化...")

        try:
            emb, llm, ready = await self._provider_waiter.wait_non_blocking(
                self._provider_loader,
                self.embedding_provider,
                self.llm_provider,
            )
            self.embedding_provider, self.llm_provider = emb, llm

            if not ready:
                missing = []
                if not self.embedding_provider:
                    missing.append(
                        "向量嵌入提供器（请在 AstrBot 中配置向量嵌入模型）"
                    )
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

        except Exception as e:
            logger.error(f"记忆插件初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            return False

    async def _on_providers_ready(self, emb, llm):
        """提供器后台重试成功后的回调。"""
        self.embedding_provider, self.llm_provider = emb, llm
        try:
            async with self._initialization_lock:
                if not self._initialization_complete:
                    await self._run_full_init()
        except Exception as e:
            logger.error(f"重试初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)

    async def _run_full_init(self):
        """执行完整初始化流程"""
        logger.info("开始完整初始化流程...")
        try:
            faiss_cls = self._faiss_checker.load_vec_db_class()
            components = await self._component_factory.build_all(
                self.embedding_provider,
                self.llm_provider,
                faiss_cls,
                self._faiss_checker,
                self._db_setup,
            )
            self.db = components["db"]
            self.graph_db = components["graph_db"]
            self.memory_engine = components["memory_engine"]
            self.memory_processor = components["memory_processor"]
            self.conversation_manager = components["conversation_manager"]
            self.index_validator = components["index_validator"]
            self.decay_scheduler = components["decay_scheduler"]
            self.injection_decision_store = components["injection_decision_store"]
            self.injection_decision_recorder = components[
                "injection_decision_recorder"
            ]
            self.prompt_protection = self._create_prompt_protection_service()
            self.memory_processor.prompt_protection_service = self.prompt_protection
            await self._initialize_cognitive_components()

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

            self._initialization_complete = True
            logger.info("记忆插件初始化成功。")
        except Exception as e:
            logger.error(f"完整初始化流程失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            raise InitializationError(f"初始化失败: {e}") from e

    def _create_prompt_protection_service(self) -> PromptProtectionService:
        """为 LLM 数据流创建共享的提示词保护服务。"""
        template_index = int(
            self.config_manager.get("security.wrapper_template_index", 0)
        )
        enable_double_check = bool(
            self.config_manager.get("security.double_check_enabled", True)
        )
        service = PromptProtectionService(
            wrapper_template_index=template_index,
            enable_double_check=enable_double_check,
        )
        logger.info("提示词保护服务已初始化")
        return service

    async def _initialize_cognitive_components(self) -> None:
        """创建共享的 v1.0+ 认知组件实例。"""
        db_path = str(Path(self.data_dir) / "memora.db")

        try:
            from .affection import AffectionManager, AffectionStore

            self.affection_store = AffectionStore(db_path)
            await self.affection_store.initialize()
            self.affection_manager = AffectionManager(
                self.affection_store,
                llm_adapter=self.llm_provider,
            )
            logger.info("好感度管理器已初始化")
        except Exception as exc:
            logger.warning("好感度管理器初始化失败，已跳过: %s", exc, exc_info=True)
            self.affection_store = None
            self.affection_manager = None

        try:
            from .expression import ExpressionPatternLearner, ExpressionPatternStore

            self.expression_store = ExpressionPatternStore(db_path)
            await self.expression_store.initialize()
            self.expression_learner = ExpressionPatternLearner(self.expression_store)
            logger.info("表达模式学习器已初始化")
        except Exception as exc:
            logger.warning("表达模式学习器初始化失败，已跳过: %s", exc, exc_info=True)
            self.expression_store = None
            self.expression_learner = None

        try:
            from .jargon import JargonMiner, JargonQueryService, JargonStatisticalFilter, JargonStore

            self.jargon_filter = JargonStatisticalFilter()
            self.jargon_store = JargonStore(db_path)
            await self.jargon_store.initialize()
            self.jargon_query_service = JargonQueryService(self.jargon_store)
            self.jargon_miner = JargonMiner(
                self.llm_provider,
                self.jargon_filter,
                self.jargon_store,
            )
            logger.info("黑话组件已初始化")
        except Exception as exc:
            logger.warning("黑话组件初始化失败，已跳过: %s", exc, exc_info=True)
            self.jargon_filter = None
            self.jargon_store = None
            self.jargon_query_service = None
            self.jargon_miner = None

        try:
            from .social import RelationManager, RelationStore

            self.relation_store = RelationStore(db_path)
            await self.relation_store.initialize()
            self.relation_manager = RelationManager(self.relation_store)
            logger.info("关系管理器已初始化")
        except Exception as exc:
            logger.warning("关系管理器初始化失败，已跳过: %s", exc, exc_info=True)
            self.relation_store = None
            self.relation_manager = None

    # ---- 对外属性 ----

    @property
    def is_initialized(self) -> bool:
        return self._initialization_complete

    @property
    def is_failed(self) -> bool:
        return self._initialization_failed

    @property
    def error_message(self) -> str | None:
        return self._initialization_error

    @property
    def provider_check_attempts(self) -> int:
        return self._provider_waiter.attempts

    def get_readiness_snapshot(self) -> dict[str, Any]:
        missing_provider = []
        if self.embedding_provider is None:
            missing_provider.append("embedding")
        if self.llm_provider is None:
            missing_provider.append("llm")
        return {
            "is_initialized": self._initialization_complete,
            "is_failed": self._initialization_failed,
            "error_message": self._initialization_error,
            "provider_attempts": self.provider_check_attempts,
            "missing_provider": missing_provider,
            "components_ready": {
                "db": self.db is not None,
                "graph_db": self.graph_db is not None,
                "memory_engine": self.memory_engine is not None,
                "memory_processor": self.memory_processor is not None,
                "conversation_manager": self.conversation_manager is not None,
                "index_validator": self.index_validator is not None,
            },
        }

    async def ensure_initialized(self, timeout: float = 30.0) -> bool:
        if self._initialization_complete:
            return True
        if self._initialization_failed:
            return False
        start_time = time.time()
        while not self._initialization_complete and not self._initialization_failed:
            if time.time() - start_time > timeout:
                logger.error(f"等待插件初始化超时（{timeout}秒）")
                return False
            await asyncio.sleep(0.2)
        return self._initialization_complete

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
        if self.backfill_scheduler:
            await self._safe_step("停止存量回填调度器", self.backfill_scheduler.stop())
            self.backfill_scheduler = None
        if self.decay_scheduler:
            await self._safe_step("停止衰减调度器", self.decay_scheduler.stop())
            self.decay_scheduler = None

    async def stop_background_tasks(self) -> None:
        await self._safe_step("取消Provider等待", self._provider_waiter.cancel())

    async def close_injection_components(self) -> None:
        async with self._injection_close_lock:
            recorder = self.injection_decision_recorder
            store = self.injection_decision_store
            if recorder is None and store is None:
                return
            try:
                try:
                    if recorder is not None:
                        await recorder.close(timeout=5.0)
                finally:
                    if store is not None:
                        await store.close()
            finally:
                self.injection_decision_recorder = None
                self.injection_decision_store = None

    async def close_extension_components(self) -> None:
        for label, obj in (
            ("AffectionStore", self.affection_store),
            ("JargonStore", self.jargon_store),
        ):
            if obj and hasattr(obj, "close"):
                await self._safe_step(f"关闭{label}", obj.close())
