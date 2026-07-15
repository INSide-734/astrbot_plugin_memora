"""测试 core/utils/ — data_helpers, injection_adapter, memory_formatter,
number_utils, stopwords_manager, and __init__ functions."""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

# ---------------------------------------------------------------------------
# data_helpers tests
# ---------------------------------------------------------------------------
from core.utils.data_helpers import (
    OperationContext,
    retry_on_failure,
    safe_parse_metadata,
    safe_serialize_metadata,
    validate_timestamp,
)


class TestSafeParseMetadata:
    def test_returns_dict_as_is(self) -> None:
        d = {"key": "value", "nested": {"a": 1}}
        assert safe_parse_metadata(d) is d

    def test_parses_valid_json_string(self) -> None:
        result = safe_parse_metadata('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_returns_empty_on_invalid_json(self) -> None:
        result = safe_parse_metadata("not valid json")
        assert result == {}

    def test_returns_empty_on_none(self) -> None:
        assert safe_parse_metadata(None) == {}

    def test_returns_empty_on_int(self) -> None:
        assert safe_parse_metadata(42) == {}

    def test_returns_empty_on_empty_string(self) -> None:
        assert safe_parse_metadata("") == {}


class TestSafeSerializeMetadata:
    def test_serializes_simple_dict(self) -> None:
        result = safe_serialize_metadata({"a": 1, "b": "hello"})
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": "hello"}

    def test_handles_unicode(self) -> None:
        result = safe_serialize_metadata({"name": "测试"})
        assert "测试" in result

    def test_returns_empty_json_on_error(self) -> None:
        # A value that cannot be serialized
        result = safe_serialize_metadata({"bad": object()})
        assert result == "{}"

    def test_empty_dict(self) -> None:
        assert safe_serialize_metadata({}) == "{}"


class TestValidateTimestamp:
    def test_returns_float_as_is(self) -> None:
        assert validate_timestamp(1234567890.0) == 1234567890.0

    def test_returns_int_as_float(self) -> None:
        assert validate_timestamp(1234567890) == 1234567890.0

    def test_parses_numeric_string(self) -> None:
        assert validate_timestamp("1234567890.5") == 1234567890.5

    def test_unparseable_string_falls_back_to_default(self) -> None:
        result = validate_timestamp("not-a-number", 42.0)
        assert result == 42.0

    def test_unparseable_string_with_no_default_uses_now(self) -> None:
        before = time.time()
        result = validate_timestamp("bad")
        after = time.time()
        assert before <= result <= after

    def test_datetime_object_with_timestamp_method(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0)
        expected = dt.timestamp()
        assert validate_timestamp(dt) == expected

    def test_none_uses_default(self) -> None:
        assert validate_timestamp(None, 100.0) == 100.0

    def test_list_returns_default(self) -> None:
        assert validate_timestamp([], 99.0) == 99.0


class TestRetryOnFailure:
    @pytest.mark.asyncio
    async def test_sync_function_succeeds_first_try(self) -> None:
        def good_func(x: int) -> int:
            return x * 2

        result = await retry_on_failure(good_func, 5, max_retries=3)
        assert result == 10

    @pytest.mark.asyncio
    async def test_async_function_succeeds_first_try(self) -> None:
        async def good_async(x: int) -> int:
            return x * 2

        result = await retry_on_failure(good_async, 3, max_retries=3)
        assert result == 6

    @pytest.mark.asyncio
    async def test_sync_function_retries_then_succeeds(self) -> None:
        call_count = {"count": 0}

        def flaky_func() -> int:
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise ValueError("fail")
            return 42

        result = await retry_on_failure(
            flaky_func, max_retries=5, exceptions=(ValueError,)
        )
        assert result == 42
        assert call_count["count"] == 3

    @pytest.mark.asyncio
    async def test_sync_function_exhausts_retries(self) -> None:
        def always_fail() -> int:
            raise RuntimeError("always fail")

        with pytest.raises(RuntimeError, match="always fail"):
            await retry_on_failure(
                always_fail, max_retries=2, exceptions=(RuntimeError,)
            )

    @pytest.mark.asyncio
    async def test_sync_function_does_not_retry_non_matching_exception(self) -> None:
        def fail_with_value_error() -> int:
            raise ValueError("bad")

        with pytest.raises(ValueError, match="bad"):
            await retry_on_failure(
                fail_with_value_error, max_retries=3, exceptions=(RuntimeError,)
            )

    @pytest.mark.asyncio
    async def test_async_function_retries_then_succeeds(self) -> None:
        call_count = {"count": 0}

        async def flaky_async() -> int:
            call_count["count"] += 1
            if call_count["count"] < 2:
                raise ConnectionError("fail")
            return 99

        result = await retry_on_failure(
            flaky_async, max_retries=3, exceptions=(ConnectionError,)
        )
        assert result == 99
        assert call_count["count"] == 2

    @pytest.mark.asyncio
    async def test_backoff_factor_increases_wait(self) -> None:
        call_count = {"count": 0}

        def flaky_func() -> int:
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise ValueError("fail")
            return 7

        with patch("core.utils.data_helpers.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await retry_on_failure(
                flaky_func, max_retries=5, backoff_factor=2.0, exceptions=(ValueError,)
            )
            assert mock_sleep.call_count == 2
            # First retry: 2.0 * 2^0 = 2.0, second: 2.0 * 2^1 = 4.0
            mock_sleep.assert_any_call(2.0)
            mock_sleep.assert_any_call(4.0)


class TestOperationContext:
    @pytest.mark.asyncio
    async def test_successful_operation(self) -> None:
        ctx = OperationContext("test_op", session_id="s1")
        assert ctx.start_time is None

        async with ctx as c:
            assert c is ctx
            assert ctx.start_time is not None

        assert ctx.start_time is not None

    @pytest.mark.asyncio
    async def test_failed_operation_does_not_suppress_exception(self) -> None:
        ctx = OperationContext("fail_op")

        with pytest.raises(ValueError, match="boom"):
            async with ctx:
                raise ValueError("boom")

    @pytest.mark.asyncio
    async def test_operation_context_without_session_id(self) -> None:
        ctx = OperationContext("bare_op")
        async with ctx:
            pass


# ---------------------------------------------------------------------------
# injection_adapter tests
# ---------------------------------------------------------------------------
from core.injection.models import DeliveryMode
from core.utils.injection_adapter import InjectionAdapter


class TestInjectionAdapter:
    def test_normal_delivery_is_preserved(self) -> None:
        mode, reason = InjectionAdapter().resolve(
            MagicMock(), DeliveryMode.EXTRA_USER_CONTENT
        )
        assert mode is DeliveryMode.EXTRA_USER_CONTENT
        assert reason is None

    @pytest.mark.parametrize("configured", [DeliveryMode.AUTO, "auto"])
    def test_auto_resolves_to_temporary_extra_user_content(self, configured) -> None:
        mode, reason = InjectionAdapter().resolve(MagicMock(), configured)
        assert mode is DeliveryMode.EXTRA_USER_CONTENT
        assert reason is None

    def test_removed_system_prompt_delivery_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            InjectionAdapter().resolve(MagicMock(), "system_prompt")

    def test_fake_tool_call_is_preserved_for_supported_provider(self) -> None:
        provider = MagicMock()
        provider.provider_config = {"type": "openai_chat_completion"}
        provider.get_model.return_value = "gpt-4"
        mode, reason = InjectionAdapter().resolve(
            provider, DeliveryMode.FAKE_TOOL_CALL
        )
        assert mode is DeliveryMode.FAKE_TOOL_CALL
        assert reason is None

    def test_fake_tool_call_downgrades_for_gemini_provider_type(self) -> None:
        provider = MagicMock()
        provider.provider_config = {"type": "googlegenai_chat_completion"}
        provider.get_model.return_value = "gemini-2.0-flash"
        mode, reason = InjectionAdapter().resolve(
            provider, DeliveryMode.FAKE_TOOL_CALL
        )
        assert mode is DeliveryMode.USER_MESSAGE_BEFORE
        assert reason is not None
        assert "Gemini" in reason

    def test_fake_tool_call_downgrades_on_gemini_model_match(self) -> None:
        provider = MagicMock()
        provider.provider_config = {"type": "custom_provider"}
        provider.get_model.return_value = "gemini-pro"
        mode, reason = InjectionAdapter().resolve(
            provider, DeliveryMode.FAKE_TOOL_CALL
        )
        assert mode is DeliveryMode.USER_MESSAGE_BEFORE
        assert reason is not None

    @pytest.mark.parametrize("provider", [None, MagicMock(spec=[])])
    def test_unknown_provider_uses_widest_compatible_delivery(self, provider) -> None:
        mode, reason = InjectionAdapter().resolve(
            provider, DeliveryMode.FAKE_TOOL_CALL
        )
        assert mode is DeliveryMode.EXTRA_USER_CONTENT
        assert reason is not None

    @pytest.mark.parametrize("provider", [None, MagicMock(spec=[])])
    def test_unknown_provider_capabilities_are_conservative(self, provider) -> None:
        provider_type, model_name, tools_supported = InjectionAdapter().capabilities(
            provider
        )
        assert provider_type == ""
        assert model_name == ""
        assert tools_supported is False

    def test_capabilities_return_provider_identity_and_known_tool_support(self) -> None:
        provider = MagicMock()
        provider.provider_config = {"type": "openai_chat_completion"}
        provider.get_model.return_value = "gpt-4.1"
        assert InjectionAdapter().capabilities(provider) == (
            "openai_chat_completion",
            "gpt-4.1",
            True,
        )


# ---------------------------------------------------------------------------
# memory_formatter tests
# ---------------------------------------------------------------------------
from core.utils.injection_budget import InjectionBudget, InjectionStats

from core.utils.memory_formatter import (
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
)


class TestFormatMemoriesForInjection:
    def test_empty_list_returns_empty_string(self) -> None:
        result = format_memories_for_injection([])
        assert result == ""

    def test_legacy_call_without_budget_returns_string(self) -> None:
        result = format_memories_for_injection(
            [{"content": "legacy", "score": 1.0, "metadata": {}}]
        )

        assert isinstance(result, str)

    def test_call_with_budget_returns_text_and_stats_tuple(self) -> None:
        result = format_memories_for_injection(
            [{"content": "budgeted", "score": 1.0, "metadata": {}}],
            InjectionBudget(total_chars=800),
        )

        assert isinstance(result, tuple)
        text, stats = result
        assert isinstance(text, str)
        assert isinstance(stats, InjectionStats)

    def test_returns_formatted_string_with_header_and_footer(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "content": "用户喜欢爬山",
                "score": 0.85,
                "metadata": {
                    "importance": 0.7,
                    "topics": ["户外", "运动"],
                    "participants": ["小明"],
                    "key_facts": ["喜欢户外运动"],
                    "create_time": 1700000000.0,
                    "interaction_type": "chat",
                },
                "timestamp": 1700000000.0,
            }
        ]
        result = format_memories_for_injection(memories)
        assert "<RAG-Faiss-Memory>" in result
        assert "</RAG-Faiss-Memory>" in result
        assert "用户喜欢爬山" in result
        assert "Importance" in result

    def test_skips_malformed_entries(self) -> None:
        # A dict that will cause an error during formatting should be skipped
        memories: list[dict[str, Any]] = [
            {"bad_key": None},  # No content key, but dict handling is safe
        ]
        # This should not raise; format_memories_for_injection handles errors gracefully
        result = format_memories_for_injection(memories)
        # Content missing note should still be there from the dict path
        assert isinstance(result, str)

    def test_multiple_memories(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "content": "记忆A",
                "score": 0.9,
                "metadata": {"importance": 0.8, "create_time": 1700000000.0},
                "timestamp": 1700000000.0,
            },
            {
                "content": "记忆B",
                "score": 0.6,
                "metadata": {"importance": 0.5},
                "timestamp": 1700001000.0,
            },
        ]
        result = format_memories_for_injection(memories)
        assert "记忆A" in result
        assert "记忆B" in result

    def test_memory_without_timestamp(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "content": "无时间戳记忆",
                "score": 0.5,
                "metadata": {"importance": 0.6},
            }
        ]
        result = format_memories_for_injection(memories)
        assert "无时间戳记忆" in result

    def test_memory_with_empty_metadata_topics(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "content": "无主题记忆",
                "score": 0.5,
                "metadata": {"importance": 0.5, "topics": []},
            }
        ]
        result = format_memories_for_injection(memories)
        assert "无主题记忆" in result


