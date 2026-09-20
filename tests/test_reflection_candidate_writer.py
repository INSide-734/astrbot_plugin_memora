"""reflection candidate writer 的应用契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.features.observability.infrastructure.metrics as monitoring_metrics
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
from tests.fact_evidence_helpers import fact_evidence


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

    owner_facts = ["项目使用 SQLite 存储会话记录", "每周五发布一次版本"]
    return {
        "id": 100,
        "text": _MERGE_CONTENT,
        "created_at": 1.0,
        "updated_at": 5.0,
        "metadata": {
            "scope_key": "group:group-1:topic-a",
            "privacy_level": "public",
            "resolver_revision": "revision-1",
            "chat_type": "group",
            "session_id": "session-1",
            "persona_id": None,
            "participant_ids": ["user-1", "user-2"],
            "key_facts": owner_facts,
            "fact_source_evidence": fact_evidence(owner_facts),
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
    run_claim_side_effect: Any | None = None,
    merged_idempotency_keys: dict[str, int] | None = None,
):
    """以固定 scope 调用一次候选写入。"""

    candidate_metadata: dict[str, Any] = {
        "idempotency_key": "reflection-key-1",
        "key_facts": ["项目使用 SQLite 存储会话记录"],
        "fact_source_evidence": fact_evidence(["项目使用 SQLite 存储会话记录"]),
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
        merged_idempotency_keys=merged_idempotency_keys,
        session_id="session-1",
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=True,
        memory_engine=memory_engine,
        memory_quality_gate=quality_gate,
        schedule_evolution_after_write=AsyncMock(),
        canonical_merge=canonical_merge,
        run_claim_side_effect=run_claim_side_effect,
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


@pytest.mark.asyncio
async def test_merged_key_replay_skips_write_and_merge() -> None:
    """重放已并入 owner 的幂等键必须直接返回 MERGED，不写回也不插入。"""

    engine = _MergeEngine(_owner_document())

    results = await _store_candidate(
        engine,
        canonical_merge=_coordinator(engine, _UnusedSearch(), mode="enforce"),
        merged_idempotency_keys={"reflection-key-1": 100},
    )

    assert results[0].outcome is ReflectionStoreOutcome.MERGED
    assert results[0].canonical_id == 100
    assert engine.add_calls == []
    assert engine.merge_updates == []


@pytest.mark.asyncio
async def test_fence_loss_after_merge_never_inserts_second_canonical() -> None:
    """合并已生效但 post-fence 失效时不得回落插入第二条 canonical。"""

    owner = _owner_document()
    engine = _MergeEngine(owner)
    search = _MergeSearch(
        [DedupDocument(owner["id"], owner["text"], owner["metadata"])]
    )

    async def _claim_lost_after_side_effect(operation: Any) -> Any:
        """执行副作用后模拟 claim 在 post-check 失效。"""

        await operation()
        raise RuntimeError("claim_lost")

    results = await _store_candidate(
        engine,
        canonical_merge=_coordinator(engine, search, mode="enforce"),
        run_claim_side_effect=_claim_lost_after_side_effect,
    )

    assert results[0].outcome is ReflectionStoreOutcome.FAILED
    assert engine.add_calls == []
    assert len(engine.merge_updates) == 1
    assert owner["metadata"]["merge_count"] == 1


class _FailingEngine:
    """canonical 写入固定失败的替身，并可返回固定既有 owner。"""

    def __init__(self, error: Exception, *, owner: int | None = None) -> None:
        """保存固定异常与可选既有 canonical owner。"""

        self.error = error
        self.owner = owner
        self.add_calls = 0
        self.continuity_tracker = None

    async def add_memory(self, **_payload: Any) -> int:
        """记录调用次数后抛出固定异常。"""

        self.add_calls += 1
        raise self.error

    async def find_memory_id_by_idempotency_key(self, _key: str) -> int | None:
        """返回固定既有 owner；``None`` 表示不存在。"""

        return self.owner


class _RecordingFailureCounter:
    """记录 stage 取值的写入失败计数器替身。"""

    def __init__(self) -> None:
        """初始化 stage 记录列表。"""

        self.stages: list[str] = []

    def labels(self, *, stage: str) -> "_RecordingFailureCounter":
        """记录标签取值并返回自身以便计数。"""

        self.stages.append(stage)
        return self

    def inc(self) -> None:
        """忽略计数增量。"""


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("claim_lost"), ("skipped_fenced", "claim_lost")),
        (RuntimeError("epoch_fenced"), ("skipped_fenced", "epoch_fenced")),
        (RuntimeError("generation_fenced"), ("skipped_fenced", "generation_fenced")),
        (
            RuntimeError("summary_source_fenced"),
            ("skipped_fenced", "summary_source_fenced"),
        ),
        (
            RuntimeError("summary_epoch_fenced"),
            ("skipped_fenced", "summary_epoch_fenced"),
        ),
        (
            ValueError("summary_scope_mismatch"),
            ("failed", "summary_scope_mismatch"),
        ),
        (
            RuntimeError("summary_source_activation_failed"),
            ("failed", "summary_source_activation_failed"),
        ),
        (
            RuntimeError("canonical_idempotency_mapping_invalid"),
            ("failed", "canonical_idempotency_mapping_invalid"),
        ),
        (
            RuntimeError("source_validation_unavailable"),
            ("failed", "source_validation_unavailable"),
        ),
        (
            RuntimeError("canonical_owner_invalid"),
            ("failed", "canonical_owner_invalid"),
        ),
        (RuntimeError("boom 正文片段"), ("failed", "canonical_write_failed")),
        (
            RuntimeError("summary_source_fenced: 用户原文"),
            ("failed", "canonical_write_failed"),
        ),
        (RuntimeError(""), ("failed", "canonical_write_failed")),
    ],
)
def test_classify_store_failure_only_accepts_stable_identifiers(
    error: BaseException, expected: tuple[str, str]
) -> None:
    """只把稳定标识符当作原因码，含正文或凭据的文本一律回落固定未知码。"""

    assert feature_writer.classify_store_failure(error) == expected


def test_classify_store_failure_rejects_non_code_text() -> None:
    """带换行或长于白名单上限的文本不得成为原因码。"""

    for message in (
        "canonical_write_failed\n",
        "a" * 80,
        "AValid_Looking_Code",
        " token=secret-canary",
    ):
        assert feature_writer.classify_store_failure(RuntimeError(message)) == (
            "failed",
            "canonical_write_failed",
        )


@pytest.mark.asyncio
async def test_claim_lost_after_side_effect_reports_fence_reason_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """claim 在副作用后失效：只记 WARN + 稳定原因码，终态保持既有 FAILED。"""

    engine = _MergeEngine(_owner_document())

    async def _claim_lost_runner(operation: Any) -> Any:
        """执行副作用后模拟 claim 失效。"""

        await operation()
        raise RuntimeError("claim_lost")

    with caplog.at_level("WARNING"):
        results = await _store_candidate(
            engine, run_claim_side_effect=_claim_lost_runner
        )

    assert results[0].outcome is ReflectionStoreOutcome.FAILED
    assert len(engine.add_calls) == 1
    assert [
        record.levelname
        for record in caplog.records
        if getattr(record, "reason_code", "") == "claim_lost"
    ] == ["WARNING"]
    assert not [
        record for record in caplog.records if "记忆写入失败" in record.getMessage()
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "owner"),
    [
        ("summary_source_fenced", None),
        ("summary_source_fenced", 770),
        # 质量门 epoch 校验抛出的 fence 同样属预期跳过，不得报 ERROR。
        ("summary_epoch_fenced", None),
    ],
)
async def test_fenced_failure_never_logs_error_and_counts_fenced(
    error_code: str,
    owner: int | None,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fence 失效属预期跳过：只记 WARN + 原因码并计入 candidate_fenced，不报 ERROR。"""

    recorder = _RecordingFailureCounter()
    monkeypatch.setattr(monitoring_metrics, "MEMORY_WRITE_FAILURES_TOTAL", recorder)
    engine = _FailingEngine(RuntimeError(error_code), owner=owner)

    with caplog.at_level("WARNING"):
        results = await _store_candidate(engine)

    assert results[0].outcome is (
        ReflectionStoreOutcome.SKIPPED_IDEMPOTENT
        if owner is not None
        else ReflectionStoreOutcome.FAILED
    )
    assert results[0].canonical_id == owner
    assert error_code in {
        getattr(record, "reason_code", "") for record in caplog.records
    }
    assert recorder.stages == ["candidate_fenced"]
    assert not [
        record for record in caplog.records if "记忆写入失败" in record.getMessage()
    ]


