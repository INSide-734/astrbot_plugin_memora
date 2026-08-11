"""
记忆插件主文件。
负责插件注册、初始化与生命周期管理。
"""

import asyncio
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .core.command_endpoints import CommandEndpointsMixin
from .core.command_handler import CommandHandler
from .core.event_handler import EventHandler
from .core.feature_delegation import FeatureDelegation
from .core.features.backup.application import BackupManager
from .core.features.observability.application import PerfTracker
from .core.features.observability.application import runtime as observability
from .core.features.updates.application import RuntimeUpdateInstaller, UpdateManager
from .core.handlers.recall_observability import RecallTimingContext
from .core.i18n_backend import init as i18n_init
from .core.i18n_backend import t
from .core.managers.backup_models import BackupOperationError
from .core.platform.composition.plugin_initializer import PluginInitializer
from .core.platform.composition.reload_lifecycle import (
    run_scheduled_plugin_reload,
)
from .core.platform.composition.reload_lifecycle import (
    schedule_learning_reload as schedule_learning_reload_callback,
)
from .core.platform.composition.shutdown_lifecycle import stop_runtime_producers
from .core.platform.config.manager import ConfigManager
from .core.platform.resources import (
    PluginResourceLocator,
    build_package_resource_reader,
)
from .core.tools import MemoryMemorizeTool, MemorySearchTool
from .core.utils.version import PLUGIN_VERSION
from .core.version_check import (  # noqa: F401
    _CURRENT_ASTRBOT_VERSION,
    _MIN_ASTRBOT_VERSION,
    _version_lt,
)


