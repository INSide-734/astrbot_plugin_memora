"""测试质量评分器的评分、告警、统计与自动暂停行为。"""

from __future__ import annotations

import time

from core.features.observability.application.quality_scorer import (
    AlertLevel,
    MemoryQualityScorer,
    QualityAlert,
    QualityScore,
    _text_to_simple_embedding,
)


def _make_atom(**overrides) -> dict:
    """构建带有稳定默认值的最小 Atom 字典。"""

    defaults = {
        "id": "atom-001",
        "content": "用户喜欢 Python 编程，因为 Python 语法简洁而且生态丰富",
        "created_at": time.time(),
        "ttl_days": 30.0,
        "source_type": "group_chat",
        "verified": False,
        "importance": 0.5,
    }
    defaults.update(overrides)
    return defaults


def _make_context(recent_messages=None, existing_atoms=None) -> dict:
    """构建可选包含近期消息和既有 Atom 的最小上下文。"""

    ctx = {}
    if recent_messages is not None:
        ctx["recent_messages"] = recent_messages
    if existing_atoms is not None:
        ctx["existing_atoms"] = existing_atoms
    return ctx


class TestScorerInit:
    """验证质量评分器初始化。"""

    def test_default_initialization(self):
        """验证默认初始化状态和历史窗口为空。"""

        scorer = MemoryQualityScorer()
        assert scorer._paused is False
        assert scorer._pause_reason == ""
        assert len(scorer._score_history) == 0
        assert len(scorer._alert_history) == 0

    def test_custom_window_size(self):
        """验证自定义评分历史窗口大小。"""

        scorer = MemoryQualityScorer(window_size=50)
        assert scorer._score_history.maxlen == 50


class TestScoreAtomHighQuality:
    """验证高质量 Atom 的评分结果。"""

    def test_high_quality_atom_scores_high(self):
        """验证可信、完整且新鲜的 Atom 获得高分。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(
            content=(
                "用户张三详细介绍了 Python 异步编程的最佳实践，"
                "并且展示了一个完整的 FastAPI 项目示例，"
                "因为 FastAPI 性能出色，而且生态完善"
            ),
            source_type="admin_command",
            verified=True,
            created_at=time.time(),  # 全新记忆
            ttl_days=365.0,
        )
        score = scorer.score_atom(atom)
        assert score.overall > 0.7, (
            f"Expected high quality, got overall={score.overall}"
        )
        assert score.accuracy > 0.8, "Admin + verified should have high accuracy"
        assert score.freshness > 0.8, "Brand new atom should be very fresh"

    def test_high_quality_with_rich_context(self):
        """验证丰富且不冲突的上下文维持高质量分数。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(
            content="用户喜欢 Python 编程，因为 Python 语法简洁而且生态丰富",
            source_type="admin_command",
            verified=True,
        )
        context = _make_context(
            recent_messages=["Python 编程", "语法简洁", "生态丰富"],
            existing_atoms=[
                {
                    "content": "完全无关的足球话题",
                    "embedding": _text_to_simple_embedding("足球比赛"),
                }
            ],
        )
        score = scorer.score_atom(atom, context=context)
        assert score.overall > 0.7, (
            f"Expected high quality, got overall={score.overall}"
        )


class TestScoreAtomLowQuality:
    """验证低质量 Atom 的评分结果。"""

    def test_low_quality_short_expired_content(self):
        """验证过期且过短的群聊内容获得低分。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(
            content="嗯",
            source_type="group_chat",
            verified=False,
            created_at=time.time() - 60 * 86400,  # 六十天前
            ttl_days=30.0,  # 已完全过期
        )
        score = scorer.score_atom(atom)
        assert score.overall < 0.5, f"Expected low quality, got overall={score.overall}"

    def test_empty_content_scores_low(self):
        """验证空内容的连贯性分数较低。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(content="", source_type="group_chat")
        score = scorer.score_atom(atom)
        assert score.coherence < 0.5, (
            f"Empty content should have low coherence, got {score.coherence}"
        )


