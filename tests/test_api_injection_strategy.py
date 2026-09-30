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
        "retrieved_count": 8,
        "injected_count": 5,
        "decision_count": 1,
        "payload_chars_p95": 640,
        "provider_fallback_rate": 0.0,
        "memory_present_count": 1,
        "payload_injected_count": 1,
        "selected_count_total": 3,
        "dropped_count_total": 2,
        "truncated_count_total": 1,
        "effective_budget_chars_avg": 1_200,
        "budget_utilization_avg": 0.625,
        "budget_utilization_p95": 0.75,
        "preset_distribution": {"balanced": 1},
        "cost_trend": [
            {
                "bucket_ms": 1_750_000_000_000,
                "decision_count": 1,
                "payload_chars_p95": 640,
                "provider_fallback_rate": 0.0,
                "selected_count_total": 3,
                "dropped_count_total": 2,
                "budget_utilization_avg": 0.625,
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
        "adopted": 99,
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
    assert set(valid["data"]) == {
        "window",
        "retrieved_count",
        "injected_count",
        "decision_count",
        "payload_chars_p95",
        "provider_fallback_rate",
        "memory_present_count",
        "payload_injected_count",
        "selected_count_total",
        "dropped_count_total",
        "truncated_count_total",
        "effective_budget_chars_avg",
        "budget_utilization_avg",
        "budget_utilization_p95",
        "preset_distribution",
        "cost_trend",
        "recent_events",
    }
    assert valid["data"]["retrieved_count"] == 8
    assert valid["data"]["injected_count"] == 5
    assert "adopted" not in valid["data"]
    assert valid["data"]["selected_count_total"] == 3
    assert valid["data"]["dropped_count_total"] == 2
    assert valid["data"]["truncated_count_total"] == 1
    assert valid["data"]["effective_budget_chars_avg"] == 1_200
    assert valid["data"]["budget_utilization_avg"] == 0.625
    assert valid["data"]["budget_utilization_p95"] == 0.75
    assert valid["data"]["cost_trend"][0]["selected_count_total"] == 3
    assert valid["data"]["cost_trend"][0]["dropped_count_total"] == 2
    assert valid["data"]["cost_trend"][0]["budget_utilization_avg"] == 0.625
    assert "raw_rows" not in valid["data"]
    assert "query" not in valid["data"]["cost_trend"][0]
    assert "prompt" not in valid["data"]["recent_events"][0]
    store.summary.assert_awaited_once_with("24h")


def test_safe_summary_fallback_keeps_the_stable_zero_contract() -> None:
    from core.platform.transport.page_api.injection_strategy_api import (
        InjectionStrategyApiMixin,
    )

    assert InjectionStrategyApiMixin._safe_summary(None) == {
        "window": "24h",
        "retrieved_count": 0,
        "injected_count": 0,
        "decision_count": 0,
        "payload_chars_p95": 0,
        "provider_fallback_rate": 0.0,
        "memory_present_count": 0,
        "payload_injected_count": 0,
        "selected_count_total": 0,
        "dropped_count_total": 0,
        "truncated_count_total": 0,
        "effective_budget_chars_avg": 0,
        "budget_utilization_avg": 0.0,
        "budget_utilization_p95": 0.0,
        "preset_distribution": {},
        "cost_trend": [],
        "recent_events": [],
    }


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
async def test_decision_projections_keep_only_valid_opaque_trace_ids() -> None:
    """合法 trace_id 原样保留用于导航；非法历史值投影为 None。"""
    valid_trace = "0f8fad5b-d9cb-469f-a165-70867728950e"
    unsafe_trace = "private:user-1/session?q=secret"
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(
                items=[
                    _decision_row(trace_id=valid_trace),
                    _decision_row(trace_id=unsafe_trace),
                    _decision_row(trace_id=None),
                ],
                total=3,
                offset=0,
                limit=50,
            )
        ),
        get_decision=AsyncMock(
            return_value=_decision_row(trace_id=unsafe_trace, reason_codes=[])
        ),
        summary=AsyncMock(
            return_value={
                "recent_events": [
                    _decision_row(trace_id=valid_trace),
                    _decision_row(trace_id="x" * 65),
                ]
            }
        ),
    )
    api = _make_api(store=store)

    page = await api.list_injection_decisions_payload({})
    detail = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})
    summary = await api.get_injection_strategy_summary_payload({})

    assert [item["trace_id"] for item in page["data"]["items"]] == [
        valid_trace,
        None,
        None,
    ]
    assert detail["data"]["trace_id"] is None
    assert [item["trace_id"] for item in summary["data"]["recent_events"]] == [
        valid_trace,
        None,
    ]
    assert unsafe_trace not in repr((page, detail, summary))


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


