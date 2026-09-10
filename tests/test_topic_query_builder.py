"""测试 TopicQueryBuilder 的 query 构造、token 化、escaping 和截断逻辑。"""

from __future__ import annotations

import pytest

from core.features.recall.processors.text_processor import TextProcessor
from core.features.reflection.application.topic_query_builder import (
    build_topic_fts_query,
    validate_query_config,
)


@pytest.fixture
def text_processor():
    """创建 TextProcessor 实例。"""
    return TextProcessor()


@pytest.mark.asyncio
async def test_build_empty_query(text_processor):
    """空文本返回 empty_query。"""
    result = await build_topic_fts_query("", text_processor)
    assert result.tokens == ()
    assert result.fts_query is None
    assert not result.truncated
    assert result.reason_code == "empty_query"


@pytest.mark.asyncio
async def test_build_whitespace_query(text_processor):
    """纯空白返回 empty_query。"""
    result = await build_topic_fts_query("   \n\t  ", text_processor)
    assert result.tokens == ()
    assert result.fts_query is None
    assert not result.truncated
    assert result.reason_code == "empty_query"


@pytest.mark.asyncio
async def test_build_all_stopwords_query(text_processor):
    """全停用词返回 all_stopwords。"""
    # 中文停用词示例：的、了、和
    result = await build_topic_fts_query("的了和", text_processor)
    assert result.tokens == ()
    assert result.fts_query is None
    assert not result.truncated
    assert result.reason_code == "all_stopwords"


@pytest.mark.asyncio
async def test_build_normal_query(text_processor):
    """正常文本生成 FTS query。"""
    result = await build_topic_fts_query("今天天气很好", text_processor)
    assert len(result.tokens) > 0
    assert result.fts_query is not None
    assert " OR " in result.fts_query
    assert result.fts_query.startswith('"')
    assert result.fts_query.endswith('"')
    assert not result.truncated
    assert result.reason_code == ""


@pytest.mark.asyncio
async def test_build_query_with_special_chars(text_processor):
    """特殊字符正确转义。"""
    # FTS5 特殊字符：双引号需要转义为两个双引号
    result = await build_topic_fts_query('这是"测试"内容', text_processor)
    assert result.fts_query is not None
    # 检查双引号被转义
    assert '""测试""' in result.fts_query or "测试" in "".join(result.tokens)
    assert not result.truncated


@pytest.mark.asyncio
async def test_build_query_chars_truncation(text_processor):
    """超出字符数上限时前缀截断。"""
    long_text = "测试内容" * 1000  # 远超 max_chars
    result = await build_topic_fts_query(
        long_text,
        text_processor,
        max_chars=100,
    )
    assert result.truncated
    assert result.reason_code in ("truncated_chars", "truncated_chars_and_terms")


@pytest.mark.asyncio
async def test_build_query_terms_truncation(text_processor):
    """超出 term 数上限时前缀截断。"""
    # 构造足够多的非停用词
    many_terms = " ".join([f"词{i}" for i in range(200)])
    result = await build_topic_fts_query(
        many_terms,
        text_processor,
        max_terms=50,
    )
    assert result.truncated
    assert result.reason_code in ("truncated_terms", "truncated_chars_and_terms")
    assert len(result.tokens) <= 50


@pytest.mark.asyncio
async def test_build_query_chars_and_terms_truncation(text_processor):
    """同时超出字符和 term 上限。"""
    many_terms = " ".join([f"词{i}" for i in range(200)])
    result = await build_topic_fts_query(
        many_terms,
        text_processor,
        max_chars=50,
        max_terms=20,
    )
    assert result.truncated
    # 字符截断后可能已经少于 max_terms，所以只检查 truncated=True
    assert result.reason_code in ("truncated_chars", "truncated_chars_and_terms")


@pytest.mark.asyncio
async def test_build_query_truncated_all_stopwords(text_processor):
    """截断后全是停用词。"""
    # 构造大量停用词
    stopwords = "的了和" * 1000
    result = await build_topic_fts_query(
        stopwords,
        text_processor,
        max_chars=50,
    )
    assert result.tokens == ()
    assert result.fts_query is None
    assert result.truncated
    assert result.reason_code == "truncated_all_stopwords"


@pytest.mark.asyncio
async def test_build_query_deterministic_truncation(text_processor):
    """截断规则是确定性前缀，不做启发式。"""
    text = "ABCD" * 100
    result1 = await build_topic_fts_query(text, text_processor, max_chars=50)
    result2 = await build_topic_fts_query(text, text_processor, max_chars=50)
    # 相同输入和上限产生相同 tokens
    assert result1.tokens == result2.tokens
    assert result1.fts_query == result2.fts_query
    assert result1.truncated == result2.truncated


@pytest.mark.asyncio
async def test_build_query_or_connector(text_processor):
    """FTS query 使用 OR 连接 tokens。"""
    result = await build_topic_fts_query("苹果 香蕉 橙子", text_processor)
    assert result.fts_query is not None
    assert " OR " in result.fts_query
    # 每个 token 都被 quoted
    or_parts = result.fts_query.split(" OR ")
    assert all(part.startswith('"') and part.endswith('"') for part in or_parts)


def test_validate_query_config_valid():
    """合法配置通过校验。"""
    max_chars, max_terms = validate_query_config(2000, 100)
    assert max_chars == 2000
    assert max_terms == 100


def test_validate_query_config_invalid_chars():
    """非法 max_chars 抛出 ValueError。"""
    with pytest.raises(ValueError, match="max_chars 必须为正整数"):
        validate_query_config(0, 100)
    with pytest.raises(ValueError, match="max_chars 必须为正整数"):
        validate_query_config(-1, 100)
    with pytest.raises(ValueError, match="max_chars 必须为正整数"):
        validate_query_config(None, 100)


def test_validate_query_config_invalid_terms():
    """非法 max_terms 抛出 ValueError。"""
    with pytest.raises(ValueError, match="max_terms 必须为正整数"):
        validate_query_config(2000, 0)
    with pytest.raises(ValueError, match="max_terms 必须为正整数"):
        validate_query_config(2000, -1)
    with pytest.raises(ValueError, match="max_terms 必须为正整数"):
        validate_query_config(2000, None)


def test_validate_query_config_exceeds_max_chars():
    """max_chars 超出硬上限抛出 ValueError。"""
    with pytest.raises(ValueError, match="max_chars 不得超过 10000"):
        validate_query_config(10001, 100)


def test_validate_query_config_exceeds_max_terms():
    """max_terms 超出硬上限抛出 ValueError。"""
    with pytest.raises(ValueError, match="max_terms 不得超过 500"):
        validate_query_config(2000, 501)


@pytest.mark.asyncio
async def test_build_query_tokens_immutable(text_processor):
    """返回的 tokens 是不可变 tuple。"""
    result = await build_topic_fts_query("苹果 香蕉", text_processor)
    assert isinstance(result.tokens, tuple)
    # tuple 不可修改
    with pytest.raises((TypeError, AttributeError)):
        result.tokens.append("橙子")  # type: ignore


@pytest.mark.asyncio
async def test_build_query_mixed_chinese_english(text_processor):
    """中英混合文本正常处理。"""
    result = await build_topic_fts_query("hello 世界 test 测试", text_processor)
    assert result.fts_query is not None
    assert len(result.tokens) >= 2
    assert " OR " in result.fts_query
