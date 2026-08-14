"""测试质量评分器的文本辅助函数、数据模型与来源权重。"""

from __future__ import annotations

import time
from unittest.mock import patch

from core.features.observability.application.quality_scorer import (
    _SOURCE_RELEVANCE_WEIGHT,
    _SOURCE_RELIABILITY,
    AlertLevel,
    QualityAlert,
    QualityScore,
    _count_connectors,
    _count_segments,
    _has_contradictory_sentiment,
    _has_multiple_sentences,
    _has_paragraph_breaks,
    _has_url,
    _text_to_simple_embedding,
    _tokenize,
)


class TestTokenize:
    """验证质量评分使用的分词辅助逻辑。"""

    def test_returns_empty_for_blank(self):
        """验证空白文本返回空分词列表。"""

        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_produces_bigrams_and_space_tokens(self):
        """验证连续中文文本生成相邻二元词。"""

        result = _tokenize("你好世界")
        assert "你好" in result
        assert "好世" in result
        assert "世界" in result

    def test_includes_space_split_tokens(self):
        """验证空格分隔文本保留完整词项。"""

        result = _tokenize("hello world")
        assert "hello" in result
        assert "world" in result


class TestContradictorySentiment:
    """验证矛盾情绪识别辅助逻辑。"""

    def test_detects_mixed_sentiment(self):
        """验证同一文本中的正负情绪可被识别。"""

        assert _has_contradictory_sentiment("今天很开心但是又有点难过")

    def test_no_mixed_sentiment_when_only_positive(self):
        """验证纯正向文本不会被误判为矛盾情绪。"""

        assert not _has_contradictory_sentiment("今天很开心很愉快")

    def test_no_mixed_sentiment_when_neutral(self):
        """验证中性文本不会被误判为矛盾情绪。"""

        assert not _has_contradictory_sentiment("今天天气晴朗")


class TestCountConnectors:
    """验证逻辑连接词计数。"""

    def test_counts_causal(self):
        """验证因果连接词会被计数。"""

        assert _count_connectors("因为他很努力，所以成功了") >= 1

    def test_counts_contrast(self):
        """验证转折连接词会被计数。"""

        assert _count_connectors("虽然累了，但还是继续工作") >= 1

    def test_counts_coordination(self):
        """验证并列连接词会被计数。"""

        assert _count_connectors("而且他还很聪明，同时也很勤奋") >= 1

    def test_zero_for_plain_text(self):
        """验证普通文本的连接词计数为零。"""

        assert _count_connectors("今天天气不错") == 0

    def test_all_three_categories(self):
        """验证混合逻辑文本可累计多个类别。"""

        text = "因为他努力但是失败了而且也没人帮忙"
        assert _count_connectors(text) >= 2


class TestCountSegments:
    """验证文本语义片段计数。"""

    def test_single_segment(self):
        """验证简短文本至少形成一个片段。"""

        assert _count_segments("简短内容") >= 1

    def test_paragraph_breaks(self):
        """验证双换行将文本拆为两个段落。"""

        text = "第一段内容\n\n第二段内容"
        assert _count_segments(text) == 2

    def test_sentence_boundaries(self):
        """验证中文全角标点能够划分有效句子。"""

        # 每个句子至少五个字符，才能通过有效句子阈值。
        text = "这是第一个测试句子。这是第二个测试句子！"
        assert _count_segments(text) >= 2


class TestParagraphAndSentenceHelpers:
    """验证段落和多句检测辅助函数。"""

    def test_has_paragraph_breaks_true(self):
        """验证双换行文本包含段落边界。"""

        assert _has_paragraph_breaks("第一段\n\n第二段") is True

    def test_has_paragraph_breaks_false(self):
        """验证单段文本不包含段落边界。"""

        assert _has_paragraph_breaks("单段文本") is False

    def test_has_multiple_sentences_true(self):
        """验证两个完整句子会被识别为多句。"""

        assert _has_multiple_sentences("这是第一句话。这是第二句话。") is True

    def test_has_multiple_sentences_false(self):
        """验证单句文本不会被识别为多句。"""

        assert _has_multiple_sentences("只有一个句子") is False


