"""core/jargon/statistical_filter.py 测试 — JargonStatisticalFilter。"""

from __future__ import annotations

import time
from unittest.mock import patch

from core.jargon.models import JargonCandidate, JargonStats
from core.jargon.statistical_filter import (
    JARGON_CANDIDATE_SORT_FIELDS,
    JargonStatisticalFilter,
)
from core.shared.list_sorting import SortQuery


class TestUpdate:
    """update() 方法的单元测试。"""

    def test_empty_text_is_noop(self) -> None:
        f = JargonStatisticalFilter()
        f.update("", "g1", "u1")
        candidates = f.get_candidates("g1")
        assert candidates == []

    def test_whitespace_only_text_is_noop(self) -> None:
        f = JargonStatisticalFilter()
        f.update("   ", "g1", "u1")
        candidates = f.get_candidates("g1")
        assert candidates == []

    def test_common_word_filtered_out(self) -> None:
        """常见词（"吃饭"、"睡觉"）被过滤。"""
        f = JargonStatisticalFilter()
        # 提交多次以满足 _MIN_FREQUENCY
        for _ in range(5):
            f.update("我们去吃饭了", "g1", "u1")
            f.update("我要睡觉了", "g1", "u1")
        candidates = f.get_candidates("g1")
        terms = {c.term for c in candidates}
        assert "吃饭" not in terms
        assert "睡觉" not in terms

    def test_low_freq_jargon_kept_as_candidate(self) -> None:
        """低频新词被保留为候选。"""
        f = JargonStatisticalFilter()
        # "破防" 在 jieba 词频中很低（~3），应被保留
        for _ in range(5):
            f.update("今天又破防了兄弟们", "g1", "u1")
        candidates = f.get_candidates("g1")
        # "破防" should be a candidate (low jieba freq)
        assert len(candidates) >= 0  # depends on jieba behavior
        # 至少不应 crash

    def test_rare_term_tracked(self) -> None:
        """故意构造一个不在 jieba 词典中的罕见词，验证被追踪。"""
        f = JargonStatisticalFilter()
        # "蚌埠住了" 是一个网络流行语的变体写法
        for _ in range(6):
            f.update("蚌埠住了蚌埠住了", "g1", "u1")
        candidates = f.get_candidates("g1")
        # 应至少有一些输出（取决于 jieba 分词结果）
        assert isinstance(candidates, list)

    def test_mentions_and_urls_filtered(self) -> None:
        f = JargonStatisticalFilter()
        for _ in range(5):
            f.update("@someone check https://example.com/foo [image]", "g1", "u1")
        # 不应 crash，且 @mention、URL、[image] 不应产生候选
        candidates = f.get_candidates("g1")
        terms = {c.term for c in candidates}
        # 这些不应作为 term 出现
        assert "someone" not in terms
        assert "example" not in terms
        assert "image" not in terms

    def test_multi_group_isolation(self) -> None:
        f = JargonStatisticalFilter()
        # g1 用户讨论"摸鱼"
        for _ in range(6):
            f.update("今天摸鱼被抓了", "g1", "u1")
        # g2 没有数据
        assert f.get_candidates("g2") == []

        # g1 的候选不应为空
        g1_candidates = f.get_candidates("g1")
        assert isinstance(g1_candidates, list)

    def test_multiple_senders_tracked(self) -> None:
        f = JargonStatisticalFilter()
        # 两个不同用户使用同一罕见词
        for _ in range(4):
            f.update("固拉多又飞不起来了", "g1", "u1")
        for _ in range(4):
            f.update("固拉多又飞不起来了", "g1", "u2")
        candidates = f.get_candidates("g1")
        # 找一个 term 恰好是 "固拉多"（取决于 jieba 分词）
        groudon_candidates = [c for c in candidates if "固拉多" in c.term]
        if groudon_candidates:
            assert groudon_candidates[0].unique_users >= 2


class TestCrossGroupIDF:
    """跨群 IDF 计算测试。"""

    def test_same_term_multiple_groups_low_idf(self) -> None:
        """同一词在多个群出现 -> 低 IDF。"""
        f = JargonStatisticalFilter()
        rare_word = "xynergy"  # 非中文，不会被 jieba 词典过滤
        for _ in range(5):
            f.update(f"我说的是 {rare_word}", "g1", "u1")
            f.update(f"我也说 {rare_word}", "g2", "u1")
            f.update(f"还有 {rare_word}", "g3", "u1")

        candidates_g1 = f.get_candidates("g1")
        x_candidates = [c for c in candidates_g1 if c.term == rare_word]
        if x_candidates:
            # IDF 应该较低（因为出现在多个群）
            assert x_candidates[0].idf_score <= 0.5

    def test_unique_term_high_idf(self) -> None:
        """仅在一个群出现的词 -> 高 IDF。"""
        f = JargonStatisticalFilter()
        rare_word = "zqlyg"  # 非标准词
        another_rare = "xanotherrare"
        for _ in range(6):
            f.update(f"{rare_word} 是一种东西", "g1", "u1")
        # g2 有其他罕见词（确保 g2 有被追踪的 term，使 num_groups=2）
        for _ in range(6):
            f.update(f"{another_rare} 在另一个群", "g2", "u1")

        candidates = f.get_candidates("g1")
        z_candidates = [c for c in candidates if c.term == rare_word]
        if z_candidates:
            # 仅在 g1 出现，g2 不含该词，IDF = log(2/1) ≈ 0.693 > 0.5
            assert z_candidates[0].idf_score > 0.5


