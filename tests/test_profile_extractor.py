"""测试 profile_extractor.py — ProfileExtractor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.user_profile import TagCategory
from core.processors.profile_extractor import ProfileExtractor


class TestParseResponse:
    def test_parse_valid_json(self) -> None:
        raw = (
            '{"tags": [{"category": "interest", "value": "coffee", "confidence": 0.8}]}'
        )
        result = ProfileExtractor._parse_response(raw)
        assert "tags" in result
        assert len(result["tags"]) == 1

    def test_parse_json_with_markdown_code_block(self) -> None:
        raw = '```json\n{"tags": [{"category": "habit", "value": "early riser", "confidence": 0.6}]}\n```'
        result = ProfileExtractor._parse_response(raw)
        assert len(result.get("tags", [])) == 1

    def test_parse_invalid_json_returns_empty(self) -> None:
        raw = "This is not JSON at all"
        result = ProfileExtractor._parse_response(raw)
        assert result == {}

    def test_parse_empty_string(self) -> None:
        result = ProfileExtractor._parse_response("")
        assert result == {}

    def test_parse_json_with_embedded_object(self) -> None:
        raw = 'prefix {"tags": [{"category": "interest", "value": "music", "confidence": 0.7}]} suffix'
        result = ProfileExtractor._parse_response(raw)
        assert len(result.get("tags", [])) == 1


class TestBuildTags:
    def test_build_tags_basic(self) -> None:
        tag_data = [
            {"category": "interest", "value": "coffee", "confidence": 0.8},
        ]
        tags = ProfileExtractor._build_tags(tag_data)
        assert len(tags) == 1
        assert tags[0].category == TagCategory.INTEREST
        assert tags[0].value == "coffee"
        assert tags[0].confidence == 0.8

    def test_build_tags_max_5(self) -> None:
        tag_data = [
            {"category": "interest", "value": f"tag{i}", "confidence": 0.5}
            for i in range(10)
        ]
        tags = ProfileExtractor._build_tags(tag_data)
        assert len(tags) <= 5

    def test_build_tags_invalid_category_falls_back_to_custom(self) -> None:
        tag_data = [
            {"category": "nonexistent", "value": "test", "confidence": 0.5},
        ]
        tags = ProfileExtractor._build_tags(tag_data)
        assert len(tags) == 1
        assert tags[0].category == TagCategory.CUSTOM

    def test_build_tags_empty_value_skipped(self) -> None:
        tag_data = [
            {"category": "interest", "value": "", "confidence": 0.5},
            {"category": "personality", "value": "outgoing", "confidence": 0.7},
        ]
        tags = ProfileExtractor._build_tags(tag_data)
        assert len(tags) == 1
        assert tags[0].value == "outgoing"

    def test_build_tags_confidence_clamped(self) -> None:
        tag_data = [
            {"category": "interest", "value": "test", "confidence": 2.0},
            {"category": "habit", "value": "test2", "confidence": -0.5},
        ]
        tags = ProfileExtractor._build_tags(tag_data)
        assert len(tags) == 2
        assert tags[0].confidence == 1.0
        assert tags[1].confidence == 0.1

    def test_build_tags_value_too_long_skipped(self) -> None:
        tag_data = [
            {"category": "interest", "value": "a" * 60, "confidence": 0.5},
        ]
        tags = ProfileExtractor._build_tags(tag_data)
        assert len(tags) == 0

    def test_build_tags_empty_list(self) -> None:
        tags = ProfileExtractor._build_tags([])
        assert tags == []

    def test_build_tags_none(self) -> None:
        tags = ProfileExtractor._build_tags(None)
        assert tags == []


class TestKeywordFallback:
    def test_extract_keywords_from_message(self) -> None:
        msg = "我喜欢喝咖啡"
        tags = ProfileExtractor.extract_keywords_fallback(msg)
        assert len(tags) >= 1
        assert any(t.category == TagCategory.PREFERENCE for t in tags)

    def test_extract_keywords_habit(self) -> None:
        msg = "我经常去健身房"
        tags = ProfileExtractor.extract_keywords_fallback(msg)
        assert any(t.category == TagCategory.HABIT for t in tags)

    def test_extract_keywords_no_match(self) -> None:
        msg = "你好，今天天气不错"
        tags = ProfileExtractor.extract_keywords_fallback(msg)
        assert tags == []

    def test_extract_keywords_multiple(self) -> None:
        msg = "我喜欢跑步，也经常去图书馆"
        tags = ProfileExtractor.extract_keywords_fallback(msg)
        assert len(tags) >= 2


class TestProfileExtractor:
    @pytest.fixture
    def mock_llm_client(self) -> MagicMock:
        client = MagicMock()
        response = json.dumps(
            {
                "tags": [
                    {"category": "interest", "value": "coffee", "confidence": 0.8},
                    {"category": "personality", "value": "outgoing", "confidence": 0.7},
                ],
                "preferences": {
                    "reply_style": "casual",
                    "preferred_topics": ["coffee", "music"],
                },
            }
        )
        client.complete = AsyncMock(return_value=response)
        return client

    def test_extract_success(self, mock_llm_client: MagicMock) -> None:
        import asyncio

        extractor = ProfileExtractor(llm_client=mock_llm_client)
        tags, prefs = asyncio.run(extractor.extract("I like coffee", "That's great!"))
        assert len(tags) >= 1
        assert len(prefs.get("preferred_topics", [])) >= 1

    def test_extract_no_llm_client_returns_empty(self) -> None:
        import asyncio

        extractor = ProfileExtractor(llm_client=None)
        tags, prefs = asyncio.run(extractor.extract("some message"))
        assert tags == []
        assert prefs == {}

    def test_extract_llm_failure_returns_empty(self) -> None:
        import asyncio

        client = MagicMock()
        client.complete = AsyncMock(side_effect=RuntimeError("LLM error"))
        extractor = ProfileExtractor(llm_client=client)
        tags, prefs = asyncio.run(extractor.extract("some message"))
        assert tags == []
        assert prefs == {}

    def test_extract_with_context(self, mock_llm_client: MagicMock) -> None:
        import asyncio

        extractor = ProfileExtractor(llm_client=mock_llm_client)
        tags, prefs = asyncio.run(
            extractor.extract(
                "user message",
                bot_response="bot reply",
                context="previous conversation context",
            )
        )
        assert len(tags) >= 1
