"""提供隐私安全的问题报告调试事件记录器。

该模块只接受固定事件和受限标量字段，并将同一份 JSON 事件同时发送到
Python/AstrBot 日志与可轮转的 JSONL 文件。任何无法通过校验的事件都会被
拒绝，避免依赖事后正则替换来保证隐私边界。
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import math
import re
import secrets
import threading
from datetime import datetime, tzinfo
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astrbot.api import logger as _astrbot_logger

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 2
FILE_NAME = "memora-debug.jsonl"
SCHEMA_VERSION = "1"

EVENTS = frozenset(
    {
        "plugin_started",
        "plugin_initialized",
        "plugin_failed",
        "provider_state",
        "message_capture",
        "recall_stage",
        "recall_completed",
        "recall_failed",
        "injection_completed",
        "reflection_state",
        "storage_task",
        "maintenance_task",
        "shutdown_step",
        "plugin_stopped",
        "instrumented_call",
        "gate_config_applied",
        "debug_event_rejected",
        "debug_file_sink_disabled",
    }
)

ALLOWED_FIELDS = frozenset(
    {
        "component",
        "stage",
        "status",
        "reason_code",
        "operation_token",
        "duration_ms",
        "count",
        "candidate_count",
        "selected_count",
        "injected_count",
        "filtered_count",
        "configured_budget_chars",
        "effective_budget_chars",
        "payload_chars",
        "task_type",
        "route",
        "delivery",
        "outcome",
        "exception_type",
        "exception_module",
        "exception_function",
        "exception_line",
        "plugin_version",
        "python_major",
        "python_minor",
        "capability",
        "function",
        "call_depth",
        "message_count",
        "batch_count",
        "success_count",
        "canonical_count",
        "quarantine_count",
        "failed_count",
        "skipped_idempotent_count",
        "retry_count",
        "attempt_count",
        "skipped_count",
        "queue_depth",
        "threshold_rounds",
        "prompt_chars",
        "response_chars",
        "prompt_tokens",
        "completion_tokens",
        "gate_mark_write_count",
        "gate_discard_count",
        "gate_quarantine_count",
    }
)

_TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,128}$")
_NUMERIC_FIELDS = frozenset(
    {
        "duration_ms",
        "count",
        "candidate_count",
        "selected_count",
        "injected_count",
        "filtered_count",
        "configured_budget_chars",
        "effective_budget_chars",
        "payload_chars",
        "exception_line",
        "python_major",
        "python_minor",
        "call_depth",
        "message_count",
        "batch_count",
        "success_count",
        "canonical_count",
        "quarantine_count",
        "failed_count",
        "skipped_idempotent_count",
        "retry_count",
        "attempt_count",
        "skipped_count",
        "queue_depth",
        "threshold_rounds",
        "prompt_chars",
        "response_chars",
        "prompt_tokens",
        "completion_tokens",
        "gate_mark_write_count",
        "gate_discard_count",
        "gate_quarantine_count",
    }
)
_ENUM_FIELDS = {
    "status": frozenset(
        {
            "started",
            "running",
            "completed",
            "failed",
            "ok",
            "degraded",
            "skipped",
            "cancelled",
            "ready",
            "waiting",
            "disabled",
        }
    ),
    "outcome": frozenset(
        {
            "injected",
            "fallback",
            "skipped",
            "empty",
            "error",
            "blocked",
            "failed",
            "cancelled",
        }
    ),
    "delivery": frozenset(
        {
            "auto",
            "extra_user_content",
            "user_message_before",
            "user_message_after",
            "fake_tool_call",
            "fake_tool_call_deepseek_v4",
            "system",
            "user",
            "tool",
            "none",
            "append",
            "replace",
        }
    ),
    "route": frozenset(
        {
            "auto",
            "manual",
            "hybrid",
            "tool_first",
            "low_cost",
            "balanced",
            "quality",
            "minimal",
            "standard",
            "detailed",
            "none",
            "default",
        }
    ),
    "task_type": frozenset(
        {
            "other",
            "maintenance",
            "memory_engine",
            "evolution",
            "group_listing",
            "index",
            "cleanup",
            "summary",
            "storage",
        }
    ),
}
_VALUE_FIELDS = {
    "component": frozenset(
        {
            "event_handler",
            "instrumentation",
            "initializer",
            "injection",
            "config_api",
            "maintenance",
            "memory_engine",
            "page_api",
            "plugin",
            "recall",
            "reflection",
        }
    ),
    "stage": frozenset(
        {
            "batch_prepare",
            "gate_hot_reload",
            "atom",
            "call",
            "capture",
            "cognitive_components",
            "component_build",
            "component_readiness",
            "context_cleanup",
            "decision",
            "document_vector",
            "event_handler",
            "evolution_schedule",
            "evolution",
            "fts",
            "format",
            "full_initialization",
            "graph",
            "index_validation",
            "injection",
            "maintenance",
            "memory_extract",
            "memory_write",
            "metadata_commit",
            "preflight",
            "parse",
            "prompt_build",
            "prospective",
            "protection",
            "provider",
            "provider_retry",
            "provider_wait",
            "query",
            "query_rewrite",
            "recall",
            "reflection",
            "request",
            "response",
            "retrieval",
            "retry",
            "runtime_publish",
            "segmentation",
            "scheduler_build",
            "session",
            "shutdown",
            "spontaneous",
            "startup",
            "startup_backup",
            "startup_restore",
            "storage",
            "summary_gate",
            "summary_window",
            "window_check",
            "window_total",
            "grounding",
            "write_guard",
        }
    ),
    "reason_code": frozenset(
        {
            "FORMAT_FAILED",
            "MUTATION_FAILED",
            "PROTECTION_FAILED",
            "PROTECTION_SCOPE_FAILED",
            "agent_tools_unavailable",
            "assistant_response_persisted",
            "backup_restore_error",
            "batch_extraction_failed",
            "batches_prepared",
            "blocked",
            "call_cancelled",
            "call_completed",
            "call_failed",
            "capture_cancelled",
            "capture_disabled",
            "capture_error",
            "cancelled",
            "cognitive_component_ready",
            "cognitive_component_unavailable",
            "cognitive_components_partial",
            "cognitive_components_ready",
            "cognitive_context_formatted",
            "component_build_completed",
            "component_build_started",
            "component_inactive",
            "component_lookup_error",
            "component_ready",
            "completed",
            "core_components_incomplete",
            "core_components_published",
            "duplicate",
            "empty",
            "empty_query",
            "empty_request",
            "empty_response_after_sanitization",
            "empty_session",
            "error",
            "event_handler_closed",
            "event_handler_shutdown_cancelled",
            "event_handler_shutdown_error",
            "event_handler_shutdown_started",
            "evolution_cancelled",
            "evolution_disabled",
            "evolution_gate_mark_write",
            "evolution_schedule_error",
            "evolution_scheduled",
            "evolution_skipped",
            "evolution_source_missing",
            "failed",
            "fallback",
            "full_initialization_cancelled",
            "full_initialization_completed",
            "full_initialization_error",
            "full_initialization_started",
            "gate_config_applied",
            "group_source_error",
            "history_injection_removed",
            "initialization_cancelled",
            "initialization_error",
            "injected",
            "insufficient_summary_window",
            "memories_extracted",
            "memories_stored",
            "memory_engine_unavailable",
            "memory_extraction_error",
            "memory_processor_unavailable",
            "memory_search_completed",
            "memory_write_completed",
            "memory_write_partial",
            "memory_write_started",
            "memory_write_stage_completed",
            "message_query_empty",
            "message_query_ready",
            "missing_protection_scope",
            "non_assistant_response",
            "not_group_message",
            "passive_recall_skipped",
            "pending_retry_exhausted",
            "plugin_readiness_error",
            "preflight_completed",
            "prospective_recall_completed",
            "protection_scope_lookup_failed",
            "provider_available",
            "provider_error_response",
            "provider_ready",
            "provider_retry_scheduled",
            "provider_unavailable",
            "provider_wait_started",
            "provider_waiting",
            "providers_available_after_retry",
            "query_rewritten",
            "ready",
            "recall_cancelled",
            "recall_completed",
            "recall_error",
            "reflection_cancelled",
            "reflection_error",
            "reflection_generation_cancelled",
            "reflection_generation_failed",
            "reflection_grounding_completed",
            "reflection_parse_completed",
            "reflection_prompt_built",
            "reflection_provider_completed",
            "reflection_segmentation_completed",
            "reflection_window_completed",
            "request_received",
            "response_received",
            "routing_decision_completed",
            "runtime_already_published",
            "runtime_components_published",
            "runtime_not_ready",
            "runtime_publish_started",
            "self_message",
            "session_info_unavailable",
            "shutdown_cancelled",
            "shutdown_completed",
            "shutdown_degraded",
            "shutdown_failed",
            "shutdown_in_progress",
            "shutdown_step_cancelled",
            "shutdown_step_completed",
            "shutdown_step_error",
            "shutdown_step_started",
            "shutdown_step_timeout",
            "skipped",
            "spontaneous_recall_completed",
            "stale_summary_task",
            "startup_backup_completed",
            "startup_backup_started",
            "startup_maintenance_error",
            "startup_restore_completed",
            "startup_restore_started",
            "startup_started",
            "storage_cancelled",
            "storage_completed",
            "storage_error",
            "storage_started",
            "storage_task_already_running",
            "storage_task_scheduled",
            "summary_invalid",
            "summary_metadata_committed",
            "summary_metadata_failed",
            "summary_metadata_retrying",
            "summary_retry_recorded",
            "summary_threshold_not_reached",
            "summary_trigger_reached",
            "task_cancelled",
            "task_error",
            "tool_call_response",
            "tool_loop_summary",
            "top_k_disabled",
            "write_blocked",
        }
    ),
    "capability": frozenset(
        {
            "affection",
            "agent_tools",
            "backfill_scheduler",
            "conversation_manager",
            "database",
            "debug_reporting",
            "decay_scheduler",
            "embedding_and_llm_ready",
            "expression",
            "gate_runtime",
            "graph_database",
            "index_validator",
            "injection_recorder",
            "injection_store",
            "jargon",
            "memory_engine",
            "memory_evolution_manager",
            "memory_evolution_store",
            "memory_processor",
            "prompt_protection",
            "provider_waiting",
            "social",
        }
    ),
}
_MAX_NUMBER = 10**12

_lock = threading.RLock()
_enabled = False
_file_handler: RotatingFileHandler | None = None
_file_path: Path | None = None
_timestamp_timezone: tzinfo | None = None
_operation_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "memora_debug_operation_token", default=None
)


def _new_token() -> str:
    """生成不包含业务标识的短期随机关联码。"""
    return secrets.token_hex(6)


def _close_file_handler() -> None:
    """关闭当前文件 sink，并清理相关模块状态。"""
    global _file_handler, _file_path
    handler = _file_handler
    _file_handler = None
    _file_path = None
    if handler is not None:
        try:
            handler.close()
        except Exception:
            pass


def _safe_exception_type(exception: BaseException) -> str:
    """返回不包含异常消息的异常类型名。"""
    return exception.__class__.__name__


def _exception_location(exception: BaseException) -> dict[str, Any]:
    """提取不含路径和参数的最后一个调用位置。"""
    traceback = exception.__traceback__
    selected = None
    while traceback is not None:
        module = str(traceback.tb_frame.f_globals.get("__name__", ""))
        if (
            module == "main"
            or module.startswith("core")
            or module.startswith("astrbot_plugin_memora")
        ):
            selected = traceback
        traceback = traceback.tb_next
    if selected is None:
        return {}
    frame = selected.tb_frame
    module = str(frame.f_globals.get("__name__", ""))
    function = str(frame.f_code.co_name)
    line = selected.tb_lineno
    if not _SAFE_TEXT_RE.fullmatch(module) or not _SAFE_TEXT_RE.fullmatch(function):
        return {}
    return {
        "exception_module": module,
        "exception_function": function,
        "exception_line": line,
    }


def _valid_number(value: Any) -> bool:
    """检查数值是否属于诊断字段允许的有限非负范围。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= _MAX_NUMBER


