"""测试 memory_formatter.py — uncovered branches in format & fake tool calls."""

import json
from unittest.mock import MagicMock

import pytest

from core.utils.memory_formatter import (
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
)


class TestFormatMemoriesForInjection:
    """测试 format_memories_for_injection 边界情况。"""

    def test_empty_memories(self):
        """空 list returns empty string."""
        assert format_memories_for_injection([]) == ""

    def test_dict_based_memories(self):
        """格式化 memories passed as dicts (the normal path)."""
        memories = [
            {
                "content": "用户喜欢爬山",
                "score": 0.85,
                "metadata": {
                    "importance": 0.7,
                    "topics": ["户外运动", "爬山"],
                    "key_facts": ["用户经常周末爬山"],
                    "create_time": 1000000.0,
                },
                "timestamp": 1000000.0,
            }
        ]
        result = format_memories_for_injection(memories)
        assert "用户喜欢爬山" in result
        assert "Importance: 0.70" in result
        assert "户外运动" in result
        assert "爬山" in result
        assert "用户经常周末爬山" in result

    def test_dict_memories_with_participants(self):
        """字典 memories with participants field (group chat)."""
        memories = [
            {
                "content": "群聊讨论",
                "score": 0.9,
                "metadata": {
                    "importance": 0.8,
                    "participants": ["张三", "李四"],
                    "topics": ["群聊"],
                },
            }
        ]
        result = format_memories_for_injection(memories)
        assert "Participants: 张三、李四" in result

    def test_object_based_memories(self):
        """格式化 memories passed as objects with attributes (covers lines 66-78)."""
        mem_obj = MagicMock()
        mem_obj.content = "用户喜欢Python编程"
        mem_obj.score = 0.92
        mem_obj.timestamp = 2000000.0
        mem_obj.metadata = {
            "importance": 0.6,
            "topics": ["编程", "Python"],
            "create_time": 2000000.0,
            "interaction_type": "chat",
        }
        result = format_memories_for_injection([mem_obj])
        assert "用户喜欢Python编程" in result
        assert "Importance: 0.60" in result

    def test_object_memories_with_string_metadata(self):
        """Object memories with metadata as JSON string (covers safe_parse_metadata path)."""
        import json

        mem_obj = MagicMock()
        mem_obj.content = "JSON metadata test"
        mem_obj.score = 0.5
        mem_obj.timestamp = None
        mem_obj.metadata = json.dumps(
            {
                "importance": 0.4,
                "topics": ["测试"],
            }
        )
        result = format_memories_for_injection([mem_obj])
        assert "JSON metadata test" in result
        assert "Importance: 0.40" in result

    def test_bad_timestamp_handled(self):
        """Bad timestamp (non-numeric) is gracefully handled (covers lines 86-87)."""
        mem_obj = MagicMock()
        mem_obj.content = "Bad timestamp test"
        mem_obj.score = 0.5
        mem_obj.timestamp = "not_a_number"
        mem_obj.metadata = {"importance": 0.3}
        result = format_memories_for_injection([mem_obj])
        # Should not crash; memory should still be formatted
        assert "Bad timestamp test" in result

    def test_exception_in_loop_skips_memory(self):
        """异常 during formatting of one memory skips it (covers lines 137-143)."""
        mem_obj = MagicMock()
        # Make content access raise an exception
        type(mem_obj).content = property(lambda self: (_ for _ in ()).throw(ValueError("boom")))
        mem_obj.score = 0.5
        mem_obj.metadata = {}
        mem_obj.timestamp = None

        good_mem = {
            "content": "这条可以正常格式化",
            "score": 0.8,
            "metadata": {"importance": 0.5},
        }
        result = format_memories_for_injection([mem_obj, good_mem])
        assert "这条可以正常格式化" in result
        assert "boom" not in result

    def test_all_memories_fail_formatting(self):
        """当 all memories fail formatting, returns empty string (covers lines 146-147)."""
        mem_obj = MagicMock()
        type(mem_obj).content = property(lambda self: (_ for _ in ()).throw(RuntimeError("fail")))

        result = format_memories_for_injection([mem_obj])
        assert result == ""


