"""ContradictionDetector 的只读冲突候选测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.processors.contradiction_detector import (
    ContradictionDetector,
    _detect_semantic_contradiction,
    _jaccard,
    _tokenize,
)
from core.shared.contracts import MemorySourceRef

UTC = timezone.utc
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)


def source(
    memory_id: int,
    content: str,
    *,
    hours_ago: int,
    subject_key: str = "subject:a",
    revision: str | None = None,
) -> MemorySourceRef:
    """构造用于冲突预筛的 canonical source。"""

    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision or f"r-{memory_id}",
        scope_key="private:scope-a",
        privacy_level="confidential",
        occurred_at=NOW - timedelta(hours=hours_ago),
        content=content,
        topic_keys=("咖啡",),
        subject_key=subject_key,
    )


class TestTextHeuristics:
    """验证低成本词面预筛的稳定边界。"""

    def test_tokenize_chinese_and_english(self) -> None:
        """中英文文本都应产生可比较 token。"""

        assert _tokenize("我喜欢咖啡")
        assert _tokenize("I like coffee and 咖啡")
        assert _tokenize("") == []
        assert _tokenize("。。。") == []

    def test_jaccard_boundaries(self) -> None:
        """Jaccard 对相同、相离和空集合返回稳定值。"""

        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
        assert _jaccard({"a"}, {"b"}) == 0.0
        assert _jaccard(set(), {"a"}) == 0.0

    def test_semantic_polarity_prefilter(self) -> None:
        """一肯定一否定才通过冲突预筛。"""

        assert _detect_semantic_contradiction("我不喜欢咖啡", "我喜欢咖啡")
        assert not _detect_semantic_contradiction("我喜欢咖啡", "我也喜欢咖啡")
        assert not _detect_semantic_contradiction("我不喝咖啡", "我也不喝咖啡")


def test_detector_returns_stable_source_evidence_without_writes() -> None:
    """冲突检测只返回稳定 ID/revision/主体/时间证据，不修改 canonical。"""

    older = source(1, "我一直喜欢喝咖啡", hours_ago=1)
    newer = source(2, "我现在不再喜欢喝咖啡", hours_ago=0)
    detector = ContradictionDetector(jaccard_threshold=0.3)

    candidates = detector.detect_candidates([older, newer])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_id == 2
    assert candidate.source_revision == "r-2"
    assert candidate.target_id == 1
    assert candidate.target_revision == "r-1"
    assert candidate.subject_key == "subject:a"
    assert candidate.conflict_type == "polarity_conflict"
    assert candidate.source_occurred_at == newer.occurred_at
    assert candidate.target_occurred_at == older.occurred_at
    assert older.revision_token == "r-1"


def test_different_subjects_are_not_compared() -> None:
    """多主体文本相似也不能生成同一人的冲突候选。"""

    detector = ContradictionDetector(jaccard_threshold=0.3)
    candidates = detector.detect_candidates(
        [
            source(1, "我一直喜欢喝咖啡", hours_ago=1, subject_key="subject:a"),
            source(2, "我现在不再喜欢喝咖啡", hours_ago=0, subject_key="subject:b"),
        ]
    )

    assert candidates == ()


def test_historical_state_change_is_classified_as_update_not_conflict() -> None:
    """显式历史状态变化应进入 update 候选，不应伪装成同时矛盾。"""

    detector = ContradictionDetector(jaccard_threshold=0.3)
    candidates = detector.detect_candidates(
        [
            source(1, "以前我一直喜欢喝咖啡", hours_ago=72),
            source(2, "现在我不再喜欢喝咖啡", hours_ago=0),
        ]
    )

    assert len(candidates) == 1
    assert candidates[0].conflict_type == "temporal_update"


def test_disabled_empty_or_non_conflicting_inputs_return_empty() -> None:
    """关闭、空正文和同极性内容均不得产生候选。"""

    disabled = ContradictionDetector(enabled=False)
    enabled = ContradictionDetector(jaccard_threshold=0.3)
    pair = [
        source(1, "我喜欢喝咖啡", hours_ago=1),
        source(2, "我也喜欢喝咖啡", hours_ago=0),
    ]

    assert disabled.detect_candidates(pair) == ()
    assert enabled.detect_candidates(pair) == ()


def test_candidate_key_changes_with_revision() -> None:
    """任一 canonical revision 变化都必须改变冲突候选幂等键。"""

    detector = ContradictionDetector(jaccard_threshold=0.3)
    older = source(1, "我一直喜欢喝咖啡", hours_ago=1)
    newer = source(2, "我现在不再喜欢喝咖啡", hours_ago=0)
    revised = source(
        2,
        "我现在不再喜欢喝咖啡",
        hours_ago=0,
        revision="r-2-new",
    )

    first = detector.detect_candidates([older, newer])
    second = detector.detect_candidates([older, revised])

    assert first[0].candidate_key != second[0].candidate_key
