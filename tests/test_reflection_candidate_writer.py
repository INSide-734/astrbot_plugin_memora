"""reflection candidate writer 的应用契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from core.features.memory.application.canonical_merge import CanonicalMergeCoordinator
from core.features.memory.domain.memory_dedup_config import MemoryDedupConfig
from core.features.memory.domain.revision import memory_revision
from core.features.quality.application.memory_quality_gate import MemoryGateResult
from core.features.quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
)
from core.features.reflection.application import candidate_writer as feature_writer
from core.features.reflection.domain.storage_outcomes import ReflectionStoreOutcome


def test_idempotency_key_is_stable_and_does_not_expose_content() -> None:
    """相同窗口和规范正文必须得到稳定摘要，输出不得泄露正文。"""

    key = feature_writer.build_reflection_idempotency_key(
        session_id="session-1",
        start_index=2,
        end_index=8,
        batch_index=1,
        memory_index=3,
        content="secret-canary",
    )

    assert key == feature_writer.build_reflection_idempotency_key(
        session_id="session-1",
        start_index=2,
        end_index=8,
        batch_index=1,
        memory_index=3,
        content="  secret-canary  ",
    )
    assert key != feature_writer.build_reflection_idempotency_key(
        session_id="session-1",
        start_index=2,
        end_index=8,
        batch_index=1,
        memory_index=4,
        content="secret-canary",
    )
    assert len(key) == 64
    assert "secret-canary" not in key


@pytest.mark.asyncio
async def test_candidate_writer_propagates_canonical_write_cancellation() -> None:
    """canonical 写入取消必须穿过批量收集边界向上传播。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(side_effect=asyncio.CancelledError()),
        continuity_tracker=None,
    )

    with pytest.raises(asyncio.CancelledError):
        await feature_writer.store_reflection_candidates(
            [{"content": "memory", "importance": 0.8, "metadata": {}}],
            completed_idempotency_keys=set(),
            session_id="session-1",
            persona_id=None,
            start_index=0,
            end_index=2,
            is_group_chat=False,
            memory_engine=memory_engine,
            memory_quality_gate=None,
            schedule_evolution_after_write=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_discard_action_yields_discarded_outcome() -> None:
    """discard 处置不得写 canonical、不落隔离库，仅返回 DISCARDED 终态。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="discard", reason_codes=("r1",))
        )
    )

    results = await feature_writer.store_reflection_candidates(
        [{"content": "discard-me", "importance": 0.4, "metadata": {}}],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
    )

    assert len(results) == 1
    assert results[0].outcome is ReflectionStoreOutcome.DISCARDED
    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_write_action_writes_with_tagged_metadata() -> None:
    """mark_write 处置写入 canonical 一次，携带门禁标记并返回 MARK_WRITE 终态。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    fake_atoms = [{"text": "低置信原子", "type": "fact"}]
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="mark_write", atoms=fake_atoms)
        )
    )
    candidate = {
        "content": "low-confidence",
        "importance": 0.4,
        "metadata": {"gate_disposition": "mark_write"},
        "atoms": [],
    }

    schedule_evolution_after_write = AsyncMock()
    results = await feature_writer.store_reflection_candidates(
        [candidate],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id="persona-1",
        start_index=0,
        end_index=2,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=schedule_evolution_after_write,
    )

    memory_engine.add_memory.assert_awaited_once()
    add_kwargs = memory_engine.add_memory.await_args.kwargs
    assert add_kwargs["metadata"]["gate_disposition"] == "mark_write"
    assert add_kwargs["atoms"] == fake_atoms
    assert results[0].outcome is ReflectionStoreOutcome.MARK_WRITE
    schedule_evolution_after_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_write_without_gate_atoms_falls_back_to_candidate_atoms() -> None:
    """门禁未返回 atoms 时回退候选自带 atoms，不得以空列表覆盖。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="mark_write", atoms=None)
        )
    )
    candidate_atoms = [{"text": "候选自带原子"}]

    results = await feature_writer.store_reflection_candidates(
        [
            {
                "content": "low-confidence",
                "importance": 0.4,
                "metadata": {"gate_disposition": "mark_write"},
                "atoms": candidate_atoms,
            }
        ],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
    )

    memory_engine.add_memory.assert_awaited_once()
    assert memory_engine.add_memory.await_args.kwargs["atoms"] == candidate_atoms
    assert results[0].outcome is ReflectionStoreOutcome.MARK_WRITE


@pytest.mark.asyncio
async def test_group_id_and_chat_type_passed_to_gate() -> None:
    """store_reflection_candidates 必须把 group_id 与 chat_type 透传给质量门。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(
            return_value=MemoryGateResult(action="allow", reason_codes=())
        )
    )

    await feature_writer.store_reflection_candidates(
        [{"content": "memory", "importance": 0.8, "metadata": {}}],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id="persona-1",
        start_index=0,
        end_index=2,
        is_group_chat=True,
        group_id="group-1",
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
    )

    quality_gate.route_candidate.assert_awaited_once()
    gate_kwargs = quality_gate.route_candidate.await_args.kwargs
    assert gate_kwargs["group_id"] == "group-1"
    assert gate_kwargs["chat_type"] == "group"


