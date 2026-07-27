"""测试 NoteGenerator — generation + raw fallback path (N6 fix)."""

import json
from unittest.mock import AsyncMock

import pytest

from core.processors.note_generator import NoteGenerator


class TestNoteGenerator:
    @pytest.fixture
    def llm_client(self):
        return AsyncMock()

    @staticmethod
    def make_gen(llm_client, min_length=1):
        return NoteGenerator(llm_client=llm_client, min_length=min_length)

    @pytest.mark.asyncio
    async def test_valid_generation(self, llm_client):
        llm_client.complete.return_value = json.dumps(
            {
                "title": "Meeting Notes",
                "content": "### Key points\n- item 1",
                "tags": ["meeting"],
            }
        )
        gen = self.make_gen(llm_client)
        result = await gen.generate("x" * 100)
        assert result is not None
        assert result["title"] == "Meeting Notes"

    @pytest.mark.asyncio
    async def test_non_json_fallback_no_nameerror(self, llm_client):
        """N6: raw="" pre-declaration ensures fallback is reachable."""
        llm_client.complete.return_value = "plain text note title: Fallback"
        gen = self.make_gen(llm_client)
        result = await gen.generate("x" * 100)
        # Must not raise NameError; may return None or parsed dict
        assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_below_min_length_skipped(self, llm_client):
        gen = NoteGenerator(llm_client=llm_client, min_length=100)
        result = await gen.generate("short")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_llm_client_returns_none(self):
        gen = NoteGenerator(llm_client=None)
        result = await gen.generate("x" * 100)
        assert result is None

    def test_title_fallback(self):
        text = "This is a very long first line that should be truncated to eighty characters\nsecond line"
        title = NoteGenerator.extract_title_fallback(text)
        assert len(title) <= 80
