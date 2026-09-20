"""scope 锁注册表的生命周期与并发契约。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.features.memory.application.canonical_merge import (
    CanonicalMergeCoordinator,
    MergeCandidate,
    MergeStatus,
)
from core.features.memory.application.scope_lock_registry import ScopeLockRegistry
from core.features.memory.domain.memory_dedup_config import MemoryDedupConfig
from core.features.memory.domain.revision import memory_revision
from core.features.quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
)
from tests.fact_evidence_helpers import fact_evidence

_OWNER_ID = 100
_FACTS = ["项目使用 SQLite 存储会话记录", "每周五发布一次版本"]
_CONTENT = "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"


class _Engine:
    """按 revision 乐观校验应用 metadata 更新的 canonical 替身。"""

    def __init__(self) -> None:
        self.document: dict[str, Any] = {
            "id": _OWNER_ID,
            "text": _CONTENT,
            "metadata": {
                "scope_key": "group:group-1:topic-a",
                "privacy_level": "public",
                "chat_type": "group",
                "session_id": "session-1",
                "persona_id": None,
                "key_facts": _FACTS,
                "fact_source_evidence": fact_evidence(_FACTS),
                "status": "active",
                "importance": 0.4,
            },
            "created_at": 1.0,
            "updated_at": 5.0,
        }
        self.revision_clock = 10.0

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        if memory_id != _OWNER_ID:
            return None
        return dict(self.document)

    async def update_memory(
        self, memory_id: int, updates: dict[str, Any], expected_revision: str | None
    ) -> bool:
        if memory_id != _OWNER_ID:
            return False
        if expected_revision != memory_revision(self.document):
            return False
        metadata = dict(self.document["metadata"])
        metadata.update(updates.get("metadata", {}))
        self.document["metadata"] = metadata
        self.revision_clock += 1.0
        self.document["updated_at"] = self.revision_clock
        return True


class _Search:
    """返回固定 canonical 投影，并可阻塞到外部放行。"""

    def __init__(self, engine: _Engine, gate: asyncio.Event | None = None) -> None:
        self.engine = engine
        self.gate = gate

    async def __call__(self, query: DedupQuery) -> list[DedupDocument]:
        if self.gate is not None:
            await self.gate.wait()
        metadata = self.engine.document["metadata"]
        return [DedupDocument(_OWNER_ID, _CONTENT, dict(metadata))]


def _coordinator(engine: _Engine, search: Any) -> CanonicalMergeCoordinator:
    """装配使用替身端口的协调器。"""

    return CanonicalMergeCoordinator(
        config_provider=lambda: MemoryDedupConfig(mode="enforce"),
        search_similar=search,
        load_memory=engine.get_memory,
        update_memory=engine.update_memory,
        clock=lambda: 777.0,
    )


def _candidate(key: str) -> MergeCandidate:
    """构造同 scope 的近重复候选。"""

    metadata: dict[str, Any] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "key_facts": _FACTS,
        "fact_source_evidence": fact_evidence(_FACTS),
        "source_refs": [{"message_index": 1, "start": 0, "end": 3}],
    }
    return MergeCandidate(
        content=_CONTENT,
        metadata=metadata,
        importance=0.9,
        session_id="session-1",
        persona_id=None,
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_registry_returns_to_empty_after_success() -> None:
    """成功持有后注册表必须回收空闲条目。"""

    registry = ScopeLockRegistry()
    async with registry.hold("scope-a"):
        assert registry.size == 1
    assert registry.size == 0


@pytest.mark.asyncio
async def test_registry_returns_to_empty_after_body_exception() -> None:
    """持有区间内抛异常也必须回收条目。"""

    registry = ScopeLockRegistry()
    with pytest.raises(RuntimeError):
        async with registry.hold("scope-a"):
            raise RuntimeError("boom")
    assert registry.size == 0
    async with registry.hold("scope-a"):
        assert registry.size == 1


@pytest.mark.asyncio
async def test_registry_keeps_entry_while_waiter_registered() -> None:
    """存在等待者时不得删除条目，等待者退出后才回收。"""

    registry = ScopeLockRegistry()
    release = asyncio.Event()
    waiter_registered = asyncio.Event()

    async def holder() -> None:
        async with registry.hold("scope-a"):
            await release.wait()

    async def waiter() -> None:
        waiter_registered.set()
        async with registry.hold("scope-a"):
            pass

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    waiter_task = asyncio.create_task(waiter())
    await waiter_registered.wait()
    await asyncio.sleep(0)
    assert registry.size == 1
    assert waiter_task.done() is False
    release.set()
    await asyncio.gather(holder_task, waiter_task)
    assert registry.size == 0


@pytest.mark.asyncio
async def test_registry_releases_reference_when_waiter_cancelled() -> None:
    """等待期取消必须归还引用，且不释放不属于自己的锁。"""

    registry = ScopeLockRegistry()
    release = asyncio.Event()

    async def holder() -> None:
        async with registry.hold("scope-a"):
            await release.wait()

    async def waiter() -> None:
        async with registry.hold("scope-a"):
            raise AssertionError("被取消的等待者不得进入持有区间")

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task
    assert registry.size == 1
    release.set()
    await holder_task
    assert registry.size == 0
    async with registry.hold("scope-a"):
        assert registry.size == 1


@pytest.mark.asyncio
async def test_same_scope_merges_serialize_and_reclaim_lock() -> None:
    """同 scope 并发合并必须串行且结束后回收锁条目。"""

    engine = _Engine()
    coordinator = _coordinator(engine, _Search(engine))

    results = await asyncio.gather(
        coordinator.merge(_candidate("key-1")),
        coordinator.merge(_candidate("key-2")),
    )

    assert {result.status for result in results} == {MergeStatus.MERGED}
    assert engine.document["metadata"]["merge_count"] == 2
    assert coordinator._locks.size == 0


@pytest.mark.asyncio
async def test_merge_cancellation_reclaims_lock() -> None:
    """合并等待期取消后注册表不得残留条目。"""

    engine = _Engine()
    gate = asyncio.Event()
    coordinator = _coordinator(engine, _Search(engine, gate=gate))

    task = asyncio.create_task(coordinator.merge(_candidate("key-1")))
    await asyncio.sleep(0)
    assert coordinator._locks.size == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert coordinator._locks.size == 0

    follow_up_engine = _Engine()
    follow_up = _coordinator(follow_up_engine, _Search(follow_up_engine))
    assert (await follow_up.merge(_candidate("key-2"))).status is MergeStatus.MERGED
    assert follow_up._locks.size == 0