@pytest.mark.asyncio
async def test_unknown_store_failure_logs_error_reason_code_without_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未知失败必须记 ERROR + 原因码 + 异常类型，且不得回显异常文本。"""

    engine = _FailingEngine(RuntimeError("boom 正文片段"))

    with caplog.at_level("ERROR"):
        results = await _store_candidate(engine)

    assert results[0].outcome is ReflectionStoreOutcome.FAILED
    failures = [
        record for record in caplog.records if "记忆写入失败" in record.getMessage()
    ]
    assert [record.levelname for record in failures] == ["ERROR"]
    assert getattr(failures[0], "reason_code", "") == "canonical_write_failed"
    assert "reason_code=canonical_write_failed" in failures[0].getMessage()
    assert "异常类型=RuntimeError" in failures[0].getMessage()
    for record in caplog.records:
        assert "boom" not in record.getMessage()
        assert "正文片段" not in record.getMessage()


@pytest.mark.asyncio
async def test_store_failures_record_new_metric_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fence 跳过与真实失败分别以新增 stage 取值累加既有写入失败计数。"""

    recorder = _RecordingFailureCounter()
    monkeypatch.setattr(monitoring_metrics, "MEMORY_WRITE_FAILURES_TOTAL", recorder)

    await _store_candidate(_FailingEngine(RuntimeError("claim_lost")))
    await _store_candidate(_FailingEngine(RuntimeError("boom 正文片段")))

    assert recorder.stages == ["candidate_fenced", "candidate_write"]


