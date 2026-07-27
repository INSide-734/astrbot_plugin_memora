"""验证隐私安全问题报告记录器的公开契约。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from core.monitoring import debug_reporter


@pytest.fixture(autouse=True)
def _reset_debug_reporter() -> None:
    """每个用例前后关闭模块级记录器，避免文件句柄和状态串扰。"""
    debug_reporter.close_debug_reporting()
    yield
    debug_reporter.close_debug_reporting()


def _debug_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    """从 AstrBot 日志 sink 提取结构化诊断事件。"""
    records: list[dict[str, object]] = []
    for record in caplog.records:
        message = record.getMessage()
        if "[MemoraDebug] " in message:
            records.append(json.loads(message.split("[MemoraDebug] ", 1)[1]))
    return records


def test_disabled_reporting_does_not_create_diagnostics_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """关闭时既不落盘，也不创建诊断目录。"""
    debug_reporter.configure_debug_reporting(False, tmp_path)

    debug_reporter.report_debug_event(
        "recall_completed",
        component="recall",
        stage="recall",
        status="completed",
    )

    assert not (tmp_path / "diagnostics").exists()
    assert "[MemoraDebug]" not in caplog.text
    assert debug_reporter.is_debug_reporting_enabled() is False


def test_accepted_event_is_emitted_to_console_and_jsonl(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """合法事件以相同内容同时进入控制台和 JSONL 文件。"""
    caplog.set_level(logging.INFO)
    debug_reporter.configure_debug_reporting(True, tmp_path)

    with debug_reporter.debug_operation() as operation_token:
        debug_reporter.report_debug_event(
            "recall_completed",
            component="recall",
            stage="recall",
            status="completed",
            duration_ms=12.5,
            candidate_count=3,
            injected_count=1,
            filtered_count=2,
        )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    file_record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    console_records = _debug_records(caplog)

    assert operation_token is not None
    assert len(operation_token) == 12
    assert all(char in "0123456789abcdef" for char in operation_token)
    assert file_record["event"] == "recall_completed"
    assert file_record["operation_token"] == operation_token
    assert file_record in console_records
    assert debug_reporter.is_debug_reporting_enabled() is True


def test_instrumented_call_event_keeps_function_timing_scalar_only(
    tmp_path: Path,
) -> None:
    """函数级诊断允许安全函数名和耗时，但不接受调用参数。"""
    debug_reporter.configure_debug_reporting(True, tmp_path)

    debug_reporter.report_debug_event(
        "instrumented_call",
        component="instrumentation",
        stage="call",
        status="completed",
        reason_code="call_completed",
        function="core_handlers_recall_RecallHandler_handle_memory_recall",
        duration_ms=4.5,
        call_depth=1,
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "instrumented_call"
    assert record["function"].startswith("core_handlers_")
    assert record["duration_ms"] == 4.5
    assert record["call_depth"] == 1
    assert "args" not in record
    assert "kwargs" not in record


def test_storage_event_accepts_named_non_negative_counts(tmp_path: Path) -> None:
    """存储诊断允许命名计数和 storage 任务类型。"""
    debug_reporter.configure_debug_reporting(True, tmp_path)

    debug_reporter.report_debug_event(
        "storage_task",
        component="reflection",
        stage="memory_write",
        status="degraded",
        reason_code="memory_write_partial",
        task_type="storage",
        message_count=20,
        batch_count=2,
        success_count=3,
        failed_count=1,
        retry_count=1,
        attempt_count=2,
        skipped_count=2,
        queue_depth=4,
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "storage_task"
    assert record["task_type"] == "storage"
    assert record["message_count"] == 20
    assert record["batch_count"] == 2
    assert record["success_count"] == 3
    assert record["failed_count"] == 1
    assert record["retry_count"] == 1
    assert record["attempt_count"] == 2
    assert record["skipped_count"] == 2
    assert record["queue_depth"] == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message_count", -1),
        ("retry_count", True),
        ("attempt_count", -2),
        ("queue_depth", float("nan")),
    ],
)
def test_named_counts_reject_negative_boolean_and_non_finite_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    """命名计数仍执行非负有限数值校验。"""
    debug_reporter.configure_debug_reporting(True, tmp_path)

    debug_reporter.report_debug_event(
        "storage_task",
        component="reflection",
        stage="memory_write",
        status="failed",
        reason_code="memory_write_partial",
        **{field: value},
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "debug_event_rejected"
    assert field not in record


def test_unknown_field_rejects_entire_event_without_writing_sensitive_value(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """未知字段导致整条事件 fail-closed，不能把原始值写入任一 sink。"""
    caplog.set_level(logging.INFO)
    sentinel = "PRIVATE_QUERY_SENTINEL"
    debug_reporter.configure_debug_reporting(True, tmp_path)

    debug_reporter.report_debug_event(
        "recall_completed",
        component="recall",
        stage="recall",
        status="completed",
        query=sentinel,
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    records = _debug_records(caplog)

    assert sentinel not in content
    assert sentinel not in caplog.text
    assert any(record["event"] == "debug_event_rejected" for record in records)
    assert all(record["event"] != "recall_completed" for record in records)


def test_unknown_reason_code_rejects_sensitive_looking_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """原因码必须属于固定集合，不能借安全字符集携带任意标识。"""
    caplog.set_level(logging.INFO)
    sentinel = "PRIVATE_QUERY_SENTINEL"
    debug_reporter.configure_debug_reporting(True, tmp_path)

    debug_reporter.report_debug_event(
        "recall_completed",
        component="recall",
        stage="recall",
        status="completed",
        reason_code=sentinel,
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    assert sentinel not in content
    assert sentinel not in caplog.text
    assert any(
        record["event"] == "debug_event_rejected" for record in _debug_records(caplog)
    )


def test_exception_event_contains_only_safe_location_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """异常摘要不得序列化消息、路径或完整 traceback。"""
    caplog.set_level(logging.INFO)
    sentinel = "PRIVATE_EXCEPTION_SENTINEL C:/private/user/data"
    debug_reporter.configure_debug_reporting(True, tmp_path)

    try:
        raise ValueError(sentinel)
    except ValueError as exception:
        debug_reporter.report_debug_exception(
            "recall_failed",
            exception,
            component="recall",
            stage="recall",
            status="failed",
            reason_code="recall_error",
        )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    content = path.read_text(encoding="utf-8")
    record = json.loads(content.splitlines()[0])

    assert record["exception_type"] == "ValueError"
    if "exception_module" in record:
        assert isinstance(record["exception_module"], str)
        assert isinstance(record["exception_function"], str)
        assert isinstance(record["exception_line"], int)
    assert sentinel not in content
    assert "C:/private/user/data" not in content
    assert "exception_message" not in record
    assert "traceback" not in record
    assert sentinel not in caplog.text


def test_file_rotation_keeps_current_file_and_two_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """诊断文件按大小轮转，保留数量严格限制为三个。"""
    monkeypatch.setattr(debug_reporter, "MAX_BYTES", 128)
    debug_reporter.configure_debug_reporting(True, tmp_path)

    for _ in range(20):
        debug_reporter.report_debug_event(
            "recall_completed",
            component="recall",
            stage="recall",
            status="completed",
            duration_ms=1,
            candidate_count=1,
        )

    files = list((tmp_path / "diagnostics").glob("memora-debug.jsonl*"))
    assert len(files) == 3
    assert (tmp_path / "diagnostics" / "memora-debug.jsonl").exists()


def test_file_sink_initialization_failure_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """文件 sink 初始化失败不能中断调用方，也不能泄露路径或异常消息。"""
    caplog.set_level(logging.INFO)
    sentinel = "PRIVATE_FILE_SINK_SENTINEL C:/private/user/data"

    class _BrokenRotatingFileHandler:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise OSError(sentinel)

    monkeypatch.setattr(
        debug_reporter, "RotatingFileHandler", _BrokenRotatingFileHandler
    )

    debug_reporter.configure_debug_reporting(True, tmp_path)
    debug_reporter.report_debug_event(
        "recall_completed",
        component="recall",
        stage="recall",
        status="completed",
    )

    assert sentinel not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert any(
        record["event"] == "debug_file_sink_disabled"
        for record in _debug_records(caplog)
    )
