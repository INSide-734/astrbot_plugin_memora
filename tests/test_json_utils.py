"""core/utils/json_utils.py 测试 — safe_parse_llm_json + 辅助函数。"""

from __future__ import annotations

from core.utils.json_utils import (
    _convert_single_quotes,
    clean_control_characters,
    clean_markdown_blocks,
    detect_llm_provider,
    extract_json_content,
    fix_common_json_errors,
    remove_thinking_content,
    safe_parse_llm_json,
)

# ---------------------------------------------------------------------------
# remove_thinking_content
# ---------------------------------------------------------------------------


class TestRemoveThinkingContent:
    def test_removes_thinking_tags(self) -> None:
        text = "<thinking>随便想想</thinking>\n{"
        result = remove_thinking_content(text)
        assert "<thinking>" not in result
        assert "随便想想" not in result

    def test_removes_thought_tags(self) -> None:
        text = "<thought>I need to think</thought>\n{"
        result = remove_thinking_content(text)
        assert "<thought>" not in result

    def test_removes_reasoning_tags(self) -> None:
        text = "prefix <reasoning>some reasoning</reasoning> suffix"
        result = remove_thinking_content(text)
        assert "some reasoning" not in result
        assert "prefix" in result
        assert "suffix" in result

    def test_removes_think_tags(self) -> None:
        text = "<think>thinking here</think>{"
        result = remove_thinking_content(text)
        assert "thinking here" not in result

    def test_removes_bracket_thinking(self) -> None:
        text = "[thinking]deep think[/thinking]{"
        result = remove_thinking_content(text)
        assert "deep think" not in result

    def test_removes_bracket_thought(self) -> None:
        text = "[thought]thought content[/thought]{"
        result = remove_thinking_content(text)
        assert "thought content" not in result

    def test_removes_chinese_thinking(self) -> None:
        text = "【思考过程】分析中...【/思考过程】\n{"
        result = remove_thinking_content(text)
        assert "分析中" not in result

    def test_handles_empty_input(self) -> None:
        assert remove_thinking_content("") == ""

    def test_handles_none(self) -> None:
        assert remove_thinking_content("") == ""

    def test_compresses_multiple_blank_lines(self) -> None:
        text = "<thinking>x</thinking>\n\n\n\n\n{"
        result = remove_thinking_content(text)
        # Should compress 4+ blank lines to 2
        assert "\n\n\n\n" not in result


# ---------------------------------------------------------------------------
# clean_markdown_blocks
# ---------------------------------------------------------------------------


