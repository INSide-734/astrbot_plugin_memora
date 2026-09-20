"""canonical 近重复合并协调器的 metadata、失败语义与指标记录契约。

单文件覆盖同一协调器的全部行为契约，共享 `_Engine`/`_Search`/`_document` 夹具；
物理行数已越过 AGENTS.md 的测试拆分评审线（700 行），仍低于 800 行硬上限。
下一次改动前必须先执行拆分：把「记录端口/Store 落库」用例移到
`tests/test_canonical_merge_metrics.py`，并把上述共享夹具抽到测试辅助模块。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import pytest

from core.features.memory.application.canonical_merge import (
    DEDUP_REASON_DETECTOR_FAILED,
    DEDUP_REASON_FACT_MISMATCH,
    DEDUP_REASON_FACT_OVERLAP,
    DEDUP_REASON_MERGE_CONFLICT,
    DEDUP_REASON_MERGED,
    DEDUP_REASON_OBSERVED,
    MAX_MERGED_IDEMPOTENCY_KEYS,
    MAX_SOURCE_EVIDENCE,
    MAX_TOPICS,
    CanonicalMergeCoordinator,
    DedupMetricsRecorder,
    MergeCandidate,
    MergeStatus,
)
from core.features.memory.domain.memory_dedup_config import MemoryDedupConfig
from core.features.memory.domain.revision import memory_revision
from core.features.quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
)
from tests.fact_evidence_helpers import fact_evidence, source_evidence

_OWNER_ID = 100
_CONTENT = "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
_NEAR_DUPLICATE = _CONTENT + "已"
# 与 _CONTENT 整段相似度 0.10：用于构造「无整段命中但事实共享」的观测场景。
_PARTIAL_OVERLAP = "团队改用 PostgreSQL 保存日志快照，每天审阅两次文档并归档历史指标"


class _Engine:
    """按 revision 乐观校验应用 metadata 更新的 canonical 替身。"""

    def __init__(self, document: dict[str, Any]) -> None:
        """保存初始 canonical 记录。"""

        self.document = document
        self.updates: list[dict[str, Any]] = []
        self.revision_clock = 10.0

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """返回记录副本。"""

        if memory_id != _OWNER_ID:
            return None
        return dict(self.document)

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None,
    ) -> bool:
        """按 expected_revision 校验并原地应用增量。"""

        if memory_id != _OWNER_ID:
            return False
        if expected_revision != memory_revision(self.document):
            return False
        self.updates.append(updates)
        metadata = dict(self.document["metadata"])
        metadata.update(updates.get("metadata", {}))
        if "importance" in updates:
            metadata["importance"] = updates["importance"]
        self.revision_clock += 1.0
        self.document["metadata"] = metadata
        self.document["updated_at"] = self.revision_clock
        return True


class _Search:
    """记录查询并返回固定候选顺序。"""

    def __init__(self, documents: list[DedupDocument] | None = None) -> None:
        """保存候选与可选异常。"""

        self.documents = list(documents or [])
        self.failure: BaseException | None = None
        self.queries: list[DedupQuery] = []

    async def __call__(self, query: DedupQuery) -> list[DedupDocument]:
        """先让出事件循环，再返回候选或抛出注入异常。"""

        await asyncio.sleep(0)
        self.queries.append(query)
        if self.failure is not None:
            raise self.failure
        return list(self.documents)


def _document(
    *,
    content: str = _CONTENT,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造落库 canonical 记录。"""

    base: dict[str, Any] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "session_id": "session-1",
        "persona_id": None,
        "participant_ids": ["user-1", "user-2"],
        "key_facts": ["项目使用 SQLite 存储会话记录", "每周五发布一次版本"],
        "status": "active",
        "importance": 0.4,
    }
    base.update(metadata or {})
    base.setdefault("fact_source_evidence", fact_evidence(base["key_facts"]))
    return {
        "id": _OWNER_ID,
        "text": content,
        "metadata": base,
        "created_at": 1.0,
        "updated_at": 5.0,
    }


def _stored(document: dict[str, Any]) -> DedupDocument:
    """把落库记录投影为检测器候选。"""

    return DedupDocument(_OWNER_ID, document["text"], document["metadata"])


