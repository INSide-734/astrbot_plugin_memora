"""测试 storage_builder.py — StorageBuilder."""

from __future__ import annotations

import pytest

from core.features.recall.processors.storage_builder import StorageBuilder


class TestStorageBuilder:
    @pytest.fixture
    def builder(self) -> StorageBuilder:
        return StorageBuilder()

    def test_build_private_chat(self, builder: StorageBuilder) -> None:
        data = {
            "summary": "用户讨论了咖啡",
            "topics": ["咖啡"],
            "key_facts": ["用户喜欢拿铁"],
            "sentiment": "positive",
        }
        _, metadata = builder.build_storage_format(
            fallback_excerpt="fallback",
            structured_data=data,
            is_group_chat=False,
        )
        assert metadata["privacy_level"] == "confidential"
        assert metadata["interaction_type"] == "private_chat"

    def test_build_group_chat(self, builder: StorageBuilder) -> None:
        data = {
            "summary": "群聊讨论",
            "topics": ["话题"],
            "key_facts": ["fact1"],
            "sentiment": "neutral",
            "participants": ["Alice", "Bob"],
        }
        content, metadata = builder.build_storage_format(
            fallback_excerpt="fallback",
            structured_data=data,
            is_group_chat=True,
        )
        assert metadata["privacy_level"] == "public"
        assert metadata["interaction_type"] == "group_chat"
        assert "Alice" in metadata["participants"]

    def test_build_canonical_summary(self, builder: StorageBuilder) -> None:
        data = {
            "summary": "测试摘要",
            "key_facts": ["事实A", "事实B", "事实C"],
            "topics": ["topic"],
            "sentiment": "neutral",
        }
        content, metadata = builder.build_storage_format(
            fallback_excerpt="fallback",
            structured_data=data,
            is_group_chat=False,
        )
        assert "事实A" in metadata["canonical_summary"]
        assert metadata["summary_schema_version"] == "v2"

    def test_build_fallback_excerpt(self, builder: StorageBuilder) -> None:
        data = {
            "summary": "",
            "key_facts": [],
            "topics": [],
            "sentiment": "neutral",
        }
        content, metadata = builder.build_storage_format(
            fallback_excerpt="这是一段回退文本",
            structured_data=data,
            is_group_chat=False,
        )
        assert content == "这是一段回退文本"
        assert metadata["canonical_summary"] == ""

    def test_paraphrased_summary_does_not_repeat_canonical_facts(
        self, builder: StorageBuilder
    ) -> None:
        """同义摘要不得再拼入正文，摘要省略的事实仍须可检索。"""
        data = {
            "summary": "用户偏爱拿铁咖啡。",
            "key_facts": ["用户喜欢喝拿铁咖啡", "用户只在上午喝咖啡"],
        }

        content, metadata = builder.build_storage_format("", data, False)

        assert content.count("拿铁") == 1
        assert "用户只在上午喝咖啡" in content
        assert " | " not in content
        assert "偏爱" not in metadata["canonical_summary"]
        assert "偏爱" in metadata["persona_summary"]

    def test_canonical_body_keeps_facts_beyond_five(
        self, builder: StorageBuilder
    ) -> None:
        """正文改用事实后，不得沿用补充片段的五条上限而丢失事实。"""
        facts = [
            "用户喜欢拿铁咖啡",
            "用户通常上午喝咖啡",
            "用户周末会去跑步",
            "用户更喜欢安静环境",
            "用户最近在学习绘画",
            "用户不喜欢香菜",
        ]

        content, _ = builder.build_storage_format(
            "", {"summary": "用户介绍了个人偏好。", "key_facts": facts}, False
        )

        assert all(fact in content for fact in facts)

    def test_untrusted_key_facts_types_are_converged(
        self, builder: StorageBuilder
    ) -> None:
        """非列表/非字符串事实不得抛错、逐字符拆分或写入字面量。"""

        for raw in (None, "用户喜欢拿铁", 42):
            content, metadata = builder.build_storage_format(
                "回退摘录",
                {"summary": "摘要", "key_facts": raw},
                False,
            )
            assert content == "摘要"
            assert metadata["key_facts"] == []

        content, metadata = builder.build_storage_format(
            "回退摘录",
            {"summary": "摘要", "key_facts": [None, " 拿铁 ", "  ", 7, "美式"]},
            False,
        )
        assert metadata["key_facts"] == ["拿铁", "美式"]
        assert content == "拿铁；美式"
        assert "None" not in content

    def test_summary_without_facts_preserves_literal_pipe(
        self, builder: StorageBuilder
    ) -> None:
        """移除组装分隔符不能破坏原文有含义的管道符。"""
        summary = "用户用 cat notes.txt | sort 整理笔记。"

        content, _ = builder.build_storage_format(
            "回退摘录", {"summary": summary, "key_facts": []}, False
        )

        assert "cat notes.txt | sort" in content
        assert "回退摘录" not in content
