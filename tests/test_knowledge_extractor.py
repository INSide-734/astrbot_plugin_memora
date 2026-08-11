"""KnowledgeExtractor 测试 — 抽取 + JSON 回退路径（N6 修复）。"""

import json
from unittest.mock import AsyncMock

import pytest

from core.features.knowledge.infrastructure import KnowledgeExtractor


class TestKnowledgeExtractor:
    @pytest.fixture
    def llm_client(self):
        return AsyncMock()

    @staticmethod
    def make_extractor(llm_client):
        return KnowledgeExtractor(llm_client)

    @pytest.mark.asyncio
    async def test_valid_json_response(self, llm_client):
        llm_client.complete.return_value = json.dumps(
            {
                "title": "test",
                "content": "test content",
                "category": "fact",
                "confidence": 0.9,
                "tags": [],
            }
        )
        extractor = self.make_extractor(llm_client)
        result = await extractor.extract("test memory content of enough length")
        assert result is not None

    @pytest.mark.asyncio
    async def test_json_in_code_block(self, llm_client):
        payload = json.dumps(
            {
                "title": "kb",
                "content": "c",
                "category": "fact",
                "confidence": 0.8,
                "tags": [],
            }
        )
        llm_client.complete.return_value = f"```json\n{payload}\n```"
        extractor = self.make_extractor(llm_client)
        result = await extractor.extract("test memory content x" * 5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_non_json_fallback_no_nameerror(self, llm_client):
        """N6：空 raw 状态应能进入回退路径且不触发 NameError。"""
        llm_client.complete.return_value = "plain text: title=fallback, content=test"
        extractor = self.make_extractor(llm_client)
        result = await extractor.extract("test " * 20)
        assert result is None or hasattr(result, "title")

    @pytest.mark.asyncio
    async def test_empty_response(self, llm_client):
        llm_client.complete.return_value = ""
        extractor = self.make_extractor(llm_client)
        result = await extractor.extract("test " * 20)
        assert result is None or hasattr(result, "title")
