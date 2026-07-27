"""提示词构建器 — 支持自定义提示词模板（配置覆盖文件模板）"""

from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger


class PromptBuilder:
    """加载提示词模板 + 构建带人格的 system_prompt

    优先级: WebUI 自定义模板 > 文件模板 (core/prompts/*.txt) > 硬编码最小回退
    """

    def __init__(
        self, prompt_dir: Path | None = None, config: dict[str, Any] | None = None
    ):
        self.private_chat_prompt = ""
        self.group_chat_prompt = ""
        self._config = config or {}
        if prompt_dir:
            self._load_prompts(prompt_dir)

    def _load_prompts(self, prompt_dir: Path) -> None:
        # 1. 优先检查配置中的自定义模板
        custom_group = (self._config.get("group_chat_template") or "").strip()
        custom_private = (self._config.get("private_chat_template") or "").strip()

        if custom_group and custom_private:
            self.group_chat_prompt = custom_group
            self.private_chat_prompt = custom_private
            logger.info("[MemoryProcessor] 使用 WebUI 自定义提示词模板")
            return

        # 2. 回退到文件模板
        try:
            if not custom_private:
                private_prompt_file = prompt_dir / "private_chat_prompt.txt"
                with open(private_prompt_file, encoding="utf-8") as f:
                    self.private_chat_prompt = f.read()
            else:
                self.private_chat_prompt = custom_private

            if not custom_group:
                group_prompt_file = prompt_dir / "group_chat_prompt.txt"
                with open(group_prompt_file, encoding="utf-8") as f:
                    self.group_chat_prompt = f.read()
            else:
                self.group_chat_prompt = custom_group

            source = "自定义+文件" if (custom_group or custom_private) else "文件"
            logger.info(f"[MemoryProcessor] 提示词模板加载成功（来源: {source}）")
        except Exception as e:
            logger.error(f"[MemoryProcessor] 加载提示词模板失败: {e}")
            # 3. 硬编码最小回退 — 仅当文件加载失败且无自定义模板时使用
            if not self.private_chat_prompt:
                self.private_chat_prompt = (
                    "分析以下对话并生成JSON格式的记忆:\n{conversation}\n\n"
                    "输出格式:\n"
                    '{"summary": "摘要", "topics": ["主题"], "key_facts": ["事实"], '
                    '"sentiment": "neutral", "importance": 0.5}\n'
                )
            if not self.group_chat_prompt:
                self.group_chat_prompt = (
                    "分析以下群聊对话并生成JSON格式的记忆:\n{conversation}\n\n"
                    "输出格式:\n"
                    '{"summary": "摘要", "topics": ["主题"], "key_facts": ["事实"], '
                    '"participants": ["参与者"], "sentiment": "neutral", "importance": 0.5}\n'
                )

    @staticmethod
    async def build_system_prompt_with_persona(
        context,
        persona_id: str | None,
        continuity_context: str | None = None,
        interest_profile: list[str] | None = None,
        topic_segmentation_enabled: bool = True,
        topic_segmentation_guidance: str = "",
    ) -> str:
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        base_prompt = (
            "你正在总结对话记忆。请严格按照JSON格式输出。\n"
            f"当前日期时间: {current_date}\n"
            '重要: 请将对话中出现的相对时间表达（如"今天"、"明天"、"昨天"、'
            '"下周"、"上个月"等）转换为具体日期后再写入记忆，'
            "以便未来查阅时仍能准确理解时间信息。"
        )

        if topic_segmentation_enabled and topic_segmentation_guidance:
            base_prompt += f"\n\n{topic_segmentation_guidance}"

        # 注入对话连续性上下文（预算：300 字符）
        if continuity_context:
            ctx_text = continuity_context
            if len(ctx_text) > 300:
                ctx_text = ctx_text[:297] + "..."
            base_prompt += (
                f"\n\n## 对话连续性提醒\n"
                f"{ctx_text}\n"
                f"如果当前对话与上述未完成话题相关，请在记忆中体现这种关联性，"
                f"并将延续性话题的 importance 提高 0.1-0.2。"
            )

        # 注入兴趣画像（限制 top 5）
        if interest_profile:
            top_interests = interest_profile[:5]
            interests_str = "、".join(top_interests)
            base_prompt += (
                f"\n\n## 对方兴趣参考\n"
                f"已知对方关注/感兴趣的话题: {interests_str}\n"
                f"如果当前对话涉及以上兴趣话题，请在记忆中标注其个人相关性，"
                f"并将 importance 提高 0.1-0.15。"
            )

        if not persona_id:
            logger.debug("[MemoryProcessor] 未指定人格ID，使用基础提示词")
            return base_prompt

        if not context:
            logger.debug("[MemoryProcessor] Context 未设置，使用基础提示词")
            return base_prompt

        try:
            persona_manager = getattr(context, "persona_manager", None)
            if not persona_manager:
                logger.warning(
                    "[MemoryProcessor] persona_manager 不可用，使用基础提示词"
                )
                return base_prompt

            persona = await persona_manager.get_persona(persona_id)
            if not persona:
                logger.warning(
                    f"[MemoryProcessor] 人格 '{persona_id}' 不存在，使用基础提示词"
                )
                return base_prompt

            if not persona.system_prompt:
                logger.debug(
                    f"[MemoryProcessor] 人格 '{persona_id}' 无 system_prompt，使用基础提示词"
                )
                return base_prompt

            persona_prompt = persona.system_prompt.strip()
            if not persona_prompt:
                logger.debug(
                    f"[MemoryProcessor] 人格 '{persona_id}' 的 system_prompt 为空，使用基础提示词"
                )
                return base_prompt

            # 人格提示词预算控制（默认 800 字符）
            persona_budget = 800
            if len(persona_prompt) > persona_budget:
                persona_prompt = persona_prompt[: persona_budget - 3] + "..."

            logger.info(
                f"[MemoryProcessor] 成功加载人格 '{persona_id}' 的提示词 "
                f"(长度={len(persona_prompt)}字符)"
            )

            return (
                f"{base_prompt}\n\n"
                f"## 你的人格设定\n"
                f"{persona_prompt}\n\n"
                f"## 记忆总结要求\n"
                f"在总结对话记忆时,你需要:\n"
                f"1. **保持你的人格特色**: 使用符合上述人格设定的语气、用词习惯和表达方式\n"
                f'2. **第一人称视角**: 以"我"的视角回顾对话,不要说"bot"、"助手"等第三人称\n'
                f"3. **体现你的关注点**: 根据你的人格特点,侧重记录你会关注的信息\n"
                f"4. **自然真实**: 让记忆读起来像是你本人在回忆这段对话,而不是机械的客观描述\n"
                f"5. **时间转换**: 将对话中的相对时间（今天、明天、下周等）转换为具体日期（当前日期: {current_date}）\n\n"
                f"例如:\n"
                f'- 如果你是活泼可爱的性格,记忆中可以使用"呀"、"呢"、"~"等语气词\n'
                f"- 如果你是专业严谨的性格,记忆应该用词准确、逻辑清晰、格式规范\n"
                f"- 如果你是幽默风趣的性格,记忆中可以包含轻松的表达和有趣的观察"
            )

        except ValueError as e:
            logger.warning(f"[MemoryProcessor] 人格 '{persona_id}' 不存在: {e}")
            return base_prompt
        except Exception as e:
            logger.error(
                f"[MemoryProcessor] 获取人格提示词时发生错误: {e}", exc_info=True
            )
            return base_prompt
