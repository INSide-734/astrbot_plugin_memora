"""P0 观测与召回追踪隐私边界测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.diagnostics.event_store import DiagnosticEventStore
from core.page_api import PluginPageApi
from core.retrieval.explainable_recall import capture_explainable_recall
from core.retrieval.trace_store import RecallTraceStore

_SENTINEL = "PRIVATE_SENTINEL_NEVER_EXPOSE"
_FORBIDDEN_TRACE_KEYS = {
    "query",
    "prompt",
    "content",
    "content_preview",
    "doc_id",
    "memory_id",
    "source_id",
    "source_mapping",
    "revision",
    "scope",
    "privacy",
    "privacy_level",
    "role",
    "job_id",
    "session_id",
    "persona_id",
    "user_id",
    "group_id",
    "message_id",
    "provider_id",
    "model",
    "url",
    "headers",
    "token",
    "secret",
    "exception_message",
    "traceback",
    "explanation",
}


def _collect_keys(value: object) -> set[str]:
    """递归收集 JSON 结构中的所有对象键。"""
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for item in value.values():
            keys.update(_collect_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_collect_keys(item))
        return keys
    return set()


def _serialized(value: object) -> str:
    """把观测对象转换为确定性的测试字符串。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@pytest.mark.asyncio
async def test_diagnostic_event_store_keeps_only_safe_scalar_allowlist(
    tmp_path: Path,
) -> None:
    """诊断事件不得保存自由文本、嵌套对象或敏感字段。"""
    store = DiagnosticEventStore(tmp_path / "diagnostics.db")
    await store.initialize()

    event = await store.add_event(
        {
            "domain": "provider",
            "severity": "critical",
            "title": _SENTINEL,
            "message": _SENTINEL,
            "source": _SENTINEL,
            "payload": {
                "component": "recall",
                "stage": "retrieval",
                "status": "failed",
                "reason_code": "recall_error",
                "duration_ms": 12.5,
                "candidate_count": 3,
                "query": _SENTINEL,
                "nested": {"secret": _SENTINEL},
                "ids": [101, 202],
            },
        }
    )

    assert event["payload"] == {
        "component": "recall",
        "stage": "retrieval",
        "status": "failed",
        "reason_code": "recall_error",
        "duration_ms": 12.5,
        "candidate_count": 3,
    }
    assert _SENTINEL not in _serialized(event)
    assert all(
        not isinstance(value, (dict, list)) for value in event["payload"].values()
    )


@pytest.mark.asyncio
async def test_diagnostic_event_store_sanitizes_legacy_rows_on_read(
    tmp_path: Path,
) -> None:
    """历史 SQLite 行也必须在详情和列表读取时重新脱敏。"""
    db_path = tmp_path / "diagnostics.db"
    store = DiagnosticEventStore(db_path)
    await store.initialize()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO diagnostic_events (
                event_id, created_at, domain, severity, title, message,
                source, payload, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-event",
                "2026-07-20T00:00:00+00:00",
                "recall",
                "error",
                _SENTINEL,
                _SENTINEL,
                _SENTINEL,
                json.dumps(
                    {
                        "reason_code": "recall_error",
                        "failed_count": 1,
                        "query": _SENTINEL,
                        "nested": {"secret": _SENTINEL},
                    }
                ),
                None,
            ),
        )
        await db.commit()

    detail = await store.get_event("legacy-event")
    listed = await store.list_events()

    assert detail is not None
    assert detail["payload"] == {"reason_code": "recall_error", "failed_count": 1}
    assert _SENTINEL not in _serialized(detail)
    assert _SENTINEL not in _serialized(listed)


@pytest.mark.asyncio
async def test_recall_trace_store_sanitizes_new_and_legacy_payloads(
    tmp_path: Path,
) -> None:
    """Recall Trace 的写入和历史读取都只能返回安全 DTO。"""
    db_path = tmp_path / "trace.db"
    store = RecallTraceStore(db_path=db_path, retention_count=5)
    await store.initialize()
    unsafe_payload = {
        "trace_id": "trace-safe",
        "query": _SENTINEL,
        "total_ms": 9.5,
        "created_at": 123.0,
        "stages": [
            {
                "name": "search_memories",
                "duration_ms": 8.0,
                "candidate_count": 1,
                "metadata": {"query": _SENTINEL, "candidate_count": 1},
            }
        ],
        "results": [
            {
                "doc_id": "memory-101",
                "rank": 1,
                "initial_score": 0.4,
                "final_score": 0.8,
                "metadata": {
                    "memory_type": "preference",
                    "content_preview": _SENTINEL,
                    "source_mapping": {"memory_id": 101},
                },
                "score_contributions": [
                    {
                        "source": "optimizer",
                        "score": 0.8,
                        "weight": 1.0,
                        "explanation": _SENTINEL,
                        "metadata": {"doc_id": 101},
                    }
                ],
                "graph_paths": [
                    {
                        "nodes": [_SENTINEL],
                        "edges": [_SENTINEL],
                        "metadata": {"job_id": _SENTINEL},
                    }
                ],
            }
        ],
        "filtered": [
            {
                "doc_id": "memory-202",
                "reason": "low_score",
                "stage": "rerank",
                "score": 0.1,
                "metadata": {"content": _SENTINEL},
            }
        ],
        "metadata": {"session_id": _SENTINEL, "debug_trace_available": True},
    }
    await store.save_trace(unsafe_payload)

    async with aiosqlite.connect(db_path) as db:
        legacy = dict(unsafe_payload)
        legacy["trace_id"] = "trace-legacy"
        legacy["created_at"] = 124.0
        await db.execute(
            "INSERT INTO recall_traces (trace_id, created_at, payload_json) VALUES (?, ?, ?)",
            ("trace-legacy", 124.0, json.dumps(legacy, ensure_ascii=False)),
        )
        await db.commit()

    new_trace = await store.get_trace("trace-safe")
    legacy_trace = await store.get_trace("trace-legacy")

    for trace in (new_trace, legacy_trace):
        assert trace is not None
        assert _SENTINEL not in _serialized(trace)
        assert not (_FORBIDDEN_TRACE_KEYS & _collect_keys(trace))
        assert trace["results"][0]["rank"] == 1
        assert trace["results"][0]["final_score"] == 0.8