def _candidate(
    *,
    content: str = _NEAR_DUPLICATE,
    metadata: dict[str, Any] | None = None,
    importance: float = 0.95,
    idempotency_key: str = "reflection-key-1",
) -> MergeCandidate:
    """构造待合并的反思候选。"""

    base: dict[str, Any] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "key_facts": ["项目使用 SQLite 存储会话记录"],
        "source_refs": [{"message_index": 1, "start": 0, "end": 3}],
        "source_evidence": [{"message_index": 1, "role": "user"}],
        "topics": ["发布流程"],
    }
    base.update(metadata or {})
    base.setdefault("fact_source_evidence", fact_evidence(base["key_facts"]))
    return MergeCandidate(
        content=content,
        metadata=base,
        importance=importance,
        session_id="session-1",
        persona_id=None,
        idempotency_key=idempotency_key,
    )


class _Recorder:
    """收集 ``(mode, outcome)`` 记录的手算替身。"""

    def __init__(self, *, failure: BaseException | None = None) -> None:
        """保存注入异常与调用序列。"""

        self.calls: list[tuple[str, str]] = []
        self.failure = failure

    async def __call__(self, mode: str, outcome: str) -> None:
        """记录一次调用；注入异常时先记录再抛出。"""

        self.calls.append((mode, outcome))
        if self.failure is not None:
            raise self.failure


def _coordinator(
    engine: _Engine,
    search: _Search,
    *,
    mode: Literal["off", "observe", "enforce"] = "enforce",
    recorder: DedupMetricsRecorder | _Recorder | None = None,
    clock=lambda: 1234.0,
) -> CanonicalMergeCoordinator:
    """装配使用替身端口的协调器。"""

    return CanonicalMergeCoordinator(
        config_provider=lambda: MemoryDedupConfig(mode=mode),
        search_similar=search,
        load_memory=engine.get_memory,
        update_memory=engine.update_memory,
        metrics_recorder=recorder,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_enforce_merges_metadata_without_rewriting_content() -> None:
    """命中后 importance 只升、并集去重、计数递增，正文保持不变。"""

    document = _document(
        metadata={
            "importance": 0.9,
            "source_refs": [{"message_index": 1, "start": 0, "end": 3}],
            "source_evidence": [{"message_index": 1, "role": "user"}],
            "topics": ["发布流程", "回归测试"],
            "merge_count": 2,
        }
    )
    engine = _Engine(document)
    search = _Search([_stored(document)])

    outcome = await _coordinator(engine, search).merge(_candidate(importance=0.95))

    assert outcome.status is MergeStatus.MERGED
    assert outcome.merged is True
    assert outcome.memory_id == _OWNER_ID
    assert outcome.reason_code == DEDUP_REASON_MERGED
    assert outcome.score >= 0.85
    metadata = engine.document["metadata"]
    assert metadata["importance"] == pytest.approx(0.95)
    assert metadata["merge_count"] == 3
    assert metadata["last_merged_at"] == 1234.0
    assert metadata["source_refs"] == [
        {"message_index": 1, "start": 0, "end": 3},
    ]
    assert metadata["source_evidence"] == [{"message_index": 1, "role": "user"}]
    assert metadata["topics"] == ["发布流程", "回归测试"]
    assert metadata["merged_idempotency_keys"] == ["reflection-key-1"]
    assert engine.document["text"] == _CONTENT
    assert "content" not in engine.updates[0]


@pytest.mark.asyncio
async def test_importance_never_decreases() -> None:
    """低重要性候选不得降低既有 canonical 的重要性。"""

    document = _document(metadata={"importance": 0.95})
    engine = _Engine(document)

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate(importance=0.4)
    )

    assert outcome.status is MergeStatus.MERGED
    assert engine.document["metadata"]["importance"] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_union_deduplicates_and_caps_fact_evidence() -> None:
    """Each fact keeps its own bounded, deduplicated relocatable evidence."""
    first, second = "项目使用 SQLite 存储会话记录", "每周五发布一次版本"
    group = [
        source_evidence(first, message_id=index + 1)[0]
        for index in range(MAX_SOURCE_EVIDENCE)
    ]
    second_group = source_evidence(second, message_id=90)
    document = _document(
        metadata={
            "key_facts": [first, second],
            "fact_source_evidence": [group, second_group],
            "source_evidence": source_evidence(first),
            "topics": [f"主题-{index}" for index in range(MAX_TOPICS)],
        }
    )
    engine = _Engine(document)
    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate(
            metadata={
                "key_facts": [first, second],
                "fact_source_evidence": [
                    [group[0], source_evidence(first, message_id=99)[0]],
                    second_group,
                ],
                "source_evidence": source_evidence(second),
                "topics": ["主题-0", "主题-9"],
            }
        )
    )
    assert outcome.merged
    metadata = engine.document["metadata"]
    assert metadata["fact_source_evidence"] == [group, second_group]
    assert metadata["source_evidence"] == source_evidence(first)
    assert metadata["topics"] == [f"主题-{index}" for index in range(MAX_TOPICS)]


