"""测试 memory_formatter.py — uncovered branches in format & fake tool calls."""

import json
from unittest.mock import MagicMock

import pytest

from core.features.injection.domain.models import ContentLevel
from core.utils.injection_budget import InjectionBudget, InjectionStats
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

    def test_none_content_level_without_budget_returns_empty_string(self):
        """NONE 级别即使存在临时身份参考也必须保持完全空输出。"""

        assert (
            format_memories_for_injection(
                [
                    {
                        "content": "must not be formatted",
                        "metadata": {
                            "identity_reference_lines": [
                                "- “旧名”是历史名称；当前显示为“新名”（QQ:10001）。"
                            ]
                        },
                    }
                ],
                content_level=ContentLevel.NONE,
            )
            == ""
        )

    def test_identity_reference_precedes_topics_and_key_facts(self):
        """临时身份参考应在 topics、participants 和 facts 之前输出。"""

        result = format_memories_for_injection(
            [
                {
                    "content": "群聊讨论",
                    "score": 0.9,
                    "metadata": {
                        "identity_reference_lines": [
                            "- “旧名”是历史名称；当前显示为“新名”（QQ:10001）。"
                        ],
                        "topics": ["主题"],
                        "participants": ["QQ:10001"],
                        "key_facts": ["事实"],
                    },
                }
            ]
        )

        assert result.index("身份参考：") < result.index("Topics: 主题")
        assert result.index("身份参考：") < result.index("Participants: QQ:10001")
        assert result.index("身份参考：") < result.index("Key facts: 事实")

    def test_internal_identity_sources_are_never_formatted(self):
        """内部参与者来源映射不得进入普通注入或伪工具调用转录。"""

        canary = "INTERNAL-STABLE-SOURCE-CANARY"
        memories = [
            {
                "id": 17,
                "content": "允许输出的记忆正文",
                "score": 0.9,
                "metadata": {
                    "participants": ["QQ官方:instance:OPENID"],
                    "participant_identity_sources": {
                        "canonical": {
                            "identity_namespace": canary,
                            "stable_user_id": canary,
                        }
                    },
                },
            }
        ]

        ordinary = format_memories_for_injection(memories)
        fake_messages = format_memories_for_fake_tool_call(memories, "查询")
        deepseek = format_memories_for_fake_tool_call_deepseek_v4(memories, "查询")

        assert canary not in ordinary
        assert canary not in json.dumps(fake_messages, ensure_ascii=False)
        assert canary not in deepseek

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
        type(mem_obj).content = property(
            lambda self: (_ for _ in ()).throw(ValueError("boom"))
        )
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
        type(mem_obj).content = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("fail"))
        )

        result = format_memories_for_injection([mem_obj])
        assert result == ""

    def test_projection_uses_allowlist_and_metadata_budget(self):
        text, stats = format_memories_for_injection(
            [
                {
                    "content": "canonical text",
                    "score": 0.9,
                    "metadata": {
                        "importance": 0.8,
                        "derived_projections": [
                            {
                                "type": "episode_summary",
                                "summary": "先迁移，再灰度发布。",
                                "confidence": 0.86,
                                "projection_id": "内部编号",
                                "source_memory_ids": [17, 18],
                            }
                        ],
                    },
                }
            ],
            budget=InjectionBudget(
                total_chars=800,
                memory_max_chars=300,
                metadata_max_chars=180,
                include_key_facts=False,
                include_topics=False,
                include_participants=False,
                compact_header=True,
            ),
            content_level=ContentLevel.COMPACT,
        )

        assert (
            "Projection: [episode_summary, confidence=0.86] 先迁移，再灰度发布。"
            in text
        )
        assert "projection_id" not in text
        assert "source_memory_ids" not in text
        assert stats.chars == len(text)

    def test_projection_is_omitted_at_content_level_none(self):
        text, _ = format_memories_for_injection(
            [
                {
                    "content": "canonical",
                    "metadata": {
                        "derived_projections": [
                            {
                                "type": "episode_summary",
                                "summary": "不应出现",
                                "confidence": 0.9,
                            }
                        ]
                    },
                }
            ],
            budget=InjectionBudget(
                total_chars=800,
                memory_max_chars=300,
                metadata_max_chars=180,
            ),
            content_level=ContentLevel.NONE,
        )
        assert text == ""


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

    def test_fake_tool_call_keeps_projection_on_canonical_result(self):
        result = format_memories_for_fake_tool_call(
            [
                {
                    "id": 17,
                    "content": "canonical",
                    "score": 0.9,
                    "metadata": {
                        "importance": 0.8,
                        "derived_projections": [
                            {
                                "type": "episode_summary",
                                "summary": "摘要",
                                "confidence": 0.86,
                                "projection_id": "内部编号",
                                "source_memory_ids": [17, 18],
                            }
                        ],
                    },
                }
            ],
            "查询",
        )
        payload = json.loads(result[1]["content"])
        canonical = payload["results"][0]
        assert canonical["id"] == 17
        assert canonical["derived_projections"] == [
            {"type": "episode_summary", "summary": "摘要", "confidence": 0.86}
        ]
        assert "projection_id" not in canonical
        assert "source_memory_ids" not in canonical


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