class TestFormatMemoriesForFakeToolCall:
    def test_empty_list_returns_empty(self) -> None:
        result = format_memories_for_fake_tool_call([], "test query")
        assert result == []

    def test_returns_two_messages(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "id": "mem_001",
                "content": "用户喜欢咖啡",
                "score": 0.88,
                "metadata": {"importance": 0.7, "session_id": "s1"},
            }
        ]
        result = format_memories_for_fake_tool_call(memories, "查询")
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "tool"
        assert result[0]["tool_calls"][0]["function"]["name"] == "recall_long_term_memory"

    def test_tool_message_contains_results(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "id": "mem_002",
                "content": "记忆内容",
                "score": 0.75,
                "metadata": {"importance": 0.6},
            }
        ]
        result = format_memories_for_fake_tool_call(memories, "query text")
        tool_content = json.loads(result[1]["content"])
        assert tool_content["count"] == 1
        assert len(tool_content["results"]) == 1
        assert tool_content["results"][0]["id"] == "mem_002"

    def test_filters_are_passed_through(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "id": "m1",
                "content": "test",
                "score": 0.5,
                "metadata": {},
            }
        ]
        result = format_memories_for_fake_tool_call(
            memories, "q", session_filtered=False, persona_filtered=True
        )
        tool_content = json.loads(result[1]["content"])
        assert tool_content["applied_filters"]["session_filtered"] is False
        assert tool_content["applied_filters"]["persona_filtered"] is True

    def test_query_truncated_to_200_chars(self) -> None:
        long_query = "x" * 500
        memories: list[dict[str, Any]] = [
            {"id": "m1", "content": "t", "score": 0.5, "metadata": {}}
        ]
        result = format_memories_for_fake_tool_call(memories, long_query)
        assistant_args = json.loads(result[0]["tool_calls"][0]["function"]["arguments"])
        assert len(assistant_args["query"]) == 200

    def test_uses_doc_id_if_id_missing(self) -> None:
        memories: list[dict[str, Any]] = [
            {"doc_id": "doc_123", "content": "test", "score": 0.5, "metadata": {}}
        ]
        result = format_memories_for_fake_tool_call(memories, "q")
        tool_content = json.loads(result[1]["content"])
        assert tool_content["results"][0]["id"] == "doc_123"

    def test_object_like_memory_with_attrs(self) -> None:
        mem = MagicMock()
        mem.doc_id = "obj_001"
        mem.content = "对象记忆"
        mem.score = 0.92
        mem.metadata = json.dumps({"importance": 0.8, "session_id": "s_obj"})

        result = format_memories_for_fake_tool_call([mem], "q")
        tool_content = json.loads(result[1]["content"])
        assert tool_content["results"][0]["id"] == "obj_001"
        assert tool_content["results"][0]["content"] == "对象记忆"


