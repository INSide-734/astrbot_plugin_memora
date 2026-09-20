"""协调器的可选语义分支：默认关闭、observe 只读、enforce 走既有 CAS。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Literal

import pytest

from core.features.memory.application.canonical_merge import (
    DEDUP_REASON_SEMANTIC_MERGED,
    DEDUP_REASON_SEMANTIC_OBSERVED,
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
from core.features.quality.application.semantic_duplicate_detector import (
    SemanticCandidate,
    SemanticQuery,
)
from tests.fact_evidence_helpers import fact_evidence, source_evidence

_OWNER_ID = 100
_OWNER_CONTENT = (
    "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
)
_OWNER_FACT = "项目使用 SQLite 存储会话记录"
# 与 owner 正文词面重合低（lexical MISS），但事实相同（语义命中场景）。
_PARAPHRASE = "会话数据保存在 SQLite 中，发版时间固定在周五，回归测试是发布前提"
_WINDOW_DIGEST = "window-digest-1"


class _Engine:
    """按 revision 乐观校验应用 metadata 更新的 canonical 替身。"""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.updates: list[dict[str, Any]] = []
        self.revision_clock = 10.0

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        if memory_id != _OWNER_ID:
            return None
        return dict(self.document)

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None,
    ) -> bool:
        await asyncio.sleep(0)
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


class _LexicalSearch:
    """记录查询并返回固定候选的 lexical 端口替身。"""

    def __init__(self, documents: list[DedupDocument] | None = None) -> None:
        self.documents = list(documents or [])
        self.queries: list[DedupQuery] = []

    async def __call__(self, query: DedupQuery) -> list[DedupDocument]:
        await asyncio.sleep(0)
        self.queries.append(query)
        return list(self.documents)


class _SemanticSearch:
    """记录查询并返回固定候选的语义端口替身。"""

    def __init__(
        self,
        candidates: list[SemanticCandidate] | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.candidates = list(candidates or [])
        self.failure = failure
        self.queries: list[SemanticQuery] = []

    async def __call__(self, query: SemanticQuery) -> list[SemanticCandidate]:
        await asyncio.sleep(0)
        self.queries.append(query)
        if self.failure is not None:
            raise self.failure
        return list(self.candidates)


class _Recorder:
    """收集 ``(mode, outcome)`` 记录的替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, mode: str, outcome: str) -> None:
        self.calls.append((mode, outcome))


def _owner_metadata(**overrides: Any) -> dict[str, Any]:
    """构造落库 owner metadata。"""

    base: dict[str, Any] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "session_id": "session-1",
        "persona_id": None,
        "participant_ids": ["user-1", "user-2"],
        "key_facts": [_OWNER_FACT],
        "status": "active",
        "importance": 0.4,
    }
    base.update(overrides)
    base.setdefault("fact_source_evidence", fact_evidence(base["key_facts"]))
    return base


def _document(**overrides: Any) -> dict[str, Any]:
    """构造落库 canonical 记录。"""

    return {
        "id": _OWNER_ID,
        "text": overrides.pop("text", _OWNER_CONTENT),
        "metadata": _owner_metadata(**overrides),
        "created_at": 1.0,
        "updated_at": 5.0,
    }


def _stored(document: Mapping[str, Any]) -> DedupDocument:
    """把落库记录投影为 lexical 候选。"""

    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    return DedupDocument(_OWNER_ID, str(document["text"]), metadata)


def _candidate(
    *,
    content: str = _PARAPHRASE,
    idempotency_key: str = "reflection-key-1",
    window_digest: str | None = _WINDOW_DIGEST,
    **metadata_overrides: Any,
) -> MergeCandidate:
    """构造反思候选；默认与 owner 共享事实且正文为改写。"""

    metadata: dict[str, Any] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "key_facts": [_OWNER_FACT],
        "source_refs": [{"message_index": 1, "start": 0, "end": 3}],
        "source_evidence": [{"message_index": 1, "role": "user"}],
        "topics": ["发布流程"],
    }
    metadata.update(metadata_overrides)
    metadata.setdefault("fact_source_evidence", fact_evidence(metadata["key_facts"]))
    if window_digest is not None:
        metadata["source_digest"] = window_digest
    return MergeCandidate(
        content=content,
        metadata=metadata,
        importance=0.95,
        session_id="session-1",
        persona_id=None,
        idempotency_key=idempotency_key,
    )