@register(
    "Memora",
    "INSide-734",
    "一个为 AstrBot 提供动态生命周期长期记忆能力的智能插件。",
    PLUGIN_VERSION,
    "https://github.com/INSide-734/astrbot_plugin_memora",
)
class MemoraPlugin(Star, CommandEndpointsMixin):
    """记忆插件主类"""

    def __init__(self, context: Context, config: dict[str, Any]):
        """初始化插件上下文、配置、持久化目录与运行时占位状态。"""

        super().__init__(context)
        self.context = context
        self.instance_id = uuid.uuid4().hex

        # 获取插件数据目录
        data_dir = str(StarTools.get_data_dir())

        # 确保控制台页面会话密钥持久化，跨重启后会话仍然有效
        _ensure_secret_key(data_dir)

        # 版本变更时自动备份数据（延迟到异步初始化阶段执行，避免 __init__ 中同步 I/O 阻塞）
        self._backup_manager = BackupManager(data_dir)

        # 保留 AstrBot 注入对象，配置变更通过其原子保存能力持久化
        self.astrbot_config = config
        self.resource_locator = PluginResourceLocator(
            Path(__file__).resolve().parent,
            package_reader=build_package_resource_reader(__package__),
        )
        self.config_manager = ConfigManager(
            self.astrbot_config,
            resource_locator=self.resource_locator,
        )
        self._update_manager = UpdateManager(
            data_dir,
            self.config_manager,
            host_config_source=getattr(self.context, "get_config", None),
        )
        self._update_installer = RuntimeUpdateInstaller(
            context=self.context,
            data_dir=data_dir,
            plugin_root=self.resource_locator.plugin_root,
            update_manager=self._update_manager,
        )

        # 初始化后端 i18n
        i18n_init(self.astrbot_config.get("bot_language", "zh"))

        # 初始化插件初始化器
        self.initializer = PluginInitializer(context, self.config_manager, data_dir)
        self._backfill_scheduler = None  # 初始化完成后再赋值

        # 事件处理器和命令处理器（初始化后创建）
        self.event_handler: EventHandler | None = None
        self.command_handler: CommandHandler | None = None

        # 后台任务跟踪集合
        self._background_tasks: set[asyncio.Task] = set()
        self._component_init_lock = asyncio.Lock()
        self._llm_tools_registered = False
        self._terminating = False

        # 功能委托：检测伴侣插件
        self.feature_delegation = FeatureDelegation(self.context)
        self.feature_delegation.log_status()

        # 召回性能跟踪器（环形缓冲区，保留 200 条样本）
        self._perf_tracker = PerfTracker(maxlen=200)

        # 若开启调试模式，则启用监控装饰器
        observability.set_debug_mode(
            self.config_manager.get("debug", False),
            data_dir=data_dir,
            timezone_name=self.context.get_config().get("timezone"),
        )
        observability.report_debug_event(
            "plugin_started",
            component="plugin",
            stage="startup",
            status="started",
            plugin_version=PLUGIN_VERSION,
            python_major=sys.version_info.major,
            python_minor=sys.version_info.minor,
            capability="debug_reporting",
        )

        self.page_api = None

        self._register_official_page_api_if_available()

        # 启动非阻塞的初始化任务
        self._create_tracked_task(self._initialize_plugin())

    def _register_official_page_api_if_available(self) -> None:
        """按需注册官方插件页面 API，避免旧版 AstrBot 因导入失败而无法加载插件。"""
        if not hasattr(self.context, "register_web_api"):
            return

        try:
            from .core.page_api import PluginPageApi
        except Exception as exc:
            logger.warning(
                f"官方插件页面 API 不可用，已跳过注册并保留旧版兼容模式：{exc}"
            )
            return

        try:
            self.page_api = PluginPageApi(self)
            self.page_api.register_routes()
        except Exception as exc:
            self.page_api = None
            logger.warning(
                f"官方插件页面 API 注册失败，已跳过并保留旧版兼容模式：{exc}",
                exc_info=True,
            )

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """创建并跟踪后台任务"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def supports_plugin_reload(self) -> bool:
        """返回当前 AstrBot 运行时是否提供插件重载能力。"""
        star_manager = getattr(self.context, "_star_manager", None)
        return callable(getattr(star_manager, "reload", None))

    def schedule_plugin_reload(self) -> bool:
        """安排配置变更后的延迟插件重载。"""
        return self._schedule_plugin_reload("config_change")

    def schedule_backup_restore_reload(self, operation_id: str) -> bool:
        """安排备份恢复后的延迟插件重载。"""
        return self._schedule_plugin_reload("backup_restore", operation_id)

    def schedule_learning_reload(self, operation_id: str) -> bool:
        """安排自主学习配置提交后的可观察延迟重载。"""

        return schedule_learning_reload_callback(self, operation_id)

    def _schedule_plugin_reload(
        self,
        reason: str,
        operation_id: str | None = None,
        *,
        learning_operation_id: str | None = None,
        expected_state_revision: str | None = None,
    ) -> bool:
        """安排延迟且不纳入关停追踪的插件重载。"""
        star_manager = getattr(self.context, "_star_manager", None)
        reload_plugin = getattr(star_manager, "reload", None)
        if not callable(reload_plugin):
            return False

        task = asyncio.create_task(
            run_scheduled_plugin_reload(
                self,
                reload_plugin,
                reason=reason,
                backup_operation_id=operation_id,
                learning_operation_id=learning_operation_id,
                expected_state_revision=expected_state_revision,
            )
        )
        task.add_done_callback(self._consume_reload_task_result)
        return True

    @staticmethod
    def _consume_reload_task_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug("延迟插件重载任务已取消")
        except Exception as exc:
            logger.error("延迟插件重载失败 type=%s", type(exc).__name__)

    async def _initialize_plugin(self):
        """初始化插件"""
        startup_started = time.perf_counter()
        observability.report_debug_event(
            "plugin_initialized",
            component="plugin",
            stage="startup",
            status="started",
            reason_code="startup_started",
        )
        startup_stage = "startup_backup"
        stage_started = startup_started
        try:
            backup_started = time.perf_counter()
            stage_started = backup_started
            observability.report_debug_event(
                "maintenance_task",
                component="plugin",
                stage="startup_backup",
                status="started",
                reason_code="startup_backup_started",
                task_type="maintenance",
            )
            await self._backup_manager.backup_if_needed_async()
            observability.report_debug_event(
                "maintenance_task",
                component="plugin",
                stage="startup_backup",
                status="completed",
                reason_code="startup_backup_completed",
                task_type="maintenance",
                duration_ms=max(0.0, (time.perf_counter() - backup_started) * 1000.0),
            )

            startup_stage = "startup_restore"
            restore_started = time.perf_counter()
            stage_started = restore_started
            observability.report_debug_event(
                "maintenance_task",
                component="plugin",
                stage="startup_restore",
                status="started",
                reason_code="startup_restore_started",
                task_type="maintenance",
            )
            self._backup_manager.apply_pending_restores()
            observability.report_debug_event(
                "maintenance_task",
                component="plugin",
                stage="startup_restore",
                status="completed",
                reason_code="startup_restore_completed",
                task_type="maintenance",
                duration_ms=max(0.0, (time.perf_counter() - restore_started) * 1000.0),
            )

            startup_stage = "provider_wait"
            stage_started = time.perf_counter()
            success = await self.initializer.initialize()
            if not success:
                self._backup_manager.mark_restore_startup_failure_if_needed(
                    self.initializer.is_failed
                )
                observability.report_debug_event(
                    "plugin_initialized",
                    component="plugin",
                    stage="startup",
                    status="degraded",
                    reason_code="provider_waiting",
                    duration_ms=max(
                        0.0, (time.perf_counter() - startup_started) * 1000.0
                    ),
                )
                return
            startup_stage = "runtime_publish"
            stage_started = time.perf_counter()
            runtime_ready = await self._ensure_runtime_components()
            if not runtime_ready:
                self._backup_manager.mark_restore_startup_failure_if_needed(True)
                observability.report_debug_event(
                    "plugin_initialized",
                    component="plugin",
                    stage="startup",
                    status="degraded",
                    reason_code="runtime_not_ready",
                    duration_ms=max(
                        0.0, (time.perf_counter() - startup_started) * 1000.0
                    ),
                )
                return
            self._backup_manager.mark_restore_succeeded()
            self._inject_delegation_services()
            self.feature_delegation.log_status()
            observability.report_debug_event(
                "plugin_initialized",
                component="plugin",
                stage="startup",
                status="completed",
                reason_code="ready",
                duration_ms=max(0.0, (time.perf_counter() - startup_started) * 1000.0),
            )
        except asyncio.CancelledError:
            observability.report_debug_event(
                "plugin_failed",
                component="plugin",
                stage=startup_stage,
                status="cancelled",
                reason_code="initialization_cancelled",
                duration_ms=max(0.0, (time.perf_counter() - startup_started) * 1000.0),
            )
            raise
        except BackupOperationError as exc:
            observability.report_debug_exception(
                "maintenance_task",
                exc,
                component="plugin",
                stage=startup_stage,
                status="failed",
                reason_code="startup_maintenance_error",
                task_type="maintenance",
                duration_ms=max(0.0, (time.perf_counter() - stage_started) * 1000.0),
            )
            observability.report_debug_exception(
                "plugin_failed",
                exc,
                component="plugin",
                stage=startup_stage,
                status="failed",
                reason_code="backup_restore_error",
                duration_ms=max(0.0, (time.perf_counter() - startup_started) * 1000.0),
            )
            logger.error("备份保护或恢复应用失败", exc_info=True)
            raise
        except Exception as exc:
            self._backup_manager.mark_restore_startup_failure_if_needed(True)
            if startup_stage in {"startup_backup", "startup_restore"}:
                observability.report_debug_exception(
                    "maintenance_task",
                    exc,
                    component="plugin",
                    stage=startup_stage,
                    status="failed",
                    reason_code="startup_maintenance_error",
                    task_type="maintenance",
                    duration_ms=max(
                        0.0, (time.perf_counter() - stage_started) * 1000.0
                    ),
                )
            observability.report_debug_exception(
                "plugin_failed",
                exc,
                component="plugin",
                stage=startup_stage,
                status="failed",
                reason_code="initialization_error",
                duration_ms=max(0.0, (time.perf_counter() - startup_started) * 1000.0),
            )
            logger.error("插件初始化失败", exc_info=True)
            raise

    async def _ensure_runtime_components(self) -> bool:
        """确保运行期组件（事件/命令处理器、控制台页面）已就绪。"""
        if self._terminating:
            return False
        if not self.initializer.is_initialized:
            return False
        if self._backfill_scheduler is None and self.initializer.backfill_scheduler:
            self._backfill_scheduler = self.initializer.backfill_scheduler
        if self.event_handler is not None and self.command_handler is not None:
            return True

        publish_started = time.perf_counter()
        observability.report_debug_event(
            "plugin_initialized",
            component="plugin",
            stage="runtime_publish",
            status="started",
            reason_code="runtime_publish_started",
        )

        async with self._component_init_lock:
            if self._terminating:
                observability.report_debug_event(
                    "plugin_initialized",
                    component="plugin",
                    stage="runtime_publish",
                    status="cancelled",
                    reason_code="shutdown_in_progress",
                    duration_ms=max(
                        0.0, (time.perf_counter() - publish_started) * 1000.0
                    ),
                )
                return False
            if self.event_handler is not None and self.command_handler is not None:
                observability.report_debug_event(
                    "plugin_initialized",
                    component="plugin",
                    stage="runtime_publish",
                    status="completed",
                    reason_code="runtime_already_published",
                    duration_ms=max(
                        0.0, (time.perf_counter() - publish_started) * 1000.0
                    ),
                    success_count=2,
                )
                return True
            # 检查必要组件是否初始化成功
            if not all(
                [
                    self.initializer.memory_engine,
                    self.initializer.memory_processor,
                    self.initializer.conversation_manager,
                ]
            ):
                observability.report_debug_event(
                    "plugin_initialized",
                    component="plugin",
                    stage="runtime_publish",
                    status="failed",
                    reason_code="core_components_incomplete",
                    duration_ms=max(
                        0.0, (time.perf_counter() - publish_started) * 1000.0
                    ),
                )
                logger.error("插件初始化不完整：部分核心组件未能初始化")
                return False

            # 创建事件处理器（幂等）
            if not self.event_handler:
                try:
                    self._register_agent_tools_if_needed()
                except Exception:
                    self._llm_tools_registered = False
                    observability.report_debug_event(
                        "plugin_initialized",
                        component="plugin",
                        stage="runtime_publish",
                        status="degraded",
                        reason_code="agent_tools_unavailable",
                        capability="agent_tools",
                    )
                    logger.error(
                        "智能体工具注册失败，将使用直接记忆召回",
                        exc_info=True,
                    )
                memory_tool_available = bool(
                    self._llm_tools_registered
                    and self.config_manager.get("agent_tools.enable_recall_tool", True)
                )
                self.event_handler = EventHandler(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,  # type: ignore[arg-type]
                    memory_processor=self.initializer.memory_processor,  # type: ignore[arg-type]
                    conversation_manager=self.initializer.conversation_manager,  # type: ignore[arg-type]
                    identity_runtime=self.initializer.identity_runtime,
                    jargon_filter=getattr(self.initializer, "jargon_filter", None),
                    jargon_miner=getattr(self.initializer, "jargon_miner", None),
                    jargon_query_service=getattr(
                        self.initializer, "jargon_query_service", None
                    ),
                    affection_manager=getattr(
                        self.initializer, "affection_manager", None
                    ),
                    expression_learner=getattr(
                        self.initializer, "expression_learner", None
                    ),
                    relation_manager=getattr(
                        self.initializer, "relation_manager", None
                    ),
                    prompt_protection_service=getattr(
                        self.initializer, "prompt_protection", None
                    ),
                    write_guard_cb=self._writes_blocked_by_pending_restore,
                    perf_tracker=self._perf_tracker,
                    injection_recorder=self.initializer.injection_decision_recorder,
                    memory_tool_available=memory_tool_available,
                    memory_evolution_manager=getattr(
                        self.initializer, "memory_evolution_manager", None
                    ),
                    memory_quality_gate=getattr(
                        self.initializer, "memory_quality_gate", None
                    ),
                )

            # 创建命令处理器（幂等）
            if not self.command_handler:
                self.command_handler = CommandHandler(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,
                    conversation_manager=self.initializer.conversation_manager,
                    index_validator=self.initializer.index_validator,
                    identity_runtime=self.initializer.identity_runtime,
                    memory_processor=self.initializer.memory_processor,
                    memory_quality_gate=getattr(
                        self.initializer, "memory_quality_gate", None
                    ),
                    initialization_status_callback=self._get_initialization_status_message,
                    summary_window_locker=self.event_handler.summary_window_locker
                    if self.event_handler
                    else None,
                    write_guard_cb=self._writes_blocked_by_pending_restore,
                    diagnostics_health_provider=getattr(
                        self.page_api,
                        "get_diagnostics_health",
                        None,
                    ),
                    diagnostics_metrics_provider=getattr(
                        self.page_api,
                        "get_metrics_summary",
                        None,
                    ),
                    recall_trace_provider=getattr(
                        self.page_api,
                        "test_recall_with_trace_payload",
                        None,
                    ),
                    update_manager=self._update_manager,
                    update_installer=self._update_installer,
                )

        observability.report_debug_event(
            "plugin_initialized",
            component="plugin",
            stage="runtime_publish",
            status="completed",
            reason_code="runtime_components_published",
            duration_ms=max(0.0, (time.perf_counter() - publish_started) * 1000.0),
            success_count=sum(
                component is not None
                for component in (self.event_handler, self.command_handler)
            ),
        )
        return True

    def _inject_delegation_services(self) -> None:
        """将 MemoryEngine / KnowledgeManager 注入 FeatureDelegation。

        使得 self_learning 等伴侣插件可通过 Memora 的 FeatureDelegation
        实例调用记忆召回和知识检索服务。
        """
        engine = self.initializer.memory_engine
        if engine is not None:
            self.feature_delegation.set_memory_engine(engine)
            knowledge_mgr = getattr(engine, "knowledge_manager", None)
            if knowledge_mgr is not None:
                self.feature_delegation.set_knowledge_manager(knowledge_mgr)

    def _register_agent_tools_if_needed(self) -> None:
        """在核心组件就绪后注册智能体工具（召回/写入）。"""
        if self._llm_tools_registered:
            return
        if not self.initializer.memory_engine or not self.initializer.memory_processor:
            return

        tools = []
        if self.config_manager.get("agent_tools.enable_recall_tool", True):
            tools.append(
                MemorySearchTool(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,
                )
            )
        if self.config_manager.get("agent_tools.enable_memorize_tool", False):
            tools.append(
                MemoryMemorizeTool(
                    context=self.context,
                    memory_engine=self.initializer.memory_engine,
                    memory_processor=self.initializer.memory_processor,
                )
            )
        # v2.5：注册笔记类工具（读写权限拆分）
        legacy_note_tools = self.config_manager.get(
            "agent_tools.enable_note_tools", None
        )
        note_read_enabled = self.config_manager.get(
            "agent_tools.enable_note_read_tools",
            legacy_note_tools if legacy_note_tools is not None else True,
        )
        note_write_enabled = self.config_manager.get(
            "agent_tools.enable_note_write_tool",
            False,
        )
        if note_read_enabled or note_write_enabled:
            engine = self.initializer.memory_engine
            if engine and engine.note_manager:
                from .core.tools.note_tools import (
                    NoteReadTool,
                    NoteSearchTool,
                    NoteWriteTool,
                )

                if note_read_enabled:
                    tools.append(NoteSearchTool(note_manager=engine.note_manager))
                    tools.append(NoteReadTool(note_manager=engine.note_manager))
                if note_write_enabled:
                    tools.append(NoteWriteTool(note_manager=engine.note_manager))

        # v2.5：注册知识库类工具
        if self.config_manager.get("agent_tools.enable_knowledge_tools", True):
            engine = self.initializer.memory_engine
            if engine and engine.knowledge_manager:
                from .core.tools.knowledge_tools import (
                    KnowledgeReadTool,
                    KnowledgeSearchTool,
                )

                tools.append(
                    KnowledgeSearchTool(knowledge_manager=engine.knowledge_manager)
                )
                tools.append(
                    KnowledgeReadTool(knowledge_manager=engine.knowledge_manager)
                )

        # v2.5：注册用户画像类工具
        if self.config_manager.get("agent_tools.enable_profile_tools", True):
            engine = self.initializer.memory_engine
            if engine and engine.profile_manager:
                from .core.tools.profile_tools import ProfileLookupTool

                tools.append(ProfileLookupTool(profile_manager=engine.profile_manager))

        # v1.0.0+：注册黑话查询工具
        if self.config_manager.get("agent_tools.enable_jargon_tools", True):
            if self.feature_delegation.should_delegate_jargon():
                logger.info(
                    "[功能融合] 跳过本地黑话工具注册（已委托给 self_learning 插件）"
                )
            else:
                jargon_query_svc = getattr(
                    self.initializer, "jargon_query_service", None
                )
                if jargon_query_svc is not None:
                    from .core.tools.jargon_tools import (
                        JargonExplainTool,
                        JargonListTool,
                    )

                    tools.append(
                        JargonExplainTool(jargon_query_service=jargon_query_svc)
                    )
                    tools.append(JargonListTool(jargon_query_service=jargon_query_svc))

        # v1.0.0+：注册好感度/情绪查询工具
        if self.config_manager.get("agent_tools.enable_affection_tools", True):
            if self.feature_delegation.should_delegate_affection():
                logger.info(
                    "[功能融合] 跳过本地好感度工具注册（已委托给 self_learning 插件）"
                )
            else:
                affection_mgr = getattr(self.initializer, "affection_manager", None)
                if affection_mgr is not None:
                    from .core.tools.affection_tools import (
                        AffectionCheckTool,
                        BotMoodTool,
                    )

                    tools.append(AffectionCheckTool(affection_manager=affection_mgr))
                    tools.append(BotMoodTool(affection_manager=affection_mgr))

        # v1.0.0+：注册社交关系查询工具
        if self.config_manager.get("agent_tools.enable_social_tools", True):
            if self.feature_delegation.should_skip_persona_processing():
                logger.info(
                    "[功能融合] 跳过本地社交关系工具注册（已委托给 self_learning 插件）"
                )
            else:
                relation_mgr = getattr(self.initializer, "relation_manager", None)
                if relation_mgr is not None:
                    from .core.tools.social_tools import (
                        RelationGraphTool,
                        RelationLookupTool,
                    )

                    tools.append(RelationLookupTool(relation_manager=relation_mgr))
                    tools.append(RelationGraphTool(relation_manager=relation_mgr))

        # v1.0.0+：注册表达模式查询工具
        if self.config_manager.get("agent_tools.enable_expression_tools", True):
            if self.feature_delegation.should_delegate_expression():
                logger.info(
                    "[功能融合] 跳过本地表达模式工具注册（已委托给 self_learning 插件）"
                )
            else:
                expression_learner = getattr(
                    self.initializer, "expression_learner", None
                )
                if expression_learner is not None:
                    from .core.tools.expression_tools import ExpressionRecallTool

                    tools.append(
                        ExpressionRecallTool(expression_learner=expression_learner)
                    )

        if tools:
            self.context.add_llm_tools(*tools)
        # 标记注册流程完成，后续不再重复检查。
        # 若用户中途修改 agent_tools 开关，需要重载插件后才能生效。
        self._llm_tools_registered = True

    async def _ensure_plugin_ready(self, *, wait: bool = True) -> tuple[bool, str]:
        """确保插件已完成初始化并且运行期组件可用"""
        if wait:
            initialized = await self.initializer.ensure_initialized()
        else:
            initialized = self.get_readiness_snapshot()["is_initialized"]
        if not initialized:
            return False, self._get_initialization_status_message()

        if not wait:
            runtime_ready = (
                not self._terminating
                and self.event_handler is not None
                and self.command_handler is not None
            )
            return (True, "") if runtime_ready else (False, t("command.core_not_ready"))

        if not await self._ensure_runtime_components():
            return (
                False,
                t("command.core_not_ready"),
            )

        return True, ""

    def get_readiness_snapshot(self) -> dict[str, Any]:
        """为状态、帮助和 WebUI 路径返回非阻塞诊断快照。"""
        snapshot = self.initializer.get_readiness_snapshot()
        snapshot["runtime_ready"] = {
            "event_handler": self.event_handler is not None,
            "command_handler": self.command_handler is not None,
            "page_api": self.page_api is not None,
            "terminating": self._terminating,
        }
        return snapshot

    def _writes_blocked_by_pending_restore(self) -> bool:
        try:
            get_state = getattr(self._backup_manager, "get_maintenance_state", None)
            if callable(get_state):
                return bool(get_state().get("blocked", False))
            return bool(self._backup_manager.has_pending_restores())
        except Exception:
            logger.error("检查备份恢复维护状态失败", exc_info=True)
            return True

    def _get_initialization_status_message(self) -> str:
        """获取对用户更友好的初始化状态消息。"""
        if self.initializer.is_initialized:
            return t("init.ready")
        elif self.initializer.is_failed:
            return t(
                "init.failed",
                error=self.initializer.error_message or t("common.unknown_error"),
            )
        else:
            return t(
                "init.in_progress",
                attempts=getattr(self.initializer, "provider_check_attempts", 0),
            )

    @staticmethod
    def _command_handler_not_ready_message() -> str:
        """返回命令处理器未就绪时的提示语。"""
        return t("command.not_ready")

    # ==================== 事件钩子 ====================

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def handle_all_group_messages(self, event: AstrMessageEvent):
        """[事件钩子] 捕获全部群聊消息并进入记忆存储链路。"""
        if not self.initializer.is_initialized:
            return False
        if self._backfill_scheduler is None and self.initializer.backfill_scheduler:
            self._backfill_scheduler = self.initializer.backfill_scheduler

        if not await self._ensure_runtime_components():
            logger.debug("插件组件未就绪，跳过群聊消息捕获")
            return None

        if not self.event_handler:
            return None

        await self.event_handler.handle_all_group_messages(event)
        return None

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """[事件钩子] 在 LLM 请求前查询并注入长期记忆。"""
        timing_context = RecallTimingContext.start(
            self.config_manager.get("recall_engine.pre_llm_soft_budget_ms", 800)
        )
        ready_started = time.perf_counter()
        ready, _ = await self._ensure_plugin_ready(wait=False)
        timing_context.record_elapsed("plugin_ready_ms", ready_started)
        if not ready:
            logger.debug("插件未完成初始化，跳过记忆召回")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_memory_recall(
            event,
            req,
            timing_context=timing_context,
        )

    @filter.on_llm_response()
    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """[事件钩子] 在 LLM 响应后检查是否需要反思与存储记忆。"""
        ready, _ = await self._ensure_plugin_ready()
        if not ready:
            logger.debug("插件未完成初始化，跳过记忆反思")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_memory_reflection(event, resp)

    @filter.after_message_sent()
    async def handle_session_reset(self, event: AstrMessageEvent):
        """[事件钩子] 发送消息后检查是否需要清空插件会话上下文。"""
        if not event.get_extra("_clean_ltm_session", False):
            return

        ready, _ = await self._ensure_plugin_ready()
        if not ready:
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_session_reset(event)

    # ==================== 生命周期管理 ====================

    async def terminate(self):
        """按依赖顺序关闭运行时组件，并传播任务取消信号。"""

        shutdown_started = time.perf_counter()
        shutdown_status = "completed"
        try:
            if not await self._terminate_impl():
                shutdown_status = "degraded"
        except asyncio.CancelledError:
            shutdown_status = "cancelled"
            raise
        except Exception:
            shutdown_status = "failed"
            raise
        finally:
            observability.report_debug_event(
                "plugin_stopped",
                component="plugin",
                stage="shutdown",
                status=shutdown_status,
                reason_code=f"shutdown_{shutdown_status}",
                duration_ms=max(0.0, (time.perf_counter() - shutdown_started) * 1000.0),
            )
            observability.close_debug_reporting()

    async def _terminate_impl(self) -> bool:
        """插件停止时执行的清理逻辑。"""
        logger.info("记忆插件正在停止……")
        self._terminating = True
        shutdown_degraded = False

        # 用于包装带超时保护的清理步骤
        async def _safe_step(
            stage: str, label: str, coro, timeout: float = 8.0
        ) -> None:
            nonlocal shutdown_degraded
            step_started = time.perf_counter()
            observability.report_debug_event(
                "shutdown_step",
                component="plugin",
                stage=stage,
                status="started",
                reason_code="shutdown_step_started",
            )
            try:
                await asyncio.wait_for(coro, timeout=timeout)
                observability.report_debug_event(
                    "shutdown_step",
                    component="plugin",
                    stage=stage,
                    status="completed",
                    reason_code="shutdown_step_completed",
                    duration_ms=max(0.0, (time.perf_counter() - step_started) * 1000.0),
                )
                logger.info(f"  {label} 完成")
            except asyncio.CancelledError:
                observability.report_debug_event(
                    "shutdown_step",
                    component="plugin",
                    stage=stage,
                    status="cancelled",
                    reason_code="shutdown_step_cancelled",
                    duration_ms=max(0.0, (time.perf_counter() - step_started) * 1000.0),
                )
                raise
            except asyncio.TimeoutError:
                shutdown_degraded = True
                observability.report_debug_event(
                    "shutdown_step",
                    component="plugin",
                    stage=stage,
                    status="degraded",
                    reason_code="shutdown_step_timeout",
                    duration_ms=max(0.0, (time.perf_counter() - step_started) * 1000.0),
                )
                logger.warning(f"  {label} 超时 ({timeout}s)")
            except Exception as e:
                shutdown_degraded = True
                observability.report_debug_exception(
                    "shutdown_step",
                    e,
                    component="plugin",
                    stage=stage,
                    status="failed",
                    reason_code="shutdown_step_error",
                    duration_ms=max(0.0, (time.perf_counter() - step_started) * 1000.0),
                )
                logger.error(f"  {label} 失败：{e}")

        def _report_skipped(stage: str, reason_code: str) -> None:
            observability.report_debug_event(
                "shutdown_step",
                component="plugin",
                stage=stage,
                status="skipped",
                reason_code=reason_code,
            )

        STEP_TIMEOUT = (
            self.initializer.SHUTDOWN_STEP_TIMEOUT
            if hasattr(self.initializer, "SHUTDOWN_STEP_TIMEOUT")
            else 8.0
        )

        # 1. 取消所有后台任务
        if self._background_tasks:
            logger.info(f"  正在取消 {len(self._background_tasks)} 个后台任务……")
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
            await _safe_step(
                "background_tasks",
                "取消后台任务",
                asyncio.gather(*self._background_tasks, return_exceptions=True),
                timeout=3.0,
            )
            self._background_tasks.clear()
        else:
            _report_skipped("background_tasks", "no_background_tasks")

        # 2. 停止初始化后台任务（如提供器重试）
        await _safe_step(
            "provider_waiter",
            "停止提供器重试",
            self.initializer.stop_background_tasks(),
            timeout=STEP_TIMEOUT,
        )

        # 3. 通知事件处理器停止（如果仍有存储任务在运行）
        if self.event_handler:
            await _safe_step(
                "event_handler",
                "停止事件处理器",
                self.event_handler.shutdown(),
                timeout=STEP_TIMEOUT,
            )
        else:
            _report_skipped("event_handler", "component_inactive")

        # 4. 停止生产者，再关闭它们共享的消费者。
        await stop_runtime_producers(
            self,
            _safe_step,
            _report_skipped,
            timeout=STEP_TIMEOUT,
        )

        # 6. 关闭扩展认知组件
        await _safe_step(
            "cognitive_components",
            "关闭扩展认知组件",
            self.initializer.close_extension_components(),
            timeout=STEP_TIMEOUT,
        )

        # 7. 关闭会话管理器
        if (
            self.initializer.conversation_manager
            and self.initializer.conversation_manager.store
        ):
            await _safe_step(
                "conversation_store",
                "关闭会话管理器",
                self.initializer.conversation_manager.store.close(),
                timeout=STEP_TIMEOUT,
            )
        else:
            _report_skipped("conversation_store", "component_inactive")

        # 8. 关闭记忆引擎
        if self.initializer.memory_engine:
            await _safe_step(
                "memory_engine",
                "关闭记忆引擎",
                self.initializer.memory_engine.close(),
                timeout=STEP_TIMEOUT,
            )
        else:
            _report_skipped("memory_engine", "component_inactive")

        # 9. 关闭向量数据库
        if self.initializer.db:
            await _safe_step(
                "vector_database",
                "关闭向量数据库",
                self.initializer.db.close(),
                timeout=STEP_TIMEOUT,
            )
        else:
            _report_skipped("vector_database", "component_inactive")

        # 关闭前输出性能摘要
        try:
            perf = self._perf_tracker.get_perf_data(recent_limit=5)
            if perf.get("count_total_ms", 0) > 0:
                logger.info(
                    f"  性能摘要：平均总耗时={perf['avg_total_ms']:.1f}ms，"
                    f"样本数={perf['count_total_ms']}"
                )
        except Exception:
            pass

        if shutdown_degraded:
            logger.warning("记忆插件已停止，但部分清理步骤未完整完成。")
        else:
            logger.info("记忆插件已成功停止。")
        return not shutdown_degraded


def _ensure_secret_key(data_dir: str) -> str:
    """获取或创建持久化的会话密钥。

    首次运行时生成随机密钥并保存到磁盘，后续重启复用同一密钥，
    确保 AstrBot Quart session cookie 在服务器重启后仍然有效。

    参数:
        data_dir: 插件数据目录路径。

    返回:
        十六进制编码的 32 字节密钥字符串。
    """
    key_file = os.path.join(data_dir, ".secret_key")
    try:
        if os.path.isfile(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        # 生成新密钥并持久化
        key = secrets.token_hex(32)
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key)
        logger.info(f"[记忆插件] 已生成并保存新的会话密钥文件：{key_file}")
        return key
    except Exception as e:
        logger.warning(f"[记忆插件] 无法持久化会话密钥（{e}），将使用临时密钥")
        return secrets.token_hex(32)
