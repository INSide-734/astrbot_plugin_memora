"""测试 prompt_builder.py — PromptBuilder."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.recall.processors.prompt_builder import (
    PromptBuilder,
    load_prompt_file,
    render_extraction_prompt,
)


def test_load_prompt_file_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "p.txt").write_text("内容", encoding="utf-8")
    assert load_prompt_file("p.txt", tmp_path) == "内容"


def test_render_extraction_prompt_placeholders() -> None:
    out = render_extraction_prompt(
        "类型:{chat_type}\n对话:{conversation}\n日期:{current_date}\n"
        "连续:{continuity_topics}\n兴趣:{interests}\n情绪:{emotion_tags}\n"
        "强度:{emotional_intensity}",
        conversation="C",
        current_date="D",
        chat_type="群聊",
        interests="猫、狗",
        emotional_intensity="0.8",
    )
    assert "类型:群聊" in out and "对话:C" in out and "日期:D" in out
    assert (
        "兴趣:猫、狗" in out and "强度:0.8" in out and "连续:" in out and "情绪:" in out
    )


def test_render_extraction_prompt_default_file_template_braces_safe() -> None:
    """默认文件模板含 JSON 示例花括号，渲染必须不抛异常且替换占位符。"""

    prompt_dir = Path(__file__).parent.parent / "core" / "prompts"
    template = load_prompt_file("private_chat_prompt.txt", prompt_dir)
    out = render_extraction_prompt(
        template,
        conversation="对话内容",
        current_date="2026-08-14 12:00",
        chat_type="私聊",
    )
    assert "{conversation}" not in out and "{current_date}" not in out
    assert "对话内容" in out


class TestPromptBuilder:
    @pytest.fixture
    def prompt_dir(self) -> Path:
        return Path(__file__).parent.parent / "core" / "prompts"

    def test_load_from_files(self, prompt_dir: Path) -> None:
        builder = PromptBuilder(prompt_dir=prompt_dir)
        assert len(builder.private_chat_prompt) > 0
        assert len(builder.group_chat_prompt) > 0

    def test_default_templates_demonstrate_stable_identity_labels(
        self, prompt_dir: Path
    ) -> None:
        """默认模板应示范多协议稳定标签，并禁止复制或猜测示例值。"""

        builder = PromptBuilder(prompt_dir=prompt_dir)
        prompts = (builder.private_chat_prompt, builder.group_chat_prompt)

        for prompt in prompts:
            assert "# 稳定参与者身份约束（系统确定，不可由模型覆盖）" in prompt
            assert (
                "示例中的 `QQ:123456` 和 "
                "`QQ官方:0123456789abcdef01234567:A1B2C3D4` "
                "仅用于展示协议标签格式，禁止复制到实际结果" in prompt
            )
            assert (
                "实际稳定标识只能使用末尾“稳定参与者身份约束”中明确提供的完整值"
                in prompt
            )
            assert (
                "未提供稳定参与者身份约束时，不得根据昵称、消息前缀中的普通账号字段"
                "或示例自行猜测、构造任何协议身份标识" in prompt
            )

        assert "张三（QQ:123456）提醒我" in builder.private_chat_prompt
        assert "张三（QQ:123456）安排明天下午3点会议" in (builder.private_chat_prompt)
        assert "张三（QQ:123456）需要确保会议准备就绪" in (builder.private_chat_prompt)

        for participant in (
            "张三（QQ:123456）",
            "李四（QQ:234567）",
            "王五（QQ:345678）",
        ):
            assert participant in builder.group_chat_prompt
        assert (
            '"participants": ["QQ:123456", "QQ:234567", "QQ:345678"]'
            in builder.group_chat_prompt
        )
        assert '"participants": ["张三", "李四", "王五", "我(Bot)"]' not in (
            builder.group_chat_prompt
        )

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