class TestFormatMemoriesForFakeToolCallDeepSeekV4:
    def test_empty_memories_returns_empty_string(self) -> None:
        result = format_memories_for_fake_tool_call_deepseek_v4([], "q")
        assert result == ""

    def test_returns_deepseek_format_text(self) -> None:
        memories: list[dict[str, Any]] = [
            {
                "id": "mem_ds",
                "content": "DeepSeek测试",
                "score": 0.8,
                "metadata": {"importance": 0.6},
            }
        ]
        result = format_memories_for_fake_tool_call_deepseek_v4(memories, "查询")
        assert "[DeepSeekV4-FakeToolCall-Replay]" in result
        assert "[/DeepSeekV4-FakeToolCall-Replay]" in result
        assert "recall_long_term_memory" in result
        assert "DeepSeek测试" in result
        assert "<RAG-Faiss-Memory>" in result
        assert "</RAG-Faiss-Memory>" in result


# ---------------------------------------------------------------------------
# number_utils tests
# ---------------------------------------------------------------------------
from core.utils.number_utils import clamp_float, safe_float


class TestSafeFloat:
    def test_returns_float_as_is(self) -> None:
        assert safe_float(3.14) == 3.14

    def test_parses_int(self) -> None:
        assert safe_float(42) == 42.0

    def test_parses_numeric_string(self) -> None:
        assert safe_float("3.14") == 3.14

    def test_returns_default_for_none(self) -> None:
        assert safe_float(None, 99.0) == 99.0

    def test_returns_default_for_empty_string(self) -> None:
        assert safe_float("", 10.0) == 10.0

    def test_returns_default_for_non_numeric_string(self) -> None:
        assert safe_float("hello", 5.0) == 5.0

    def test_returns_default_for_nan(self) -> None:
        assert safe_float(float("nan"), 7.0) == 7.0

    def test_returns_default_for_infinity(self) -> None:
        assert safe_float(float("inf"), 3.0) == 3.0

    def test_default_default_is_zero(self) -> None:
        assert safe_float(None) == 0.0

    def test_parses_negative_float(self) -> None:
        assert safe_float("-2.5") == -2.5


