"""只读诊断命令处理器测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.platform.resources import i18n_backend
from core.platform.resources.i18n_backend import init as i18n_init
from core.platform.transport.commands.diagnostic_commands import DiagnosticCommandMixin

_SENTINEL = "PRIVATE_SENTINEL_NEVER_EXPOSE"


class _Handler(DiagnosticCommandMixin):
    def __init__(
        self,
        *,
        health_provider=None,
        metrics_provider=None,
        trace_provider=None,
    ) -> None:
        self._diagnostics_health_provider = health_provider
        self._diagnostics_metrics_provider = metrics_provider
        self._recall_trace_provider = trace_provider


def _event(*, group: bool = False):
    event = MagicMock()
    event.unified_msg_origin = "group-session" if group else "private-session"
    event.get_message_type.return_value = (
        MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    )
    event.plain_result = MagicMock(side_effect=lambda message: message)
    return event


async def _collect(gen: AsyncGenerator):
    results = []
    async for item in gen:
        results.append(item)
    return results


@pytest.fixture(autouse=True)
def _initialize_backend_i18n():
    previous = (
        i18n_backend._fallback,
        i18n_backend._translations,
        i18n_backend._current_lang,
    )
    i18n_init("zh")
    try:
        yield
    finally:
        (
            i18n_backend._fallback,
            i18n_backend._translations,
            i18n_backend._current_lang,
        ) = previous


class TestHealthCommand:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("score", "level", "domain", "status"),
        (
            (92, "healthy", None, None),
            (70, "watch", "provider", "watch"),
            (50, "degraded", "recall", "degraded"),
            (44, "critical", "provider", "critical"),
        ),
    )
    async def test_formats_localized_health_levels_without_raw_messages(
        self,
        score: int,
        level: str,
        domain: str | None,
        status: str | None,
    ) -> None:
        domains = []
        if domain is not None and status is not None:
            domains.append(
                {
                    "name": domain,
                    "score": score,
                    "status": status,
                    "message": _SENTINEL,
                }
            )
        provider = AsyncMock(
            return_value={
                "status": "ok",
                "data": {
                    "score": score,
                    "level": level,
                    "domains": domains,
                    "recommended_actions": [_SENTINEL],
                },
            }
        )
        handler = _Handler(health_provider=provider)

        results = await _collect(handler.handle_health(_event()))

        assert len(results) == 1
        assert str(score) in results[0]
        assert _SENTINEL not in results[0]
        provider.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_missing_health_provider_returns_component_error(self) -> None:
        results = await _collect(_Handler().handle_health(_event()))

        assert len(results) == 1
        assert "/memora health" in results[0]


class TestDiagnosticsCommand:
    @pytest.mark.asyncio
    async def test_outputs_only_allowlisted_runtime_scalars(self) -> None:
        provider = AsyncMock(
            return_value={
                "status": "ok",
                "data": {
                    "provider": {
                        "status": "waiting",
                        "attempts": 3,
                        "max_attempts": 60,
                        "retry_active": True,
                        "error_message": _SENTINEL,
                        "missing_provider": [_SENTINEL],
                    },
                    "recall": {
                        "sample_count": 12,
                        "avg_total_ms": 34.5,
                        "p50_total_ms": 30.0,
                        "p95_total_ms": 80.0,
                        "recent": [{"query": _SENTINEL}],
                    },
                    "background_tasks": {
                        "tracked": 5,
                        "active": 1,
                        "completed": 2,
                        "failed": 1,
                        "cancelled": 1,
                        "failed_tasks": [{"message": _SENTINEL}],
                    },
                    "index": {
                        "validator_available": True,
                        "last_check_consistent": False,
                        "last_check_needs_rebuild": True,
                        "last_check_reason": _SENTINEL,
                        "last_rebuild_total": 20,
                        "last_rebuild_errors": 2,
                        "last_rebuild_message": _SENTINEL,
                    },
                    "write_coordinator": {
                        "operations_total": 100,
                        "lock_retries_total": 4,
                        "failures_total": 2,
                        "fatal_failures_total": 1,
                        "non_retryable_failures_total": 1,
                        "last_error": _SENTINEL,
                    },
                    "prometheus": {
                        "available": True,
                        "collector_count": 7,
                        "metric_names": [_SENTINEL],
                    },
                },
            }
        )
        handler = _Handler(metrics_provider=provider)

        results = await _collect(handler.handle_diagnostics(_event()))

        assert len(results) == 1
        for expected in ("3", "60", "12", "34.50", "20", "100", "7"):
            assert expected in results[0]
        assert _SENTINEL not in results[0]
        provider.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_missing_snapshot_fields_degrade_to_safe_defaults(self) -> None:
        provider = AsyncMock(return_value={"status": "ok", "data": {}})
        handler = _Handler(metrics_provider=provider)

        results = await _collect(handler.handle_diagnostics(_event()))

        assert len(results) == 1
        assert _SENTINEL not in results[0]


class TestTraceCommand:
    @pytest.mark.asyncio
    async def test_rejects_empty_query_without_calling_provider(self) -> None:
        provider = AsyncMock()
        handler = _Handler(trace_provider=provider)

        results = await _collect(handler.handle_trace(_event(), "   "))

        assert len(results) == 1
        assert "/memora trace" in results[0]
        provider.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("requested", "expected"), ((-5, 1), (5, 5), (99, 20)))
    async def test_clamps_k_and_passes_current_group_scope(
        self,
        requested: int,
        expected: int,
    ) -> None:
        provider = AsyncMock(
            return_value={
                "status": "ok",
                "data": {
                    "trace_id": "trace-123",
                    "total_ms": 10.0,
                    "stages": [],
                    "results": [],
                    "filtered": [],
                },
            }
        )
        handler = _Handler(trace_provider=provider)
        event = _event(group=True)

        await _collect(handler.handle_trace(event, "  coffee  ", requested))

        provider.assert_awaited_once_with(
            {
                "query": "coffee",
                "k": expected,
                "session_id": "group-session",
                "chat_type": "group",
            }
        )

    @pytest.mark.asyncio
    async def test_outputs_scores_and_stage_summary_without_ids_or_content(
        self,
    ) -> None:
        """命令输出应保留排名和分数，但隐藏 memory ID 与敏感正文。"""
        provider = AsyncMock(
            return_value={
                "status": "ok",
                "data": {
                    "trace_id": "trace-123",
                    "query": _SENTINEL,
                    "total_ms": 12.345,
                    "stages": [
                        {
                            "name": "search_memories",
                            "duration_ms": 10.5,
                            "candidate_count": 3,
                            "metadata": {"raw": _SENTINEL},
                        },
                        {
                            "name": "injection_decision",
                            "duration_ms": 1.5,
                            "candidate_count": 2,
                            "metadata": {
                                "routing_mode": "hybrid",
                                "resolved_preset": "balanced",
                                "reason_code": "AUTO_FALLBACK",
                                "raw": _SENTINEL,
                            },
                        },
                    ],
                    "results": [
                        {
                            "doc_id": "101",
                            "rank": 1,
                            "initial_score": 0.4,
                            "final_score": 0.82,
                            "metadata": {"content_preview": _SENTINEL},
                            "score_contributions": [
                                {"source": "optimizer", "explanation": _SENTINEL}
                            ],
                        }
                    ],
                    "filtered": [{"doc_id": _SENTINEL, "reason": _SENTINEL}],
                    "metadata": {"raw": _SENTINEL},
                },
            }
        )
        handler = _Handler(trace_provider=provider)

        results = await _collect(handler.handle_trace(_event(), "coffee", 5))

        assert len(results) == 1
        for expected in ("trace-123", "12.35", "10.50", "0.40", "0.82"):
            assert expected in results[0]
        assert "101" not in results[0]
        assert _SENTINEL not in results[0]

    @pytest.mark.asyncio
    async def test_error_envelope_does_not_expose_provider_message(self) -> None:
        provider = AsyncMock(return_value={"status": "error", "message": _SENTINEL})
        handler = _Handler(trace_provider=provider)

        results = await _collect(handler.handle_trace(_event(), "coffee"))

        assert len(results) == 1
        assert _SENTINEL not in results[0]

    @pytest.mark.asyncio
    async def test_provider_exception_does_not_expose_exception_message(self) -> None:
        provider = AsyncMock(side_effect=RuntimeError(_SENTINEL))
        handler = _Handler(trace_provider=provider)

        results = await _collect(handler.handle_trace(_event(), "coffee"))

        assert len(results) == 1
        assert _SENTINEL not in results[0]

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self) -> None:
        provider = AsyncMock(side_effect=asyncio.CancelledError())
        handler = _Handler(trace_provider=provider)

        with pytest.raises(asyncio.CancelledError):
            await _collect(handler.handle_trace(_event(), "coffee"))