def _coordinator(
    engine: _Engine,
    lexical: _LexicalSearch,
    semantic: _SemanticSearch | None,
    *,
    mode: Literal["off", "observe", "enforce"] = "enforce",
    semantic_mode: Literal["off", "observe", "enforce"] = "observe",
    threshold: float = 0.9,
    recorder: DedupMetricsRecorder | _Recorder | None = None,
) -> CanonicalMergeCoordinator:
    """装配使用替身端口的协调器。"""

    return CanonicalMergeCoordinator(
        config_provider=lambda: MemoryDedupConfig(
            mode=mode,
            semantic_mode=semantic_mode,
            semantic_threshold=threshold,
        ),
        search_similar=lexical,
        load_memory=engine.get_memory,
        update_memory=engine.update_memory,
        metrics_recorder=recorder,
        semantic_search=semantic,
        clock=lambda: 1234.0,
    )


def _hit_candidate(score: float = 0.95) -> list[SemanticCandidate]:
    """返回单条阈值内语义候选。"""

    return [SemanticCandidate(_OWNER_ID, score)]


@pytest.mark.asyncio
async def test_semantic_off_and_mode_off_perform_zero_provider_calls() -> None:
    """语义默认关闭；lexical 模式 off 时连 lexical 端口都不调用。"""

    document = _document()
    engine = _Engine(document)
    lexical = _LexicalSearch()
    semantic = _SemanticSearch(_hit_candidate())

    off_outcome = await _coordinator(
        engine, lexical, semantic, semantic_mode="off"
    ).merge(_candidate())

    assert off_outcome.status is MergeStatus.MISS
    assert semantic.queries == []
    assert engine.updates == []
    assert len(lexical.queries) == 1

    mode_off_outcome = await _coordinator(
        engine, lexical, semantic, mode="off", semantic_mode="enforce"
    ).merge(_candidate())

    assert mode_off_outcome.status is MergeStatus.MISS
    assert len(lexical.queries) == 1
    assert semantic.queries == []


@pytest.mark.asyncio
async def test_lexical_hit_never_calls_semantic_port() -> None:
    """lexical 命中优先：语义端口不被调用。"""

    document = _document()
    engine = _Engine(document)
    lexical = _LexicalSearch([_stored(document)])
    semantic = _SemanticSearch(_hit_candidate())
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        lexical,
        semantic,
        mode="enforce",
        semantic_mode="enforce",
        recorder=recorder,
    ).merge(_candidate(content=_OWNER_CONTENT + "。"))

    assert outcome.status is MergeStatus.MERGED
    assert semantic.queries == []
    assert recorder.calls == [
        ("enforce", "checked"),
        ("enforce", "hit"),
        ("enforce", "merged"),
    ]


