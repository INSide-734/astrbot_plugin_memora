"""跨窗口近重复检测器的确定性契约。"""

from __future__ import annotations

from typing import Any

import pytest

from core.features.quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
    NearDuplicateVerdict,
    candidate_scope,
    detect_near_duplicate,
    same_dedup_scope,
    stored_scope,
)

_CONTENT = "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
_NEAR_DUPLICATE = _CONTENT + "已"
_PARTIAL_OVERLAP = "团队改用 PostgreSQL 保存日志快照，每天审阅两次文档并归档历史指标"


class _Search:
    """记录近邻查询并按固定顺序返回候选。"""

    def __init__(self, documents: list[DedupDocument]) -> None:
        """保存候选副本。"""

        self.documents = list(documents)
        self.queries: list[DedupQuery] = []

    async def __call__(self, query: DedupQuery) -> list[DedupDocument]:
        """记录查询并返回候选副本。"""

        self.queries.append(query)
        return list(self.documents)


def _metadata(**overrides: Any) -> dict[str, Any]:
    """构造同 scope 的群聊候选 metadata。"""

    metadata: dict[str, Any] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "session_id": "session-1",
        "persona_id": None,
        "participant_ids": ["user-1", "user-2"],
        "key_facts": ["项目使用 SQLite 存储会话记录", "每周五发布一次版本"],
        "status": "active",
    }
    metadata.update(overrides)
    return metadata


def _document(
    memory_id: int = 7,
    *,
    content: str = _CONTENT,
    metadata: dict[str, Any] | None = None,
) -> DedupDocument:
    """构造既有 canonical 投影。"""

    return DedupDocument(
        memory_id=memory_id,
        content=content,
        metadata=_metadata(**(metadata or {})),
    )


async def _detect(
    documents: list[DedupDocument],
    *,
    content: str = _CONTENT,
    metadata: dict[str, Any] | None = None,
    similarity_threshold: float = 0.85,
    candidate_limit: int = 5,
    min_tokens: int = 12,
):
    """在固定候选上执行一次检测。"""

    search = _Search(documents)
    outcome = await detect_near_duplicate(
        content=content,
        metadata=metadata or _metadata(),
        session_id="session-1",
        persona_id=None,
        search_similar=search,
        similarity_threshold=similarity_threshold,
        candidate_limit=candidate_limit,
        min_tokens=min_tokens,
    )
    return outcome, search


@pytest.mark.asyncio
async def test_near_duplicate_content_hits_existing_canonical() -> None:
    """同 scope 近重复正文必须命中既有 canonical 并给出分数。"""

    outcome, search = await _detect([_document(11, content=_NEAR_DUPLICATE)])

    assert outcome.verdict is NearDuplicateVerdict.HIT
    assert outcome.memory_id == 11
    assert outcome.score >= 0.85
    assert search.queries == [DedupQuery(_CONTENT, "session-1", 5)]


@pytest.mark.asyncio
async def test_identical_content_scores_one() -> None:
    """逐字相同的正文必须得到满分。"""

    outcome, _ = await _detect([_document(3)])

    assert outcome.verdict is NearDuplicateVerdict.HIT
    assert outcome.score == 1.0


@pytest.mark.asyncio
async def test_partial_overlap_below_threshold_misses() -> None:
    """部分重合但低于阈值的候选不得判定为近重复。"""

    outcome, _ = await _detect([_document(5, content=_PARTIAL_OVERLAP)])

    assert outcome.verdict is NearDuplicateVerdict.MISS
    assert outcome.memory_id is None


@pytest.mark.asyncio
async def test_short_candidate_skips_search() -> None:
    """候选自身低于 min_tokens 时不得发起近邻查询。"""

    outcome, search = await _detect([_document()], content="太短")

    assert outcome.verdict is NearDuplicateVerdict.MISS
    assert search.queries == []


@pytest.mark.asyncio
async def test_short_stored_canonical_is_not_compared() -> None:
    """既有 canonical 低于 min_tokens 时不得进入打分。"""

    outcome, _ = await _detect([_document(9, content="短")])

    assert outcome.verdict is NearDuplicateVerdict.MISS


@pytest.mark.asyncio
async def test_fact_mismatch_blocks_merge() -> None:
    """同主题不同事实（facts Jaccard < 0.5）必须返回事实护栏终态。"""

    outcome, _ = await _detect(
        [
            _document(
                13,
                content=_NEAR_DUPLICATE,
                metadata={
                    "key_facts": [
                        "数据库每周日凌晨三点做全量冷备份",
                        "备份密钥轮换由运维执行",
                    ]
                },
            )
        ]
    )

    assert outcome.verdict is NearDuplicateVerdict.FACT_MISMATCH
    assert outcome.memory_id == 13
    assert outcome.score >= 0.85


