"""Tests for the adaptive injection strategy read-only Page APIs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

VALID_ID = "12345678-1234-5678-1234-567812345678"


class _ConfigManager:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self._values = values or {}

    def get(self, path: str, default: object = None) -> object:
        return self._values.get(path, default)


class _Provider:
    def __init__(self, provider_type: str, model: str) -> None:
        self.provider_config = {"type": provider_type}
        self._model = model

    def get_model(self) -> str:
        return self._model


def _decision_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "decision_id": VALID_ID,
        "created_at_ms": 1_750_000_000_000,
        "trace_id": None,
        "routing_mode": "manual",
        "configured_preset": "balanced",
        "recommended_preset": "balanced",
        "resolved_preset": "balanced",
        "preferred_delivery": "extra_user_content",
        "resolved_delivery": "extra_user_content",
        "fallback_applied": False,
        "outcome": "injected",
        "error_code": None,
        "primary_reason": "MANUAL_SELECTED",
        "provider_type": "openai_chat_completion",
        "provider_model": "gpt-5",
        "candidate_count": 3,
        "selected_count": 2,
        "dropped_count": 1,
        "truncated_count": 0,
        "configured_budget_chars": 1200,
        "effective_budget_chars": 1200,
        "actual_payload_chars": 640,
        "context_headroom_chars": 8000,
        "decision_ms": 0.4,
        "format_ms": 0.8,
        "inject_ms": 0.2,
    }
    row.update(overrides)
    return row


def _make_api(
    *,
    store: object | None = None,
    provider: object | None = None,
    config: dict[str, object] | None = None,
    tools_registered: bool = True,
):
    from core.platform.transport.page_api.injection_strategy_api import (
        InjectionStrategyApiMixin,
    )

    class _Api(InjectionStrategyApiMixin):
        pass

    context = SimpleNamespace(get_using_provider=lambda: provider)
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(injection_decision_store=store),
        context=context,
        config_manager=_ConfigManager(config),
        _llm_tools_registered=tools_registered,
    )
    api = _Api()
    api.plugin = plugin
    return api


def _decision_page(**values: object):
    from core.features.injection.infrastructure.injection_decision_store import (
        DecisionPage,
    )

    return DecisionPage(**values)


def _decision_query(**values: object):
    from core.features.injection.infrastructure.injection_decision_store import (
        DecisionQuery,
    )

    return DecisionQuery(**values)


@pytest.mark.asyncio
async def test_catalog_is_registry_backed_and_contains_no_system_prompt() -> None:
    store = SimpleNamespace(
        summary=AsyncMock(side_effect=AssertionError("catalog must not query SQLite")),
        list_decisions=AsyncMock(
            side_effect=AssertionError("catalog must not query SQLite")
        ),
    )
    api = _make_api(
        store=store,
        provider=_Provider("openai_chat_completion", "gpt-5"),
        config={"agent_tools.enable_recall_tool": True},
    )

    result = await api.get_injection_strategy_catalog_payload({})

    assert result["status"] == "ok"
    data = result["data"]
    assert [item["name"] for item in data["presets"]] == [
        "tool_first",
        "low_cost",
        "balanced",
        "quality",
    ]
    assert data["routing_modes"] == ["manual", "auto", "hybrid"]
    assert "system_prompt" not in data["deliveries"]
    assert data["retention_options"] == [7, 30, 90, 180, 0]
    assert data["provider_tools_supported"] is True
    assert data["memory_tool_available"] is True
    assert data["recall_trace_available"] is True
    assert data["effective_default_delivery"] == "extra_user_content"
    store.summary.assert_not_awaited()
    store.list_decisions.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_uses_adapter_for_effective_provider_delivery() -> None:
    config = {
        "recall_engine.injection_delivery_override": "fake_tool_call",
        "agent_tools.enable_recall_tool": True,
    }
    supported = _make_api(
        provider=_Provider("openai_chat_completion", "gpt-5"),
        config=config,
    )
    unknown = _make_api(provider=_Provider("custom", "mystery"), config=config)

    supported_result = await supported.get_injection_strategy_catalog_payload({})
    unknown_result = await unknown.get_injection_strategy_catalog_payload({})

    assert supported_result["data"]["effective_default_delivery"] == "fake_tool_call"
    assert unknown_result["data"]["effective_default_delivery"] == "extra_user_content"
    assert unknown_result["data"]["provider_tools_supported"] is False


@pytest.mark.asyncio
async def test_summary_validates_window_and_returns_allowlisted_store_result() -> None:
    summary = {
        "window": "24h",
        "decision_count": 1,
        "payload_chars_p95": 640,
        "provider_fallback_rate": 0.0,
        "preset_distribution": {"balanced": 1},
        "cost_trend": [
            {
                "bucket_ms": 1_750_000_000_000,
                "decision_count": 1,
                "payload_chars_p95": 640,
                "provider_fallback_rate": 0.0,
                "query": "must be removed",
            }
        ],
        "recent_events": [
            {
                "decision_id": VALID_ID,
                "created_at_ms": 1_750_000_000_000,
                "trace_id": None,
                "routing_mode": "manual",
                "resolved_preset": "balanced",
                "outcome": "injected",
                "primary_reason": "MANUAL_SELECTED",
                "fallback_applied": False,
                "actual_payload_chars": 640,
                "prompt": "must be removed",
            }
        ],
        "raw_rows": ["must be removed"],
    }
    store = SimpleNamespace(summary=AsyncMock(return_value=summary))
    api = _make_api(store=store)

    invalid = await api.get_injection_strategy_summary_payload({"window": "all"})
    valid = await api.get_injection_strategy_summary_payload({"window": "24h"})

    assert invalid == {
        "status": "error",
        "message": "window must be one of 1h, 24h, 7d, 30d",
    }
    assert valid["status"] == "ok"
    assert "raw_rows" not in valid["data"]
    assert "query" not in valid["data"]["cost_trend"][0]
    assert "prompt" not in valid["data"]["recent_events"][0]
    store.summary.assert_awaited_once_with("24h")


@pytest.mark.asyncio
async def test_decisions_validate_limit_and_return_true_page() -> None:
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(
                items=[_decision_row(query="must be removed")],
                total=43,
                offset=0,
                limit=25,
            )
        )
    )
    api = _make_api(store=store)

    bad = await api.list_injection_decisions_payload({"offset": "0", "limit": "101"})
    ok = await api.list_injection_decisions_payload({"offset": "0", "limit": "25"})

    assert bad == {"status": "error", "message": "limit must be between 1 and 100"}
    assert ok["data"]["offset"] == 0
    assert ok["data"]["limit"] == 25
    assert ok["data"]["total"] == 43
    assert "query" not in ok["data"]["items"][0]
    store.list_decisions.assert_awaited_once_with(_decision_query(offset=0, limit=25))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"unknown": "1"}, "unknown query field: unknown"),
        ({"offset": True}, "offset must be an integer"),
        ({"offset": "-1"}, "offset must be non-negative"),
        ({"limit": False}, "limit must be an integer"),
        ({"from_ms": "20", "to_ms": "10"}, "from_ms must not exceed to_ms"),
        ({"routing_mode": "smart"}, "routing_mode is invalid"),
        ({"resolved_preset": "custom"}, "resolved_preset is invalid"),
        ({"fallback_applied": "yes"}, "fallback_applied must be true or false"),
        ({"outcome": "success"}, "outcome is invalid"),
        ({"sort_by": "decision_id"}, "sort_by is invalid"),
        ({"sort_order": "ASC"}, "sort_order must be asc or desc"),
        ({"sort_order": 1}, "sort_order must be asc or desc"),
    ],
)
async def test_decision_list_rejects_invalid_filters_without_querying_store(
    payload: dict[str, object], message: str
) -> None:
    store = SimpleNamespace(list_decisions=AsyncMock())
    api = _make_api(store=store)

    result = await api.list_injection_decisions_payload(payload)

    assert result == {"status": "error", "message": message}
    store.list_decisions.assert_not_awaited()


@pytest.mark.asyncio
async def test_decision_list_maps_all_valid_filters_to_store_query() -> None:
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(items=[], total=0, offset=10, limit=20)
        )
    )
    api = _make_api(store=store)
    payload = {
        "offset": "10",
        "limit": "20",
        "from_ms": "100",
        "to_ms": "200",
        "routing_mode": "hybrid",
        "resolved_preset": "quality",
        "provider_type": "openai_chat_completion",
        "primary_reason": "EXPLICIT_HISTORY",
        "fallback_applied": "false",
        "outcome": "injected",
    }

    result = await api.list_injection_decisions_payload(payload)

    assert result["status"] == "ok"
    store.list_decisions.assert_awaited_once_with(
        _decision_query(
            offset=10,
            limit=20,
            from_ms=100,
            to_ms=200,
            routing_mode="hybrid",
            resolved_preset="quality",
            provider_type="openai_chat_completion",
            primary_reason="EXPLICIT_HISTORY",
            fallback_applied=False,
            outcome="injected",
        )
    )


@pytest.mark.asyncio
async def test_detail_response_uses_safe_allowlist() -> None:
    detail = _decision_row(
        reason_codes=["MANUAL_SELECTED"],
        query="secret query",
        prompt="secret prompt",
        memory_content="secret body",
        memory_ids=["m-1"],
        user_id="u-1",
        group_id="g-1",
        session_id="s-1",
        stack_trace="trace",
    )
    store = SimpleNamespace(get_decision=AsyncMock(return_value=detail))
    api = _make_api(store=store)

    result = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})

    payload = result["data"]
    forbidden = {
        "query",
        "prompt",
        "memory_content",
        "memory_ids",
        "user_id",
        "group_id",
        "session_id",
        "stack_trace",
    }
    assert forbidden.isdisjoint(payload)
    assert payload["reason_codes"] == ["MANUAL_SELECTED"]
    store.get_decision.assert_awaited_once_with(VALID_ID)


@pytest.mark.asyncio
async def test_detail_validates_uuid_and_reports_missing_record_stably() -> None:
    store = SimpleNamespace(get_decision=AsyncMock(return_value=None))
    api = _make_api(store=store)

    invalid = await api.get_injection_decision_detail_payload(
        {"decision_id": "../../unsafe"}
    )
    missing = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})

    assert invalid == {"status": "error", "message": "decision_id must be a valid UUID"}
    assert missing == {"status": "error", "message": "Injection decision not found"}
    store.get_decision.assert_awaited_once_with(VALID_ID)


@pytest.mark.asyncio
async def test_store_unavailability_has_stable_error_for_data_endpoints() -> None:
    api = _make_api(store=None)

    summary = await api.get_injection_strategy_summary_payload({})
    decisions = await api.list_injection_decisions_payload({})
    detail = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})

    expected = {"status": "error", "message": "Injection decision store unavailable"}
    assert summary == expected
    assert decisions == expected
    assert detail == expected
