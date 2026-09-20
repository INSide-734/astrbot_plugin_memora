"""近重复指标 Page API 的白名单、错误码与零值契约。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from core.features.memory.infrastructure.dedup_metrics_store import (
    DEDUP_METRIC_MODES,
    DEDUP_METRIC_OUTCOMES,
    HOUR_MS,
    DedupMetricsStore,
)
from core.platform.transport.page_api.page_api import PAGE_API_PREFIX, PluginPageApi

NOW_MS = 1_699_999_200_000

EXPECTED_KEYS = {
    "window",
    *DEDUP_METRIC_OUTCOMES,
    "hit_rate",
    "guard_rate",
    "overlap_rate",
    "failure_rate",
    "by_mode",
    "trend",
}


def _counts(**overrides: int) -> dict[str, int]:
    """构造覆盖全部 outcome 闭集的计数模板，缺省全零。"""

    return {outcome: 0 for outcome in DEDUP_METRIC_OUTCOMES} | overrides


def _by_mode(**per_mode: dict[str, int]) -> dict[str, dict[str, int]]:
    """构造覆盖全部模式闭集的分模式计数。"""

    return {mode: per_mode.get(mode, _counts()) for mode in DEDUP_METRIC_MODES}


_SENSITIVE_MARKERS = (
    "scope_key",
    "session_id",
    "persona_id",
    "content",
    "memory_id",
    "reason",
    "idempotency",
)


def _make_api(store) -> PluginPageApi:
    """构造与生产一致的多 mixin 组合 API。

    端点必须经由真实 ``PluginPageApi`` 调用：只有组合后的 MRO 才能暴露
    mixin 之间的私有 helper 名称遮蔽（历史缺陷：``_safe_summary`` 被
    ``InjectionStrategyApiMixin`` 遮蔽后端点抛 TypeError）。
    """

    return _make_page_api(store)


def _make_page_api(store) -> PluginPageApi:
    """构造可注册路由的 Page API；Store 由 initializer 提供。"""

    initializer = SimpleNamespace(
        dedup_metrics_store=store,
        memory_engine=MagicMock(),
        conversation_manager=MagicMock(),
        index_validator=MagicMock(),
    )
    plugin = SimpleNamespace(
        initializer=initializer,
        context=MagicMock(),
        _ensure_plugin_ready=AsyncMock(return_value=(True, "")),
    )
    return PluginPageApi(plugin)


async def _seeded_store(tmp_path) -> DedupMetricsStore:
    """写入与手算期望值一致的事件序列（固定墙钟便于断言）。"""

    store = DedupMetricsStore(
        str(tmp_path / "dedup_metrics.sqlite3"),
        clock=lambda: NOW_MS / 1000.0,
    )
    await store.initialize()
    for _ in range(4):
        await store.record("observe", "checked", now_ms=NOW_MS)
    for _ in range(3):
        await store.record("observe", "hit", now_ms=NOW_MS)
    await store.record("observe", "fact_mismatch", now_ms=NOW_MS)
    for _ in range(2):
        await store.record("observe", "fact_overlap", now_ms=NOW_MS)
    for _ in range(6):
        await store.record("enforce", "checked", now_ms=NOW_MS)
    for _ in range(4):
        await store.record("enforce", "hit", now_ms=NOW_MS)
    for _ in range(3):
        await store.record("enforce", "merged", now_ms=NOW_MS)
    await store.record("enforce", "fact_overlap", now_ms=NOW_MS)
    await store.record("enforce", "conflict", now_ms=NOW_MS)
    await store.record("enforce", "failed", now_ms=NOW_MS)
    return store


@pytest_asyncio.fixture
async def seeded_store(tmp_path) -> AsyncIterator[DedupMetricsStore]:
    """提供已写入手算样本的 Store，用例结束后关闭。"""

    store = await _seeded_store(tmp_path)
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_store_missing_returns_zero_value_contract() -> None:
    """Store 不可用时返回零值契约而不是错误 envelope。"""

    result = await _make_api(None).get_memory_dedup_metrics_payload({})

    assert result["status"] == "ok"
    data = result["data"]
    assert set(data) == EXPECTED_KEYS
    assert data == {
        "window": "24h",
        **_counts(),
        "hit_rate": 0.0,
        "guard_rate": 0.0,
        "overlap_rate": 0.0,
        "failure_rate": 0.0,
        "by_mode": _by_mode(),
        "trend": [],
    }


@pytest.mark.asyncio
async def test_unknown_window_returns_stable_error_code() -> None:
    """未知窗口必须返回 ``invalid_window`` 稳定错误码。"""

    result = await _make_api(None).get_memory_dedup_metrics_payload({"window": "12h"})

    assert result["status"] == "error"
    assert result["code"] == "invalid_window"
    assert "data" not in result


@pytest.mark.asyncio
async def test_store_failure_degrades_to_zero_contract() -> None:
    """Store 读取异常不得产生 500，也不得暴露异常文本。"""

    store = SimpleNamespace(
        summary=AsyncMock(side_effect=RuntimeError("sqlite unavailable"))
    )

    result = await _make_api(store).get_memory_dedup_metrics_payload({"window": "7d"})

    assert result["status"] == "ok"
    assert result["data"]["window"] == "7d"
    assert result["data"]["checked"] == 0
    assert "sqlite unavailable" not in json.dumps(result)


@pytest.mark.asyncio
async def test_summary_is_whitelisted_and_matches_hand_computed_values(
    seeded_store,
) -> None:
    """响应只有白名单字段，且计数与比率等于手算值。"""

    result = await _make_api(seeded_store).get_memory_dedup_metrics_payload(
        {"window": "24h"}
    )

    assert result["status"] == "ok"
    data = result["data"]
    assert set(data) == EXPECTED_KEYS
    assert {
        field: data[field]
        for field in (
            "window",
            "by_mode",
            "trend",
            *DEDUP_METRIC_OUTCOMES,
        )
    } == {
        "window": "24h",
        **_counts(
            checked=10,
            hit=7,
            merged=3,
            fact_mismatch=1,
            fact_overlap=3,
            conflict=1,
            failed=1,
        ),
        "by_mode": _by_mode(
            observe=_counts(
                checked=4,
                hit=3,
                fact_mismatch=1,
                fact_overlap=2,
            ),
            enforce=_counts(
                checked=6,
                hit=4,
                merged=3,
                fact_overlap=1,
                conflict=1,
                failed=1,
            ),
        ),
        "trend": [
            _counts(
                bucket_ms=NOW_MS // HOUR_MS * HOUR_MS,
                checked=10,
                hit=7,
                merged=3,
                fact_mismatch=1,
                fact_overlap=3,
                conflict=1,
                failed=1,
            )
        ],
    }
    assert data["hit_rate"] == pytest.approx(0.7)
    assert data["guard_rate"] == pytest.approx(0.1)
    assert data["overlap_rate"] == pytest.approx(0.3)
    assert data["failure_rate"] == pytest.approx(0.2)
    serialized = json.dumps(data)
    for marker in _SENSITIVE_MARKERS:
        assert marker not in serialized


@pytest.mark.asyncio
async def test_closed_store_degrades_to_zero_contract(seeded_store) -> None:
    """已关闭的 Store 读取失败时同样回落零值契约。"""

    await seeded_store.close()

    result = await _make_api(seeded_store).get_memory_dedup_metrics_payload(
        {"window": "24h"}
    )

    assert result["status"] == "ok"
    assert result["data"]["checked"] == 0


def test_route_registered_as_read_only_get() -> None:
    """路由必须是 GET 只读，并带审计元数据。"""

    api = _make_page_api(None)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in api.plugin.context.register_web_api.call_args_list
    }
    path = f"{PAGE_API_PREFIX}/memory-dedup/metrics"
    assert (path, ("GET",)) in registered

    metadata = {item["path"]: item for item in api.get_route_metadata()}
    route = metadata[path]
    assert route["risk"] == "read"
    assert route["methods"] == ["GET"]