class TestClampFloat:
    def test_within_range_passes_through(self) -> None:
        assert clamp_float(0.5, minimum=0.0, maximum=1.0) == 0.5

    def test_clamps_below_minimum(self) -> None:
        assert clamp_float(-0.5, minimum=0.0, maximum=1.0) == 0.0

    def test_clamps_above_maximum(self) -> None:
        assert clamp_float(1.5, minimum=0.0, maximum=1.0) == 1.0

    def test_respects_custom_bounds(self) -> None:
        assert clamp_float(100, minimum=10, maximum=50) == 50

    def test_handles_none_input(self) -> None:
        assert clamp_float(None, default=0.5, minimum=0.0, maximum=1.0) == 0.5

    def test_handles_empty_string(self) -> None:
        assert clamp_float("", default=0.3, minimum=0.0, maximum=1.0) == 0.3

    def test_handles_nan_to_default_then_clamp(self) -> None:
        assert clamp_float(float("nan"), default=0.7, minimum=0.2, maximum=0.6) == 0.6

    def test_clamps_at_zero_minimum(self) -> None:
        # Negative value clamped up to zero
        assert clamp_float(-10, default=0.0, minimum=0.0, maximum=1.0) == 0.0


# ---------------------------------------------------------------------------
# stopwords_manager tests
# ---------------------------------------------------------------------------
from core.utils.stopwords_manager import StopwordsManager, get_stopwords_manager


