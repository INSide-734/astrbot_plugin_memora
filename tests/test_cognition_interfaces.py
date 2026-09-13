"""认知组件接口与宿主边界行为测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.cognition.affection import AffectionManager, AffectionStore
from core.features.recall.processors.llm_client import LLMClient
from core.features.recall.processors.prompt_builder import PromptBuilder
from core.platform.composition.cognitive_lifecycle import (
    initialize_cognitive_components,
)


@pytest.mark.asyncio
async def test_affection_uses_real_llm_client_text_chat_boundary(tmp_db_path) -> None:
    """好感度分类应通过 LLMClient 的 complete 入口到达 text_chat。"""
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=SimpleNamespace(completion_text="compliment")
    )
    client = LLMClient(context=None, llm_provider=provider)
    store = AffectionStore(tmp_db_path)
    await store.initialize()
    try:
        manager = AffectionManager(store, llm_adapter=client)
        result = await manager.process_interaction("user", "group", "问候", "回复")

        assert result["interaction_type"] == "compliment"
        assert provider.text_chat.await_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_affection_invalid_classification_log_excludes_model_text(
    tmp_db_path,
) -> None:
    """无效分类日志不得记录模型返回正文。"""
    store = AffectionStore(tmp_db_path)
    await store.initialize()
    try:
        llm = AsyncMock()
        llm.complete.return_value = "classification-canary"
        manager = AffectionManager(store, llm_adapter=llm)
        with patch("core.features.cognition.affection.affection_manager.logger") as log:
            await manager.process_interaction("user", "group", "问候", "回复")

        assert all("classification-canary" not in str(call) for call in log.mock_calls)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_affection_propagates_llm_cancellation(tmp_db_path) -> None:
    """好感度分类收到取消时不得改成关键词回退结果。"""
    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=asyncio.CancelledError)
    client = LLMClient(context=None, llm_provider=provider)
    store = AffectionStore(tmp_db_path)
    await store.initialize()
    try:
        manager = AffectionManager(store, llm_adapter=client)
        with pytest.raises(asyncio.CancelledError):
            await manager.process_interaction("user", "group", "问候", "回复")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cognitive_lifecycle_requires_auxiliary_client(tmp_path) -> None:
    """认知生命周期缺少辅助客户端时应在创建 Store 前显式失败。"""
    initializer = SimpleNamespace(
        data_dir=str(tmp_path),
        auxiliary_llm_client=None,
    )

    with pytest.raises(RuntimeError, match="辅助 LLM 客户端"):
        await initialize_cognitive_components(initializer)


@pytest.mark.asyncio
async def test_cognitive_lifecycle_passes_auxiliary_client_to_affection(
    tmp_path,
) -> None:
    """认知生命周期应把工厂发布的同一客户端交给好感度管理器。"""
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=SimpleNamespace(completion_text="compliment")
    )
    auxiliary_client = LLMClient(context=None, llm_provider=provider)
    initializer = SimpleNamespace(
        data_dir=str(tmp_path),
        auxiliary_llm_client=auxiliary_client,
        config_manager=SimpleNamespace(get=lambda _key, default=None: default),
        llm_provider=None,
        affection_store=None,
        affection_manager=None,
        expression_store=None,
        expression_learner=None,
        jargon_filter=None,
        jargon_store=None,
        jargon_miner=None,
        jargon_query_service=None,
        relation_store=None,
        relation_manager=None,
    )

    await initialize_cognitive_components(initializer)
    try:
        result = await initializer.affection_manager.process_interaction(
            "user", "group", "问候", "回复"
        )
        assert result["interaction_type"] == "compliment"
        assert provider.text_chat.await_count == 1
    finally:
        await initializer.affection_store.close()


def test_prompt_builder_failure_log_excludes_persona_and_exception_text() -> None:
    """人格读取失败日志不得记录人格标识或异常正文。"""
    persona_manager = MagicMock()
    persona_manager.get_persona_v3_by_id.side_effect = RuntimeError(
        "persona-exception-canary"
    )
    context = SimpleNamespace(persona_manager=persona_manager)

    with patch("core.features.recall.processors.prompt_builder.logger") as log:
        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=context,
                persona_id="persona-id-canary",
            )
        )

    assert "你的人格设定" not in prompt
    messages = " ".join(str(call) for call in log.mock_calls)
    assert "persona-id-canary" not in messages
    assert "persona-exception-canary" not in messages


def test_prompt_builder_uses_sync_v3_prompt_with_budget() -> None:
    """人格构建应调用同步 v3 API，并将 prompt 截断到 800 字符。"""
    persona_manager = MagicMock()
    persona_manager.get_persona_v3_by_id.return_value = {"prompt": "P" * 900}
    context = SimpleNamespace(persona_manager=persona_manager)

    prompt = asyncio.run(
        PromptBuilder.build_system_prompt_with_persona(
            context=context,
            persona_id="persona-canary",
        )
    )

    assert "P" * 797 + "..." in prompt
    assert "P" * 798 + "..." not in prompt
    persona_manager.get_persona_v3_by_id.assert_called_once_with("persona-canary")