@pytest.mark.asyncio
async def test_merge_pairs_evidence_by_normalized_fact_not_position():
    first, second = "项目使用 SQLite 存储会话记录", "每周五发布一次版本"
    original = [
        source_evidence(first, message_id=10),
        source_evidence(second, message_id=20),
    ]
    addition = source_evidence(second, message_id=30)
    document = _document(metadata={"fact_source_evidence": original})
    engine = _Engine(document)
    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate(
            metadata={
                "key_facts": [f" {second} ", first],
                "fact_source_evidence": [addition, original[0]],
            }
        )
    )
    assert outcome.merged
    groups = engine.document["metadata"]["fact_source_evidence"]
    assert groups[0] == original[0]
    assert {ref["message_id"] for ref in groups[1]} == {20, 30}


@pytest.mark.asyncio
async def test_merge_refuses_legacy_fact_evidence():
    document = _document(metadata={"fact_source_evidence": []})
    engine = _Engine(document)
    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate()
    )
    assert outcome.status is MergeStatus.CONFLICT
    assert engine.updates == []


@pytest.mark.asyncio
async def test_merged_keys_keep_newest_entries() -> None:
    """幂等键上限被占满时保留最新键，保证重放可以短路。"""

    document = _document(
        metadata={"merged_idempotency_keys": [f"old-{index}" for index in range(20)]}
    )
    engine = _Engine(document)

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate(idempotency_key="replay-key")
    )

    assert outcome.status is MergeStatus.MERGED
    keys = engine.document["metadata"]["merged_idempotency_keys"]
    assert len(keys) == MAX_MERGED_IDEMPOTENCY_KEYS
    assert keys[-1] == "replay-key"


@pytest.mark.asyncio
async def test_replay_does_not_merge_twice() -> None:
    """同一候选重放不得重复提升 importance 或计数。"""

    document = _document(metadata={"importance": 0.5})
    engine = _Engine(document)
    search = _Search([_stored(document)])
    coordinator = _coordinator(engine, search)
    candidate = _candidate(idempotency_key="replay-key")

    first = await coordinator.merge(candidate)
    second = await coordinator.merge(candidate)

    assert first.status is MergeStatus.MERGED
    assert second.status is MergeStatus.MERGED
    assert len(engine.updates) == 1
    assert engine.document["metadata"]["merge_count"] == 1
    assert engine.document["metadata"]["merged_idempotency_keys"] == ["replay-key"]


@pytest.mark.asyncio
async def test_observe_records_hit_without_write_back() -> None:
    """observe 模式只观测命中，不写回既有 canonical。"""

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])

    outcome = await _coordinator(engine, search, mode="observe").merge(_candidate())

    assert outcome.status is MergeStatus.OBSERVED
    assert outcome.merged is False
    assert outcome.reason_code == DEDUP_REASON_OBSERVED
    assert engine.updates == []
    assert engine.document["metadata"].get("merge_count") is None


@pytest.mark.asyncio
async def test_off_mode_skips_detection_entirely() -> None:
    """关闭模式不得发起近邻查询或写回。"""

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])

    outcome = await _coordinator(engine, search, mode="off").merge(_candidate())

    assert outcome.status is MergeStatus.MISS
    assert search.queries == []
    assert engine.updates == []


