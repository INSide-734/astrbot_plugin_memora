"""组合根发布事件与命令处理器。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...event_handler import EventHandler
from ...features.observability.application import runtime as observability
from ..transport.commands.command_handler import CommandHandler


def publish_runtime_handlers(plugin: Any) -> None:
    """基于初始化器已发布组件幂等构造事件和命令处理器。"""
    bind_backup = getattr(plugin._backup_manager, "bind_summary_scheduler", None)
    if callable(bind_backup):
        bind_backup(plugin.initializer.summary_scheduler)

    if plugin.event_handler is None:
        try:
            plugin._register_agent_tools_if_needed()
        except Exception:
            plugin._llm_tools_registered = False
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
            plugin._llm_tools_registered
            and plugin.config_manager.get("agent_tools.enable_recall_tool", True)
        )
        plugin.event_handler = EventHandler(
            context=plugin.context,
            config_manager=plugin.config_manager,
            memory_engine=plugin.initializer.memory_engine,
            memory_processor=plugin.initializer.memory_processor,
            conversation_manager=plugin.initializer.conversation_manager,
            identity_runtime=plugin.initializer.identity_runtime,
            jargon_filter=getattr(plugin.initializer, "jargon_filter", None),
            jargon_miner=getattr(plugin.initializer, "jargon_miner", None),
            jargon_query_service=getattr(
                plugin.initializer,
                "jargon_query_service",
                None,
            ),
            affection_manager=getattr(plugin.initializer, "affection_manager", None),
            expression_learner=getattr(
                plugin.initializer,
                "expression_learner",
                None,
            ),
            relation_manager=getattr(plugin.initializer, "relation_manager", None),
            prompt_protection_service=getattr(
                plugin.initializer,
                "prompt_protection",
                None,
            ),
            write_guard_cb=plugin._writes_blocked_by_pending_restore,
            perf_tracker=plugin._perf_tracker,
            injection_recorder=plugin.initializer.injection_decision_recorder,
            memory_tool_available=memory_tool_available,
            memory_quality_gate=getattr(
                plugin.initializer,
                "memory_quality_gate",
                None,
            ),
            summary_scheduler=getattr(
                plugin.initializer,
                "summary_scheduler",
                None,
            ),
        )

    if plugin.command_handler is None:
        plugin.command_handler = CommandHandler(
            context=plugin.context,
            config_manager=plugin.config_manager,
            memory_engine=plugin.initializer.memory_engine,
            conversation_manager=plugin.initializer.conversation_manager,
            index_validator=plugin.initializer.index_validator,
            summary_scheduler=getattr(
                plugin.initializer,
                "summary_scheduler",
                None,
            ),
            identity_runtime=plugin.initializer.identity_runtime,
            memory_processor=plugin.initializer.memory_processor,
            memory_quality_gate=getattr(
                plugin.initializer,
                "memory_quality_gate",
                None,
            ),
            initialization_status_callback=plugin._get_initialization_status_message,
            write_guard_cb=plugin._writes_blocked_by_pending_restore,
            diagnostics_health_provider=getattr(
                plugin.page_api,
                "get_diagnostics_health",
                None,
            ),
            diagnostics_metrics_provider=getattr(
                plugin.page_api,
                "get_metrics_summary",
                None,
            ),
            recall_trace_provider=getattr(
                plugin.page_api,
                "test_recall_with_trace_payload",
                None,
            ),
            update_manager=plugin._update_manager,
            update_installer=plugin._update_installer,
        )


__all__ = ["publish_runtime_handlers"]
