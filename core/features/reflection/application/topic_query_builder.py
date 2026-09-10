"""Topic 候选查询构造器：唯一的 FTS query 构造与截断逻辑。

从 BM25Retriever 提取可复用的 query/token/escaping 逻辑为独立纯函数，
供 TopicCandidateSelector 使用，避免重复实现和不一致的截断规则。
"""

from __future__ import annotations

from dataclasses import dataclass

from ....shared.sql import build_fts5_or_query
from ...recall.processors.text_processor import TextProcessor


@dataclass(frozen=True, slots=True)
class TopicQueryResult:
    """查询构造结果；空 query 或全停用词时 fts_query 为 None。"""

    tokens: tuple[str, ...] = ()
    """规范化后的 tokens；可能为空。"""

    fts_query: str | None = None
    """FTS5 查询字符串；None 表示无命中或全停用词。"""

    truncated: bool = False
    """是否因为超出字符/term 上限而截断。"""

    reason_code: str = ""
    """截断或失败的固定 reason code。"""


_MAX_QUERY_CHARS = 2000
"""查询文本的最大字符数（计划默认值，实际由配置控制）。"""

_MAX_TERMS = 100
"""FTS query 的最大 term 数（防止 SQLite 解析器超限）。"""


async def build_topic_fts_query(
    text: str,
    text_processor: TextProcessor,
    *,
    max_chars: int = _MAX_QUERY_CHARS,
    max_terms: int = _MAX_TERMS,
) -> TopicQueryResult:
    """从原始文本构造 FTS5 query，复用 TextProcessor token 化和停用词。

    Args:
        text: 原始查询文本（通常是当前窗口全文）
        text_processor: TextProcessor 实例
        max_chars: 最大查询字符数；超出时确定性前缀截断
        max_terms: 最大 term 数；超出时确定性前缀截断

    Returns:
        TopicQueryResult: 包含 tokens、fts_query、truncated、reason_code

    Notes:
        - 空文本或全停用词返回 fts_query=None
        - 截断使用确定性前缀规则，不做"重要位置"启发式
        - FTS query 使用 OR 连接 quoted tokens
        - 转义规则：`"` → `""`
    """
    if not text or not text.strip():
        return TopicQueryResult(reason_code="empty_query")

    # 前缀截断到最大字符数
    truncated_by_chars = False
    if len(text) > max_chars > 0:
        text = text[:max_chars]
        truncated_by_chars = True

    # 异步 token 化和停用词过滤
    tokens_list = await text_processor.tokenize_async(text, remove_stopwords=True)
    if not tokens_list:
        return TopicQueryResult(
            truncated=truncated_by_chars,
            reason_code="all_stopwords"
            if not truncated_by_chars
            else "truncated_all_stopwords",
        )

    # 前缀截断到最大 term 数
    truncated_by_terms = False
    if len(tokens_list) > max_terms > 0:
        tokens_list = tokens_list[:max_terms]
        truncated_by_terms = True

    fts_query = build_fts5_or_query(tokens_list)

    truncated = truncated_by_chars or truncated_by_terms
    reason_code = ""
    if truncated_by_chars and truncated_by_terms:
        reason_code = "truncated_chars_and_terms"
    elif truncated_by_chars:
        reason_code = "truncated_chars"
    elif truncated_by_terms:
        reason_code = "truncated_terms"

    return TopicQueryResult(
        tokens=tuple(tokens_list),
        fts_query=fts_query,
        truncated=truncated,
        reason_code=reason_code,
    )


def validate_query_config(
    max_chars: int | None,
    max_terms: int | None,
) -> tuple[int, int]:
    """校验并返回有效的查询配置上限。

    Args:
        max_chars: 配置的最大字符数
        max_terms: 配置的最大 term 数

    Returns:
        (有效 max_chars, 有效 max_terms)

    Raises:
        ValueError: 配置非法时抛出
    """
    if max_chars is None or max_chars <= 0:
        raise ValueError(f"max_chars 必须为正整数，当前: {max_chars}")
    if max_terms is None or max_terms <= 0:
        raise ValueError(f"max_terms 必须为正整数，当前: {max_terms}")
    if max_chars > 10000:
        raise ValueError(f"max_chars 不得超过 10000，当前: {max_chars}")
    if max_terms > 500:
        raise ValueError(f"max_terms 不得超过 500，当前: {max_terms}")
    return (max_chars, max_terms)