class TestHasUrl:
    """验证 URL 识别辅助函数。"""

    def test_detects_http(self):
        """验证 HTTP URL 能够被识别。"""

        assert _has_url("参见 https://example.com 了解更多")

    def test_detects_www(self):
        """验证 www 前缀 URL 能够被识别。"""

        assert _has_url("访问 www.example.com 查看")

    def test_no_url(self):
        """验证普通文本不会被误判为 URL。"""

        assert not _has_url("普通文本没有链接")


class TestTextToSimpleEmbedding:
    """验证确定性的简单文本向量。"""

    def test_returns_vector_of_correct_dim(self):
        """验证向量维度与请求维度一致。"""

        vec = _text_to_simple_embedding("测试内容", dim=64)
        assert len(vec) == 64

    def test_empty_text_returns_zero_vector(self):
        """验证空文本返回全零向量。"""

        vec = _text_to_simple_embedding("", dim=64)
        assert all(v == 0.0 for v in vec)

    def test_non_empty_text_has_non_zero_entries(self):
        """验证非空文本产生至少一个非零分量。"""

        vec = _text_to_simple_embedding("这是一段测试内容", dim=64)
        assert any(v != 0.0 for v in vec)

    def test_embedding_does_not_depend_on_process_randomized_hash(self):
        """验证向量不依赖进程级随机哈希。"""

        with patch("builtins.hash", return_value=1):
            first = _text_to_simple_embedding("稳定的质量评分向量", dim=64)
        with patch("builtins.hash", return_value=2):
            second = _text_to_simple_embedding("稳定的质量评分向量", dim=64)

        assert first == second


class TestQualityScore:
    """验证质量分数数据模型。"""

    def test_creates_with_all_dimensions(self):
        """验证数据模型保留全部评分维度。"""

        qs = QualityScore(
            atom_id="test-1",
            consistency=0.9,
            coherence=0.8,
            relevance=0.7,
            freshness=0.6,
            accuracy=0.5,
            overall=0.72,
        )
        assert qs.atom_id == "test-1"
        assert qs.consistency == 0.9
        assert qs.coherence == 0.8
        assert qs.relevance == 0.7
        assert qs.freshness == 0.6
        assert qs.accuracy == 0.5
        assert qs.overall == 0.72
        assert qs.timestamp > 0

    def test_timestamp_is_auto_generated(self):
        """验证新分数自动生成递增时间戳。"""

        qs1 = QualityScore(
            atom_id="a",
            consistency=1,
            coherence=1,
            relevance=1,
            freshness=1,
            accuracy=1,
            overall=1,
        )
        time.sleep(0.01)
        qs2 = QualityScore(
            atom_id="b",
            consistency=1,
            coherence=1,
            relevance=1,
            freshness=1,
            accuracy=1,
            overall=1,
        )
        assert qs2.timestamp > qs1.timestamp


class TestAlertLevel:
    """验证质量告警等级枚举。"""

    def test_four_levels(self):
        """验证四个稳定告警等级值。"""

        assert AlertLevel.CRITICAL.value == "critical"
        assert AlertLevel.HIGH.value == "high"
        assert AlertLevel.MEDIUM.value == "medium"
        assert AlertLevel.INFO.value == "info"


class TestQualityAlert:
    """验证质量告警数据模型。"""

    def test_creates_alert(self):
        """验证告警保留等级、维度并生成时间戳。"""

        alert = QualityAlert(
            level=AlertLevel.HIGH,
            dimension="consistency",
            score=0.35,
            threshold=0.45,
            message="consistency low",
            suggestion="dedup check",
        )
        assert alert.level == AlertLevel.HIGH
        assert alert.dimension == "consistency"
        assert alert.timestamp > 0


class TestSourceTables:
    """验证来源可靠性和相关性权重表。"""

    def test_admin_command_has_highest_reliability(self):
        """验证管理员命令具有最高可靠性。"""

        assert _SOURCE_RELIABILITY["admin_command"] == 0.95

    def test_group_chat_has_lowest_reliability(self):
        """验证群聊来源具有最低可靠性。"""

        assert _SOURCE_RELIABILITY["group_chat"] == 0.55

    def test_admin_command_has_highest_relevance(self):
        """验证管理员命令具有最高相关性权重。"""

        assert _SOURCE_RELEVANCE_WEIGHT["admin_command"] == 1.0

    def test_group_chat_has_lowest_relevance(self):
        """验证群聊来源具有最低相关性权重。"""

        assert _SOURCE_RELEVANCE_WEIGHT["group_chat"] == 0.6