class TestStopwordsManager:
    def test_init_default(self) -> None:
        mgr = StopwordsManager()
        assert isinstance(mgr.stopwords, set)
        assert len(mgr.stopwords) == 0
        assert mgr.custom_stopwords_dir is None

    def test_init_with_custom_dir(self, tmp_path: Path) -> None:
        custom_dir = tmp_path / "custom_stopwords"
        mgr = StopwordsManager(stopwords_dir=str(custom_dir))
        assert mgr.custom_stopwords_dir == custom_dir
        assert custom_dir.exists()

    @pytest.mark.asyncio
    async def test_load_with_builtin_fallback(self) -> None:
        mgr = StopwordsManager()
        # "hit" source but the file doesn't exist -> falls back to builtin
        result = await mgr.load_stopwords(source="hit")
        assert isinstance(result, set)
        assert len(result) > 0
        assert "的" in result or len(result) > 0  # at least has some stopwords

    @pytest.mark.asyncio
    async def test_load_with_custom_words(self) -> None:
        mgr = StopwordsManager()
        custom = ["testword1", "testword2"]
        result = await mgr.load_stopwords(source="hit", custom_words=custom)
        assert "testword1" in result
        assert "testword2" in result

    @pytest.mark.asyncio
    async def test_load_with_custom_file_missing_falls_back(self) -> None:
        mgr = StopwordsManager()
        result = await mgr.load_stopwords(source="/nonexistent/path/stopwords.txt")
        assert isinstance(result, set)
        assert len(result) > 0  # falls back to builtin

    def test_add_custom_stopwords(self) -> None:
        mgr = StopwordsManager()
        mgr.add_custom_stopwords(["custom1", "custom2"])
        assert "custom1" in mgr.stopwords
        assert "custom1" in mgr.custom_stopwords

    def test_remove_stopwords(self) -> None:
        mgr = StopwordsManager()
        mgr.add_custom_stopwords(["to_remove"])
        assert "to_remove" in mgr.stopwords
        mgr.remove_stopwords(["to_remove"])
        assert "to_remove" not in mgr.stopwords
        assert "to_remove" not in mgr.custom_stopwords

    def test_is_stopword(self) -> None:
        mgr = StopwordsManager()
        mgr.add_custom_stopwords(["test_word"])
        assert mgr.is_stopword("test_word") is True
        assert mgr.is_stopword("not_a_stopword") is False

    def test_filter_stopwords(self) -> None:
        mgr = StopwordsManager()
        mgr.add_custom_stopwords(["stop1", "stop2"])
        tokens = ["keep", "stop1", "keep2", "stop2"]
        filtered = mgr.filter_stopwords(tokens)
        assert filtered == ["keep", "keep2"]

    def test_filter_empty_list(self) -> None:
        mgr = StopwordsManager()
        assert mgr.filter_stopwords([]) == []

    def test_get_builtin_stopwords_is_non_empty(self) -> None:
        builtin = StopwordsManager._get_builtin_stopwords()
        assert isinstance(builtin, set)
        assert len(builtin) > 0

    def test_get_stopwords_manager_singleton(self) -> None:
        m1 = get_stopwords_manager()
        m2 = get_stopwords_manager()
        assert m1 is m2

    def test_add_custom_stopwords_empty_list(self) -> None:
        mgr = StopwordsManager()
        mgr.add_custom_stopwords([])
        assert len(mgr.stopwords) == 0

    def test_remove_stopwords_empty_list(self) -> None:
        mgr = StopwordsManager()
        mgr.add_custom_stopwords(["word"])
        mgr.remove_stopwords([])
        assert "word" in mgr.stopwords

    def test_remove_nonexistent_word(self) -> None:
        mgr = StopwordsManager()
        mgr.remove_stopwords(["nonexistent"])
        assert len(mgr.stopwords) == 0

    @pytest.mark.asyncio
    async def test_save_custom_stopwords_no_dir(self) -> None:
        mgr = StopwordsManager()
        await mgr.save_custom_stopwords()
        # Should not raise, just warn

    @pytest.mark.asyncio
    async def test_save_custom_stopwords_with_dir(self, tmp_path: Path) -> None:
        mgr = StopwordsManager(stopwords_dir=str(tmp_path))
        mgr.add_custom_stopwords(["word1", "word2"])
        filepath = tmp_path / "custom_stopwords.txt"
        await mgr.save_custom_stopwords(filepath)
        assert filepath.exists()
        content = filepath.read_text(encoding="utf-8")
        assert "word1" in content
        assert "word2" in content

    @pytest.mark.asyncio
    async def test_save_custom_stopwords_to_default(self, tmp_path: Path) -> None:
        mgr = StopwordsManager(stopwords_dir=str(tmp_path))
        mgr.add_custom_stopwords(["default_save"])
        await mgr.save_custom_stopwords()
        default_file = tmp_path / "custom_stopwords.txt"
        assert default_file.exists()

    @pytest.mark.asyncio
    async def test_load_from_file_with_comments(self, tmp_path: Path) -> None:
        stopwords_file = tmp_path / "test_stopwords.txt"
        stopwords_file.write_text("# comment line\nword1\nword2\n# another comment\nword3\n", encoding="utf-8")
        result = await StopwordsManager._load_from_file(stopwords_file)
        assert result == {"word1", "word2", "word3"}

    @pytest.mark.asyncio
    async def test_load_from_file_empty(self, tmp_path: Path) -> None:
        stopwords_file = tmp_path / "empty.txt"
        stopwords_file.write_text("", encoding="utf-8")
        result = await StopwordsManager._load_from_file(stopwords_file)
        assert result == set()

    @pytest.mark.asyncio
    async def test_load_from_file_not_found(self) -> None:
        result = await StopwordsManager._load_from_file(Path("/nonexistent/file.txt"))
        assert result == set()

    @pytest.mark.asyncio
    async def test_get_stopwords_builtin_exists(self, tmp_path: Path) -> None:
        """Test get_stopwords when builtin file exists."""
        from core.utils.stopwords_manager import StopwordsManager

        mgr = StopwordsManager.__new__(StopwordsManager)
        mgr.stopwords = set()
        mgr.custom_stopwords = set()
        mgr.custom_stopwords_dir = tmp_path / "custom"
        mgr.builtin_stopwords_dir = tmp_path / "builtin"
        mgr.builtin_stopwords_dir.mkdir(parents=True, exist_ok=True)
        (mgr.builtin_stopwords_dir / "stopwords_hit.txt").write_text("word1\n", encoding="utf-8")
        result = await mgr.get_stopwords("hit")
        assert result is not None
        assert "stopwords_hit.txt" in result

    @pytest.mark.asyncio
    async def test_get_stopwords_fallback(self, tmp_path: Path) -> None:
        """Test get_stopwords falls back to custom dir when builtin missing."""
        from core.utils.stopwords_manager import StopwordsManager

        mgr = StopwordsManager.__new__(StopwordsManager)
        mgr.stopwords = set()
        mgr.custom_stopwords = set()
        mgr.custom_stopwords_dir = tmp_path / "custom"
        mgr.custom_stopwords_dir.mkdir(parents=True, exist_ok=True)
        mgr.builtin_stopwords_dir = tmp_path / "nonexistent_builtin"
        # Patch _write_fallback_stopwords to avoid writing
        mgr._write_fallback_stopwords = AsyncMock()
        result = await mgr.get_stopwords("hit")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_stopwords_no_dirs(self) -> None:
        """Test get_stopwords returns None when no dirs available."""
        from core.utils.stopwords_manager import StopwordsManager

        mgr = StopwordsManager.__new__(StopwordsManager)
        mgr.stopwords = set()
        mgr.custom_stopwords = set()
        mgr.custom_stopwords_dir = None
        mgr.builtin_stopwords_dir = Path("/nonexistent_xyz")
        result = await mgr.get_stopwords("hit")
        assert result is None

    @pytest.mark.asyncio
    async def test_write_fallback_stopwords(self, tmp_path: Path) -> None:
        """Test _write_fallback_stopwords writes a valid file."""
        from core.utils.stopwords_manager import StopwordsManager

        mgr = StopwordsManager.__new__(StopwordsManager)
        mgr.stopwords = set()
        mgr.custom_stopwords = set()
        filepath = tmp_path / "fallback.txt"
        await mgr._write_fallback_stopwords(filepath)
        assert filepath.exists()
        content = filepath.read_text(encoding="utf-8")
        assert "Generated fallback stopwords" in content

    @pytest.mark.asyncio
    async def test_write_fallback_skips_existing(self, tmp_path: Path) -> None:
        """Test _write_fallback_stopwords does not overwrite existing."""
        from core.utils.stopwords_manager import StopwordsManager

        mgr = StopwordsManager.__new__(StopwordsManager)
        filepath = tmp_path / "existing.txt"
        filepath.write_text("existing content", encoding="utf-8")
        await mgr._write_fallback_stopwords(filepath)
        assert filepath.read_text(encoding="utf-8") == "existing content"