_UNSAFE_LEGACY_VALUES = {
    "decision_id": "legacy:user-42/session-7",
    "created_at_ms": "1750000000000; DROP",
    "trace_id": "private:user-1/session?q=secret",
    "routing_mode": "canary-smart-mode",
    "configured_preset": "custom preset: user 张三",
    "recommended_preset": "CANARY_PRESET",
    "resolved_preset": "Balanced",
    "preferred_delivery": "system_prompt",
    "resolved_delivery": "system_prompt",
    "fallback_applied": "yes",
    "outcome": "success: prompt=secret",
    "error_code": "Traceback (most recent call last): secret",
    "primary_reason": "user asked about secret project",
    "provider_type": "https://tenant.example/api?key=sk-secret",
    "provider_model": "model for alice@example.com",
    "candidate_count": -3,
    "selected_count": True,
    "dropped_count": "2",
    "truncated_count": 1.5,
    "configured_budget_chars": float("inf"),
    "effective_budget_chars": 10**15,
    "actual_payload_chars": float("nan"),
    "context_headroom_chars": None,
    "decision_ms": float("nan"),
    "format_ms": -1.0,
    "inject_ms": "0.2",
}
_UNSAFE_MARKERS = (
    "legacy:user-42",
    "DROP",
    "private:user-1",
    "canary",
    "CANARY",
    "张三",
    "system_prompt",
    "success",
    "Traceback",
    "secret",
    "tenant.example",
    "alice@example.com",
    "user-note",
)


def _assert_no_unsafe_marker(value: object) -> None:
    rendered = repr(value)
    for marker in _UNSAFE_MARKERS:
        assert marker not in rendered, marker


@pytest.mark.asyncio
async def test_legacy_unsafe_values_are_nulled_in_list_detail_and_recent_events() -> (
    None
):
    """历史行的每个值都要复核：非法值投影为 None，绝不字符串化回显。"""
    # 身份可复核、其余字段全部非法的历史行：保留行，但逐值置空。
    legacy_row = {**_decision_row(**_UNSAFE_LEGACY_VALUES), "decision_id": VALID_ID}
    # 身份本身非法的历史行：列表与近期事件中整行丢弃。
    unsafe_row = _decision_row(**_UNSAFE_LEGACY_VALUES)
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(
                items=[dict(legacy_row), dict(unsafe_row)], total=2, offset=0, limit=50
            )
        ),
        get_decision=AsyncMock(
            return_value={
                **unsafe_row,
                "reason_codes": ["MANUAL_SELECTED"],
                "reason_codes_json": '["user-note secret"]',
            }
        ),
        summary=AsyncMock(
            return_value={"recent_events": [dict(legacy_row), dict(unsafe_row)]}
        ),
    )
    api = _make_api(store=store)

    page = await api.list_injection_decisions_payload({})
    detail = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})
    summary = await api.get_injection_strategy_summary_payload({})

    assert len(page["data"]["items"]) == 1
    assert page["data"]["total"] == 2
    list_item = page["data"]["items"][0]
    detail_data = detail["data"]
    assert len(summary["data"]["recent_events"]) == 1
    event = summary["data"]["recent_events"][0]
    assert set(list_item) == set(_UNSAFE_LEGACY_VALUES)
    assert list_item["decision_id"] == VALID_ID
    assert all(v is None for k, v in list_item.items() if k != "decision_id")
    # 详情身份来自请求中已校验的 UUID，而非存储行里的非法值。
    assert {k: v for k, v in detail_data.items() if k != "reason_codes"} == list_item
    assert "reason_codes_json" not in detail_data
    assert detail_data["reason_codes"] == ["MANUAL_SELECTED"]
    assert event["decision_id"] == VALID_ID
    assert all(v is None for k, v in event.items() if k != "decision_id")
    assert set(event) == {
        "decision_id",
        "created_at_ms",
        "trace_id",
        "routing_mode",
        "resolved_preset",
        "outcome",
        "primary_reason",
        "fallback_applied",
        "actual_payload_chars",
    }
    _assert_no_unsafe_marker((page, detail, summary))


