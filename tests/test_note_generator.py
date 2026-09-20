"""测试 NoteGenerator 的生成与 raw 回退路径。"""

import json
from unittest.mock import AsyncMock

import pytest

from core.features.notes.infrastructure import NoteGenerator


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
        """N6：预先声明空 raw 后应能进入回退路径。"""
        llm_client.complete.return_value = "plain text note title: Fallback"
        gen = self.make_gen(llm_client)
        result = await gen.generate("x" * 100)
        # 不得触发 NameError；允许返回 None 或解析后的字典。
        assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_non_object_json_is_treated_as_unparseable(self, llm_client):
        """合法但非对象的 JSON 必须按「无法解析」返回 None。"""
        llm_client.complete.return_value = "[1, 2]"
        gen = self.make_gen(llm_client)
        assert await gen.generate("x" * 100) is None

    @pytest.mark.asyncio
    async def test_non_list_tags_keeps_generated_title_and_content(self, llm_client):
        """非法 tags 容器只丢弃标签，不能连标题正文一起丢给 fallback。"""
        llm_client.complete.return_value = json.dumps(
            {"title": "T", "content": "C", "tags": 5}
        )
        gen = self.make_gen(llm_client)
        assert await gen.generate("x" * 100) == {
            "title": "T",
            "content": "C",
            "tags": [],
        }

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