# ---------------------------------------------------------------------------
# utils/__init__.py  — extract_json_from_response, get_now_datetime,
#                      get_now_datetime_from_context
# ---------------------------------------------------------------------------

class TestExtractJsonFromResponse:
    def test_extract_from_markdown_json_block(self) -> None:
        from core.utils import extract_json_from_response
        text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        result = extract_json_from_response(text)
        assert result == '{"key": "value"}'

    def test_extract_from_markdown_block_no_lang(self) -> None:
        from core.utils import extract_json_from_response
        text = '```\n{"key": "value"}\n```'
        result = extract_json_from_response(text)
        assert result == '{"key": "value"}'

    def test_extract_no_code_block_returns_trimmed(self) -> None:
        from core.utils import extract_json_from_response
        text = '  {"key": "value"}  '
        result = extract_json_from_response(text)
        assert result == '{"key": "value"}'

    def test_extract_nested_braces(self) -> None:
        from core.utils import extract_json_from_response
        text = '```json\n{"outer": {"inner": "value"}}\n```'
        result = extract_json_from_response(text)
        assert "outer" in result
        assert "inner" in result

    def test_extract_first_json_only(self) -> None:
        from core.utils import extract_json_from_response
        text = '```json\n{"first": 1}\n```\n```json\n{"second": 2}\n```'
        result = extract_json_from_response(text)
        assert "first" in result
        assert "second" not in result