@pytest.mark.asyncio
async def test_valid_current_values_survive_value_level_revalidation() -> None:
    """当前生产写入的合法值保持原样，SQLite 0/1 布尔与整数型浮点被规范化。"""
    valid_trace = "0f8fad5b-d9cb-469f-a165-70867728950e"
    row = _decision_row(
        trace_id=valid_trace,
        routing_mode="hybrid",
        configured_preset="tool_first",
        recommended_preset="tool_first",
        resolved_preset="low_cost",
        preferred_delivery="fake_tool_call",
        resolved_delivery="fake_tool_call_deepseek_v4",
        fallback_applied=1,
        outcome="error",
        error_code="PROTECTION_SCOPE_FAILED",
        primary_reason="PROVIDER_TOOL_UNAVAILABLE",
        provider_type="deepseek_chat_completion",
        provider_model="deepseek-ai/DeepSeek-V3.1:free",
        candidate_count=0,
        actual_payload_chars=640.0,
        decision_ms=0,
    )
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(items=[row], total=1, offset=0, limit=50)
        )
    )
    api = _make_api(store=store)

    item = (await api.list_injection_decisions_payload({}))["data"]["items"][0]

    expected = {**row, "fallback_applied": True, "actual_payload_chars": 640}
    assert item == expected
    assert type(item["actual_payload_chars"]) is int
    assert item["decision_ms"] == 0.0


@pytest.mark.asyncio
async def test_detail_reason_codes_keep_only_production_routing_codes() -> None:
    """reason_codes 只保留生产路由闭集，丢弃任意字符串、非字符串与重复项。"""
    store = SimpleNamespace(
        get_decision=AsyncMock(
            return_value=_decision_row(
                reason_codes=[
                    "AUTO_HISTORY_INTENT",
                    "user-note secret",
                    "manual_selected",
                    {"code": "MANUAL_SELECTED"},
                    7,
                    None,
                    "HYBRID_CLAMPED_MIN",
                    "AUTO_HISTORY_INTENT",
                    "PROVIDER_DELIVERY_DOWNGRADED",
                    "NO_USEFUL_CANDIDATES",
                    "CANARY_REASON",
                ]
            )
        )
    )
    api = _make_api(store=store)

    result = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})

    assert result["data"]["reason_codes"] == [
        "AUTO_HISTORY_INTENT",
        "HYBRID_CLAMPED_MIN",
        "PROVIDER_DELIVERY_DOWNGRADED",
        "NO_USEFUL_CANDIDATES",
    ]
    _assert_no_unsafe_marker(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason_codes", ["MANUAL_SELECTED", None, {"a": 1}, 3])
async def test_detail_non_list_reason_codes_become_empty(reason_codes: object) -> None:
    store = SimpleNamespace(
        get_decision=AsyncMock(return_value=_decision_row(reason_codes=reason_codes))
    )
    api = _make_api(store=store)

    result = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})

    assert result["data"]["reason_codes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", 12345),
        ("provider_type", "OpenAI Chat"),
        ("provider_model", "../../etc/passwd"),
        ("provider_model", "a" * 97),
        ("provider_model", "http://host//model"),
        ("fallback_applied", 2),
        ("created_at_ms", 2**53),
        ("decision_ms", 10**9 + 1),
    ],
)
async def test_boundary_values_are_nulled(field: str, value: object) -> None:
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(
                items=[_decision_row(**{field: value})], total=1, offset=0, limit=50
            )
        )
    )
    api = _make_api(store=store)

    item = (await api.list_injection_decisions_payload({}))["data"]["items"][0]

    assert item[field] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision_id",
    [
        "0F8FAD5B-D9CB-469F-A165-70867728950E",
        "{" + VALID_ID + "}",
        "legacy:user-42/session-7",
        12345,
        None,
        "",
    ],
)
async def test_rows_without_safe_decision_id_are_dropped_from_list_and_events(
    decision_id: object,
) -> None:
    """行键契约：decision_id 复核失败的行不进入列表与近期事件。"""
    bad = _decision_row(decision_id=decision_id)
    good = _decision_row()
    missing = {k: v for k, v in _decision_row().items() if k != "decision_id"}
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(
                items=[bad, good, missing], total=3, offset=0, limit=50
            )
        ),
        summary=AsyncMock(return_value={"recent_events": [bad, missing, good]}),
    )
    api = _make_api(store=store)

    page = await api.list_injection_decisions_payload({})
    summary = await api.get_injection_strategy_summary_payload({})

    assert [item["decision_id"] for item in page["data"]["items"]] == [VALID_ID]
    assert page["data"]["total"] == 3
    assert [e["decision_id"] for e in summary["data"]["recent_events"]] == [VALID_ID]
    _assert_no_unsafe_marker((page, summary))


@pytest.mark.asyncio
async def test_missing_fields_stay_absent_and_non_dict_rows_are_dropped() -> None:
    """缺失字段不被伪造为 None；非映射历史行无身份，整行丢弃。"""
    store = SimpleNamespace(
        list_decisions=AsyncMock(
            return_value=_decision_page(
                items=[{"decision_id": VALID_ID}, "raw legacy row secret"],
                total=2,
                offset=0,
                limit=50,
            )
        )
    )
    api = _make_api(store=store)

    page = await api.list_injection_decisions_payload({})

    assert page["data"]["items"] == [{"decision_id": VALID_ID}]
    _assert_no_unsafe_marker(page)