@pytest.mark.asyncio
async def test_semantic_observe_records_hit_without_mutation() -> None:
    """observe 模式记录 semantic_hit，但不回读写入 canonical。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        _SemanticSearch(_hit_candidate()),
        mode="observe",
        semantic_mode="observe",
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.OBSERVED
    assert outcome.merged is False
    assert outcome.memory_id == _OWNER_ID
    assert outcome.reason_code == DEDUP_REASON_SEMANTIC_OBSERVED
    assert engine.updates == []
    assert recorder.calls == [
        ("observe", "checked"),
        ("semantic_observe", "semantic_checked"),
        ("semantic_observe", "semantic_hit"),
    ]


@pytest.mark.asyncio
async def test_semantic_enforce_merges_qualified_hit() -> None:
    """enforce 模式在全部护栏通过后按既有 CAS 写回 owner metadata。"""

    document = _document()
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        _SemanticSearch(_hit_candidate()),
        mode="enforce",
        semantic_mode="enforce",
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MERGED
    assert outcome.reason_code == DEDUP_REASON_SEMANTIC_MERGED
    assert outcome.memory_id == _OWNER_ID
    assert engine.document["metadata"]["merge_count"] == 1
    assert engine.document["metadata"]["merged_idempotency_keys"] == [
        "reflection-key-1"
    ]
    assert engine.document["text"] == _OWNER_CONTENT
    assert recorder.calls == [
        ("enforce", "checked"),
        ("semantic_enforce", "semantic_checked"),
        ("semantic_enforce", "semantic_hit"),
        ("semantic_enforce", "merged"),
    ]


@pytest.mark.asyncio
async def test_semantic_unavailable_falls_back_to_lexical_miss() -> None:
    """端口缺失只记录 semantic_unavailable，终态仍是可回落的 MISS。"""

    engine = _Engine(_document())
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        None,
        mode="enforce",
        semantic_mode="enforce",
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MISS
    assert engine.updates == []
    assert recorder.calls == [
        ("enforce", "checked"),
        ("semantic_enforce", "semantic_unavailable"),
    ]


@pytest.mark.asyncio
async def test_semantic_provider_failure_is_fail_open() -> None:
    """provider 异常不阻断普通写入：终态 MISS、只记 semantic_failed。"""

    engine = _Engine(_document())
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        _SemanticSearch(failure=RuntimeError("provider down")),
        mode="enforce",
        semantic_mode="enforce",
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MISS
    assert outcome.merged is False
    assert engine.updates == []
    assert recorder.calls == [
        ("enforce", "checked"),
        ("semantic_enforce", "semantic_failed"),
    ]


@pytest.mark.asyncio
async def test_cancellation_propagates_from_semantic_port() -> None:
    """取消必须继续上抛，不得被 fail-open 吞掉。"""

    with pytest.raises(asyncio.CancelledError):
        await _coordinator(
            _Engine(_document()),
            _LexicalSearch(),
            _SemanticSearch(failure=asyncio.CancelledError()),
            mode="enforce",
            semantic_mode="enforce",
        ).merge(_candidate())


@pytest.mark.asyncio
async def test_semantic_hit_requires_user_fact_evidence() -> None:
    """owner 事实只有助手来源时不构成命中，也不写回。"""

    document = _document(
        fact_source_evidence=[[source_evidence(role="assistant")]],
    )
    engine = _Engine(document)
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        _SemanticSearch(_hit_candidate()),
        mode="enforce",
        semantic_mode="enforce",
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MISS
    assert engine.updates == []
    assert recorder.calls == [
        ("enforce", "checked"),
        ("semantic_enforce", "semantic_checked"),
    ]


@pytest.mark.asyncio
async def test_semantic_threshold_filters_candidates() -> None:
    """低于 semantic_threshold 的候选既不回读也不命中。"""

    engine = _Engine(_document())
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        _SemanticSearch(_hit_candidate(0.6)),
        mode="enforce",
        semantic_mode="enforce",
        threshold=0.9,
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.MISS
    assert recorder.calls == [
        ("enforce", "checked"),
        ("semantic_enforce", "semantic_checked"),
    ]


@pytest.mark.asyncio
async def test_semantic_requests_are_capped_per_window() -> None:
    """同一来源窗口最多 8 次语义查询，超出的候选记为预算耗尽。"""

    engine = _Engine(_document())
    semantic = _SemanticSearch(_hit_candidate())
    recorder = _Recorder()
    coordinator = _coordinator(
        engine,
        _LexicalSearch(),
        semantic,
        mode="enforce",
        semantic_mode="observe",
        recorder=recorder,
    )

    for index in range(9):
        await coordinator.merge(_candidate(idempotency_key=f"key-{index}"))

    assert len(semantic.queries) == 8
    assert recorder.calls.count(("semantic_observe", "semantic_budget_exhausted")) == 1
    assert recorder.calls.count(("semantic_observe", "semantic_checked")) == 8


@pytest.mark.asyncio
async def test_semantic_budget_isolated_across_windows() -> None:
    """不同来源窗口各自拥有独立预算。"""

    engine = _Engine(_document())
    semantic = _SemanticSearch(_hit_candidate())
    coordinator = _coordinator(
        engine,
        _LexicalSearch(),
        semantic,
        mode="enforce",
        semantic_mode="observe",
    )

    for index in range(9):
        await coordinator.merge(
            _candidate(
                idempotency_key=f"window-a-{index}",
                window_digest="window-a",
            )
        )
    first_window_calls = len(semantic.queries)
    await coordinator.merge(
        _candidate(idempotency_key="window-b-0", window_digest="window-b")
    )

    assert first_window_calls == 8
    assert len(semantic.queries) == 9


@pytest.mark.asyncio
async def test_semantic_cas_conflict_is_not_reported_as_merged() -> None:
    """owner 在检测后被改写：CAS 冲突回落普通写入，不谎报合并。"""

    document = _document()
    engine = _Engine(document)

    async def conflicting_update(
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None,
    ) -> bool:
        """模拟写回前 owner 已被并发改写。"""

        engine.document["updated_at"] = 99.0
        return False

    engine.update_memory = conflicting_update  # type: ignore[assignment]
    recorder = _Recorder()

    outcome = await _coordinator(
        engine,
        _LexicalSearch(),
        _SemanticSearch(_hit_candidate()),
        mode="enforce",
        semantic_mode="enforce",
        recorder=recorder,
    ).merge(_candidate())

    assert outcome.status is MergeStatus.CONFLICT
    assert outcome.merged is False
    assert recorder.calls[-1] == ("semantic_enforce", "conflict")
