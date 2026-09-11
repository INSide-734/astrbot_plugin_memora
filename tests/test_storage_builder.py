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
        content, metadata = builder.build_storage_format(
            fallback_excerpt="fallback",
            structured_data=data,
            is_group_chat=False,
        )
        assert "咖啡" in content
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