@pytest.mark.asyncio
async def test_store_failures_log_operation_and_type_without_stack_or_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 89 日志规则：只记录固定操作名与异常类型，不带堆栈或异常文本。"""
    from core.platform.transport.page_api import injection_strategy_api as module

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    secret_error = RuntimeError("secret query /data/user-1.db")
    store = SimpleNamespace(
        summary=AsyncMock(side_effect=secret_error),
        list_decisions=AsyncMock(side_effect=secret_error),
        get_decision=AsyncMock(side_effect=secret_error),
    )
    api = _make_api(store=store)

    summary = await api.get_injection_strategy_summary_payload({})
    decisions = await api.list_injection_decisions_payload({})
    detail = await api.get_injection_decision_detail_payload({"decision_id": VALID_ID})

    assert summary == {
        "status": "error",
        "message": "Unable to load injection strategy summary",
    }
    assert decisions == {
        "status": "error",
        "message": "Unable to load injection decisions",
    }
    assert detail == {
        "status": "error",
        "message": "Unable to load injection decision detail",
    }
    assert [args[1:] for args, _ in calls] == [
        ("summary", "RuntimeError"),
        ("list_decisions", "RuntimeError"),
        ("get_decision", "RuntimeError"),
    ]
    assert all(kwargs == {} for _, kwargs in calls)
    assert "secret" not in repr(calls)


@pytest.mark.asyncio
async def test_summary_scalars_and_cost_points_are_value_revalidated() -> None:
    """聚合标量与趋势点逐值复核：非法值回落稳定零值，非法 bucket 丢弃整点。"""
    summary = {
        "window": "all-time secret",
        "retrieved_count": "8 secret",
        "injected_count": -1,
        "decision_count": True,
        "payload_chars_p95": float("nan"),
        "provider_fallback_rate": 1.5,
        "memory_present_count": float("inf"),
        "payload_injected_count": 10**15,
        "selected_count_total": 3,
        "dropped_count_total": 2.0,
        "truncated_count_total": None,
        "effective_budget_chars_avg": "1200",
        "budget_utilization_avg": float("-inf"),
        "budget_utilization_p95": 1.02,
        "preset_distribution": {
            "balanced": 4,
            "quality": "secret",
            "low_cost": -2,
            "canary_preset": 9,
        },
        "cost_trend": [
            {
                "bucket_ms": 1_750_000_000_000,
                "decision_count": "secret",
                "payload_chars_p95": 640,
                "provider_fallback_rate": float("nan"),
                "selected_count_total": 3,
                "dropped_count_total": -2,
                "budget_utilization_avg": 0.625,
                "query": "secret query",
            },
            {"bucket_ms": "secret bucket", "decision_count": 1},
            {"bucket_ms": -1, "decision_count": 1},
            "raw secret row",
        ],
        "recent_events": "secret events",
    }
    api = _make_api(store=SimpleNamespace(summary=AsyncMock(return_value=summary)))

    data = (await api.get_injection_strategy_summary_payload({}))["data"]

    assert data == {
        "window": "24h",
        "retrieved_count": 0,
        "injected_count": 0,
        "decision_count": 0,
        "payload_chars_p95": 0,
        "provider_fallback_rate": 0.0,
        "memory_present_count": 0,
        "payload_injected_count": 0,
        "selected_count_total": 3,
        "dropped_count_total": 2,
        "truncated_count_total": 0,
        "effective_budget_chars_avg": 0,
        "budget_utilization_avg": 0.0,
        "budget_utilization_p95": 1.02,
        "preset_distribution": {"balanced": 4},
        "cost_trend": [
            {
                "bucket_ms": 1_750_000_000_000,
                "decision_count": 0,
                "payload_chars_p95": 640,
                "provider_fallback_rate": 0.0,
                "selected_count_total": 3,
                "dropped_count_total": 0,
                "budget_utilization_avg": 0.625,
            }
        ],
        "recent_events": [],
    }
    assert "secret" not in repr(data)
    assert "canary" not in repr(data)


@pytest.mark.asyncio
async def test_summary_missing_fields_fall_back_to_stable_zero_contract() -> None:
    from core.platform.transport.page_api.injection_strategy_api import (
        InjectionStrategyApiMixin,
    )

    api = _make_api(store=SimpleNamespace(summary=AsyncMock(return_value={})))

    data = (await api.get_injection_strategy_summary_payload({"window": "7d"}))["data"]

    assert data == InjectionStrategyApiMixin._safe_summary(None)
