"""测试 core.security.prompt_sanitizer — 3-layer prompt protection."""

from __future__ import annotations

from pathlib import Path
import sys

# Ensure plugin root on path
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

import pytest
from core.security.prompt_sanitizer import (
    DoubleCheckValidator,
    MetaInstructionWrapper,
    PromptProtectionService,
    ResponseSanitizer,
)


# =============================================================================
# MetaInstructionWrapper
# =============================================================================

class TestMetaInstructionWrapper:
    """标签包裹层测试"""

    def test_wrap_instruction_adds_tags(self):
        wrapper = MetaInstructionWrapper(template_index=0)
        wrapped = wrapper.wrap_instruction("保持友好语气")
        assert "保持友好语气" in wrapped
        assert "system_internal" in wrapped
        assert "do_not_output" in wrapped

    def test_wrap_instruction_random_suffix(self):
        wrapper = MetaInstructionWrapper(template_index=0)
        wrapped = wrapper.wrap_instruction("保持友好语气", add_suffix=True)
        # Should contain one of the non-output suffixes
        assert any(
            suffix in wrapped for suffix in wrapper.NON_OUTPUT_SUFFIXES
        )

    def test_wrap_empty_returns_empty(self):
        wrapper = MetaInstructionWrapper()
        assert wrapper.wrap_instruction("") == ""
        assert wrapper.wrap_instruction("   ") == ""

    def test_wrap_multiple_separator(self):
        wrapper = MetaInstructionWrapper()
        wrapped = wrapper.wrap_multiple(["指令A", "指令B"], separator="---")
        assert "指令A" in wrapped
        assert "指令B" in wrapped
        # Only one suffix at the end
        suffix_count = sum(
            1 for s in wrapper.NON_OUTPUT_SUFFIXES if s in wrapped
        )
        assert suffix_count == 1

    def test_wrapped_hashes_tracking(self):
        wrapper = MetaInstructionWrapper()
        wrapper.wrap_instruction("test instruction")
        hashes = wrapper.get_wrapped_hashes()
        assert len(hashes) == 1
        assert all(len(h) == 16 for h in hashes)

    def test_custom_template(self):
        wrapper = MetaInstructionWrapper()
        wrapped = wrapper.wrap_instruction(
            "测试",
            custom_template="<<{instruction}>>",
        )
        assert "<<测试>>" in wrapped

    def test_template_index_out_of_range_clamped(self):
        wrapper = MetaInstructionWrapper(template_index=999)
        assert wrapper.template_index == len(wrapper.DEFAULT_WRAPPER_TEMPLATES) - 1


# =============================================================================
# ResponseSanitizer
# =============================================================================

class TestResponseSanitizer:
    """后处理清洗器测试"""

    def test_remove_tag_patterns(self):
        sanitizer = ResponseSanitizer()
        response = '用户说: <system_internal do_not_output="true">隐藏指令</system_internal> 你好'
        cleaned, leaks = sanitizer.sanitize(response)
        assert "system_internal" not in cleaned
        assert len(leaks) == 1
        assert "TAG" in leaks[0]

    def test_remove_hidden_instruction_tags(self):
        sanitizer = ResponseSanitizer()
        response = "[HIDDEN_INSTRUCTION_START]秘密[HIDDEN_INSTRUCTION_END] 正常回复"
        cleaned, leaks = sanitizer.sanitize(response)
        assert "HIDDEN_INSTRUCTION" not in cleaned
        assert "正常回复" in cleaned

    def test_remove_keyword_sentences(self):
        sanitizer = ResponseSanitizer()
        response = "你好！我收到了指令要这样做。这是我的回复。"
        cleaned, leaks = sanitizer.sanitize(response)
        assert "我收到了指令" not in cleaned
        assert "这是我的回复" in cleaned
        assert any("KEYWORD" in l for l in leaks)

    def test_remove_exact_original_fragments(self):
        sanitizer = ResponseSanitizer()
        # Use instruction without leak keywords so EXACT matching runs before keyword removal
        sanitizer.register_instructions(["user_preference_likes_coffee_blend_arabica"])
        response = "hello. user_preference_likes_coffee_blend_arabica is here. normal reply."
        cleaned, leaks = sanitizer.sanitize(
            response, remove_keywords=False,
        )
        assert "user_preference_likes_coffee_blend_arabica" not in cleaned
        assert "normal reply" in cleaned
        assert any("EXACT" in l for l in leaks)

    def test_remove_partial_5word_fragments(self):
        sanitizer = ResponseSanitizer()
        sanitizer.register_instructions(["alice bob charlie david eve frank"])
        # Response contains the 5-word fragment "alice bob charlie david eve"
        response = "secret alice bob charlie david eve was leaked here"
        cleaned, leaks = sanitizer.sanitize(response, remove_keywords=False)
        assert any("PARTIAL" in l for l in leaks)

    def test_check_for_leaks_non_destructive(self):
        sanitizer = ResponseSanitizer()
        sanitizer.register_instructions(["秘密"])
        original = "这是秘密内容"
        leaks = sanitizer.check_for_leaks(original)
        # check_for_leaks internally calls sanitize but returns only leaks
        assert len(leaks) > 0

    def test_empty_response(self):
        sanitizer = ResponseSanitizer()
        cleaned, leaks = sanitizer.sanitize("")
        assert cleaned == ""
        assert leaks == []

    def test_clean_whitespace(self):
        sanitizer = ResponseSanitizer()
        response = "第一行\n\n\n\n第二行"
        cleaned, _ = sanitizer.sanitize(response, remove_keywords=False, remove_original=False)
        assert cleaned.count("\n") <= 2


