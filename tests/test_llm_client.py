"""llm_client.py 测试 — LLMClient。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.processors.llm_client import LLMClient


class TestLLMClient:
    @pytest.fixture
    def mock_context(self) -> MagicMock:
        ctx = MagicMock()
        ctx.get_using_provider.return_value = None
        ctx.get_provider_by_id.return_value = None
        return ctx

    @pytest.fixture
    def mock_provider(self) -> MagicMock:
        provider = MagicMock()
        response = AsyncMock()
        response.completion_text = "test response"
        provider.text_chat = AsyncMock(return_value=response)
        return provider

    def test_get_provider_from_direct_reference(self, mock_provider: MagicMock) -> None:
        client = LLMClient(context=None, llm_provider=mock_provider)
        result = client.get_current_llm_provider()
        assert result is mock_provider

    def test_get_provider_from_context(
        self, mock_context: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_context.get_using_provider.return_value = mock_provider
        client = LLMClient(context=mock_context)
        result = client.get_current_llm_provider()
        assert result is mock_provider

    def test_get_provider_by_id_string(
        self, mock_context: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_context.get_provider_by_id.return_value = mock_provider
        client = LLMClient(context=mock_context, llm_provider="provider-123")
        result = client.get_current_llm_provider()
        assert result is mock_provider

    def test_get_provider_fallback_to_using_provider(
        self, mock_context: MagicMock, mock_provider: MagicMock
    ) -> None:
        # get_provider_by_id returns None, fallback to get_using_provider
        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        client = LLMClient(context=mock_context, llm_provider="invalid-id")
        result = client.get_current_llm_provider()
        assert result is mock_provider

    def test_get_provider_none_when_all_fail(self, mock_context: MagicMock) -> None:
        mock_context.get_using_provider.return_value = None
        mock_context.get_provider_by_id.return_value = None
        client = LLMClient(context=mock_context)
        result = client.get_current_llm_provider()
        assert result is None

    def test_get_provider_no_context_no_direct(self) -> None:
        client = LLMClient(context=None, llm_provider=None)
        result = client.get_current_llm_provider()
        assert result is None

    def test_call_llm_success(self, mock_provider: MagicMock) -> None:
        client = LLMClient(context=None, llm_provider=mock_provider)
        result = asyncio.run(client.call_llm_with_retry("test prompt", "system prompt"))
        assert result == "test response"
        mock_provider.text_chat.assert_called_once()

    def test_complete_uses_one_physical_provider_call(
        self, mock_provider: MagicMock
    ) -> None:
        """通用单次入口失败时不得为同一预算 reservation 重试。"""

        mock_provider.text_chat = AsyncMock(side_effect=RuntimeError("调用失败"))
        client = LLMClient(context=None, llm_provider=mock_provider)

        with pytest.raises(RuntimeError, match="调用失败"):
            asyncio.run(client.complete("test prompt"))

        mock_provider.text_chat.assert_awaited_once()

    def test_call_llm_no_provider_raises(self) -> None:
        client = LLMClient(context=None, llm_provider=None)
        with pytest.raises(RuntimeError):
            asyncio.run(client.call_llm_with_retry("prompt", "system"))

    def test_call_llm_retry_on_failure(self, mock_provider: MagicMock) -> None:
        # First call fails, second succeeds
        response = AsyncMock()
        response.completion_text = "retry success"
        mock_provider.text_chat = AsyncMock(
            side_effect=[
                RuntimeError("first call failed"),
                response,
            ]
        )
        client = LLMClient(context=None, llm_provider=mock_provider)
        result = asyncio.run(
            client.call_llm_with_retry("prompt", "system", max_retries=3)
        )
        assert result == "retry success"
        assert mock_provider.text_chat.call_count == 2

    def test_call_llm_max_retries_exhausted(self, mock_provider: MagicMock) -> None:
        mock_provider.text_chat = AsyncMock(side_effect=RuntimeError("always fails"))
        client = LLMClient(context=None, llm_provider=mock_provider)
        with pytest.raises(RuntimeError):
            asyncio.run(client.call_llm_with_retry("prompt", "system", max_retries=2))
        assert mock_provider.text_chat.call_count == 2

    def test_get_provider_with_context_exception(
        self, mock_context: MagicMock, mock_provider: MagicMock
    ) -> None:
        # get_provider_by_id raises, but fallback works
        mock_context.get_provider_by_id.side_effect = RuntimeError("not found")
        mock_context.get_using_provider.return_value = mock_provider
        client = LLMClient(context=mock_context, llm_provider="bad-id")
        result = client.get_current_llm_provider()
        assert result is mock_provider