class TestFormatMemoriesForFakeToolCall:
    """测试 format_memories_for_fake_tool_call."""

    def test_empty_memories(self):
        """空 list returns empty list."""
        assert format_memories_for_fake_tool_call([], "test query") == []

    def test_dict_based_memories_with_id(self):
        """字典 memories produce valid tool call messages."""
        memories = [
            {
                "id": 42,
                "content": "记忆内容",
                "score": 0.88,
                "metadata": {
                    "importance": 0.6,
                    "session_id": "s1",
                    "persona_id": "p1",
                    "create_time": 3000000.0,
                    "last_access_time": 3100000.0,
                },
            }
        ]
        result = format_memories_for_fake_tool_call(memories, "测试查询", k=3)
        assert len(result) == 2
        assistant_msg, tool_msg = result
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] is None
        assert len(assistant_msg["tool_calls"]) == 1
        assert tool_msg["role"] == "tool"
        # Check tool result JSON
        content = json.loads(tool_msg["content"])
        assert content["query"] == "测试查询"
        assert content["count"] == 1
        assert content["results"][0]["id"] == 42

    def test_dict_memories_with_doc_id_fallback(self):
        """字典 memories fall back to doc_id when id is missing."""
        memories = [
            {
                "doc_id": 99,
                "content": "fallback id test",
                "score": 0.5,
                "metadata": {},
            }
        ]
        result = format_memories_for_fake_tool_call(memories, "q")
        content = json.loads(result[1]["content"])
        assert content["results"][0]["id"] == 99

    def test_object_based_memories(self):
        """Object memories produce valid tool call messages (covers lines 210-212)."""
        mem_obj = MagicMock()
        mem_obj.doc_id = 7
        mem_obj.content = "对象记忆内容"
        mem_obj.score = 0.75
        mem_obj.metadata = {"importance": 0.55}

        result = format_memories_for_fake_tool_call([mem_obj], "对象查询")
        assert len(result) == 2
        content = json.loads(result[1]["content"])
        assert content["results"][0]["id"] == 7
        assert content["results"][0]["content"] == "对象记忆内容"

    def test_object_memories_with_id_fallback(self):
        """Object memories fall back to id attribute when doc_id is not int/str."""
        mem_obj = MagicMock()
        mem_obj.doc_id = None  # Not int/str, triggers fallback to id
        mem_obj.id = 55
        mem_obj.content = "id fallback"
        mem_obj.score = 0.6
        mem_obj.metadata = {}

        result = format_memories_for_fake_tool_call([mem_obj], "q")
        content = json.loads(result[1]["content"])
        assert content["results"][0]["id"] == 55

    def test_object_memories_with_string_metadata(self):
        """Object memories with string metadata are parsed."""
        import json as json_mod

        mem_obj = MagicMock()
        mem_obj.doc_id = 3
        mem_obj.content = "字符串metadata"
        mem_obj.score = 0.65
        mem_obj.metadata = json_mod.dumps({"importance": 0.45})

        result = format_memories_for_fake_tool_call([mem_obj], "q")
        content = json_mod.loads(result[1]["content"])
        assert content["results"][0]["importance"] == 0.45

    def test_fake_tool_call_filters(self):
        """Applied filters are reflected in the result."""
        memories = [
            {
                "id": 1,
                "content": "filter test",
                "score": 1.0,
                "metadata": {},
            }
        ]
        result = format_memories_for_fake_tool_call(
            memories, "q", session_filtered=False, persona_filtered=True
        )
        content = json.loads(result[1]["content"])
        assert content["applied_filters"]["session_filtered"] is False
        assert content["applied_filters"]["persona_filtered"] is True


class TestFormatMemoriesForFakeToolCallDeepSeekV4:
    """测试 DeepSeek V4 特定格式化。"""

    def test_empty_memories(self):
        """空 list returns empty string."""
        assert format_memories_for_fake_tool_call_deepseek_v4([], "q") == ""

    def test_normal_case(self):
        """Normal case produces DeepSeek V4 text format."""
        memories = [
            {
                "id": 1,
                "content": "deepseek test",
                "score": 0.9,
                "metadata": {"importance": 0.7},
            }
        ]
        result = format_memories_for_fake_tool_call_deepseek_v4(
            memories, "deepseek query", k=5
        )
        assert "[DeepSeekV4-FakeToolCall-Replay]" in result
        assert "recall_long_term_memory" in result
        assert "deepseek query" in result