class TestGetNowDatetime:
    def test_returns_datetime_with_tz(self) -> None:
        from core.utils import get_now_datetime
        result = get_now_datetime("UTC")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_default_timezone(self) -> None:
        from core.utils import get_now_datetime
        result = get_now_datetime()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_invalid_timezone_falls_back(self) -> None:
        from core.utils import get_now_datetime
        result = get_now_datetime("Invalid/Timezone")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_with_context_object(self) -> None:
        from core.utils import get_now_datetime
        mock_ctx = MagicMock()
        mock_ctx.plugin_config = {}
        result = get_now_datetime(mock_ctx)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None


class TestGetNowDatetimeFromContext:
    def test_with_plugin_config_dict(self) -> None:
        from core.utils import get_now_datetime_from_context
        mock_ctx = MagicMock()
        mock_ctx.plugin_config = {"timezone_settings": {"timezone": "UTC"}}
        result = get_now_datetime_from_context(mock_ctx)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_without_plugin_config(self) -> None:
        from core.utils import get_now_datetime_from_context
        mock_ctx = MagicMock(spec=[])
        result = get_now_datetime_from_context(mock_ctx)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_with_attribute_error(self) -> None:
        from core.utils import get_now_datetime_from_context
        mock_ctx = object()  # has no plugin_config
        result = get_now_datetime_from_context(mock_ctx)
        assert isinstance(result, datetime)

    def test_config_attr_error_triggers_fallback(self) -> None:
        """If timezone_settings is not a dict, .get() raises AttributeError → fallback (lines 152-154)."""
        from core.utils import get_now_datetime_from_context
        mock_ctx = MagicMock()
        mock_ctx.plugin_config = {"timezone_settings": 42}  # int, not dict → .get() fails
        result = get_now_datetime_from_context(mock_ctx)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# data_helpers — additional edge cases
# ---------------------------------------------------------------------------

class TestDataHelpersEdgeCases:
    def test_safe_parse_metadata_string_with_special_chars(self) -> None:
        from core.utils.data_helpers import safe_parse_metadata
        result = safe_parse_metadata('{"key": "value with \\"quotes\\""}')
        assert isinstance(result, dict)

    def test_validate_timestamp_float_edge(self) -> None:
        from core.utils.data_helpers import validate_timestamp
        assert validate_timestamp(0.0) == 0.0
        assert validate_timestamp(-1.0) == -1.0

    def test_validate_timestamp_bool(self) -> None:
        from core.utils.data_helpers import validate_timestamp
        # bool is a subclass of int in Python, so True == 1 as float
        result = validate_timestamp(True, 42.0)
        assert result == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_retry_on_failure_sync_success(self) -> None:
        from core.utils.data_helpers import retry_on_failure
        def good() -> int:
            return 1
        result = await retry_on_failure(good, max_retries=0)
        assert result == 1

    @pytest.mark.asyncio
    async def test_retry_no_exceptions_tuple_uses_default(self) -> None:
        from core.utils.data_helpers import retry_on_failure
        def fail() -> int:
            raise ValueError("fail")
        with pytest.raises(ValueError):
            await retry_on_failure(fail, max_retries=1)

    @pytest.mark.asyncio
    async def test_operation_context_start_time(self) -> None:
        from core.utils.data_helpers import OperationContext
        ctx = OperationContext("op")
        assert ctx.start_time is None
        async with ctx:
            assert ctx.start_time is not None
        assert ctx.start_time is not None

    @pytest.mark.asyncio
    async def test_operation_context_with_exception(self) -> None:
        from core.utils.data_helpers import OperationContext
        ctx = OperationContext("failing_op", session_id="s1")
        try:
            async with ctx:
                raise RuntimeError("test error")
        except RuntimeError:
            pass
        assert ctx.start_time is not None