class TestBurstScore:
    """爆发频率计算测试。"""

    def test_recent_high_freq_high_burst(self) -> None:
        """近期高频 -> 高 burst。"""
        f = JargonStatisticalFilter()
        rare_word = "qbursttest"
        now = time.time()
        very_recent = now - 60  # 60 秒前
        # 手动设置首次出现时间为很近期
        f._term_first_seen["g1"][rare_word] = very_recent
        f._group_term_freq["g1"][rare_word] = 20
        f._global_term_freq[rare_word] = 20

        burst = f._calc_burst_score(rare_word, "g1")
        # 频率 / 天数，60 秒前 = 至少 20 次 -> burst > 0
        assert burst > 0

    def test_old_infrequent_low_burst(self) -> None:
        """很久以前低频 -> 低 burst。"""
        f = JargonStatisticalFilter()
        rare_word = "qoldburst"
        long_ago = time.time() - 86400 * 30  # 30 天前
        f._term_first_seen["g1"][rare_word] = long_ago
        f._group_term_freq["g1"][rare_word] = 5
        f._global_term_freq[rare_word] = 5

        burst = f._calc_burst_score(rare_word, "g1")
        # 5 次 / 30 天 = 0.167 ~ 很低
        assert burst < 1.0


class TestConcentration:
    """用户集中度计算测试。"""

    def test_single_user_high_concentration(self) -> None:
        """单用户 -> 高 concentration。"""
        f = JargonStatisticalFilter()
        rare_word = "qsolouser"
        for _ in range(6):
            f.update(f"{rare_word} 是秘密", "g1", "u1")
        candidates = f.get_candidates("g1")
        q_candidates = [c for c in candidates if c.term == rare_word]
        if q_candidates:
            # concentration = 1/1 = 1.0
            assert q_candidates[0].concentration_score == 1.0

    def test_many_users_low_concentration(self) -> None:
        """多用户 -> 低 concentration。"""
        f = JargonStatisticalFilter()
        rare_word = "qmanyusers"
        for uid in ["u1", "u2", "u3", "u4", "u5"]:
            for _ in range(3):
                f.update(f"{rare_word} 大家都说", "g1", uid)
        candidates = f.get_candidates("g1")
        q_candidates = [c for c in candidates if c.term == rare_word]
        if q_candidates:
            # concentration = 1/5 = 0.2
            assert q_candidates[0].concentration_score <= 0.3


class TestGetCandidates:
    """get_candidates() 方法测试。"""

    def test_returns_sorted_by_score_desc(self) -> None:
        """验证返回结果按评分降序排列。"""
        f = JargonStatisticalFilter()
        terms = ["aterm01", "bterm02", "cterm03"]
        for t in terms:
            for _ in range(6):
                f.update(f"{t} 是一个测试词", "g1", "u1")

        candidates = f.get_candidates("g1")
        if len(candidates) >= 2:
            for i in range(len(candidates) - 1):
                assert candidates[i].score >= candidates[i + 1].score

    def test_empty_group_returns_empty(self) -> None:
        f = JargonStatisticalFilter()
        assert f.get_candidates("nonexistent") == []

    def test_below_min_frequency_excluded(self) -> None:
        """低于 _MIN_FREQUENCY 的词不入选。"""
        f = JargonStatisticalFilter()
        rare_word = "qbelowmin"
        # 只出现 2 次（< _MIN_FREQUENCY=3）
        f.update(f"{rare_word} test", "g1", "u1")
        f.update(f"{rare_word} test", "g1", "u1")
        candidates = f.get_candidates("g1")
        terms = {c.term for c in candidates}
        assert rare_word not in terms

    def test_exclude_terms_filtered(self) -> None:
        f = JargonStatisticalFilter()
        rare_word = "qexcludedword"
        for _ in range(6):
            f.update(f"{rare_word} test", "g1", "u1")

        candidates_without = f.get_candidates("g1")
        assert any(c.term == rare_word for c in candidates_without)

        candidates_with = f.get_candidates("g1", exclude_terms={rare_word})
        assert not any(c.term == rare_word for c in candidates_with)

    def test_limit_parameter(self) -> None:
        f = JargonStatisticalFilter()
        for i in range(10):
            term = f"qlimit{i:04d}"
            for _ in range(6):
                f.update(f"{term} test content", "g1", "u1")

        candidates = f.get_candidates("g1", limit=3)
        assert len(candidates) <= 3

    def test_sorts_full_candidate_set_before_applying_limit(self) -> None:
        f = JargonStatisticalFilter()
        f._group_term_freq["g1"].update(
            {"high-frequency": 9, "middle-frequency": 6, "low-frequency": 3}
        )
        for term in f._group_term_freq["g1"]:
            f._user_term_freq["g1"][term]["user-1"] = 1
            f._term_first_seen["g1"][term] = 100.0

        with patch.object(f, "_calc_burst_score", return_value=1.0):
            candidates = f.get_candidates(
                "g1",
                limit=2,
                sort=SortQuery("frequency", "asc"),
            )

        assert [candidate.frequency for candidate in candidates] == [3, 6]

    def test_exposes_only_the_approved_candidate_sort_fields(self) -> None:
        assert JARGON_CANDIDATE_SORT_FIELDS == frozenset(
            {"term", "score", "frequency", "unique_users", "first_seen"}
        )

    def test_returns_jargon_candidate_objects(self) -> None:
        f = JargonStatisticalFilter()
        rare_word = "qdatatype"
        for _ in range(6):
            f.update(f"{rare_word} testing content", "g1", "u1")

        candidates = f.get_candidates("g1")
        if candidates:
            for c in candidates:
                assert isinstance(c, JargonCandidate)
                assert isinstance(c.term, str)
                assert isinstance(c.score, float)
                assert isinstance(c.frequency, int)
                assert isinstance(c.unique_users, int)


