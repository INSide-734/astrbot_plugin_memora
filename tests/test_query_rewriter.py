"""测试 QueryRewriter — intent classification + query expansion."""

from unittest.mock import AsyncMock

import pytest
from core.retrieval.query_rewriter import QueryIntent, QueryRewriter


VALID_INTENTS = {"default", "factual", "relational", "temporal", "preference", "contextual"}


class TestQueryIntent:

    def test_all_intents_exist(self):
        """所有 expected intent string values should be representable via QueryIntent."""
        for intent_name in VALID_INTENTS:
            qi = QueryIntent(intent=intent_name)
            assert qi.intent == intent_name

    def test_from_keywords_returns_query_intent(self):
        """fallback factory should always return a valid QueryIntent."""
        result = QueryIntent.from_keywords("上次那个事")
        assert isinstance(result, QueryIntent)
        assert result.intent in VALID_INTENTS
        assert len(result.rewritten_queries) > 0

    def test_from_keywords_empty_query(self):
        result = QueryIntent.from_keywords("")
        assert isinstance(result, QueryIntent)
        assert result.rewritten_queries == [""]


class TestQueryRewriter:

    @pytest.fixture
    def llm_client(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_keyword_fallback_no_llm(self):
        """在没有 LLM client, should use keyword-based fallback returning QueryIntent."""
        rw = QueryRewriter()
        result = await rw.rewrite("上次那个事")
        assert isinstance(result, QueryIntent)
        assert len(result.rewritten_queries) > 0

    @pytest.mark.asyncio
    async def test_rewrite_returns_query_intent(self):
        rw = QueryRewriter()
        result = await rw.rewrite("小明喜欢的餐厅")
        assert isinstance(result, QueryIntent)
        assert len(result.rewritten_queries) > 0

    @pytest.mark.asyncio
    async def test_empty_query_handled(self):
        rw = QueryRewriter()
        result = await rw.rewrite("")
        assert isinstance(result, QueryIntent)
        assert result.rewritten_queries == [""]

    @pytest.mark.asyncio
    async def test_english_query_rewrite(self):
        rw = QueryRewriter()
        result = await rw.rewrite("what did we discuss about the project")
        assert isinstance(result, QueryIntent)
        assert len(result.rewritten_queries) > 0

    @pytest.mark.asyncio
    async def test_disabled_returns_fallback(self):
        rw = QueryRewriter(enabled=False)
        result = await rw.rewrite("anything")
        assert isinstance(result, QueryIntent)
        assert result.intent != ""  # should have a fallback intent

    @pytest.mark.asyncio
    async def test_llm_rewrite_with_mock(self, llm_client):
        """在 LLM client, should parse JSON response into QueryIntent."""
        import json
        llm_client.return_value = json.dumps({
            "intent": "temporal",
            "extracted_entities": ["那个事"],
            "time_reference": "recent",
            "rewritten_queries": ["最近对话", "之前提到的话题"],
            "memory_types": ["EPISODIC"],
        })
        rw = QueryRewriter(llm_caller=llm_client)
        result = await rw.rewrite("上次那个事")
        assert isinstance(result, QueryIntent)
        assert result.intent == "temporal"
        assert "最近对话" in result.rewritten_queries

    @pytest.mark.asyncio
    async def test_llm_rewrite_returns_fallback_on_error(self, llm_client):
        """当 LLM raises, fallback to keyword-based intent."""
        llm_client.side_effect = RuntimeError("LLM down")
        rw = QueryRewriter(llm_caller=llm_client)
        result = await rw.rewrite("上次那个事")
        assert isinstance(result, QueryIntent)
        assert result.intent in VALID_INTENTS

    @pytest.mark.asyncio
    async def test_llm_rewrite_invalid_json_uses_fallback(self, llm_client):
        """当 LLM returns invalid JSON, fallback to keyword intent."""
        llm_client.return_value = "not valid json {{{"
        rw = QueryRewriter(llm_caller=llm_client)
        result = await rw.rewrite("上次那个事")
        assert isinstance(result, QueryIntent)

    def test_parse_llm_response_valid(self) -> None:
        """_parse_llm_response parses valid JSON."""
        import json
        raw = json.dumps({
            "intent": "relational",
            "rewritten_queries": ["query1", "query2"],
        })
        result = QueryRewriter._parse_llm_response(raw, "fallback")
        assert result is not None
        assert result.intent == "relational"
        assert len(result.rewritten_queries) == 2

    def test_parse_llm_response_invalid(self) -> None:
        """_parse_llm_response returns None for invalid input."""
        result = QueryRewriter._parse_llm_response("not json", "fallback")
        assert result is None

    def test_parse_llm_response_empty(self) -> None:
        """_parse_llm_response returns None for empty string."""
        result = QueryRewriter._parse_llm_response("", "fallback")
        assert result is None

    def test_enabled_getter_setter(self) -> None:
        """enabled property getter/setter works correctly."""
        rw = QueryRewriter()
        assert rw.enabled is True
        rw.enabled = False
        assert rw.enabled is False
        rw.enabled = True
        assert rw.enabled is True

    def test_from_keywords_relationship(self) -> None:
        """from_keywords detects relationship intent."""
        result = QueryIntent.from_keywords("小明和小红是什么关系")
        assert result.intent == "relationship"

    def test_from_keywords_temporal(self) -> None:
        """from_keywords detects temporal intent."""
        result = QueryIntent.from_keywords("昨天发生了什么")
        assert result.intent == "temporal"

    def test_from_keywords_factual(self) -> None:
        """from_keywords detects factual intent."""
        result = QueryIntent.from_keywords("Python是什么")
        assert result.intent == "factual"

    def test_from_keywords_default(self) -> None:
        """from_keywords defaults to default intent."""
        result = QueryIntent.from_keywords("你好")
        assert result.intent == "default"
