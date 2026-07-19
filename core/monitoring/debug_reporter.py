"""隐私安全的问题报告调试事件记录器。

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
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

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
    }
)
_ENUM_FIELDS = {
    "status": frozenset({"started", "running", "completed", "failed", "degraded", "skipped", "cancelled", "ready", "waiting", "disabled"}),
    "outcome": frozenset({"injected", "fallback", "skipped", "empty", "error", "blocked", "failed", "cancelled"}),
    "delivery": frozenset({"auto", "extra_user_content", "user_message_before", "user_message_after", "fake_tool_call", "fake_tool_call_deepseek_v4", "system", "user", "tool", "none", "append", "replace"}),
    "route": frozenset({"auto", "manual", "hybrid", "tool_first", "low_cost", "balanced", "quality", "minimal", "standard", "detailed", "none", "default"}),
    "task_type": frozenset({"other", "maintenance", "evolution", "group_listing", "index", "cleanup", "summary"}),
}
_MAX_NUMBER = 10**12

_lock = threading.RLock()
_enabled = False
_file_handler: RotatingFileHandler | None = None
_operation_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "memora_debug_operation_token", default=None
)


def _new_token() -> str:
    return secrets.token_hex(6)


def _close_file_handler() -> None:
    global _file_handler
    handler = _file_handler
    _file_handler = None
    if handler is not None:
        try:
            handler.close()
        except Exception:
            pass


def _safe_exception_type(exception: BaseException) -> str:
    return exception.__class__.__name__


def _exception_location(exception: BaseException) -> dict[str, Any]:
    """提取不含路径和参数的最后一个调用位置。"""
    traceback = exception.__traceback__
    selected = None
    while traceback is not None:
        module = str(traceback.tb_frame.f_globals.get("__name__", ""))
        if module == "main" or module.startswith("core") or module.startswith("astrbot_plugin_memora"):
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
    return {"exception_module": module, "exception_function": function, "exception_line": line}


def _valid_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= _MAX_NUMBER


def _valid_text(field: str, value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 128:
        return False
    if field == "operation_token":
        return _TOKEN_RE.fullmatch(value) is not None
    if field in _ENUM_FIELDS:
        return value in _ENUM_FIELDS[field]
    return _SAFE_TEXT_RE.fullmatch(value) is not None


def _emit_serialized(serialized: str) -> None:
    """向两个 sink 写入已规范化的同一条 JSON。"""
    global _file_handler
    try:
        _astrbot_logger.info("[MemoraDebug] %s", serialized)
    except Exception:
        pass
    with _lock:
        handler = _file_handler
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
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "schema_version": SCHEMA_VERSION,
                "event": "debug_file_sink_disabled",
                "operation_token": _operation_token.get() or _new_token(),
                "exception_type": _safe_exception_type(exception),
            }
            try:
                _astrbot_logger.info(
                    "[MemoraDebug] %s",
                    json.dumps(disabled_event, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                )
            except Exception:
                pass


def _emit_rejection(reason_code: str) -> None:
    # 拒绝事件只包含固定值，永远不带入非法键名或值。
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_version": SCHEMA_VERSION,
        "event": "debug_event_rejected",
        "operation_token": _operation_token.get() or _new_token(),
        "reason_code": reason_code if reason_code in {"unknown_event", "unknown_field", "invalid_field", "invalid_value"} else "invalid_value",
    }
    _emit_serialized(json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def configure_debug_reporting(enabled: bool, data_dir: str | Path | None = None) -> None:
    """配置问题报告调试模式；禁用时不创建目录或文件。"""
    global _enabled, _file_handler
    with _lock:
        _close_file_handler()
        _enabled = bool(enabled)
        if not _enabled or data_dir is None:
            return
        try:
            diagnostics_dir = Path(data_dir) / "diagnostics"
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            path = diagnostics_dir / FILE_NAME
            handler = RotatingFileHandler(
                path,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            handler.setLevel(logging.INFO)
            _file_handler = handler
        except Exception as exception:
            _file_handler = None
            # 失败事件只发送到控制台，绝不泄露路径或错误文本。
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "schema_version": SCHEMA_VERSION,
                "event": "debug_file_sink_disabled",
                "operation_token": _operation_token.get() or _new_token(),
                "exception_type": _safe_exception_type(exception),
            }
            _emit_serialized(json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def close_debug_reporting() -> None:
    """关闭文件 sink 并停用问题报告调试事件。"""
    global _enabled
    with _lock:
        _enabled = False
        _close_file_handler()


def is_debug_reporting_enabled() -> bool:
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
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_version": SCHEMA_VERSION,
        "event": event_name,
        **normalized,
    }
    serialized = json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    _emit_serialized(serialized)


def report_debug_exception(event_name: str, exception: BaseException, **fields: Any) -> None:
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
