"""近重复指标 Store 的聚合、清理与节流契约。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio

from core.features.memory.infrastructure.dedup_metrics_store import (
    CLEANUP_WRITE_INTERVAL,
    DAY_MS,
    DEDUP_METRIC_OUTCOMES,
    HOUR_MS,
    DedupMetricsStore,
)

# 对齐小时桶的固定基准时刻（UTC epoch ms）。
NOW_MS = 1_699_999_200_000
_OLDER_BUCKET_MS = NOW_MS - 2 * HOUR_MS
_EXPIRED_BUCKET_MS = NOW_MS - 25 * HOUR_MS


@pytest_asyncio.fixture
async def store_factory(
    tmp_path,
) -> AsyncIterator[Callable[..., DedupMetricsStore]]:
    """创建使用固定墙钟的 Store，并在用例结束后统一关闭。"""

    created: list[DedupMetricsStore] = []

    def factory(**kwargs) -> DedupMetricsStore:
        kwargs.setdefault("clock", lambda: NOW_MS / 1000.0)
        store = DedupMetricsStore(
            str(tmp_path / "dedup_metrics.sqlite3"),
            **kwargs,
        )
        created.append(store)
        return store

    yield factory
    for store in created:
        await store.close()


async def _seed_window(store: DedupMetricsStore) -> None:
    """写入手算期望值对应的事件序列。"""

    for _ in range(4):
        await store.record("observe", "checked", now_ms=NOW_MS)
    for _ in range(3):
        await store.record("observe", "hit", now_ms=NOW_MS)
    await store.record("observe", "fact_mismatch", now_ms=NOW_MS)
    for _ in range(6):
        await store.record("enforce", "checked", now_ms=NOW_MS)
    for _ in range(4):
        await store.record("enforce", "hit", now_ms=NOW_MS)
    for _ in range(3):
        await store.record("enforce", "merged", now_ms=NOW_MS)
    await store.record("enforce", "conflict", now_ms=NOW_MS)
    await store.record("enforce", "failed", now_ms=NOW_MS)
    await store.record("observe", "checked", now_ms=_OLDER_BUCKET_MS)
    await store.record("observe", "checked", now_ms=_EXPIRED_BUCKET_MS)


@pytest.mark.asyncio
async def test_summary_matches_hand_computed_totals_and_rates(store_factory) -> None:
    """24h 合计、分模式计数与三个比率必须等于手算值。"""

    store = store_factory()
    await store.initialize()
    await _seed_window(store)

    summary = await store.summary("24h", now_ms=NOW_MS)

    assert summary["window"] == "24h"
    assert {outcome: summary[outcome] for outcome in DEDUP_METRIC_OUTCOMES} == {
        "checked": 11,
        "hit": 7,
        "merged": 3,
        "fact_mismatch": 1,
        "conflict": 1,
        "failed": 1,
    }
    assert summary["hit_rate"] == pytest.approx(7 / 11)
    assert summary["guard_rate"] == pytest.approx(1 / 11)
    assert summary["failure_rate"] == pytest.approx(2 / 11)
    assert summary["by_mode"] == {
        "observe": {
            "checked": 5,
            "hit": 3,
            "merged": 0,
            "fact_mismatch": 1,
            "conflict": 0,
            "failed": 0,
        },
        "enforce": {
            "checked": 6,
            "hit": 4,
            "merged": 3,
            "fact_mismatch": 0,
            "conflict": 1,
            "failed": 1,
        },
    }
    assert summary["trend"] == [
        {
            "bucket_ms": _OLDER_BUCKET_MS,
            "checked": 1,
            "hit": 0,
            "merged": 0,
            "fact_mismatch": 0,
            "conflict": 0,
            "failed": 0,
        },
        {
            "bucket_ms": NOW_MS,
            "checked": 10,
            "hit": 7,
            "merged": 3,
            "fact_mismatch": 1,
            "conflict": 1,
            "failed": 1,
        },
    ]


@pytest.mark.asyncio
async def test_window_boundaries_exclude_out_of_window_buckets(store_factory) -> None:
    """1h 只看当前桶；30d 纳入 24h 窗口外的桶。"""

    store = store_factory()
    await store.initialize()
    await _seed_window(store)

    hourly = await store.summary("1h", now_ms=NOW_MS)
    monthly = await store.summary("30d", now_ms=NOW_MS)

    assert hourly["checked"] == 10
    assert monthly["checked"] == 12
    # 25 小时前的桶仍属 30 天保留期，只是被 24h 窗口排除。
    assert monthly["trend"][0]["bucket_ms"] == _EXPIRED_BUCKET_MS


@pytest.mark.asyncio
async def test_empty_window_returns_zero_value_contract(store_factory) -> None:
    """空窗口的所有计数为零、比率为 0.0、趋势为空。"""

    store = store_factory()
    await store.initialize()

    summary = await store.summary("24h", now_ms=NOW_MS)

    assert summary == DedupMetricsStore.empty_summary("24h")
    assert summary["hit_rate"] == 0.0
    assert summary["guard_rate"] == 0.0
    assert summary["failure_rate"] == 0.0
    assert summary["trend"] == []
    assert summary["by_mode"]["observe"]["checked"] == 0
    assert summary["by_mode"]["enforce"]["checked"] == 0


@pytest.mark.asyncio
async def test_cleanup_drops_expired_buckets_and_keeps_boundary(store_factory) -> None:
    """保留期外的桶被删除，边界桶与更晚的桶不变。"""

    store = store_factory(retention_days=2)
    await store.initialize()
    await store.record("observe", "checked", now_ms=NOW_MS - 3 * DAY_MS)
    await store.record("enforce", "merged", now_ms=NOW_MS - 2 * DAY_MS)
    await store.record("enforce", "merged", now_ms=NOW_MS)

    deleted = await store.cleanup(now_ms=NOW_MS)

    assert deleted == 1
    assert await store.row_count() == 2
    summary = await store.summary("30d", now_ms=NOW_MS)
    assert summary["checked"] == 0
    assert summary["merged"] == 2


@pytest.mark.asyncio
async def test_cleanup_rejects_invalid_retention_days(tmp_path, store_factory) -> None:
    """保留期只接受 1..3650 的整数。"""

    for invalid in (0, 3651, True):
        with pytest.raises(ValueError):
            DedupMetricsStore(str(tmp_path / "invalid.sqlite3"), retention_days=invalid)

    store = store_factory()
    await store.initialize()
    with pytest.raises(ValueError):
        await store.cleanup(0, now_ms=NOW_MS)
    with pytest.raises(ValueError):
        await store.cleanup(retention_days=30, now_ms=-1)


@pytest.mark.asyncio
async def test_write_throttle_cleans_expired_buckets_after_interval(
    store_factory,
) -> None:
    """写入节流：间隔未满不清账，超过一小时后的下一次写入才删除过期桶。"""

    store = store_factory(retention_days=2)
    await store.initialize()
    await store.record("observe", "checked", now_ms=NOW_MS - 3 * DAY_MS)
    assert await store.row_count() == 1

    await store.record("observe", "hit", now_ms=NOW_MS + 30 * 60_000)
    assert await store.row_count() == 2
    assert (await store.summary("30d", now_ms=NOW_MS))["checked"] == 1

    await store.record("observe", "hit", now_ms=NOW_MS + HOUR_MS)
    assert (await store.summary("30d", now_ms=NOW_MS + HOUR_MS))["checked"] == 0
    assert (await store.summary("30d", now_ms=NOW_MS + HOUR_MS))["hit"] == 2


@pytest.mark.asyncio
async def test_write_throttle_cleans_after_bounded_writes(store_factory) -> None:
    """写入次数达到节流阈值时也会清账，不依赖时间间隔。"""

    store = store_factory(retention_days=2)
    await store.initialize()
    await store.record("observe", "checked", now_ms=NOW_MS - 3 * DAY_MS)
    assert await store.row_count() == 1

    for _ in range(CLEANUP_WRITE_INTERVAL - 2):
        await store.record("observe", "hit", now_ms=NOW_MS)
    assert await store.row_count() == 2
    assert (await store.summary("30d", now_ms=NOW_MS))["checked"] == 1

    # 第 CLEANUP_WRITE_INTERVAL 次写入触发清账：过期 checked 桶消失，
    # 其余命中桶仍在。
    await store.record("observe", "hit", now_ms=NOW_MS)
    assert await store.row_count() == 1
    summary = await store.summary("30d", now_ms=NOW_MS)
    assert summary["checked"] == 0
    assert summary["hit"] == CLEANUP_WRITE_INTERVAL - 1


@pytest.mark.asyncio
async def test_concurrent_upserts_accumulate_every_event(store_factory) -> None:
    """并发写入同一桶不会丢计数，UPSERT 逐条累加。"""

    store = store_factory()
    await store.initialize()

    await asyncio.gather(
        *[store.record("observe", "checked", now_ms=NOW_MS) for _ in range(50)],
        *[store.record("enforce", "merged", now_ms=NOW_MS) for _ in range(7)],
    )

    summary = await store.summary("1h", now_ms=NOW_MS)
    assert summary["checked"] == 50
    assert summary["merged"] == 7
    assert await store.row_count() == 2


@pytest.mark.asyncio
async def test_record_rejects_unknown_enums_without_writing(store_factory) -> None:
    """枚举闭集之外的写入被拒绝，且不产生任何行。"""

    store = store_factory()
    await store.initialize()

    assert await store.record("observe", "bogus", now_ms=NOW_MS) is False
    assert await store.record("off", "checked", now_ms=NOW_MS) is False
    assert await store.record("observe", "checked", now_ms=-1) is False
    assert await store.row_count() == 0


@pytest.mark.asyncio
async def test_summary_rejects_unknown_window(store_factory) -> None:
    """未知窗口必须抛出稳定 ValueError，而不是静默返回空摘要。"""

    store = store_factory()
    await store.initialize()

    with pytest.raises(ValueError, match="window"):
        await store.summary("12h", now_ms=NOW_MS)
