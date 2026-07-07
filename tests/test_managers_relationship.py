"""测试 RelationshipTracker 与 RelationshipStage。"""

from __future__ import annotations

import pytest

from core.managers.relationship_tracker import RelationshipStage, RelationshipTracker


class TestRelationshipStage:
    """测试 RelationshipStage 枚举与 from_warmth 分类器。"""

    @pytest.mark.parametrize(
        "warmth,expected_stage",
        [
            (0.0, RelationshipStage.STRANGER),
            (0.05, RelationshipStage.STRANGER),
            (0.14, RelationshipStage.STRANGER),
            (0.15, RelationshipStage.ACQUAINTANCE),
            (0.20, RelationshipStage.ACQUAINTANCE),
            (0.34, RelationshipStage.ACQUAINTANCE),
            (0.35, RelationshipStage.FRIEND),
            (0.50, RelationshipStage.FRIEND),
            (0.59, RelationshipStage.FRIEND),
            (0.60, RelationshipStage.CLOSE_FRIEND),
            (0.70, RelationshipStage.CLOSE_FRIEND),
            (0.79, RelationshipStage.CLOSE_FRIEND),
            (0.80, RelationshipStage.CONFIDANT),
            (0.90, RelationshipStage.CONFIDANT),
            (1.0, RelationshipStage.CONFIDANT),
        ],
    )
    def test_from_warmth_boundaries(
        self,
        warmth: float,
        expected_stage: RelationshipStage,
    ) -> None:
        """温暖度阈值映射到正确的关系阶段。"""
        assert RelationshipStage.from_warmth(warmth) == expected_stage

    def test_from_warmth_above_1(self) -> None:
        """Warmth above 1.0 maps to CONFIDANT (clamped in tracker)."""
        assert RelationshipStage.from_warmth(1.5) == RelationshipStage.CONFIDANT

    def test_from_warmth_negative(self) -> None:
        """负 warmth maps to STRANGER (clamped in tracker)."""
        assert RelationshipStage.from_warmth(-10.0) == RelationshipStage.STRANGER


class TestRelationshipTracker:
    """测试 RelationshipTracker。"""

    def test_initial_warmth_is_zero(self) -> None:
        """未知参与者温暖度为 0.0。"""
        tracker = RelationshipTracker()
        assert tracker.get_warmth("unknown_user") == 0.0

    def test_initial_stage_is_stranger(self) -> None:
        """未知参与者初始为陌生人。"""
        tracker = RelationshipTracker()
        assert tracker.get_stage("unknown_user") == RelationshipStage.STRANGER

    @pytest.mark.asyncio
    async def test_record_interaction_increases_warmth(self) -> None:
        """交互增加温暖度分数。"""
        tracker = RelationshipTracker()
        new_warmth, new_stage = await tracker.record_interaction(
            "user1", importance=0.8, sentiment="positive"
        )
        # delta = 0.8 * 0.1 * 1.3 = 0.104
        assert new_warmth == pytest.approx(0.104)
        assert new_warmth > 0.0

    @pytest.mark.asyncio
    async def test_sentiment_affects_delta(self) -> None:
        """正面情感产生的增量高于负面情感。"""
        tracker = RelationshipTracker()
        wt_pos, _ = await tracker.record_interaction(
            "pos_user", importance=1.0, sentiment="positive"
        )
        # Reset tracker for clean test
        tracker2 = RelationshipTracker()
        wt_neg, _ = await tracker2.record_interaction(
            "neg_user", importance=1.0, sentiment="negative"
        )
        # positive delta: 1.0*0.1*1.3=0.13, negative: 1.0*0.1*0.7=0.07
        assert wt_pos > wt_neg

    @pytest.mark.asyncio
    async def test_stage_transitions_on_cumulative_warmth(self) -> None:
        """多次交互可以触发关系阶段转变。"""
        tracker = RelationshipTracker()
        # 6 interactions at importance=1.0, positive → each ~0.13, total ~0.78
        for _ in range(6):
            await tracker.record_interaction(
                "user1", importance=1.0, sentiment="positive"
            )
        stage = tracker.get_stage("user1")
        assert stage in (
            RelationshipStage.CLOSE_FRIEND,
            RelationshipStage.CONFIDANT,
        )

    def test_get_stats_returns_structure(self) -> None:
        """get_stats 返回预期的字段结构。"""
        tracker = RelationshipTracker()
        stats = tracker.get_stats("user1")
        assert stats["participant_id"] == "user1"
        assert "warmth" in stats
        assert "stage" in stats
        assert "interaction_count" in stats

    def test_all_stats_empty(self) -> None:
        """无参与者时 all_stats 返回空列表。"""
        tracker = RelationshipTracker()
        assert tracker.all_stats() == []

    @pytest.mark.asyncio
    async def test_all_stats_with_participants(self) -> None:
        """all_stats 返回所有被追踪的参与者。"""
        tracker = RelationshipTracker()
        await tracker.record_interaction("user_a", importance=1.0)
        await tracker.record_interaction("user_b", importance=0.5)
        stats = tracker.all_stats()
        assert len(stats) == 2
        pids = {s["participant_id"] for s in stats}
        assert pids == {"user_a", "user_b"}