@pytest.mark.asyncio
async def test_capture_explainable_recall_returns_no_sensitive_observation_fields() -> (
    None
):
    """在线追踪返回值不得包含查询、正文、身份或 canonical memory ID。"""

    class SensitiveEngine:
        """返回带敏感字段的最小召回引擎替身。"""

        debug_trace = [
            {
                "doc_id": 101,
                "source": "optimizer",
                "final_score": 0.9,
                "explanation": _SENTINEL,
                "job_id": _SENTINEL,
            }
        ]

        async def search_memories(self, **_kwargs: object) -> list[dict[str, object]]:
            """返回一条包含正文、身份和来源映射的候选。"""
            return [
                {
                    "doc_id": 101,
                    "content": _SENTINEL,
                    "final_score": 0.9,
                    "metadata": {
                        "memory_type": "preference",
                        "status": "active",
                        "session_id": _SENTINEL,
                        "source_mapping": {"memory_id": 101},
                    },
                }
            ]

    trace = await capture_explainable_recall(
        SensitiveEngine(),
        {
            "query": _SENTINEL,
            "session_id": _SENTINEL,
            "user_id": _SENTINEL,
            "chat_type": "private",
        },
    )

    assert _SENTINEL not in _serialized(trace)
    assert not (_FORBIDDEN_TRACE_KEYS & _collect_keys(trace))
    assert trace["results"][0]["rank"] == 1
    assert trace["results"][0]["metadata"] == {
        "memory_type": "preference",
        "status": "active",
    }


@pytest.mark.asyncio
async def test_diagnostics_api_returns_stable_codes_without_exception_text(
    tmp_path: Path,
) -> None:
    """Diagnostics API 的异常和未知动作不得回显输入或异常正文。"""
    plugin = SimpleNamespace(
        context=MagicMock(),
        initializer=SimpleNamespace(memory_engine=object(), data_dir=tmp_path),
    )
    api = PluginPageApi(plugin)
    api._get_diagnostic_event_store = AsyncMock(side_effect=RuntimeError(_SENTINEL))

    listed = await api.get_diagnostics_events_payload({})
    detail = await api.get_diagnostics_event_detail_payload({"event_id": "safe-event"})
    action = await api.run_diagnostics_action_payload(
        {"action": f"unknown-{_SENTINEL}"}
    )

    assert listed == {"status": "error", "message": "diagnostics_events_failed"}
    assert detail == {"status": "error", "message": "diagnostics_event_failed"}
    assert action == {"status": "error", "message": "unknown_diagnostics_action"}
    assert _SENTINEL not in _serialized([listed, detail, action])


@pytest.mark.asyncio
async def test_recall_trace_api_returns_stable_codes_without_exception_text() -> None:
    """Recall Trace API 的搜索和详情异常不得返回原始异常消息。"""

    class FailingEngine:
        """抛出带敏感正文异常的召回引擎替身。"""

        async def search_memories(self, **_kwargs: object) -> list[object]:
            """模拟 Provider 或存储异常。"""
            raise RuntimeError(_SENTINEL)

    plugin = MagicMock()
    plugin.initializer = SimpleNamespace(memory_engine=FailingEngine(), data_dir=None)
    api = PluginPageApi(plugin)
    api._recall_trace_store = RecallTraceStore()

    capture = await api.test_recall_with_trace_payload({"query": "safe query"})
    api._get_recall_trace_store = AsyncMock(side_effect=RuntimeError(_SENTINEL))
    detail = await api.get_recall_trace_detail_payload({"trace_id": "trace-safe"})

    assert capture == {"status": "error", "message": "recall_trace_failed"}
    assert detail == {"status": "error", "message": "recall_trace_detail_failed"}
    assert _SENTINEL not in _serialized([capture, detail])
