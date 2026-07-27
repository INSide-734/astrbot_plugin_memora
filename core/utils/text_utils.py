"""共享文本分词工具。

提供代码库中使用的三种分词变体，均使用清晰、
描述性命名，便于调用方选择合适的分词器。

.. versionadded:: 1.0.0
   从 ``quality_scorer``、``contradiction_detector`` 和
   ``knowledge_retriever`` 中提取，消除重复实现。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 知识检索器停用词（放在此处以避免循环导入）
# ---------------------------------------------------------------------------
_KNOWLEDGE_STOPWORDS: frozenset[str] = frozenset(
    {
        "的",
        "了",
        "在",
        "是",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "他",
        "她",
        "它",
        "们",
        "那",
        "些",
        "什么",
        "怎么",
        "如何",
        "为什么",
        "可以",
        "这个",
        "那个",
        "还是",
        "只是",
        "已经",
        "因为",
        "所以",
        "但是",
        "如果",
        "虽然",
        "而且",
        "或者",
        "不过",
        "然后",
        "最后",
        "之后",
        "以前",
        "以后",
        "时候",
        "知道",
        "觉得",
        "认为",
        "应该",
        "可能",
        "需要",
        "能够",
        "希望",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "about",
        "up",
        "out",
        "if",
        "but",
        "or",
        "not",
        "and",
        "this",
        "that",
        "it",
        "its",
        "they",
        "them",
        "their",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "she",
        "him",
        "her",
        "his",
        "my",
        "me",
        "no",
        "nor",
        "which",
        "who",
        "whom",
        "what",
        "isn",
        "aren",
        "wasn",
        "weren",
        "hasn",
        "haven",
        "hadn",
        "doesn",
        "don",
        "didn",
        "won",
        "wouldn",
        "shan",
        "shouldn",
        "can",
        "couldn",
        "mustn",
        "let",
        "re",
        "ve",
        "ll",
        "d",
        "s",
        "t",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def tokenize_bigrams(text: str) -> list[str]:
    """中文字符二元分词器。

    对 CJK 密集文本使用重叠字符二元模型，
    对非 CJK 部分使用空格分割。无外部 NLP 依赖。

    用于: :class:`~core.monitoring.quality_scorer.MemoryQualityScorer`
    的内容重叠 Jaccard 计算。

    Args:
        text: Input text to tokenize.

    Returns:
        List of bigram strings plus space-split tokens.
    """
    if not text.strip():
        return []
    cleaned = text.strip()
    bigrams = [cleaned[i : i + 2] for i in range(max(1, len(cleaned) - 1))]
    space_tokens = cleaned.split()
    return bigrams + space_tokens


def tokenize_cjk_words(text: str) -> list[str]:
    """CJK 单字符 + 英文单词分词器。

    将中文字符逐字切分，英文单词保持完整。
    无外部 NLP 依赖。

    用于: :class:`~core.processors.contradiction_detector.ContradictionDetector`
    的基于 Jaccard 的矛盾检测。

    Args:
        text: 待分词的输入文本。

    Returns:
        Token 列表（单个 CJK 字符 + 英文单词）。
    """
    tokens: list[str] = []
    for chunk in re.findall(r"[一-鿿]|[a-zA-Z]+", text.lower()):
        tokens.append(chunk)
    return tokens


def tokenize_keywords(text: str) -> set[str]:
    """空格 + 标点分割的关键词分词器。

    按空格和常见标点分割，过滤短 token
    （< 2 字符）和停用词。

    用于: :class:`~core.retrieval.knowledge_retriever.KnowledgeRetriever`
    的轻量级关键词匹配。

    Args:
        text: 待分词的输入文本。

    Returns:
        过滤后的关键词 token 集合（每个 >= 2 字符）。
    """
    tokens: set[str] = set()
    for token in re.split(r"[\s,.;:!?，。；：！？、]+", text.lower()):
        token = token.strip()
        if token and len(token) >= 2 and token not in _KNOWLEDGE_STOPWORDS:
            tokens.add(token)
    return tokens


__all__ = ["tokenize_bigrams", "tokenize_cjk_words", "tokenize_keywords"]
