"""canonical 近重复合并协调器的 metadata 与失败语义契约。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import pytest

from core.features.memory.application.canonical_merge import (
    DEDUP_REASON_DETECTOR_FAILED,
    DEDUP_REASON_FACT_MISMATCH,
    DEDUP_REASON_MERGE_CONFLICT,
    DEDUP_REASON_MERGED,
    DEDUP_REASON_OBSERVED,
    MAX_MERGED_IDEMPOTENCY_KEYS,
    MAX_SOURCE_EVIDENCE,
    MAX_SOURCE_REFS,
    MAX_TOPICS,
    CanonicalMergeCoordinator,
    MergeCandidate,
    MergeStatus,
)
from core.features.memory.domain.memory_dedup_config import MemoryDedupConfig
from core.features.memory.domain.revision import memory_revision
from core.features.quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
)

_OWNER_ID = 100
_CONTENT = "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
_NEAR_DUPLICATE = _CONTENT + "已"


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
    return MergeCandidate(
        content=content,
        metadata=base,
        importance=importance,
        session_id="session-1",
        persona_id=None,
        idempotency_key=idempotency_key,
    )


def _coordinator(
    engine: _Engine,
    search: _Search,
    *,
    mode: Literal["off", "observe", "enforce"] = "enforce",
    clock=lambda: 1234.0,
) -> CanonicalMergeCoordinator:
    """装配使用替身端口的协调器。"""

    return CanonicalMergeCoordinator(
        config_provider=lambda: MemoryDedupConfig(mode=mode),
        search_similar=search,
        load_memory=engine.get_memory,
        update_memory=engine.update_memory,
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
async def test_union_deduplicates_and_caps_payloads() -> None:
    """来源证据与主题并集去重，且各自遵守上限。"""

    document = _document(
        metadata={
            "source_refs": [
                {"message_index": index, "start": 0, "end": 3}
                for index in range(MAX_SOURCE_REFS)
            ],
            "source_evidence": [
                {"message_index": index, "role": "user"}
                for index in range(MAX_SOURCE_EVIDENCE)
            ],
            "topics": [f"主题-{index}" for index in range(MAX_TOPICS)],
        }
    )
    engine = _Engine(document)

    outcome = await _coordinator(engine, _Search([_stored(document)])).merge(
        _candidate(
            metadata={
                "source_refs": [{"message_index": 0, "start": 0, "end": 3}],
                "source_evidence": [{"message_index": 0, "role": "user"}],
                "topics": ["主题-0", "主题-9"],
            }
        )
    )

    assert outcome.status is MergeStatus.MERGED
    metadata = engine.document["metadata"]
    assert len(metadata["source_refs"]) == MAX_SOURCE_REFS
    assert len(metadata["source_evidence"]) == MAX_SOURCE_EVIDENCE
    assert len(metadata["topics"]) == MAX_TOPICS
    assert metadata["topics"] == [f"主题-{index}" for index in range(MAX_TOPICS)]


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