class TestScoreAtomFreshness:
    """验证 Atom 新鲜度评分。"""

    def test_fresh_atom_scores_high(self):
        """验证刚创建的 Atom 具有高新鲜度。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(created_at=time.time(), ttl_days=30.0)
        score = scorer.score_atom(atom)
        assert score.freshness > 0.8, (
            f"Brand new atom should be fresh, got {score.freshness}"
        )

    def test_half_expired_atom_has_lower_freshness(self):
        """验证经过一半有效期后新鲜度下降但仍为正。"""

        scorer = MemoryQualityScorer()
        half_life_ago = time.time() - (15 * 86400)  # 三十天有效期的十五天前
        atom = _make_atom(created_at=half_life_ago, ttl_days=30.0)
        score = scorer.score_atom(atom)
        # 剩余一半有效期时，新鲜度应下降但仍大于零。
        assert 0.2 <= score.freshness <= 0.7, (
            f"Half-life freshness got {score.freshness}"
        )

    def test_fully_expired_atom_scores_zero_or_near_zero(self):
        """验证完全过期的 Atom 新鲜度接近零。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(
            created_at=time.time() - 120 * 86400,  # 一百二十天前
            ttl_days=30.0,
        )
        score = scorer.score_atom(atom)
        assert score.freshness < 0.1, (
            f"Fully expired atom should have near-zero freshness, got {score.freshness}"
        )

    def test_zero_ttl_returns_zero(self):
        """验证零有效期直接得到零新鲜度。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(ttl_days=0.0)
        score = scorer.score_atom(atom)
        assert score.freshness == 0.0


class TestScoreAtomAccuracy:
    """验证 Atom 准确性评分。"""

    def test_admin_command_has_high_accuracy(self):
        """验证管理员命令来源具有高准确性。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(source_type="admin_command", verified=False)
        score = scorer.score_atom(atom)
        assert score.accuracy >= 0.85, f"Admin command accuracy got {score.accuracy}"

    def test_group_chat_has_low_accuracy(self):
        """验证未核验群聊来源的准确性较低。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(source_type="group_chat", verified=False)
        score = scorer.score_atom(atom)
        assert score.accuracy <= 0.65, f"Group chat accuracy got {score.accuracy}"

    def test_verified_bonus(self):
        """验证已核验内容获得准确性加成。"""

        scorer = MemoryQualityScorer()
        unverified = scorer.score_atom(
            _make_atom(source_type="group_chat", verified=False)
        )
        verified = scorer.score_atom(
            _make_atom(source_type="group_chat", verified=True)
        )
        assert verified.accuracy > unverified.accuracy, (
            f"Verified={verified.accuracy} should > unverified={unverified.accuracy}"
        )

    def test_url_bonus(self):
        """验证包含来源链接的内容获得准确性加成。"""

        scorer = MemoryQualityScorer()
        no_url = scorer.score_atom(
            _make_atom(content="普通文本", source_type="group_chat")
        )
        with_url = scorer.score_atom(
            _make_atom(
                content="参考 https://example.com 了解更多", source_type="group_chat"
            )
        )
        assert with_url.accuracy > no_url.accuracy, (
            f"URL accuracy={with_url.accuracy} should > no-url={no_url.accuracy}"
        )

    def test_unknown_source_type_falls_back(self):
        """验证未知来源类型使用稳定回退准确性。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(source_type="unknown_platform", verified=False)
        score = scorer.score_atom(atom)
        assert score.accuracy == 0.55  # 默认回退值


class TestScoreAtomWithContext:
    """验证带既有记忆上下文的评分。"""

    def test_consistency_lower_when_context_has_similar_atoms(self):
        """验证近似既有内容会降低一致性分数。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(content="用户喜欢 Python 编程")
        context = _make_context(
            existing_atoms=[
                {"content": "用户喜欢 Python 编程，经常用它写脚本"},
                {"content": "Python 是该用户最喜欢的语言"},
            ]
        )
        score = scorer.score_atom(atom, context=context)
        # 存在近似重复内容时，一致性应低于默认值 0.8。
        assert score.consistency < 0.8, (
            f"Expected low consistency, got {score.consistency}"
        )

    def test_consistency_high_when_context_has_unrelated_atoms(self):
        """验证无关既有内容不会显著降低一致性。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(content="用户喜欢 Python 编程")
        context = _make_context(
            existing_atoms=[
                {"content": "足球比赛结果"},
                {"content": "天气预报"},
            ]
        )
        score = scorer.score_atom(atom, context=context)
        assert score.consistency >= 0.7, (
            f"Expected high consistency, got {score.consistency}"
        )


# ---------------------------------------------------------------------------
# 告警系统
# ---------------------------------------------------------------------------


