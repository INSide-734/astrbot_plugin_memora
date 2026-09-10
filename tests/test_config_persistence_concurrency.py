"""配置持久化、取消与并发写入契约测试。"""

from __future__ import annotations

import asyncio
import copy
import threading
from typing import Any

import pytest

from tests.config_contract_support import BlockingSavingConfig, SavingConfig


@pytest.mark.asyncio
async def test_apply_updates_source_and_saves_before_publishing() -> None:
    """配置发布前必须先更新来源并完成同步持久化。"""

    from core.platform.config import ConfigManager

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()
    event_loop_thread = threading.get_ident()

    result = await manager.apply_config_changes(
        {"recall_engine.top_k": 8},
        expected_revision=revision,
    )

    assert result.changed_paths == ("recall_engine.top_k",)
    assert source["recall_engine"]["top_k"] == 8
    assert source.saved_snapshots[-1]["recall_engine"]["top_k"] == 8
    assert source.save_thread_id != event_loop_thread
    assert manager.get_config_snapshot()[1] == result.revision


@pytest.mark.asyncio
async def test_apply_rolls_back_source_and_snapshot_when_save_fails() -> None:
    """同步保存失败时必须回滚来源映射与运行时快照。"""

    from core.platform.config import ConfigManager, ConfigPersistenceError

    source = SavingConfig({"recall_engine": {"top_k": 5}}, fail_save=True)
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    snapshot_before = manager.get_config_snapshot()

    with pytest.raises(ConfigPersistenceError):
        await manager.apply_config_changes(
            {"recall_engine.top_k": 9},
            expected_revision=snapshot_before[1],
        )

    assert dict(source) == source_before
    assert manager.get_config_snapshot() == snapshot_before


@pytest.mark.asyncio
async def test_failed_save_preserves_concurrent_external_source_change() -> None:
    """保存失败后的协调不得覆盖并发写入的外部来源值。"""

    from core.platform.config import ConfigManager, ConfigPersistenceError

    source = BlockingSavingConfig(
        {"recall_engine": {"top_k": 5}},
        fail_save=True,
    )
    manager = ConfigManager(source)
    _, original_revision = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 6},
            expected_revision=original_revision,
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    source["recall_engine"]["top_k"] = 9
    source.release_save.set()

    with pytest.raises(ConfigPersistenceError):
        await apply_task

    snapshot, reconciled_revision = await manager.get_config_snapshot_async()
    assert reconciled_revision != original_revision
    assert snapshot["recall_engine"]["top_k"] == 9
    assert source["recall_engine"]["top_k"] == 9


@pytest.mark.asyncio
async def test_cancelled_apply_publishes_successful_save_before_propagating() -> None:
    """取消到达时若保存已成功，应先发布新快照再传播取消。"""

    from core.platform.config import ConfigManager

    source = BlockingSavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, original_revision = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 9},
            expected_revision=original_revision,
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    apply_task.cancel()
    await asyncio.sleep(0)
    assert not apply_task.done()
    apply_task.cancel()
    await asyncio.sleep(0)
    assert not apply_task.done()
    source.release_save.set()
    assert await asyncio.to_thread(source.save_finished.wait, 2)

    with pytest.raises(asyncio.CancelledError):
        await apply_task

    snapshot, revision = manager.get_config_snapshot()
    assert source.saved_snapshots == [snapshot]
    assert dict(source) == snapshot
    assert snapshot["recall_engine"]["top_k"] == 9
    assert revision != original_revision


@pytest.mark.asyncio
async def test_cancelled_apply_rolls_back_after_failed_save() -> None:
    """取消期间保存失败时必须保持来源和快照均未发布。"""

    from core.platform.config import ConfigManager

    source = BlockingSavingConfig(
        {"recall_engine": {"top_k": 5}},
        fail_save=True,
    )
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    snapshot_before = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 9},
            expected_revision=snapshot_before[1],
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    apply_task.cancel()
    await asyncio.sleep(0)
    source.release_save.set()
    assert await asyncio.to_thread(source.save_finished.wait, 2)

    with pytest.raises(asyncio.CancelledError):
        await apply_task

    assert source.saved_snapshots == []
    assert dict(source) == source_before
    assert manager.get_config_snapshot() == snapshot_before


@pytest.mark.asyncio
async def test_concurrent_writes_with_same_revision_are_serialized() -> None:
    """同一 revision 的并发写入只能成功一次并报告一次冲突。"""

    from core.platform.config import (
        ConfigApplyResult,
        ConfigConflictError,
        ConfigManager,
    )

    source = BlockingSavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()

    async def write(value: int) -> ConfigApplyResult | ConfigConflictError:
        """提交单个候选值，并把 revision 冲突转换为可比较结果。"""

        try:
            return await manager.apply_config_changes(
                {"recall_engine.top_k": value},
                expected_revision=revision,
            )
        except ConfigConflictError as exc:
            return exc

    first_write = asyncio.create_task(write(6))
    assert await asyncio.to_thread(source.save_entered.wait, 2)
    second_write = asyncio.create_task(write(7))
    await asyncio.sleep(0)
    replacements_before_release = source.clear_calls
    source.release_save.set()
    outcomes = await asyncio.gather(first_write, second_write)

    assert replacements_before_release == 1
    assert sum(isinstance(item, ConfigApplyResult) for item in outcomes) == 1
    assert sum(isinstance(item, ConfigConflictError) for item in outcomes) == 1
    assert len(source.saved_snapshots) == 1
    assert source["recall_engine"]["top_k"] == 6
    assert manager.get("recall_engine.top_k") == 6


@pytest.mark.asyncio
async def test_persist_false_changes_only_runtime_snapshot() -> None:
    """关闭持久化时只更新隔离运行时快照，不改写来源。"""

    from core.platform.config import ConfigManager

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    _, revision = manager.get_config_snapshot()

    result = await manager.apply_config_changes(
        {"recall_engine.top_k": 9},
        expected_revision=revision,
        persist=False,
    )

    assert dict(source) == source_before
    assert source.saved_snapshots == []
    assert manager.get("recall_engine.top_k") == 9
    assert manager.get_config_snapshot()[1] == result.revision


@pytest.mark.asyncio
async def test_update_runtime_config_wraps_apply_contract() -> None:
    """兼容更新入口应复用正式事务并把校验失败映射为 False。"""

    from core.platform.config import ConfigManager

    source: dict[str, Any] = {"recall_engine": {"top_k": 5}}
    manager = ConfigManager(source)

    assert await manager.update_runtime_config(
        {"recall_engine.top_k": 10}, persist=True
    )
    assert source["recall_engine"]["top_k"] == 10
    assert not await manager.update_runtime_config(
        {"recall_engine.top_k": 51}, persist=True
    )
    assert manager.get("recall_engine.top_k") == 10
