"""测试 prompt_builder.py — PromptBuilder."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.processors.prompt_builder import PromptBuilder


class TestPromptBuilder:
    @pytest.fixture
    def prompt_dir(self) -> Path:
        return Path(__file__).parent.parent / "core" / "prompts"

    def test_load_from_files(self, prompt_dir: Path) -> None:
        builder = PromptBuilder(prompt_dir=prompt_dir)
        assert len(builder.private_chat_prompt) > 0
        assert len(builder.group_chat_prompt) > 0

    def test_load_with_custom_templates(self, prompt_dir: Path) -> None:
        config = {
            "group_chat_template": "自定义群聊模板: {conversation}",
            "private_chat_template": "自定义私聊模板: {conversation}",
        }
        builder = PromptBuilder(prompt_dir=prompt_dir, config=config)
        assert "自定义群聊模板" in builder.group_chat_prompt
        assert "自定义私聊模板" in builder.private_chat_prompt

    def test_load_partial_custom(self, prompt_dir: Path) -> None:
        config = {"group_chat_template": "自定义群聊: {conversation}"}
        builder = PromptBuilder(prompt_dir=prompt_dir, config=config)
        assert "自定义群聊" in builder.group_chat_prompt

    def test_load_without_prompt_dir(self) -> None:
        builder = PromptBuilder(prompt_dir=None)
        assert builder.private_chat_prompt == ""
        assert builder.group_chat_prompt == ""

    def test_load_nonexistent_dir_falls_back_to_hardcoded(self) -> None:
        builder = PromptBuilder(prompt_dir=Path("/nonexistent/path"))
        assert len(builder.private_chat_prompt) > 0
        assert len(builder.group_chat_prompt) > 0

    def test_build_system_prompt_base(self) -> None:
        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=None,
                persona_id=None,
            )
        )
        assert "JSON" in prompt
        assert "当前日期" in prompt

    def test_build_system_prompt_with_topic_guidance(self) -> None:
        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=None,
                persona_id=None,
                topic_segmentation_enabled=True,
                topic_segmentation_guidance="请按话题分割记忆",
            )
        )
        assert "请按话题分割记忆" in prompt

    def test_build_system_prompt_topic_disabled(self) -> None:
        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=None,
                persona_id=None,
                topic_segmentation_enabled=False,
                topic_segmentation_guidance="请按话题分割记忆",
            )
        )
        assert "请按话题分割记忆" not in prompt

    def test_build_system_prompt_with_continuity(self) -> None:
        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=None,
                persona_id=None,
                continuity_context="Still discussing project plans",
            )
        )
        assert "对话连续性提醒" in prompt

    def test_build_system_prompt_with_interest(self) -> None:
        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=None,
                persona_id=None,
                interest_profile=["machine learning", "python"],
            )
        )
        assert "对方兴趣参考" in prompt
        assert "machine learning" in prompt

    def test_build_system_prompt_with_persona(self) -> None:
        persona = MagicMock()
        persona.system_prompt = "你是一个活泼可爱的助手"

        persona_manager = AsyncMock()
        persona_manager.get_persona = AsyncMock(return_value=persona)

        context = MagicMock()
        context.persona_manager = persona_manager

        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=context,
                persona_id="test-persona",
            )
        )
        assert "你的人格设定" in prompt
        assert "活泼可爱" in prompt

    def test_build_system_prompt_persona_not_found(self) -> None:
        persona_manager = AsyncMock()
        persona_manager.get_persona = AsyncMock(return_value=None)

        context = MagicMock()
        context.persona_manager = persona_manager

        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=context,
                persona_id="missing-persona",
            )
        )
        assert "你的人格设定" not in prompt

    def test_build_system_prompt_no_persona_manager(self) -> None:
        context = MagicMock()
        context.persona_manager = None

        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=context,
                persona_id="test",
            )
        )
        assert "你的人格设定" not in prompt

    def test_build_system_prompt_persona_exception(self) -> None:
        persona_manager = AsyncMock()
        persona_manager.get_persona = AsyncMock(side_effect=RuntimeError("db error"))

        context = MagicMock()
        context.persona_manager = persona_manager

        prompt = asyncio.run(
            PromptBuilder.build_system_prompt_with_persona(
                context=context,
                persona_id="test",
            )
        )
        assert "你的人格设定" not in prompt