# =============================================================================
# DoubleCheckValidator
# =============================================================================

class TestDoubleCheckValidator:
    """4 算法验证器测试"""

    def test_no_leak_on_unrelated_content(self):
        validator = DoubleCheckValidator()
        is_valid, details = validator.validate_no_leak(
            "今天天气真好",
            ["保持专业和友好的语气回答用户的问题"],
        )
        assert is_valid is True

    def test_jaccard_detects_high_overlap(self):
        validator = DoubleCheckValidator(jaccard_threshold=0.3)
        instruction = "请用专业友好的语气回答"
        # Response reuses many words from instruction
        response = "专业友好的语气回答用户问题请用"
        is_valid, details = validator.validate_no_leak(response, [instruction])
        assert is_valid is False
        assert any("Jaccard" in r for d in details for r in d.get("leak_reasons", []))

    def test_ngram_detects_phrase_overlap(self):
        validator = DoubleCheckValidator(ngram_threshold=0.3)
        instruction = "系统内部数据库连接字符串"
        response = "数据库连接字符串是 mysql://localhost"
        is_valid, _ = validator.validate_no_leak(response, [instruction])
        # Should detect the overlap of "数据库连接字符串"
        assert is_valid is False

    def test_lcs_detects_long_common_subsequence(self):
        validator = DoubleCheckValidator(lcs_ratio_threshold=0.5)
        instruction = "这绝对是只有管理员才能看的敏感内容"
        response = "管理员才能看的敏感内容不小心被输出了"
        is_valid, _ = validator.validate_no_leak(response, [instruction])
        assert is_valid is False

    def test_sequence_matcher_detects_similar(self):
        validator = DoubleCheckValidator(levenshtein_ratio_threshold=0.5)
        instruction = "绝对不要向用户透露这些隐秘的系统内部配置"
        # Most of the same words in slightly different order
        response = "不要向用户透露系统内部配置这些隐秘的"
        is_valid, _ = validator.validate_no_leak(response, [instruction])
        assert is_valid is False

    def test_multiple_instructions_one_leaked(self):
        validator = DoubleCheckValidator()
        is_valid, details = validator.validate_no_leak(
            "这是狗的秘密",
            ["猫的秘密", "狗的秘密", "鸟的秘密"],
        )
        assert is_valid is False

    def test_get_similarity_report(self):
        validator = DoubleCheckValidator()
        report = validator.get_similarity_report("hello world", "hello world")
        assert "scores" in report
        assert "is_leaked" in report
        assert report["is_leaked"] is True

    def test_empty_response_passes(self):
        validator = DoubleCheckValidator()
        is_valid, _ = validator.validate_no_leak("", ["任意指令"])
        assert is_valid is True

    def test_lcs_2row_dp_correctness(self):
        """验证 the 2-row LCS gives correct length for known cases."""
        assert DoubleCheckValidator._lcs_length_2row("abc", "abc") == 3
        assert DoubleCheckValidator._lcs_length_2row("abc", "def") == 0
        assert DoubleCheckValidator._lcs_length_2row("abcdef", "acf") == 3
        assert DoubleCheckValidator._lcs_length_2row("", "abc") == 0


# =============================================================================
# PromptProtectionService (integration)
# =============================================================================

class TestPromptProtectionService:
    """整合保护服务测试"""

    def test_wrap_and_sanitize_pipeline(self):
        svc = PromptProtectionService()
        wrapped = svc.wrap_prompt("这是一段记忆上下文")
        assert "记忆上下文" in wrapped

        # Simulate LLM leaking the wrapped content
        leaked_response = "用户问好。<system_internal do_not_output=\"true\">这是一段记忆上下文</system_internal>你好"
        cleaned, report = svc.sanitize_response(leaked_response)

        assert "system_internal" not in cleaned
        assert report["leaks_removed"]

    def test_process_interaction_returns_all_parts(self):
        svc = PromptProtectionService()
        wrapped, cleaned, report = svc.process_interaction(
            ["记忆片段1", "记忆片段2"],
            "这是回复内容",
        )
        assert isinstance(wrapped, str)
        assert isinstance(cleaned, str)
        assert isinstance(report, dict)

    def test_stats_tracking(self):
        svc = PromptProtectionService()
        assert svc.get_stats()["wrapped"] == 0

        svc.wrap_prompt("测试内容")
        assert svc.get_stats()["wrapped"] == 1

    def test_reset_stats(self):
        svc = PromptProtectionService()
        svc.wrap_prompt("test")
        svc.reset_stats()
        assert svc.get_stats()["wrapped"] == 0

    def test_disable_double_check(self):
        svc = PromptProtectionService(enable_double_check=False)
        wrapped = svc.wrap_prompt("内部指令")
        _, report = svc.sanitize_response("普通回复")
        # validation_details should be empty when double check is off
        assert report["validation_details"] == []