class TestCheckAlerts:
    """验证质量告警生成与历史记录。"""

    def test_high_scores_produce_no_alerts(self):
        """验证全维度高分不会生成告警。"""

        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="good",
            consistency=0.9,
            coherence=0.9,
            relevance=0.9,
            freshness=0.9,
            accuracy=0.9,
            overall=0.9,
        )
        alerts = scorer.check_alerts(qs)
        assert alerts == []

    def test_critical_overall_triggers_alert(self):
        """验证极低总分触发严重告警。"""

        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="bad",
            consistency=0.2,
            coherence=0.2,
            relevance=0.2,
            freshness=0.2,
            accuracy=0.2,
            overall=0.2,
        )
        alerts = scorer.check_alerts(qs)
        critical = [a for a in alerts if a.level == AlertLevel.CRITICAL]
        assert len(critical) >= 1, f"Expected at least 1 CRITICAL alert, got {alerts}"

    def test_high_alert_threshold(self):
        """验证高等级告警阈值边界。"""

        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="mid",
            consistency=0.4,
            coherence=0.4,
            relevance=0.4,
            freshness=0.4,
            accuracy=0.4,
            overall=0.4,
        )
        alerts = scorer.check_alerts(qs)
        # 0.4 低于高等级阈值 0.45，但高于严重阈值 0.30。
        levels = {a.level for a in alerts}
        assert AlertLevel.HIGH in levels or AlertLevel.MEDIUM in levels

    def test_medium_alert_threshold(self):
        """验证中等级告警阈值边界。"""

        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="mid_high",
            consistency=0.55,
            coherence=0.55,
            relevance=0.55,
            freshness=0.55,
            accuracy=0.55,
            overall=0.55,
        )
        alerts = scorer.check_alerts(qs)
        # 0.55 低于中等级阈值 0.60，但高于高等级阈值 0.45。
        levels = {a.level for a in alerts}
        assert AlertLevel.MEDIUM in levels

    def test_alerts_recorded_in_history(self):
        """验证生成的告警写入评分器历史。"""

        scorer = MemoryQualityScorer()
        qs = QualityScore(
            atom_id="x",
            consistency=0.1,
            coherence=0.1,
            relevance=0.1,
            freshness=0.1,
            accuracy=0.1,
            overall=0.1,
        )
        scorer.check_alerts(qs)
        assert len(scorer._alert_history) > 0


# ---------------------------------------------------------------------------
# 自动暂停
# ---------------------------------------------------------------------------


class TestShouldPause:
    """验证质量评分器自动暂停决策。"""

    def test_no_pause_when_scores_are_good(self):
        """验证连续高分不会触发暂停。"""

        scorer = MemoryQualityScorer()
        for i in range(10):
            scorer._score_history.append(
                QualityScore(
                    atom_id=str(i),
                    consistency=0.8,
                    coherence=0.8,
                    relevance=0.8,
                    freshness=0.8,
                    accuracy=0.8,
                    overall=0.8,
                )
            )
        should, reason = scorer.should_pause()
        assert should is False
        assert reason == ""

    def test_pause_after_consecutive_low_scores(self):
        """验证连续低分触发暂停并返回原因。"""

        scorer = MemoryQualityScorer()
        for i in range(5):
            scorer._score_history.append(
                QualityScore(
                    atom_id=str(i),
                    consistency=0.2,
                    coherence=0.2,
                    relevance=0.2,
                    freshness=0.2,
                    accuracy=0.2,
                    overall=0.2,
                )
            )
        should, reason = scorer.should_pause()
        assert should is True
        assert "连续" in reason
        assert str(0.30) in reason

    def test_pause_when_enough_scores_but_not_all_low(self):
        """验证低分未达到连续阈值时不暂停。"""

        scorer = MemoryQualityScorer()
        for i in range(5):
            overall = 0.2 if i < 3 else 0.8  # 只有三个连续低分
            scorer._score_history.append(
                QualityScore(
                    atom_id=str(i),
                    consistency=overall,
                    coherence=overall,
                    relevance=overall,
                    freshness=overall,
                    accuracy=overall,
                    overall=overall,
                )
            )
        should, reason = scorer.should_pause()
        assert should is False, f"Only 3/5 low — should not pause, got: {reason}"

    def test_pause_from_critical_alerts_in_one_hour(self):
        """验证一小时内多个严重告警触发暂停。"""

        scorer = MemoryQualityScorer()
        now = time.time()
        # 添加最近三十分钟内的两个严重告警。
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="overall",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 1800,  # 三十分钟前
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="coherence",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 600,  # 十分钟前
            )
        )
        should, reason = scorer.should_pause()
        assert should is True
        assert "严重告警" in reason

    def test_old_critical_alerts_dont_trigger_pause(self):
        """验证一小时窗口外的严重告警不会触发暂停。"""

        scorer = MemoryQualityScorer()
        now = time.time()
        # 添加两小时前、位于一小时窗口外的两个严重告警。
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="overall",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 7200,  # 两小时前
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="coherence",
                score=0.1,
                threshold=0.3,
                message="critical",
                suggestion="fix",
                timestamp=now - 7500,  # 两个多小时前
            )
        )
        should, reason = scorer.should_pause()
        assert should is False, f"Old alerts should not trigger pause, got: {reason}"


