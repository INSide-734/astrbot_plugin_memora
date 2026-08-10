"""向后兼容导出可观测性 feature 的记忆质量评分服务。"""

from ..features.observability.application import quality_scorer as _owner
from ..features.observability.application.quality_scorer import (
    AlertLevel,
    MemoryQualityScorer,
    QualityAlert,
    QualityScore,
)

# 旧测试路径仍显式导入这些模块私有符号，不将其扩大为正式公开 API。
_SOURCE_RELEVANCE_WEIGHT = _owner._SOURCE_RELEVANCE_WEIGHT
_SOURCE_RELIABILITY = _owner._SOURCE_RELIABILITY
_count_connectors = _owner._count_connectors
_count_segments = _owner._count_segments
_has_contradictory_sentiment = _owner._has_contradictory_sentiment
_has_multiple_sentences = _owner._has_multiple_sentences
_has_paragraph_breaks = _owner._has_paragraph_breaks
_has_url = _owner._has_url
_text_to_simple_embedding = _owner._text_to_simple_embedding
_tokenize = _owner._tokenize

__all__ = ["AlertLevel", "MemoryQualityScorer", "QualityAlert", "QualityScore"]