@pytest.mark.asyncio
async def test_metric_recording_failure_keeps_store_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """观测通道异常必须 fail-open：不得升级为批量异常或丢失原有原因码归因。"""

    monkeypatch.setattr(
        monitoring_metrics,
        "MEMORY_WRITE_FAILURES_TOTAL",
        SimpleNamespace(labels=MagicMock(side_effect=RuntimeError("metrics-down"))),
    )
    engine = _FailingEngine(RuntimeError("summary_scope_mismatch"))

    with caplog.at_level("DEBUG"):
        results = await _store_candidate(engine)

    assert results[0].outcome is ReflectionStoreOutcome.FAILED
    assert engine.add_calls == 1
    assert [
        record.levelname
        for record in caplog.records
        if getattr(record, "reason_code", "") == "summary_scope_mismatch"
    ] == ["ERROR"]
    assert not [
        record for record in caplog.records if "批量写入异常" in record.getMessage()
    ]
    assert not [
        record
        for record in caplog.records
        if getattr(record, "reason_code", "") == "canonical_write_failed"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code", ["claim_lost", "epoch_fenced", "generation_fenced"]
)
async def test_fenced_claim_codes_never_reconcile_existing_owner(
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """claim/epoch/generation 失效不复核既有 owner：终态保持 FAILED，仍按 fence 计数。"""

    recorder = _RecordingFailureCounter()
    monkeypatch.setattr(monitoring_metrics, "MEMORY_WRITE_FAILURES_TOTAL", recorder)
    engine = _FailingEngine(RuntimeError(error_code), owner=770)

    results = await _store_candidate(engine)

    assert results[0].outcome is ReflectionStoreOutcome.FAILED
    assert results[0].canonical_id is None
    assert recorder.stages == ["candidate_fenced"]


@pytest.mark.asyncio
async def test_batch_level_failure_surfaces_stable_reason_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """逃逸到批量边界的候选失败仍须给出稳定原因码，且不写 canonical。"""

    engine = _MergeEngine(_owner_document())

    with caplog.at_level("ERROR"):
        results = await _store_candidate(
            engine, metadata={"scope_key": "scope-conflict"}
        )

    assert results[0].outcome is ReflectionStoreOutcome.FAILED
    assert engine.add_calls == []
    assert "scope_snapshot_conflict" in {
        getattr(record, "reason_code", "") for record in caplog.records
    }