@pytest.mark.asyncio
async def test_claimed_candidate_passes_complete_source_fence() -> None:
    """已领取的候选写入必须把完整 claim 来源交给 canonical 入口。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )

    await feature_writer.store_reflection_candidates(
        [{"content": "memory", "importance": 0.8, "metadata": {}}],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=False,
        session_epoch=1,
        source_digest="source-digest",
        worker_generation=2,
        job_id="job-1",
        claim_token="claim-token",
        memory_engine=memory_engine,
        memory_quality_gate=None,
        schedule_evolution_after_write=AsyncMock(),
    )

    source_fence = memory_engine.add_memory.await_args.kwargs["source_fence"]
    assert source_fence.job_id == "job-1"
    assert source_fence.session_id == "session-1"
    assert source_fence.session_epoch == 1
    assert source_fence.start_seq == 0
    assert source_fence.end_seq == 2
    assert source_fence.expected_count == 2
    assert source_fence.source_digest == "source-digest"
    assert source_fence.worker_generation == 2

    metadata = memory_engine.add_memory.await_args.kwargs["metadata"]
    assert metadata["source_start_seq"] == source_fence.start_seq == 0
    assert metadata["source_end_seq"] == source_fence.end_seq == 2
    assert metadata["source_digest"] == "source-digest"
    assert metadata["source_epoch"] == 1
    assert metadata["source_fence_generation"] == 2


@pytest.mark.asyncio
async def test_candidate_without_claim_fence_omits_source_seq_metadata() -> None:
    """未领取窗口的候选不得写入 claim 窗口边界，保持既有 metadata 契约。"""

    memory_engine = SimpleNamespace(
        add_memory=AsyncMock(return_value=11),
        continuity_tracker=None,
    )

    results = await feature_writer.store_reflection_candidates(
        [{"content": "memory", "importance": 0.8, "metadata": {}}],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=3,
        end_index=7,
        is_group_chat=False,
        memory_engine=memory_engine,
        memory_quality_gate=None,
        schedule_evolution_after_write=AsyncMock(),
    )

    assert results[0].outcome is ReflectionStoreOutcome.CANONICAL
    add_kwargs = memory_engine.add_memory.await_args.kwargs
    assert "source_fence" not in add_kwargs
    assert "source_start_seq" not in add_kwargs["metadata"]
    assert "source_end_seq" not in add_kwargs["metadata"]


_MERGE_CONTENT = (
    "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
)


class _MergeEngine:
    """实现近重复合并所需 canonical 端口的写入替身。"""

    def __init__(self, owner: dict[str, Any]) -> None:
        """保存既有 canonical 记录与计数器。"""

        self.owner = owner
        self.add_calls: list[dict[str, Any]] = []
        self.merge_updates: list[dict[str, Any]] = []
        self.revision_clock = 10.0
        self.continuity_tracker = None

    async def add_memory(self, **payload: Any) -> int:
        """记录一次 canonical 写入并返回新 ID。"""

        self.add_calls.append(payload)
        return 900 + len(self.add_calls)

    async def find_memory_id_by_idempotency_key(self, _key: str) -> int | None:
        """固定返回无既有 owner。"""

        return None

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """返回既有 canonical 的副本。"""

        if memory_id != self.owner["id"]:
            return None
        return dict(self.owner)

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None = None,
    ) -> bool:
        """按 revision 乐观校验并原地应用增量。"""

        if memory_id != self.owner["id"]:
            return False
        if expected_revision != memory_revision(self.owner):
            return False
        self.merge_updates.append(updates)
        metadata = dict(self.owner["metadata"])
        metadata.update(updates.get("metadata", {}))
        if "importance" in updates:
            metadata["importance"] = updates["importance"]
        self.owner["metadata"] = metadata
        self.revision_clock += 1.0
        self.owner["updated_at"] = self.revision_clock
        return True


class _MergeSearch:
    """按固定顺序返回既有 canonical 投影。"""

    def __init__(self, documents: list[DedupDocument]) -> None:
        """保存候选副本。"""

        self.documents = list(documents)
        self.queries: list[DedupQuery] = []

    async def __call__(self, query: DedupQuery) -> list[DedupDocument]:
        """记录查询并返回候选副本。"""

        self.queries.append(query)
        return list(self.documents)


class _UnusedSearch:
    """记录调用并在被触达时立即失败。"""

    def __init__(self) -> None:
        """初始化查询记录。"""

        self.queries: list[DedupQuery] = []

    async def __call__(self, query: DedupQuery) -> list[DedupDocument]:
        """记录查询后抛出断言失败。"""

        self.queries.append(query)
        raise AssertionError("关闭模式不应发起近重复检测")


def _owner_document() -> dict[str, Any]:
    """构造同 scope 的既有 canonical 记录。"""

    return {
        "id": 100,
        "text": _MERGE_CONTENT,
        "created_at": 1.0,
        "updated_at": 5.0,
        "metadata": {
            "scope_key": "group:group-1:topic-a",
            "privacy_level": "public",
            "chat_type": "group",
            "session_id": "session-1",
            "persona_id": None,
            "participant_ids": ["user-1", "user-2"],
            "key_facts": ["项目使用 SQLite 存储会话记录", "每周五发布一次版本"],
            "status": "active",
            "importance": 0.4,
            "merge_count": 0,
        },
    }


def _coordinator(
    engine: _MergeEngine,
    search: Any,
    *,
    mode: Literal["off", "observe", "enforce"],
) -> CanonicalMergeCoordinator:
    """装配使用替身端口的近重复合并协调器。"""

    return CanonicalMergeCoordinator(
        config_provider=lambda: MemoryDedupConfig(mode=mode),
        search_similar=search,
        load_memory=engine.get_memory,
        update_memory=engine.update_memory,
        clock=lambda: 777.0,
    )


async def _store_candidate(
    memory_engine: Any,
    *,
    canonical_merge: Any | None = None,
    content: str = _MERGE_CONTENT + "已",
    metadata: dict[str, Any] | None = None,
    quality_gate: Any | None = None,
):
    """以固定 scope 调用一次候选写入。"""

    candidate_metadata: dict[str, Any] = {
        "idempotency_key": "reflection-key-1",
        "key_facts": ["项目使用 SQLite 存储会话记录"],
        "participant_ids": ["user-1"],
        "source_refs": [{"message_index": 1, "start": 0, "end": 3}],
        "topics": ["发布流程"],
    }
    candidate_metadata.update(metadata or {})
    return await feature_writer.store_reflection_candidates(
        [
            {
                "content": content,
                "importance": 0.9,
                "metadata": candidate_metadata,
            }
        ],
        completed_idempotency_keys=set(),
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=True,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
        canonical_merge=canonical_merge,
        scope_key="group:group-1:topic-a",
        privacy_level="public",
        resolver_revision="revision-1",
        chat_type="group",
    )


@pytest.mark.asyncio
async def test_off_mode_writes_canonical_without_detection() -> None:
    """off 模式必须零行为变化：不查询近邻、不写回既有 canonical。"""

    owner = _owner_document()
    engine = _MergeEngine(owner)
    search = _UnusedSearch()

    results = await _store_candidate(
        engine,
        canonical_merge=_coordinator(engine, search, mode="off"),
    )

    assert results[0].outcome is ReflectionStoreOutcome.CANONICAL
    assert len(engine.add_calls) == 1
    assert engine.merge_updates == []
    assert search.queries == []
    assert owner["metadata"]["merge_count"] == 0


@pytest.mark.asyncio
async def test_observe_mode_reports_hit_without_write_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """observe 模式记录命中并照常写入候选，但不合并既有 canonical。"""

    owner = _owner_document()
    engine = _MergeEngine(owner)
    search = _MergeSearch([DedupDocument(100, owner["text"], owner["metadata"])])

    with caplog.at_level("INFO"):
        results = await _store_candidate(
            engine,
            canonical_merge=_coordinator(engine, search, mode="observe"),
        )

    assert results[0].outcome is ReflectionStoreOutcome.CANONICAL
    assert len(engine.add_calls) == 1
    assert engine.merge_updates == []
    assert len(search.queries) == 1
    assert "dedup_observed" in {
        getattr(record, "reason_code", "") for record in caplog.records
    }


@pytest.mark.asyncio
async def test_enforce_mode_merges_and_skips_canonical_insert() -> None:
    """enforce 命中必须强化既有 canonical 且不插入第二条。"""

    owner = _owner_document()
    engine = _MergeEngine(owner)
    search = _MergeSearch([DedupDocument(100, owner["text"], owner["metadata"])])

    results = await _store_candidate(
        engine,
        canonical_merge=_coordinator(engine, search, mode="enforce"),
    )

    assert results[0].outcome is ReflectionStoreOutcome.MERGED
    assert results[0].canonical_id == 100
    assert results[0].idempotency_key == "reflection-key-1"
    assert engine.add_calls == []
    assert owner["metadata"]["importance"] == pytest.approx(0.9)
    assert owner["metadata"]["merge_count"] == 1
    assert owner["metadata"]["last_merged_at"] == 777.0
    assert owner["metadata"]["merged_idempotency_keys"] == ["reflection-key-1"]
    assert owner["metadata"]["source_refs"] == [
        {"message_index": 1, "start": 0, "end": 3}
    ]
    assert owner["metadata"]["topics"] == ["发布流程"]
    assert owner["text"] == _MERGE_CONTENT


@pytest.mark.asyncio
async def test_enforce_replay_does_not_merge_twice() -> None:
    """同 key 重放不得重复合并，也不得插入第二条 canonical。"""

    owner = _owner_document()
    engine = _MergeEngine(owner)
    search = _MergeSearch([DedupDocument(100, owner["text"], owner["metadata"])])
    coordinator = _coordinator(engine, search, mode="enforce")

    first = await _store_candidate(engine, canonical_merge=coordinator)
    second = await _store_candidate(engine, canonical_merge=coordinator)

    assert first[0].outcome is ReflectionStoreOutcome.MERGED
    assert second[0].outcome is ReflectionStoreOutcome.MERGED
    assert engine.add_calls == []
    assert len(engine.merge_updates) == 1
    assert owner["metadata"]["merge_count"] == 1
    assert owner["metadata"]["importance"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_cross_scope_candidate_is_written_normally() -> None:
    """跨 scope 候选不得合并，仍走普通 canonical 写入。"""

    owner = _owner_document()
    owner["metadata"]["scope_key"] = "group:group-2:topic-a"
    engine = _MergeEngine(owner)
    search = _MergeSearch([DedupDocument(100, owner["text"], owner["metadata"])])

    results = await _store_candidate(
        engine,
        canonical_merge=_coordinator(engine, search, mode="enforce"),
    )

    assert results[0].outcome is ReflectionStoreOutcome.CANONICAL
    assert len(engine.add_calls) == 1
    assert engine.merge_updates == []


@pytest.mark.asyncio
async def test_mark_write_candidates_skip_dedup() -> None:
    """低置信 mark_write 候选不得强化既有可信 canonical。"""

    owner = _owner_document()
    engine = _MergeEngine(owner)
    search = _UnusedSearch()
    quality_gate = SimpleNamespace(
        route_candidate=AsyncMock(return_value=MemoryGateResult(action="mark_write"))
    )

    results = await _store_candidate(
        engine,
        canonical_merge=_coordinator(engine, search, mode="enforce"),
        quality_gate=quality_gate,
    )

    assert results[0].outcome is ReflectionStoreOutcome.MARK_WRITE
    assert len(engine.add_calls) == 1
    assert engine.merge_updates == []
    assert search.queries == []


@pytest.mark.asyncio
async def test_broken_merge_port_falls_back_to_canonical_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """合并端口异常必须 fail-open，候选仍写入且记录冲突 reason code。"""

    engine = _MergeEngine(_owner_document())
    broken = SimpleNamespace(merge=AsyncMock(side_effect=RuntimeError("boom")))

    with caplog.at_level("ERROR"):
        results = await _store_candidate(engine, canonical_merge=broken)

    assert results[0].outcome is ReflectionStoreOutcome.CANONICAL
    assert len(engine.add_calls) == 1
    assert "dedup_merge_conflict" in {
        getattr(record, "reason_code", "") for record in caplog.records
    }