@pytest.mark.asyncio
async def test_fact_mismatch_does_not_write_back() -> None:
    """事实护栏命中时不得合并，只返回护栏 reason code。"""

    document = _document(
        metadata={"key_facts": ["数据库每周日凌晨三点做全量冷备份"]},
        content=_CONTENT,
    )
    engine = _Engine(document)

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate()
    )

    assert outcome.status is MergeStatus.FACT_MISMATCH
    assert outcome.reason_code == DEDUP_REASON_FACT_MISMATCH
    assert engine.updates == []


@pytest.mark.asyncio
async def test_cross_scope_candidates_never_merge() -> None:
    """跨 scope 候选必须回落到普通写入。"""

    document = _document(metadata={"scope_key": "group:group-2:topic-a"})
    engine = _Engine(document)

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate()
    )

    assert outcome.status is MergeStatus.MISS
    assert engine.updates == []


@pytest.mark.asyncio
async def test_detector_failure_falls_open() -> None:
    """检测端口异常必须 fail-open 并记录降级 reason code。"""

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])
    search.failure = RuntimeError("fts unavailable")

    outcome = await _coordinator(engine, search).merge(_candidate())

    assert outcome.status is MergeStatus.FAILED
    assert outcome.merged is False
    assert outcome.reason_code == DEDUP_REASON_DETECTOR_FAILED
    assert engine.updates == []


@pytest.mark.asyncio
async def test_update_failure_without_commit_is_a_conflict() -> None:
    """CAS 未生效时必须返回冲突终态，由调用方回落普通写入。"""

    document = _document()
    engine = _Engine(document)
    engine.update_memory = _rejecting_update  # type: ignore[method-assign]

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate()
    )

    assert outcome.status is MergeStatus.CONFLICT
    assert outcome.reason_code == DEDUP_REASON_MERGE_CONFLICT
    assert engine.document["metadata"].get("merge_count") is None


@pytest.mark.asyncio
async def test_update_exception_falls_open() -> None:
    """写回抛异常时必须 fail-open，不得让候选写入失败。"""

    document = _document()
    engine = _Engine(document)
    engine.update_memory = _raising_update  # type: ignore[method-assign]

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate()
    )

    assert outcome.status in {MergeStatus.CONFLICT, MergeStatus.FAILED}
    assert outcome.merged is False
    assert outcome.reason_code == DEDUP_REASON_MERGE_CONFLICT


@pytest.mark.asyncio
async def test_committed_update_with_derived_failure_still_counts_as_merged() -> None:
    """写回已提交但派生重建失败时不得再插入第二条 canonical。"""

    document = _document()
    engine = _Engine(document)

    async def _commit_then_fail(
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None,
    ) -> bool:
        """先按 CAS 提交，再模拟派生重建失败的 False 返回。"""

        await _Engine.update_memory(engine, memory_id, updates, expected_revision)
        return False

    engine.update_memory = _commit_then_fail  # type: ignore[method-assign]

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate()
    )

    assert outcome.status is MergeStatus.MERGED
    assert engine.document["metadata"]["merge_count"] == 1


@pytest.mark.asyncio
async def test_rewritten_owner_is_not_merged() -> None:
    """检测后正文被改写的 canonical 不得沿用旧相似度判定。"""

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])
    engine.document["text"] = "完全不同的正文，记录着另一件毫不相干的运维事项"

    outcome = await _coordinator(engine, search).merge(_candidate())

    assert outcome.status is MergeStatus.CONFLICT
    assert outcome.reason_code == DEDUP_REASON_MERGE_CONFLICT
    assert engine.updates == []


@pytest.mark.asyncio
async def test_concurrent_same_scope_merges_serialize() -> None:
    """同 scope 并发候选必须串行化，两个候选都能落在同一 owner 上。"""

    document = _document(metadata={"importance": 0.3})
    engine = _Engine(document)
    search = _Search([_stored(document)])
    coordinator = _coordinator(engine, search)

    first, second = await asyncio.gather(
        coordinator.merge(_candidate(idempotency_key="key-1")),
        coordinator.merge(_candidate(idempotency_key="key-2")),
    )

    assert {first.status, second.status} == {MergeStatus.MERGED}
    metadata = engine.document["metadata"]
    assert metadata["merge_count"] == 2
    assert metadata["merged_idempotency_keys"] == ["key-1", "key-2"]