@pytest.mark.asyncio
async def test_missing_facts_on_either_side_blocks_merge() -> None:
    """任一侧没有事实 token 时按事实不匹配处理（保守护栏）。"""

    outcome, _ = await _detect(
        [_document(17, content=_NEAR_DUPLICATE, metadata={"key_facts": []})]
    )

    assert outcome.verdict is NearDuplicateVerdict.FACT_MISMATCH


@pytest.mark.asyncio
async def test_inactive_and_invalid_canonicals_are_skipped() -> None:
    """非活跃、ID 非法或 metadata 缺失 scope 的候选不得参与比较。"""

    outcome, _ = await _detect(
        [
            _document(1, content=_NEAR_DUPLICATE, metadata={"status": "deleted"}),
            DedupDocument(2, _NEAR_DUPLICATE, {"scope_key": "group:group-1:topic-a"}),
            DedupDocument(0, _NEAR_DUPLICATE, _metadata()),
        ]
    )

    assert outcome.verdict is NearDuplicateVerdict.MISS
    assert outcome.memory_id is None


@pytest.mark.asyncio
async def test_highest_score_wins_and_ties_prefer_earliest_id() -> None:
    """命中取最高分；同分时固定选择最早的 canonical。"""

    outcome, _ = await _detect(
        [
            _document(20, content=_NEAR_DUPLICATE),
            _document(12, content=_CONTENT),
            _document(30, content=_CONTENT),
        ]
    )

    assert outcome.verdict is NearDuplicateVerdict.HIT
    assert outcome.memory_id == 12
    assert outcome.score == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored_overrides",
    [
        {"scope_key": "group:group-2:topic-a"},
        {"privacy_level": "shared"},
        {"session_id": "session-2"},
        {"persona_id": "persona-1"},
        {"chat_type": "private"},
    ],
)
async def test_cross_scope_candidates_are_not_compared(
    stored_overrides: dict[str, Any],
) -> None:
    """scope/privacy/会话/人格/聊天类型不同的候选绝不参与比较。"""

    outcome, _ = await _detect(
        [_document(41, content=_CONTENT, metadata=stored_overrides)]
    )

    assert outcome.verdict is NearDuplicateVerdict.MISS
    assert outcome.memory_id is None


@pytest.mark.asyncio
async def test_confidential_scope_requires_participant_overlap() -> None:
    """机密/私聊记忆额外要求主体交集非空。"""

    candidate_metadata = _metadata(
        scope_key="private:person-1:topic-a",
        privacy_level="confidential",
        chat_type="private",
        participant_ids=["user-1"],
    )
    disjoint, _ = await _detect(
        [
            _document(
                51,
                content=_CONTENT,
                metadata={
                    "scope_key": "private:person-1:topic-a",
                    "privacy_level": "confidential",
                    "chat_type": "private",
                    "participant_ids": ["user-2"],
                },
            )
        ],
        metadata=candidate_metadata,
    )
    overlapping, _ = await _detect(
        [
            _document(
                52,
                content=_CONTENT,
                metadata={
                    "scope_key": "private:person-1:topic-a",
                    "privacy_level": "confidential",
                    "chat_type": "private",
                    "participant_ids": ["user-1", "user-9"],
                },
            )
        ],
        metadata=candidate_metadata,
    )

    assert disjoint.verdict is NearDuplicateVerdict.MISS
    assert overlapping.verdict is NearDuplicateVerdict.HIT
    assert overlapping.memory_id == 52


@pytest.mark.asyncio
async def test_missing_scope_snapshot_skips_comparison() -> None:
    """候选缺少可信 scope 快照时不得比较。"""

    outcome, search = await _detect(
        [_document()],
        metadata=_metadata(scope_key=None, privacy_level=None),
    )

    assert outcome.verdict is NearDuplicateVerdict.MISS
    assert search.queries == []


def test_candidate_scope_ignores_model_supplied_session_identity() -> None:
    """候选作用域的会话身份只采信调用方实参。"""

    scope = candidate_scope(
        _metadata(session_id="forged", persona_id="forged"),
        session_id="session-1",
        persona_id=None,
    )
    trusted = stored_scope(_metadata())
    forged = stored_scope(_metadata(session_id="forged", persona_id="forged"))

    assert scope is not None and trusted is not None and forged is not None
    assert scope.session_id == "session-1"
    assert scope.persona_id is None
    assert same_dedup_scope(scope, trusted) is True
    assert same_dedup_scope(scope, forged) is False


def test_invalid_participant_payload_rejects_scope() -> None:
    """参与者字段类型非法时作用域不可用。"""

    assert (
        candidate_scope(
            _metadata(participant_ids="user-1"),
            session_id="session-1",
            persona_id=None,
        )
        is None
    )
    assert (
        candidate_scope(
            _metadata(participant_ids=["user-1", ""]),
            session_id="session-1",
            persona_id=None,
        )
        is None
    )