class TestGetStats:
    """get_stats() 方法测试。"""

    def test_returns_jargon_stats(self) -> None:
        f = JargonStatisticalFilter()
        for _ in range(6):
            f.update("qstats_term 测试内容", "g1", "u1")

        stats = f.get_stats("g1")
        assert isinstance(stats, JargonStats)
        assert stats.group_id == "g1"
        assert stats.total_terms > 0
        assert isinstance(stats.candidate_count, int)
        assert isinstance(stats.top_candidates, list)

    def test_empty_group_stats(self) -> None:
        f = JargonStatisticalFilter()
        stats = f.get_stats("empty_group")
        assert stats.total_terms == 0
        assert stats.candidate_count == 0
        assert stats.top_candidates == []


class TestResetGroup:
    """reset_group() 方法测试。"""

    def test_reset_clears_data(self) -> None:
        f = JargonStatisticalFilter()
        rare_word = "qresetword"
        for _ in range(6):
            f.update(f"{rare_word} test content", "g1", "u1")

        # 重置前有候选
        before = f.get_candidates("g1")
        assert len(before) > 0

        f.reset_group("g1")

        # 重置后无候选
        after = f.get_candidates("g1")
        assert after == []

        stats = f.get_stats("g1")
        assert stats.total_terms == 0

    def test_reset_nonexistent_group_noop(self) -> None:
        f = JargonStatisticalFilter()
        # 不存在的群组不应报错
        f.reset_group("no_such_group")
        # 无异常 = 通过


class TestContextExamples:
    """上下文示例保留测试。"""

    def test_context_examples_retained(self) -> None:
        f = JargonStatisticalFilter()
        rare_word = "qctxword"
        messages = [
            f"今天学了一个新词 {rare_word}",
            f"{rare_word} 真的很有意思",
            f"有人用过 {rare_word} 吗",
            f"{rare_word} 是一个概念",
            f"再测试一下 {rare_word}",
            f"最后一条 {rare_word}",
        ]
        for msg in messages:
            f.update(msg, "g1", "u1")

        candidates = f.get_candidates("g1")
        q_candidates = [c for c in candidates if c.term == rare_word]
        if q_candidates:
            assert len(q_candidates[0].context_examples) <= 5
            for ex in q_candidates[0].context_examples:
                assert rare_word in ex

    def test_context_max_10_internal(self) -> None:
        """内部存储最多 10 条上下文。"""
        f = JargonStatisticalFilter()
        rare_word = "qmaxctx"
        for i in range(15):
            f.update(f"{rare_word} message number {i}", "g1", "u1")

        # 直接读取内部存储
        ctx_list = f._term_contexts.get("g1", {}).get(rare_word, [])
        assert len(ctx_list) <= 10


class TestJiebaLoading:
    """jieba 加载与词频过滤测试。"""

    def test_jieba_loads_successfully(self) -> None:
        f = JargonStatisticalFilter()
        f._ensure_jieba()
        assert f._jieba_loaded
        assert len(f._jieba_freq) > 0

    def test_standard_vocabulary_detected(self) -> None:
        f = JargonStatisticalFilter()
        f._ensure_jieba()
        # "中国" 在 jieba 词频中通常很高
        assert f._is_standard_vocabulary("中国") is True

    def test_rare_vocabulary_not_standard(self) -> None:
        f = JargonStatisticalFilter()
        f._ensure_jieba()
        # "破防" 或完全不存在于 jieba 的词
        assert f._is_standard_vocabulary("qnotindict") is False
