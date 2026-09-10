"""TopicCatalogReconcileScheduler 测试 — 周期收敛、跳过条件、生命周期。"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from core.features.memory.application.catalog_reconcile_scheduler import (
    CONFIG_INTERVAL_KEY,
    DEFAULT_INTERVAL_SECONDS,
    REPAIR_BATCH_LIMIT,
    TopicCatalogReconcileScheduler,
)


class _FakeConfigManager:
    """按点号路径返回配置值的替身，支持运行期热改。"""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        """返回配置叶当前值，未声明时返回默认值。"""
        return self.values.get(key, default)


@dataclass
class _FakeCatalogStore:
    """记录调用并提供可控状态与 dirty 队列的目录端口替身。"""

    status: str = "ready"
    active_generation: int | None = 3
    dirty_count: int = 1
    repair_error: Exception | None = None
    list_dirty_calls: list[dict[str, Any]] = field(default_factory=list)
    repair_calls: list[dict[str, Any]] = field(default_factory=list)
    repaired_results: list[int] = field(default_factory=list)

    async def get_state(self) -> dict[str, Any]:
        """返回可配置的目录状态标量。"""
        return {
            "status": self.status,
            "active_generation": self.active_generation,
        }

    async def list_dirty(
        self, *, states: tuple[str, ...], limit: int
    ) -> tuple[Any, ...]:
        """按剩余 dirty 数量返回非空或空探测结果。"""
        self.list_dirty_calls.append({"states": states, "limit": limit})
        if self.dirty_count <= 0:
            return ()
        return tuple(object() for _ in range(min(self.dirty_count, limit)))

    async def repair_pending(self, owner_token: str, *, limit: int) -> int:
        """消费一批 dirty 并记录调用参数。"""
        self.repair_calls.append({"owner_token": owner_token, "limit": limit})
        if self.repair_error is not None:
            raise self.repair_error
        repaired = min(self.dirty_count, limit)
        self.dirty_count -= repaired
        self.repaired_results.append(repaired)
        return repaired


def _make_scheduler(
    store: _FakeCatalogStore | None = None,
    config: _FakeConfigManager | None = None,
) -> TopicCatalogReconcileScheduler:
    """构造以替身为依赖的调度器实例。"""
    return TopicCatalogReconcileScheduler(
        catalog_store=store or _FakeCatalogStore(),
        config_manager=config or _FakeConfigManager(),
    )


@pytest.mark.asyncio
async def test_reconcile_once_consumes_dirty() -> None:
    """ready 且存在 dirty 时单轮应调用 repair_pending 领取修复。"""
    store = _FakeCatalogStore(dirty_count=3)
    scheduler = _make_scheduler(store)

    await scheduler._reconcile_once()

    assert len(store.repair_calls) == 1
    call = store.repair_calls[0]
    assert call["owner_token"] == scheduler.owner_token
    assert call["owner_token"].startswith("catalog-reconcile-")
    assert call["limit"] == REPAIR_BATCH_LIMIT
    assert store.dirty_count == 0


@pytest.mark.asyncio
async def test_reconcile_once_skips_when_not_ready() -> None:
    """catalog 非 ready 时不得调用 repair（归启动重建路径）。"""
    for status in ("degraded", "backfilling", "rebuilding"):
        store = _FakeCatalogStore(status=status)
        scheduler = _make_scheduler(store)

        await scheduler._reconcile_once()

        assert store.repair_calls == [], f"status={status} 不应消费"
        assert store.list_dirty_calls == []


@pytest.mark.asyncio
async def test_reconcile_once_skips_without_active_generation() -> None:
    """ready 但无 active_generation 时不得消费。"""
    store = _FakeCatalogStore(active_generation=None)
    scheduler = _make_scheduler(store)

    await scheduler._reconcile_once()

    assert store.repair_calls == []
    assert store.list_dirty_calls == []


@pytest.mark.asyncio
async def test_reconcile_once_no_dirty_returns_without_repair() -> None:
    """dirty 探测为空时应直接返回，不触发修复。"""
    store = _FakeCatalogStore(dirty_count=0)
    scheduler = _make_scheduler(store)

    await scheduler._reconcile_once()

    assert store.list_dirty_calls == [{"states": ("pending", "failed"), "limit": 1}]
    assert store.repair_calls == []


@pytest.mark.asyncio
async def test_reconcile_once_disabled_interval_consumes_nothing() -> None:
    """interval=0 时单轮应直接跳过，不读目录状态也不消费。"""
    store = _FakeCatalogStore(dirty_count=5)
    config = _FakeConfigManager({CONFIG_INTERVAL_KEY: 0})
    scheduler = _make_scheduler(store, config)

    await scheduler._reconcile_once()

    assert store.list_dirty_calls == []
    assert store.repair_calls == []


@pytest.mark.asyncio
async def test_interval_read_supports_hot_reload_and_fallback() -> None:
    """间隔每轮重读：改配置立即生效；非法值回退默认。"""
    config = _FakeConfigManager({CONFIG_INTERVAL_KEY: 120})
    scheduler = _make_scheduler(config=config)

    assert scheduler._read_interval_seconds() == 120

    config.values[CONFIG_INTERVAL_KEY] = 0
    assert scheduler._read_interval_seconds() == 0

    # 未声明叶子时回退默认值。
    del config.values[CONFIG_INTERVAL_KEY]
    assert scheduler._read_interval_seconds() == DEFAULT_INTERVAL_SECONDS

    # 非法值（字符串数字以外）不抛出，回退默认。
    config.values[CONFIG_INTERVAL_KEY] = "not-a-number"
    assert scheduler._read_interval_seconds() == DEFAULT_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    """二次 start 不应再创建新任务。"""
    scheduler = _make_scheduler(
        _FakeCatalogStore(),
        _FakeConfigManager({CONFIG_INTERVAL_KEY: 3600}),
    )
    try:
        await scheduler.start()
        first_task = scheduler._task
        assert first_task is not None

        await scheduler.start()

        assert scheduler._task is first_task
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_loop_survives_repair_failure(caplog) -> None:
    """repair 抛普通异常时记 warning 后循环继续，下一轮仍执行。"""
    store = _FakeCatalogStore(dirty_count=2)
    store.repair_error = RuntimeError("db locked")
    scheduler = _make_scheduler(store)

    with (
        caplog.at_level(logging.WARNING, logger="astrbot.test"),
        patch.object(scheduler, "_read_interval_seconds", return_value=0.01),
    ):
        await scheduler.start()
        # 等待至少两轮循环：第一轮异常后循环必须仍存活。
        await asyncio.sleep(0.1)
        await scheduler.stop()

    assert len(store.repair_calls) >= 2, "异常后下一轮仍应继续执行"
    assert scheduler._task is None
    assert any(
        "catalog_reconcile_round_failed" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_loop_consumes_dirty_periodically() -> None:
    """循环应按间隔周期消费直到排空后空转。"""
    store = _FakeCatalogStore(dirty_count=2)
    scheduler = _make_scheduler(store)

    with patch.object(scheduler, "_read_interval_seconds", return_value=0.01):
        await scheduler.start()
        try:
            # 两轮即可消费完（batch limit 覆盖 2 条）。
            await asyncio.sleep(0.15)
        finally:
            await scheduler.stop()

    assert sum(store.repaired_results) == 2
    assert store.dirty_count == 0
