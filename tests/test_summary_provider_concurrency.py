"""总结物理 Provider 限流与取消释放契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.event_handler import EventHandler
from core.features.recall.processors.llm_client import LLMClient
from core.shared.summary_llm_limiter import SummaryLlmLimiter


@pytest.mark.asyncio
async def test_limiter_one_serializes_cross_window_provider_attempts() -> None:
    """容量为一时两个独立调用不得同时进入物理 Provider。"""
    limiter = SummaryLlmLimiter(1)
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak = 0

    async def generate(**_kwargs: object) -> SimpleNamespace:
        """记录 Provider 临界区并等待测试释放。"""
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        await release.wait()
        active -= 1
        return SimpleNamespace(completion_text="ok")

    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=generate)
    client = LLMClient(context=None, llm_provider=provider, limiter=limiter)

    first = asyncio.create_task(client.complete("first"))
    await entered.wait()
    second = asyncio.create_task(client.complete("second"))
    assert limiter.active == 1
    assert provider.text_chat.await_count == 1

    release.set()
    await asyncio.gather(first, second)

    assert peak == 1
    assert limiter.active == 0
    assert provider.text_chat.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_provider_attempt_releases_limiter_permit() -> None:
    """取消物理 Provider 调用后 limiter permit 必须归还。"""
    limiter = SummaryLlmLimiter(1)
    entered = asyncio.Event()
    provider = MagicMock()

    async def generate(**_kwargs: object) -> SimpleNamespace:
        """阻塞到调用方取消。"""
        entered.set()
        await asyncio.Event().wait()
        return SimpleNamespace(completion_text="never")

    provider.text_chat = AsyncMock(side_effect=generate)
    client = LLMClient(context=None, llm_provider=provider, limiter=limiter)
    task = asyncio.create_task(client.complete("cancel"))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert limiter.active == 0


@pytest.mark.asyncio
async def test_retry_reacquires_limiter_for_each_provider_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每次物理重试都应独立持有 permit，退避期间不占用。"""
    limiter = SummaryLlmLimiter(1)
    active_during_attempt: list[int] = []

    async def generate(**_kwargs: object) -> SimpleNamespace:
        """首次失败、第二次成功，并记录 Provider 临界区活动数。"""
        active_during_attempt.append(limiter.active)
        if len(active_during_attempt) == 1:
            raise RuntimeError("provider_failed")
        return SimpleNamespace(completion_text="ok")

    monkeypatch.setattr(
        "core.features.recall.processors.llm_client.asyncio.sleep",
        AsyncMock(),
    )
    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=generate)
    client = LLMClient(context=None, llm_provider=provider, limiter=limiter)

    result = await client.call_llm_with_retry("prompt", "system", max_retries=2)

    assert result == "ok"
    assert active_during_attempt == [1, 1]
    assert limiter.active == 0


def test_query_rewrite_client_does_not_share_summary_limiter() -> None:
    """在线查询改写不得占用后台总结的物理并发配额。"""
    limiter = SummaryLlmLimiter(1)
    processor = MagicMock()
    processor.llm_client = LLMClient(
        context=None,
        llm_provider=MagicMock(),
        limiter=limiter,
    )
    config = MagicMock()
    config.get.return_value = None
    config.get_section.return_value = {}

    handler = EventHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
    )

    assert processor.llm_client._limiter is limiter
    assert handler._query_rewrite_llm_client._limiter is None