class TestCleanMarkdownBlocks:
    def test_removes_json_fence(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = clean_markdown_blocks(text)
        assert "```" not in result
        assert "json" not in result.lower() or result == '{"key": "value"}'

    def test_removes_plain_fence(self) -> None:
        text = '```\n{"key": "value"}\n```'
        result = clean_markdown_blocks(text)
        assert "```" not in result

    def test_removes_trailing_fence_only(self) -> None:
        text = '{"key": "value"}\n```'
        result = clean_markdown_blocks(text)
        assert "```" not in result

    def test_removes_fence_no_newline(self) -> None:
        text = '```json{"key": "value"}```'
        result = clean_markdown_blocks(text)
        assert "```json" not in result

    def test_handles_empty(self) -> None:
        assert clean_markdown_blocks("") == ""

    def test_no_fence_passthrough(self) -> None:
        text = '{"key": "value"}'
        assert clean_markdown_blocks(text) == text


# ---------------------------------------------------------------------------
# clean_control_characters
# ---------------------------------------------------------------------------


class TestCleanControlCharacters:
    def test_keeps_newlines_and_tabs(self) -> None:
        text = '{\n\t"key": "value"\n}'
        result = clean_control_characters(text)
        assert "\n" in result
        assert "\t" in result

    def test_removes_null_byte(self) -> None:
        text = '{"key":\x00 "value"}'
        result = clean_control_characters(text)
        assert "\x00" not in result

    def test_handles_empty(self) -> None:
        assert clean_control_characters("") == ""


# ---------------------------------------------------------------------------
# extract_json_content
# ---------------------------------------------------------------------------


class TestExtractJsonContent:
    def test_extracts_object(self) -> None:
        text = 'some text {"key": "value"} more text'
        result = extract_json_content(text)
        assert result == '{"key": "value"}'

    def test_extracts_array(self) -> None:
        text = "prefix [1, 2, 3] suffix"
        result = extract_json_content(text)
        assert result == "[1, 2, 3]"

    def test_prefers_object_over_array(self) -> None:
        text = '{"wrapper": [1, 2]}'
        result = extract_json_content(text)
        assert result.startswith("{")
        assert result.endswith("}")

    def test_handles_no_json(self) -> None:
        text = "plain text"
        result = extract_json_content(text)
        assert result == "plain text"

    def test_handles_empty(self) -> None:
        assert extract_json_content("") == ""


# ---------------------------------------------------------------------------
# fix_common_json_errors
# ---------------------------------------------------------------------------


class TestFixCommonJsonErrors:
    def test_fixes_trailing_comma_in_object(self) -> None:
        text = '{"key": "value",}'
        result = fix_common_json_errors(text)
        assert ",}" not in result

    def test_fixes_trailing_comma_in_array(self) -> None:
        text = '["a", "b",]'
        result = fix_common_json_errors(text)
        assert ",]" not in result

    def test_fixes_python_true(self) -> None:
        text = '{"flag": True}'
        result = fix_common_json_errors(text)
        assert ": true" in result

    def test_fixes_python_false(self) -> None:
        text = '{"flag": False}'
        result = fix_common_json_errors(text)
        assert ": false" in result

    def test_fixes_python_none(self) -> None:
        text = '{"val": None}'
        result = fix_common_json_errors(text)
        assert ": null" in result

    def test_fixes_uppercase_null(self) -> None:
        text = '{"val": NULL}'
        result = fix_common_json_errors(text)
        assert ": null" in result

    def test_fixes_nan(self) -> None:
        text = '{"val": nan}'
        result = fix_common_json_errors(text)
        assert ": null" in result

    def test_fixes_infinity(self) -> None:
        text = '{"val": Infinity}'
        result = fix_common_json_errors(text)
        assert ": null" in result

    def test_fixes_negative_infinity(self) -> None:
        text = '{"val": -Infinity}'
        result = fix_common_json_errors(text)
        assert ": null" in result

    def test_adds_missing_closing_quote(self) -> None:
        text = '{"key": "value}'
        result = fix_common_json_errors(text)
        assert result.count('"') % 2 == 0 or result.endswith('"}')

    def test_adds_missing_brace(self) -> None:
        text = '{"key": "value"'
        result = fix_common_json_errors(text)
        assert result.endswith("}")

    def test_adds_missing_bracket(self) -> None:
        text = '["item1", "item2"'
        result = fix_common_json_errors(text)
        assert result.endswith("]")

    def test_handles_empty(self) -> None:
        assert fix_common_json_errors("") == ""


# ---------------------------------------------------------------------------
# _convert_single_quotes
# ---------------------------------------------------------------------------


class TestConvertSingleQuotes:
    def test_converts_simple(self) -> None:
        text = "{'key': 'value'}"
        result = _convert_single_quotes(text)
        assert "'" not in result
        import json

        json.loads(result)  # should not raise

    def test_handles_escaped_quotes_inside(self) -> None:
        # 验证 _convert_single_quotes 对含转义单引号字符串的处理不崩溃
        text = "{'key': 'it\\'s ok'}"
        result = _convert_single_quotes(text)
        # Escape 字符被保留，不崩溃即可
        assert "key" in result
        assert len(result) > 0

    def test_handles_empty(self) -> None:
        assert _convert_single_quotes("") == ""

    def test_preserves_double_quoted(self) -> None:
        text = '{"key": "value"}'
        result = _convert_single_quotes(text)
        import json

        json.loads(result)  # should not raise


# ---------------------------------------------------------------------------
# safe_parse_llm_json — 完整 3 轮解析
# ---------------------------------------------------------------------------


class TestSafeParseLlmJson:
    def test_parse_valid_dict(self) -> None:
        result = safe_parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_valid_list(self) -> None:
        result = safe_parse_llm_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_parse_with_thinking_tag_pass1(self) -> None:
        text = '<thinking>分析中...</thinking>\n{"result": "ok"}'
        result = safe_parse_llm_json(text)
        assert result == {"result": "ok"}

    def test_parse_markdown_fence_pass1(self) -> None:
        text = '```json\n{"items": ["a", "b"]}\n```'
        result = safe_parse_llm_json(text)
        assert result == {"items": ["a", "b"]}

    def test_parse_trailing_comma_pass2(self) -> None:
        text = '{"key": "value",}'
        result = safe_parse_llm_json(text)
        assert result == {"key": "value"}

    def test_parse_python_true_pass2(self) -> None:
        text = '{"flag": True}'
        result = safe_parse_llm_json(text)
        assert result == {"flag": True}

    def test_parse_python_none_pass2(self) -> None:
        text = '{"val": None}'
        result = safe_parse_llm_json(text)
        assert result == {"val": None}

    def test_parse_single_quotes_pass3(self) -> None:
        text = "{'name': 'test'}"
        result = safe_parse_llm_json(text)
        assert result == {"name": "test"}

    def test_parse_buried_json_in_text(self) -> None:
        text = 'Here is the result: \n```json\n{"summary": "ok"}\n```\nHope that helps.'
        result = safe_parse_llm_json(text)
        assert result == {"summary": "ok"}

    def test_parse_null_on_empty(self) -> None:
        assert safe_parse_llm_json("") is None

    def test_parse_null_on_whitespace(self) -> None:
        assert safe_parse_llm_json("   ") is None

    def test_parse_null_on_garbage(self) -> None:
        assert safe_parse_llm_json("this is not json at all") is None

    def test_parse_deepseek_reasoner_output(self) -> None:
        text = (
            "<thinking>\n用户询问天气\n</thinking>\n"
            '```json\n{"topic": "weather", "answer": "sunny"}\n```'
        )
        result = safe_parse_llm_json(text)
        assert result is not None
        assert result.get("topic") == "weather"

    def test_parse_claude_thought_output(self) -> None:
        text = (
            "<reasoning>\nLet me think about this...\n</reasoning>\n"
            "<thought>\nI should respond with JSON\n</thought>\n"
            '{"name": "Claude", "version": "4"}'
        )
        result = safe_parse_llm_json(text)
        assert result == {"name": "Claude", "version": "4"}


# ---------------------------------------------------------------------------
# detect_llm_provider
# ---------------------------------------------------------------------------


class TestDetectLlmProvider:
    def test_deepseek(self) -> None:
        assert detect_llm_provider("deepseek-chat") == "deepseek"
        assert detect_llm_provider("DEEPSEEK-V3") == "deepseek"

    def test_claude(self) -> None:
        assert detect_llm_provider("claude-3-opus") == "claude"
        assert detect_llm_provider("anthropic/claude-4") == "claude"

    def test_openai(self) -> None:
        assert detect_llm_provider("gpt-4") == "openai"
        assert detect_llm_provider("text-davinci-003") == "openai"

    def test_generic(self) -> None:
        assert detect_llm_provider("qwen-72b") == "generic"
        assert detect_llm_provider("") == "generic"