# ---------------------------------------------------------------------------
# 统计信息
# ---------------------------------------------------------------------------


class TestGetStats:
    """验证质量评分器统计快照。"""

    def test_empty_stats(self):
        """验证空历史返回零计数和未暂停状态。"""

        scorer = MemoryQualityScorer()
        stats = scorer.get_stats()
        assert stats["total_scored"] == 0
        assert stats["paused"] is False
        assert stats["recent_scores"] == []

    def test_stats_after_scoring(self):
        """验证评分后统计包含各维度均值与近期记录。"""

        scorer = MemoryQualityScorer()
        for i in range(5):
            atom = _make_atom(
                id=f"atom-{i}",
                content=f"测试内容 {i}",
                source_type="admin_command",
                verified=True,
            )
            scorer.score_atom(atom)
        stats = scorer.get_stats()
        assert stats["total_scored"] == 5
        assert "avg_overall" in stats
        assert "avg_consistency" in stats
        assert "avg_coherence" in stats
        assert "avg_relevance" in stats
        assert "avg_freshness" in stats
        assert "avg_accuracy" in stats
        assert stats["paused"] is False
        assert len(stats["recent_scores"]) <= 10
        assert all(
            isinstance(v, float)
            for v in stats["recent_scores"][0].values()
            if v != stats["recent_scores"][0].get("atom_id")
        )

    def test_stats_reflects_pause_state(self):
        """验证统计快照反映暂停状态与原因。"""

        scorer = MemoryQualityScorer()
        scorer._paused = True
        scorer._pause_reason = "test reason"
        stats = scorer.get_stats()
        assert stats["paused"] is True
        assert stats["pause_reason"] == "test reason"

    def test_alert_counts_in_stats(self):
        """验证统计按等级汇总告警数量。"""

        scorer = MemoryQualityScorer()
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="x",
                score=0.1,
                threshold=0.3,
                message="m",
                suggestion="s",
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.HIGH,
                dimension="x",
                score=0.4,
                threshold=0.45,
                message="m",
                suggestion="s",
            )
        )
        scorer._alert_history.append(
            QualityAlert(
                level=AlertLevel.CRITICAL,
                dimension="x",
                score=0.2,
                threshold=0.3,
                message="m",
                suggestion="s",
            )
        )
        stats = scorer.get_stats()
        assert stats["alert_counts"].get("critical") == 2
        assert stats["alert_counts"].get("high") == 1


# ---------------------------------------------------------------------------
# 边界场景
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """验证质量评分器边界输入。"""

    def test_atom_with_missing_fields(self):
        """验证最小 Atom 仍可产生有界分数。"""

        scorer = MemoryQualityScorer()
        score = scorer.score_atom({"id": "minimal"})
        assert score.atom_id == "minimal"
        assert 0.0 <= score.overall <= 1.0

    def test_atom_with_ttl_key_variant(self):
        """验证备用 ttl 字段可作为有效期回退。"""

        scorer = MemoryQualityScorer()
        atom_ttl = _make_atom(ttl=60.0)
        # 移除 ttl_days，以验证备用字段回退。
        atom_ttl.pop("ttl_days", None)
        score = scorer.score_atom(atom_ttl)
        assert 0.0 <= score.freshness <= 1.0

    def test_context_with_embedding_uses_cosine(self):
        """验证带向量的既有内容走余弦相似度路径。"""

        scorer = MemoryQualityScorer()
        atom = _make_atom(content="Python 编程")
        context = _make_context(
            existing_atoms=[
                {
                    "content": "Python 编程入门",
                    "embedding": _text_to_simple_embedding("Python 编程入门"),
                }
            ]
        )
        score = scorer.score_atom(atom, context=context)
        assert 0.0 <= score.consistency <= 1.0

    def test_window_overflow_evicts_oldest(self):
        """验证评分窗口溢出时淘汰最早记录。"""

        scorer = MemoryQualityScorer(window_size=5)
        for i in range(10):
            atom = _make_atom(id=f"atom-{i}")
            scorer.score_atom(atom)
        assert len(scorer._score_history) == 5
        # 最早的 atom-0 至 atom-4 应已被淘汰。
        ids = [s.atom_id for s in scorer._score_history]
        assert "atom-0" not in ids
        assert "atom-9" in ids