def _valid_text(field: str, value: Any) -> bool:
    """按字段 allowlist 检查诊断文本值。"""
    if not isinstance(value, str) or len(value) > 128:
        return False
    if field == "operation_token":
        return _TOKEN_RE.fullmatch(value) is not None
    if field in _VALUE_FIELDS:
        return value in _VALUE_FIELDS[field]
    if field in _ENUM_FIELDS:
        return value in _ENUM_FIELDS[field]
    return _SAFE_TEXT_RE.fullmatch(value) is not None


def _resolve_timezone(timezone_name: str | None) -> tzinfo | None:
    """解析 AstrBot 的 IANA 时区；空值或无效值回退系统本地时区。"""
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        return None
    try:
        return ZoneInfo(timezone_name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _current_timestamp() -> str:
    """按 AstrBot 配置时区生成带 UTC 偏移的 ISO 8601 时间戳。"""
    configured_timezone = _timestamp_timezone
    if configured_timezone is None:
        current = datetime.now().astimezone()
    else:
        current = datetime.now(configured_timezone)
    return current.isoformat().replace("+00:00", "Z")


def _emit_serialized(serialized: str) -> None:
    """向两个 sink 写入已规范化的同一条 JSON。"""
    global _file_handler, _file_path
    try:
        _astrbot_logger.info("[MemoraDebug] %s", serialized)
    except Exception:
        pass
    with _lock:
        handler = _file_handler
        if handler is None and _file_path is not None:
            try:
                _file_path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    _file_path,
                    maxBytes=MAX_BYTES,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                    delay=True,
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                handler.setLevel(logging.INFO)
                _file_handler = handler
            except Exception as exception:
                _file_path = None
                disabled_event = {
                    "timestamp": _current_timestamp(),
                    "schema_version": SCHEMA_VERSION,
                    "event": "debug_file_sink_disabled",
                    "operation_token": _operation_token.get() or _new_token(),
                    "exception_type": _safe_exception_type(exception),
                }
                try:
                    _astrbot_logger.info(
                        "[MemoraDebug] %s",
                        json.dumps(
                            disabled_event,
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                except Exception:
                    pass
                return
        if handler is None:
            return
        try:
            # 文件中保留纯 JSON，便于用户直接使用 json.loads 逐行检查。
            record = logging.LogRecord(
                name="astrbot_plugin_memora.debug_reporter",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=serialized,
                args=(),
                exc_info=None,
            )
            handler.handle(record)
        except Exception as exception:
            if _file_handler is handler:
                _close_file_handler()
            disabled_event = {
                "timestamp": _current_timestamp(),
                "schema_version": SCHEMA_VERSION,
                "event": "debug_file_sink_disabled",
                "operation_token": _operation_token.get() or _new_token(),
                "exception_type": _safe_exception_type(exception),
            }
            try:
                _astrbot_logger.info(
                    "[MemoraDebug] %s",
                    json.dumps(
                        disabled_event,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            except Exception:
                pass


def _emit_rejection(reason_code: str) -> None:
    """输出不携带非法字段内容的固定拒绝事件。"""
    # 拒绝事件只包含固定值，永远不带入非法键名或值。
    event = {
        "timestamp": _current_timestamp(),
        "schema_version": SCHEMA_VERSION,
        "event": "debug_event_rejected",
        "operation_token": _operation_token.get() or _new_token(),
        "reason_code": reason_code
        if reason_code
        in {"unknown_event", "unknown_field", "invalid_field", "invalid_value"}
        else "invalid_value",
    }
    _emit_serialized(
        json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    )


def configure_debug_reporting(
    enabled: bool,
    data_dir: str | Path | None = None,
    *,
    timezone_name: str | None = None,
) -> None:
    """配置问题报告调试模式、文件目录与 AstrBot 时区。

    禁用时不创建目录或文件。时区为空或无效时与 AstrBot 一致，使用系统本地
    时区生成时间戳。
    """
    global _enabled, _file_handler, _file_path, _timestamp_timezone
    with _lock:
        _close_file_handler()
        _enabled = bool(enabled)
        _timestamp_timezone = _resolve_timezone(timezone_name)
        if not _enabled or data_dir is None:
            return
        try:
            _file_path = Path(data_dir) / "diagnostics" / FILE_NAME
        except Exception as exception:
            _file_path = None
            # 失败事件只发送到控制台，绝不泄露路径或错误文本。
            event = {
                "timestamp": _current_timestamp(),
                "schema_version": SCHEMA_VERSION,
                "event": "debug_file_sink_disabled",
                "operation_token": _operation_token.get() or _new_token(),
                "exception_type": _safe_exception_type(exception),
            }
            try:
                _astrbot_logger.info(
                    "[MemoraDebug] %s",
                    json.dumps(
                        event, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                    ),
                )
            except Exception:
                pass


def close_debug_reporting() -> None:
    """关闭文件 sink 并停用问题报告调试事件。"""
    global _enabled
    with _lock:
        _enabled = False
        _close_file_handler()


def is_debug_reporting_enabled() -> bool:
    """返回问题报告调试事件是否启用。"""
    with _lock:
        return _enabled


@contextlib.contextmanager
def debug_operation() -> Iterator[str | None]:
    """为一次异步操作建立短期随机关联码。"""
    if not is_debug_reporting_enabled():
        yield None
        return
    token = _new_token()
    reset = _operation_token.set(token)
    try:
        yield token
    finally:
        _operation_token.reset(reset)


def report_debug_event(event_name: str, **fields: Any) -> None:
    """记录一个通过 allowlist 校验的结构化诊断事件。"""
    if not is_debug_reporting_enabled():
        return
    if event_name not in EVENTS:
        _emit_rejection("unknown_event")
        return
    if any(field not in ALLOWED_FIELDS for field in fields):
        _emit_rejection("unknown_field")
        return
    normalized: dict[str, Any] = {}
    for field, value in fields.items():
        if field in _NUMERIC_FIELDS:
            if not _valid_number(value):
                _emit_rejection("invalid_field")
                return
            normalized[field] = value
        elif not _valid_text(field, value):
            _emit_rejection("invalid_value")
            return
        else:
            normalized[field] = value
    normalized.setdefault("operation_token", _operation_token.get() or _new_token())
    event = {
        "timestamp": _current_timestamp(),
        "schema_version": SCHEMA_VERSION,
        "event": event_name,
        **normalized,
    }
    serialized = json.dumps(
        event, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    _emit_serialized(serialized)


def report_debug_exception(
    event_name: str, exception: BaseException, **fields: Any
) -> None:
    """记录异常类型和安全调用位置，不读取异常消息或完整 traceback。"""
    if not is_debug_reporting_enabled():
        return
    location = _exception_location(exception)
    safe_fields = dict(fields)
    safe_fields["exception_type"] = _safe_exception_type(exception)
    safe_fields.update(location)
    report_debug_event(event_name, **safe_fields)


__all__ = [
    "ALLOWED_FIELDS",
    "BACKUP_COUNT",
    "EVENTS",
    "FILE_NAME",
    "MAX_BYTES",
    "close_debug_reporting",
    "configure_debug_reporting",
    "debug_operation",
    "is_debug_reporting_enabled",
    "report_debug_event",
    "report_debug_exception",
]