# ---------------------------------------------------------------------------
# get_persona_id tests
# ---------------------------------------------------------------------------

class TestGetPersonaId:
    """测试 get_persona_id — three-tier persona resolution."""

    @pytest.fixture
    def mock_context(self) -> MagicMock:
        ctx = MagicMock()
        ctx.conversation_manager.get_curr_conversation_id = AsyncMock(return_value="session_001")
        ctx.conversation_manager.get_conversation = AsyncMock(return_value=None)
        ctx.persona_manager.get_default_persona_v3 = AsyncMock(return_value=None)
        return ctx

    @pytest.fixture
    def mock_event(self) -> MagicMock:
        evt = MagicMock()
        evt.unified_msg_origin = "user_test_umo"
        return evt

    @pytest.mark.asyncio
    async def test_priority1_session_config_has_persona(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """Priority 1: session_service_config has persona_id → return it."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={"persona_id": "session_persona_123"})
        result = await get_persona_id(mock_context, mock_event)
        assert result == "session_persona_123"

    @pytest.mark.asyncio
    async def test_priority1_no_persona_key_in_config(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """session_service_config exists but has no persona_id key."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.conversation_manager.get_conversation = AsyncMock(
            return_value=MagicMock(persona_id="conv_persona")
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "conv_persona"

    @pytest.mark.asyncio
    async def test_priority2_conversation_has_persona(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """Priority 2: no session config, conversation has persona_id."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.conversation_manager.get_conversation = AsyncMock(
            return_value=MagicMock(persona_id="conv_persona_456")
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "conv_persona_456"

    @pytest.mark.asyncio
    async def test_priority2_conversation_none_persona(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """Priority 2: conversation persona_id is '[%None]' → returns None."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.conversation_manager.get_conversation = AsyncMock(
            return_value=MagicMock(persona_id="[%None]")
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result is None

    @pytest.mark.asyncio
    async def test_priority2_conversation_no_persona(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """Conversation exists but persona_id is None/empty → falls to default."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.conversation_manager.get_conversation = AsyncMock(
            return_value=MagicMock(persona_id=None)
        )
        mock_context.persona_manager.get_default_persona_v3 = AsyncMock(
            return_value={"name": "default_persona"}
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "default_persona"

    @pytest.mark.asyncio
    async def test_priority2_no_session_id(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """Priority 2: get_curr_conversation_id returns None → skip to default."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=None)
        mock_context.persona_manager.get_default_persona_v3 = AsyncMock(
            return_value={"name": "fallback_persona"}
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "fallback_persona"

    @pytest.mark.asyncio
    async def test_priority2_conversation_is_none(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """get_conversation returns None → skip to default."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.conversation_manager.get_conversation = AsyncMock(return_value=None)
        mock_context.persona_manager.get_default_persona_v3 = AsyncMock(
            return_value={"name": "default_from_none_conv"}
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "default_from_none_conv"

    @pytest.mark.asyncio
    async def test_priority3_default_persona_exists(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """Priority 3: global default persona is set."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.persona_manager.get_default_persona_v3 = AsyncMock(
            return_value={"name": "global_default"}
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "global_default"

    @pytest.mark.asyncio
    async def test_priority3_default_persona_is_none(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """没有 persona found at any level → returns None."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={})
        mock_context.persona_manager.get_default_persona_v3 = AsyncMock(return_value=None)
        result = await get_persona_id(mock_context, mock_event)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """异常 during persona resolution → returns None gracefully."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(side_effect=RuntimeError("provider down"))
        result = await get_persona_id(mock_context, mock_event)
        assert result is None

    @pytest.mark.asyncio
    async def test_priority1_session_config_empty_persona_id(
        self, mock_context: MagicMock, mock_event: MagicMock
    ) -> None:
        """session_service_config has persona_id='' (empty string) → falsy, skip."""
        from core.utils import get_persona_id
        from astrbot.api import sp

        sp.get_async = AsyncMock(return_value={"persona_id": ""})
        mock_context.conversation_manager.get_conversation = AsyncMock(
            return_value=MagicMock(persona_id="fallback_conv")
        )
        result = await get_persona_id(mock_context, mock_event)
        assert result == "fallback_conv"
