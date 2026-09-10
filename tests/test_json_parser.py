"""json_parser.py 测试 — JsonParser。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.features.recall.processors.json_parser import JsonParser, SummaryParseError
from core.features.recall.processors.quality_validator import QualityValidator


class TestJsonParserFix:
    def test_removes_markdown_code_block(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = JsonParser.try_fix_json(text)
        assert "```" not in result

    def test_removes_triple_backtick(self) -> None:
        text = '```\n{"key": "value"}\n```'
        result = JsonParser.try_fix_json(text)
        assert "```" not in result

    def test_fixes_trailing_comma(self) -> None:
        text = '{"key": "value",}'
        result = JsonParser.try_fix_json(text)
        assert ",}" not in result

    def test_fixes_unmatched_brace(self) -> None:
        text = '{"key": "value"'
        result = JsonParser.try_fix_json(text)
        assert result.endswith("}")

    def test_fixes_unmatched_bracket(self) -> None:
        text = '["item1", "item2"'
        result = JsonParser.try_fix_json(text)
        assert result.endswith("]")

    def test_fixes_unmatched_quote(self) -> None:
        text = '{"key": "value}'
        result = JsonParser.try_fix_json(text)
        # Extra quote added
        assert result.count('"') % 2 == 0

    def test_removes_trailing_comma_before_brace(self) -> None:
        text = '{"items": [1, 2,]}'
        result = JsonParser.try_fix_json(text)
        assert ",]" not in result


class TestStrictSummaryJsonParser:
    """严格总结解析只接受完整 JSON 对象外壳。"""

    @pytest.fixture
    def parser(self) -> JsonParser:
        """构造严格解析测试对象。"""

        return JsonParser(QualityValidator())

    @pytest.mark.parametrize(
        "response",
        [
            '{"memories": [],}',
            'prefix {"memories": []}',
            '[{"memories": []}]',
            "{}",
            '{"memories": "none"}',
            '{"memories": [{"summary": "稳定事实", "key_facts": ["事实"], "importance": "0.8"}]}',
        ],
    )
    def test_rejects_repair_extraction_and_invalid_shapes(
        self,
        parser: JsonParser,
        response: str,
    ) -> None:
        """严格路径不得修复、提取或补齐无效总结结构。"""

        with pytest.raises(SummaryParseError, match="summary_invalid"):
            parser.parse_summary_response(response)

    @pytest.mark.parametrize(
        "response",
        [
            '{"memories": []}',
            '```json\n{"memories": []}\n```',
            '```\n{"memories": []}\n```',
        ],
    )
    def test_accepts_empty_memories_with_optional_code_fence(
        self,
        parser: JsonParser,
        response: str,
    ) -> None:
        assert parser.parse_summary_response(response).memories == []


def test_strict_summary_accepts_integer_numeric_fields() -> None:
    """JSON 整数形式的合法数值仍属于严格总结的有效输出。"""

    result = JsonParser(QualityValidator()).parse_summary_response(
        '{"memories":[{"summary":"这是稳定事实","key_facts":["事实内容"],'
        '"importance":1,"confidence":0}]}'
    )

    assert result.memories[0].importance == 1
    assert result.memories[0].confidence == 0


class TestJsonParser:
    @pytest.fixture
    def parser(self) -> JsonParser:
        return JsonParser(QualityValidator())

    def test_parse_valid_json_private_chat(self, parser: JsonParser) -> None:
        response = '{"summary": "测试摘要", "topics": ["测试"], "key_facts": ["事实1"], "sentiment": "positive", "importance": 0.8}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "测试摘要"
        assert "测试" in result["topics"]
        assert result["sentiment"] == "positive"
        assert result["importance"] == 0.8

    def test_parse_valid_json_group_chat(self, parser: JsonParser) -> None:
        response = '{"summary": "群聊摘要", "topics": ["话题"], "key_facts": ["事实"], "sentiment": "neutral", "importance": 0.5, "participants": ["张三", "李四"]}'
        result = parser.parse_llm_response(response, is_group_chat=True)
        assert result["summary"] == "群聊摘要"
        assert "张三" in result["participants"]

    def test_parse_broken_json_falls_back_to_defaults(self, parser: JsonParser) -> None:
        response = "这不是JSON格式的输出"
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "对话记录"

    def test_parse_with_markdown_code_block(self, parser: JsonParser) -> None:
        response = '```json\n{"summary": "code block内", "topics": ["code"], "key_facts": ["f1"], "sentiment": "neutral", "importance": 0.6}\n```'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "code block内"

    def test_parse_missing_fields_fills_defaults(self, parser: JsonParser) -> None:
        response = '{"summary": "只有摘要", "key_facts": ["f1"]}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["topics"] == []
        assert result["sentiment"] == "neutral"
        assert result["importance"] == 0.5

    def test_parse_truncates_long_lists(self, parser: JsonParser) -> None:
        response = '{"summary": "s", "topics": ["t1","t2","t3","t4","t5","t6"], "key_facts": ["f1","f2","f3","f4","f5","f6"], "sentiment": "neutral", "importance": 0.5}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert len(result["topics"]) <= 5
        assert len(result["key_facts"]) <= 5

    def test_parse_invalid_sentiment_normalized(self, parser: JsonParser) -> None:
        response = '{"summary": "s", "topics": ["t"], "key_facts": ["f"], "sentiment": "happy", "importance": 0.5}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["sentiment"] == "neutral"

    def test_parse_invalid_importance_clamped(self, parser: JsonParser) -> None:
        response = '{"summary": "s", "topics": ["t"], "key_facts": ["f"], "sentiment": "neutral", "importance": 2.5}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["importance"] == 1.0

    def test_parse_not_a_dict(self, parser: JsonParser) -> None:
        response = '["this is an array, not a dict"]'
        result = parser.parse_llm_response(response, is_group_chat=False)
        # Falls through to regex/default
        assert isinstance(result, dict)

    def test_parse_unicode_json(self, parser: JsonParser) -> None:
        response = '{"summary": "中文摘要", "topics": ["话题"], "key_facts": ["事实"], "sentiment": "positive", "importance": 0.7}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "中文摘要"

    def test_try_fix_json_newline_escaping(self) -> None:
        text = '{"summary": "line1\\nline2"}'
        result = JsonParser.try_fix_json(text)
        assert "\\n" in result

    def test_try_fix_json_no_changes_needed(self) -> None:
        text = '{"key": "value"}'
        result = JsonParser.try_fix_json(text)
        assert "key" in result

    def test_parse_with_only_triple_backtick_start(self) -> None:
        """Test parse when only ``` at start, no json tag."""
        parser = JsonParser(QualityValidator())
        response = '```\n{"summary": "test", "topics": ["t"], "key_facts": ["f"], "sentiment": "neutral", "importance": 0.5}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "test"

    def test_parse_regex_extracts_fields(self) -> None:
        """Test regex path when the raw JSON can't be parsed as a whole but individual JSON fields exist."""
        parser = JsonParser(QualityValidator())
        # The regex `"summary"\s*:\s*"([^"]+)"` looks for actual JSON key format
        response = 'some garbage {"summary": "测试摘要", "topics": ["t1"]} more garbage'
        result = parser.parse_llm_response(response, is_group_chat=False)
        # The regex path finds the summary field
        assert result["summary"] == "测试摘要"

    def test_parse_regex_extracts_importance_and_sentiment(self) -> None:
        parser = JsonParser(QualityValidator())
        response = (
            'broken {"summary": "partial", "importance": 0.75, "sentiment": "negative"'
        )
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["importance"] == 0.75
        assert result["sentiment"] == "negative"

    def test_parse_regex_finds_deep_json(self) -> None:
        """Test regex finds JSON buried in text."""
        parser = JsonParser(QualityValidator())
        response = 'Some text here\n{"summary": "深度摘要", "key_facts": ["f1"], "sentiment": "negative", "importance": 0.3}\nMore text'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "深度摘要"

    def test_parse_with_quality_validator_none(self) -> None:
        parser = JsonParser(None)
        response = '{"summary": "test", "topics": ["t"], "key_facts": ["f"], "sentiment": "positive", "importance": 0.6}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "test"

    def test_parse_unexpected_exception_falls_to_default(self) -> None:
        """Test that unexpected exceptions during parsing return default data."""
        parser = JsonParser(QualityValidator())
        # Something that causes a non-JSON/non-ValueError exception during parsing
        with patch(
            "core.features.recall.processors.json_parser.json.loads",
            side_effect=TypeError("unexpected type error"),
        ):
            result = parser.parse_llm_response('{"key": "value"}', is_group_chat=False)
            assert isinstance(result, dict)

    def test_try_fix_json_unbalanced_brackets_and_braces(self) -> None:
        text = '{"items": ["a", "b"'
        result = JsonParser.try_fix_json(text)
        assert result.endswith("]}")
        assert result.count("[") == result.count("]")
        assert result.count("{") == result.count("}")

    def test_parse_fix_success_then_normalize(self) -> None:
        """Test that after successful fix, normalize_parsed_data is called."""
        parser = JsonParser(QualityValidator())
        response = '{"summary": "fixed", "topics": ["t"], "key_facts": ["f"], "sentiment": "happy", "importance": 2.0}'
        result = parser.parse_llm_response(response, is_group_chat=False)
        assert result["summary"] == "fixed"
        assert result["sentiment"] == "neutral"  # normalized
        assert result["importance"] == 1.0  # clamped

    def test_parse_group_chat_missing_participants(self) -> None:
        parser = JsonParser(QualityValidator())
        response = '{"summary": "s", "topics": ["t"], "key_facts": ["f"], "sentiment": "neutral", "importance": 0.5}'
        result = parser.parse_llm_response(response, is_group_chat=True)
        assert "participants" in result

    def test_parse_nested_memories_promotes_first_candidate(
        self, parser: JsonParser
    ) -> None:
        """嵌套 memories 回退解析时应保留候选并填充兼容顶层字段。"""
        response = (
            '{"memories":[{"summary":"张三讨论项目计划",'
            '"topics":["项目"],"key_facts":["张三有项目计划"],'
            '"sentiment":"neutral","importance":0.7}]}'
        )

        result = parser.parse_llm_response(response, is_group_chat=False)

        assert result["summary"] == "张三讨论项目计划"
        assert result["topics"] == ["项目"]
        assert result["key_facts"] == ["张三有项目计划"]
        assert result["importance"] == 0.7
        assert len(result["memories"]) == 1

    def test_repaired_nested_memories_promote_first_candidate(
        self, parser: JsonParser
    ) -> None:
        """修复 JSON 的 memories[] 也应填充兼容顶层字段。"""
        response = (
            '{"memories":[{"summary":"张三讨论项目计划",'
            '"topics":["项目"],"key_facts":["张三有项目计划"],'
            '"sentiment":"neutral","importance":0.7,}]}'
        )

        result = parser.parse_llm_response(response, is_group_chat=False)

        assert result["summary"] == "张三讨论项目计划"
        assert result["topics"] == ["项目"]
        assert result["key_facts"] == ["张三有项目计划"]
        assert result["importance"] == 0.7