def _rich_memory(index: int) -> dict:
    return {
        "content": f"<entry-{index}>Complete memory {index}.</entry-{index}>",
        "score": 1.0 - index / 100,
        "timestamp": 1_700_000_000.0 + index,
        "metadata": {
            "importance": 0.8,
            "topics": [f"topic-{index}"],
            "participants": [f"person-{index}"],
            "key_facts": [f"fact-{index}"],
            "create_time": 1_700_000_000.0 + index,
        },
    }


def _budget(level: ContentLevel, total_chars: int, **overrides) -> InjectionBudget:
    values = {
        "total_chars": total_chars,
        "memory_max_chars": 800,
        "metadata_max_chars": 300,
        "include_key_facts": True,
        "include_topics": True,
        "include_participants": True,
        "compact_header": level is not ContentLevel.DETAILED,
    }
    values.update(overrides)
    return InjectionBudget(**values)


class TestBudgetedInjectionFormatting:
    def test_budgeted_call_returns_text_and_stats(self):
        result = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.COMPACT, 1200),
            content_level=ContentLevel.COMPACT,
        )

        assert isinstance(result, tuple)
        text, stats = result
        assert isinstance(text, str)
        assert isinstance(stats, InjectionStats)
        assert stats.chars == len(text)

    def test_none_content_level_returns_empty_payload(self):
        """预算模式下 NONE 级别同样不得输出身份参考或正文。"""

        memory = _rich_memory(0)
        memory["metadata"]["identity_reference_lines"] = [
            "- “旧名”是历史名称；当前显示为“新名”（QQ:10001）。"
        ]
        text, stats = format_memories_for_injection(
            [memory],
            budget=_budget(ContentLevel.NONE, 2400),
            content_level=ContentLevel.NONE,
        )

        assert text == ""
        assert stats.chars == 0
        assert stats.memory_count == 0

    def test_identity_reference_counts_toward_metadata_budget(self):
        """身份说明占满 metadata 预算后不得继续挤入 topics 或 facts。"""

        line = "- “旧名”是历史名称；当前显示为“新名”（QQ:10001）。"
        block = f"身份参考：\n{line}"
        memory = _rich_memory(0)
        memory["metadata"]["identity_reference_lines"] = [line]
        text, _ = format_memories_for_injection(
            [memory],
            budget=_budget(
                ContentLevel.COMPACT,
                1200,
                metadata_max_chars=len(block),
            ),
            content_level=ContentLevel.COMPACT,
        )

        assert block in text
        assert "Topics:" not in text
        assert "Participants:" not in text
        assert "Key facts:" not in text

    def test_facts_prefers_key_facts_and_excludes_other_metadata(self):
        text, _ = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.FACTS, 800),
            content_level=ContentLevel.FACTS,
        )

        assert "fact-0" in text
        assert "Complete memory 0." not in text
        assert "topic-0" not in text
        assert "person-0" not in text

    def test_facts_ignore_flags_and_metadata_cap_when_key_facts_are_available(self):
        memory = _rich_memory(0)
        memory["content"] = "raw body"
        memory["metadata"]["key_facts"] = ["prefers tea"]

        text, _ = format_memories_for_injection(
            [memory],
            budget=_budget(
                ContentLevel.FACTS,
                800,
                metadata_max_chars=5,
                include_key_facts=False,
            ),
            content_level=ContentLevel.FACTS,
        )

        assert "prefers tea" in text
        assert "raw body" not in text

    def test_facts_does_not_count_truncation_of_omitted_raw_content(self):
        memory = _rich_memory(0)
        memory["content"] = "raw content " * 100

        text, stats = format_memories_for_injection(
            [memory],
            budget=_budget(
                ContentLevel.FACTS,
                800,
                memory_max_chars=32,
            ),
            content_level=ContentLevel.FACTS,
        )

        assert "fact-0" in text
        assert "raw content" not in text
        assert stats.truncated_count == 0

    def test_facts_without_key_facts_falls_back_to_truncated_body(self):
        memory = _rich_memory(0)
        memory["content"] = "fallback body " * 20
        memory["metadata"]["key_facts"] = []

        text, stats = format_memories_for_injection(
            [memory],
            budget=_budget(
                ContentLevel.FACTS,
                800,
                memory_max_chars=32,
            ),
            content_level=ContentLevel.FACTS,
        )

        assert "fallback body" in text
        assert "topic-0" not in text
        assert "person-0" not in text
        assert "Memory write time:" not in text
        assert stats.truncated_count == 1

    def test_compact_obeys_metadata_flags(self):
        text, _ = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(
                ContentLevel.COMPACT,
                1200,
                include_key_facts=False,
                include_topics=True,
                include_participants=False,
            ),
            content_level=ContentLevel.COMPACT,
        )

        assert "topic-0" in text
        assert "fact-0" not in text
        assert "person-0" not in text

    def test_compact_does_not_touch_disabled_key_facts(self):
        class RaisingFact:
            def __str__(self) -> str:
                raise AssertionError("disabled key facts must stay unread")

        memory = _rich_memory(0)
        memory["metadata"]["key_facts"] = [RaisingFact()]

        text, stats = format_memories_for_injection(
            [memory],
            budget=_budget(
                ContentLevel.COMPACT,
                1200,
                include_key_facts=False,
            ),
            content_level=ContentLevel.COMPACT,
        )

        assert "Complete memory 0." in text
        assert stats.memory_count == 1

    def test_detailed_may_emit_all_supported_metadata(self):
        text, _ = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.DETAILED, 2400),
            content_level=ContentLevel.DETAILED,
        )

        assert "Memory write time:" in text
        assert "topic-0" in text
        assert "person-0" in text
        assert "fact-0" in text

    @pytest.mark.parametrize("hard_cap", [0, 1, 64, 100, 800, 1200, 2400])
    @pytest.mark.parametrize("candidate_count", [0, 1, 2, 10])
    @pytest.mark.parametrize("level", list(ContentLevel))
    def test_every_payload_respects_hard_cap_and_contains_only_complete_entries(
        self,
        hard_cap,
        candidate_count,
        level,
    ):
        memories = [_rich_memory(index) for index in range(candidate_count)]

        text, stats = format_memories_for_injection(
            memories,
            budget=_budget(level, hard_cap),
            content_level=level,
        )

        assert len(text) <= hard_cap
        assert stats.chars == len(text)
        if level is ContentLevel.NONE or candidate_count == 0:
            assert text == ""
        for index in range(candidate_count):
            assert text.count(f"<entry-{index}>") == text.count(f"</entry-{index}>")

    @pytest.mark.parametrize("hard_cap", [1, 64, 100])
    def test_no_complete_entry_fitting_returns_empty_not_wrapper_only(self, hard_cap):
        text, stats = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.DETAILED, hard_cap),
            content_level=ContentLevel.DETAILED,
        )

        assert text == ""
        assert stats.chars == 0
        assert stats.memory_count == 0

        assert stats.truncated_count == 0
        assert stats.header_chars == 0
        assert stats.footer_chars == 0
        assert stats.dropped_by_budget == 1

    def test_hard_cap_counts_every_wrapper_and_separator_character(self):
        complete, _ = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.DETAILED, 2400),
            content_level=ContentLevel.DETAILED,
        )

        too_small, stats = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.DETAILED, len(complete) - 1),
            content_level=ContentLevel.DETAILED,
        )

        assert too_small == ""
        assert stats.chars == 0

    def test_safety_boundaries_are_never_partially_sliced(self):
        text, _ = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.DETAILED, 2400),
            content_level=ContentLevel.DETAILED,
        )

        assert text.count("--- BEGIN HISTORICAL MEMORY REFERENCE ---") == 1
        assert text.count("--- END HISTORICAL MEMORY REFERENCE ---") == 1
        assert text.count("--- BEGIN REMINDER ---") == 1
        assert text.count("--- END REMINDER ---") == 1

    def test_rebuilding_drops_last_complete_entry_without_slicing_boundaries(self):
        first_only, _ = format_memories_for_injection(
            [_rich_memory(0)],
            budget=_budget(ContentLevel.COMPACT, 2400),
            content_level=ContentLevel.COMPACT,
        )
        exact_cap = len(first_only)

        rebuilt, stats = format_memories_for_injection(
            [_rich_memory(0), _rich_memory(1)],
            budget=_budget(ContentLevel.COMPACT, exact_cap),
            content_level=ContentLevel.COMPACT,
        )

        assert rebuilt == first_only
        assert "<entry-0>Complete memory 0.</entry-0>" in rebuilt
        assert "<entry-1>" not in rebuilt
        assert stats.memory_count == 1
        assert len(rebuilt) <= exact_cap

    def test_rebuilding_excludes_dropped_tail_from_truncation_stats(self):
        first = _rich_memory(0)
        truncated_tail = _rich_memory(1)
        truncated_tail["content"] = "tail content " * 100
        budget = _budget(
            ContentLevel.COMPACT,
            2400,
            memory_max_chars=60,
        )
        first_only, _ = format_memories_for_injection(
            [first],
            budget=budget,
            content_level=ContentLevel.COMPACT,
        )

        rebuilt, stats = format_memories_for_injection(
            [first, truncated_tail],
            budget=_budget(
                ContentLevel.COMPACT,
                len(first_only),
                memory_max_chars=60,
            ),
            content_level=ContentLevel.COMPACT,
        )

        assert rebuilt == first_only
        assert stats.memory_count == 1
        assert stats.truncated_count == 0
        assert stats.dropped_by_budget == 1