@pytest.mark.asyncio
async def test_enforce_records_checked_hit_and_merged() -> None:
    """enforce 成功合并按 checked → hit → merged 顺序记录。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine, _Search([_stored(document)]), recorder=recorder
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MERGED
    assert recorder.calls == [
        ("enforce", "checked"),
        ("enforce", "hit"),
        ("enforce", "merged"),
    ]


@pytest.mark.asyncio
async def test_observe_records_checked_and_hit_without_write_back() -> None:
    """observe 命中记录 checked 与 hit，但不记录 merged。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine, _Search([_stored(document)]), mode="observe", recorder=recorder
    ).merge(_candidate())

    assert outcome.status is MergeStatus.OBSERVED
    assert recorder.calls == [("observe", "checked"), ("observe", "hit")]


@pytest.mark.asyncio
async def test_miss_records_checked_only() -> None:
    """未命中只记录 checked（命中率分母包含未命中候选）。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(engine, _Search([]), recorder=recorder).merge(
        _candidate()
    )

    assert outcome.status is MergeStatus.MISS
    assert recorder.calls == [("enforce", "checked")]


@pytest.mark.asyncio
async def test_fact_mismatch_records_guard_outcome() -> None:
    """事实护栏拦截记录 checked 与 fact_mismatch，不计命中。"""

    document = _document(
        metadata={"key_facts": ["数据库每周日凌晨三点做全量冷备份"]},
        content=_CONTENT,
    )
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine, _Search([_stored(document)]), recorder=recorder
    ).merge(_candidate())

    assert outcome.status is MergeStatus.FACT_MISMATCH
    assert recorder.calls == [("enforce", "checked"), ("enforce", "fact_mismatch")]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["observe", "enforce"])
async def test_fact_overlap_records_overlap_outcome_without_write_back(
    mode: Literal["observe", "enforce"],
) -> None:
    """事实重叠只观测：记录 checked + fact_overlap，任何模式都不写回。"""

    document = _document(content=_PARTIAL_OVERLAP)
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine, _Search([_stored(document)]), mode=mode, recorder=recorder
    ).merge(_candidate())

    assert outcome.status is MergeStatus.OBSERVED
    assert outcome.merged is False
    assert outcome.reason_code == DEDUP_REASON_FACT_OVERLAP
    assert outcome.memory_id == _OWNER_ID
    assert recorder.calls == [(mode, "checked"), (mode, "fact_overlap")]
    assert engine.updates == []


@pytest.mark.asyncio
async def test_detector_failure_records_failed_outcome() -> None:
    """检测异常记录 checked 与 failed，不影响回落结论。"""

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])
    search.failure = RuntimeError("fts unavailable")
    recorder = _Recorder()

    outcome = await _coordinator(engine, search, recorder=recorder).merge(_candidate())

    assert outcome.status is MergeStatus.FAILED
    assert recorder.calls == [("enforce", "checked"), ("enforce", "failed")]


@pytest.mark.asyncio
async def test_conflict_records_conflict_outcome() -> None:
    """检测后目标被改写的冲突记录 checked → hit → conflict。"""

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])
    engine.document["text"] = "完全不同的正文，记录着另一件毫不相干的运维事项"
    recorder = _Recorder()

    outcome = await _coordinator(engine, search, recorder=recorder).merge(_candidate())

    assert outcome.status is MergeStatus.CONFLICT
    assert recorder.calls == [
        ("enforce", "checked"),
        ("enforce", "hit"),
        ("enforce", "conflict"),
    ]


@pytest.mark.asyncio
async def test_missing_scope_records_nothing() -> None:
    """无法构造比较作用域时没有进入检测，因此不产生任何记录。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine, _Search([_stored(document)]), recorder=recorder
    ).merge(_candidate(metadata={"scope_key": None}))

    assert outcome.status is MergeStatus.MISS
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_recorder_failure_does_not_break_merge() -> None:
    """记录端口抛异常时必须吞掉，合并仍然完成。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder(failure=RuntimeError("metrics store unavailable"))

    outcome = await _coordinator(
        engine, _Search([_stored(document)]), recorder=recorder
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MERGED
    assert engine.document["metadata"]["merge_count"] == 1
    assert recorder.calls == [
        ("enforce", "checked"),
        ("enforce", "hit"),
        ("enforce", "merged"),
    ]


@pytest.mark.asyncio
async def test_off_mode_writes_no_metric_rows(tmp_path) -> None:
    """``mode=off`` 不查询近邻，也不产生任何指标行。"""

    from core.features.memory.infrastructure.dedup_metrics_store import (
        DedupMetricsStore,
    )

    document = _document()
    engine = _Engine(document)
    search = _Search([_stored(document)])
    store = DedupMetricsStore(str(tmp_path / "dedup_metrics.sqlite3"))
    await store.initialize()
    try:
        outcome = await _coordinator(
            engine, search, mode="off", recorder=store.record
        ).merge(_candidate())

        assert outcome.status is MergeStatus.MISS
        assert search.queries == []
        assert await store.row_count() == 0
        assert (await store.summary("24h"))["checked"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_observe_hit_persists_checked_and_hit_rows(tmp_path) -> None:
    """记录端口接到真实 Store 时，observe 命中落一行 checked 与一行 hit。"""

    from core.features.memory.infrastructure.dedup_metrics_store import (
        DedupMetricsStore,
    )

    document = _document()
    engine = _Engine(document)
    store = DedupMetricsStore(str(tmp_path / "dedup_metrics.sqlite3"))
    await store.initialize()
    try:
        outcome = await _coordinator(
            engine,
            _Search([_stored(document)]),
            mode="observe",
            recorder=store.record,
        ).merge(_candidate())

        assert outcome.status is MergeStatus.OBSERVED
        assert await store.row_count() == 2
        summary = await store.summary("24h")
        assert summary["checked"] == 1
        assert summary["hit"] == 1
        assert summary["merged"] == 0
        assert summary["by_mode"]["observe"] == {
            "checked": 1,
            "hit": 1,
            "merged": 0,
            "fact_mismatch": 0,
            "fact_overlap": 0,
            "conflict": 0,
            "failed": 0,
            "semantic_checked": 0,
            "semantic_hit": 0,
            "semantic_failed": 0,
            "semantic_unavailable": 0,
            "semantic_budget_exhausted": 0,
        }
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_fact_overlap_persists_overlap_rows(tmp_path) -> None:
    """记录端口接到真实 Store 时，事实重叠落 checked + fact_overlap 两行。"""

    from core.features.memory.infrastructure.dedup_metrics_store import (
        DedupMetricsStore,
    )

    document = _document(content=_PARTIAL_OVERLAP)
    engine = _Engine(document)
    store = DedupMetricsStore(str(tmp_path / "dedup_metrics.sqlite3"))
    await store.initialize()
    try:
        outcome = await _coordinator(
            engine,
            _Search([_stored(document)]),
            mode="observe",
            recorder=store.record,
        ).merge(_candidate())

        assert outcome.status is MergeStatus.OBSERVED
        assert await store.row_count() == 2
        summary = await store.summary("24h")
        assert summary["checked"] == 1
        assert summary["fact_overlap"] == 1
        assert summary["overlap_rate"] == 1.0
        assert summary["hit"] == 0
        assert summary["merged"] == 0
        assert summary["by_mode"]["observe"]["fact_overlap"] == 1
    finally:
        await store.close()


async def _rejecting_update(
    memory_id: int,
    updates: dict[str, Any],
    expected_revision: str | None,
) -> bool:
    """模拟 revision 冲突的写回失败。"""

    return False


async def _raising_update(
    memory_id: int,
    updates: dict[str, Any],
    expected_revision: str | None,
) -> bool:
    """模拟写回端口异常。"""

    raise RuntimeError("update unavailable")
