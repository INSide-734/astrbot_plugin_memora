"""召回、注入和反思诊断事件的隐私边界测试。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.features.injection.domain.models import (
    DeliveryMode,
    InjectionDecision,
    InjectionExecutionResult,
    InjectionOutcome,
    PresetName,
    RequestSignals,
    RoutingMode,
)
from core.features.observability.infrastructure import debug_reporter
from core.features.recall.application.recall_handler import RecallHandler
from core.features.reflection.application.reflection_handler import ReflectionHandler
from core.features.retrieval.trace_store import RecallTraceStore
from core.page_api import PluginPageApi


@pytest.fixture(autouse=True)
def _reset_debug_reporter() -> None:
    """每个用例结束后关闭文件 sink，避免测试之间共享句柄。"""
    debug_reporter.close_debug_reporting()
    yield
    debug_reporter.close_debug_reporting()


def _records(path: Path) -> list[dict[str, object]]:
    """读取诊断 JSONL 中的结构化事件。"""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _decision() -> InjectionDecision:
    """构造不会携带请求正文的固定注入决策。"""
    return InjectionDecision(
        routing_mode=RoutingMode.AUTO,
        configured_preset=PresetName.BALANCED,
        recommended_preset=PresetName.QUALITY,
        resolved_preset=PresetName.QUALITY,
        content_level="COMPACT",
        memory_budget_chars=500,
        max_memories=4,
        preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
        resolved_delivery=DeliveryMode.EXTRA_USER_CONTENT,
        skip_passive_recall=False,
        allow_tool_fallback=True,
    )


@pytest.mark.asyncio
async def test_recall_trace_page_api_writes_debug_completion_event(
    tmp_path: Path,
) -> None:
    """控制台召回追踪应把安全完成事件写入问题报告文件。"""

    class EmptyEngine:
        """返回零候选以覆盖用户问题中的控制台场景。"""

        async def search_memories(self, **_kwargs: object) -> list[object]:
            """模拟一次成功但无命中的检索。"""
            return []

    class Config:
        """提供路由预览所需的最小配置读取接口。"""

        runtime_injection_fallback = False

        @staticmethod
        def get(_key: str, default: object = None) -> object:
            """返回调用方提供的默认配置值。"""
            return default

    debug_reporter.configure_debug_reporting(True, tmp_path)
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(memory_engine=EmptyEngine(), data_dir=None),
        config_manager=Config(),
    )
    api = PluginPageApi(plugin)
    api._recall_trace_store = RecallTraceStore()

    response = await api.test_recall_with_trace_payload({"query": "safe query"})

    assert response["status"] == "ok"
    assert response["data"]["metadata"] == {
        "debug_trace_available": False,
        "debug_reporting_enabled": True,
    }
    records = _records(tmp_path / "diagnostics" / "memora-debug.jsonl")
    event = next(record for record in records if record["event"] == "recall_completed")
    assert event["component"] == "page_api"
    assert event["reason_code"] == "memory_search_completed"
    assert event["candidate_count"] == 0


def test_injection_and_recall_events_exclude_sensitive_request_signals(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """注入和召回事件只允许安全枚举、计数和预算字段。"""
    caplog.set_level(logging.INFO)
    debug_reporter.configure_debug_reporting(True, tmp_path)
    sentinel = "PRIVATE_QUERY_PROMPT_MEMORY_USER_GROUP_PROVIDER_SENTINEL"
    signals = RequestSignals(
        query_intent=sentinel,
        provider_type=sentinel,
        provider_model=sentinel,
        candidate_count=7,
        context_headroom_chars=900,
    )
    result = InjectionExecutionResult(
        outcome=InjectionOutcome.INJECTED,
        configured_budget_chars=500,
        effective_budget_chars=450,
        actual_payload_chars=320,
        selected_count=2,
        actual_resolved_delivery=DeliveryMode.EXTRA_USER_CONTENT,
    )

    RecallHandler._report_injection_result(_decision(), signals, result)
    handler = object.__new__(RecallHandler)
    handler._perf_tracker = None
    handler._record_recall_observability(
        total_ms=12.5,
        injected_count=2,
        filtered_count=1,
        candidate_count=7,
    )

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    content = path.read_text(encoding="utf-8")
    records = _records(path)

    assert sentinel not in content
    assert sentinel not in caplog.text
    injection = next(
        record for record in records if record["event"] == "injection_completed"
    )
    assert injection["route"] == "quality"
    assert injection["delivery"] == "extra_user_content"
    assert injection["candidate_count"] == 7
    assert injection["selected_count"] == 2
    recall = next(record for record in records if record["event"] == "recall_completed")
    assert recall["candidate_count"] == 7
    assert recall["injected_count"] == 2


@pytest.mark.asyncio
async def test_reflection_exception_reports_type_without_message_or_identity(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """反思派生任务失败时不输出异常消息、记忆 ID 或用户标识。"""
    caplog.set_level(logging.INFO)
    debug_reporter.configure_debug_reporting(True, tmp_path)
    sentinel = "PRIVATE_PROVIDER_ERROR session=PRIVATE_SESSION memory=PRIVATE_MEMORY"

    class BrokenEvolutionStore:
        async def load_sources(self, memory_ids: tuple[int, ...]):
            raise ValueError(sentinel)

    handler = object.__new__(ReflectionHandler)
    handler._memory_evolution_manager = SimpleNamespace(
        store=BrokenEvolutionStore(),
        schedule_consider=lambda source: None,
    )

    await handler._schedule_evolution_after_write(42)

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    content = path.read_text(encoding="utf-8")
    records = _records(path)
    event = next(record for record in records if record["event"] == "storage_task")
    assert event["status"] == "failed"
    assert event["exception_type"] == "ValueError"
    assert sentinel not in content
    assert sentinel not in caplog.text
    assert "memory_id" not in event
    assert "session_id" not in event


@pytest.mark.asyncio
async def test_reflection_cancellation_is_re_raised_after_safe_event(
    tmp_path: Path,
) -> None:
    """取消信号记录后仍然向上传播，不能被诊断逻辑吞掉。"""
    debug_reporter.configure_debug_reporting(True, tmp_path)

    class CancelledEvolutionStore:
        async def load_sources(self, memory_ids: tuple[int, ...]):
            raise asyncio.CancelledError

    handler = object.__new__(ReflectionHandler)
    handler._memory_evolution_manager = SimpleNamespace(
        store=CancelledEvolutionStore(),
        schedule_consider=lambda source: None,
    )

    with pytest.raises(asyncio.CancelledError):
        await handler._schedule_evolution_after_write(42)

    path = tmp_path / "diagnostics" / "memora-debug.jsonl"
    events = _records(path)
    assert any(
        record["event"] == "storage_task"
        and record["status"] == "cancelled"
        and record["reason_code"] == "evolution_cancelled"
        for record in events
    )
