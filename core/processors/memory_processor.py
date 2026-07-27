"""
记忆处理器 - 使用LLM将对话历史处理为结构化记忆
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..identity.memory import build_memory_identity_context
from ..models.conversation_models import Message
from ..models.memory_atom import MemoryAtom
from ..security.guardrails import MemoryExtractionResult, validate_llm_response
from .atom_classifier import classify_atoms
from .conversation_formatter import ConversationFormatter
from .json_parser import JsonParser
from .llm_client import LLMClient
from .prompt_builder import PromptBuilder
from .quality_validator import QualityValidator
from .storage_builder import StorageBuilder


class MemoryProcessor:
    """
    记忆处理器

    使用LLM将对话历史转换为结构化记忆。
    支持私聊和群聊两种场景的不同处理策略。
    """

    def __init__(
        self,
        context=None,
        llm_provider: Any = None,
        config: dict[str, Any] | None = None,
    ):
        """初始化 LLM、Prompt、解析、质量校验与存储格式协作对象。"""

        self.context = context
        self.config = config or {}

        self.llm_client = LLMClient(context, llm_provider)
        prompt_dir = Path(__file__).parent.parent / "prompts"
        prompt_config = {
            k: self.config.get(k, "")
            for k in ("group_chat_template", "private_chat_template")
        }
        self.prompt_builder = PromptBuilder(prompt_dir, config=prompt_config)
        self.quality = QualityValidator()
        self.json_parser = JsonParser(self.quality)
        self.formatter = ConversationFormatter()
        self.storage = StorageBuilder()
        self.prompt_protection_service: Any | None = None
        self._topic_guidance = self._load_topic_guidance(prompt_dir)
        self._topic_segmentation_enabled = self.config.get(
            "topic_segmentation.enabled", True
        )

    @staticmethod
    def _load_topic_guidance(prompt_dir: Path | None) -> str:
        """加载话题分割引导文本。"""
        if prompt_dir is None:
            return ""
        path = prompt_dir / "topic_segmentation_guidance.txt"
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            logger.debug("[MemoryProcessor] 未找到话题分割引导文本")
            return ""

    # ---- 对外 API ----

    @property
    def conversation_formatter(self):
        """公开访问对话格式化器，供话题分割策略使用。"""
        return self.formatter

    @property
    def llm_client_instance(self):
        """公开访问 LLM 客户端，供话题分割策略使用。"""
        return self.llm_client

    async def process_conversation(
        self,
        messages: list[Message],
        is_group_chat: bool = False,
        persona_id: str | None = None,
        emotion_tags: list[str] | None = None,
        emotional_intensity: float = 0.5,
        serial_position_hint: str | None = None,
        interest_profile: list[str] | None = None,
        continuity_context: str | None = None,
    ) -> list[dict[str, Any]]:
        """处理对话批次并生成结构化记忆（可能返回多条独立话题记忆）。

        返回:
            list[dict]: 每条 dict = {content, metadata, importance, atoms}
        """
        if not messages:
            raise ValueError("消息列表不能为空")

        conversation_text = self.formatter.format_conversation(messages)
        identity_context = build_memory_identity_context(messages)

        current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        if is_group_chat:
            prompt = self.prompt_builder.group_chat_prompt.replace(
                "{conversation}", conversation_text
            )
        else:
            prompt = self.prompt_builder.private_chat_prompt.replace(
                "{conversation}", conversation_text
            )
        prompt = prompt.replace("{current_date}", current_date)
        prompt += identity_context.prompt_constraint()
        identity_metadata = identity_context.metadata()

        conversation_type = "群聊" if is_group_chat else "私聊"
        try:
            logger.info(
                f"[MemoryProcessor] 准备调用 LLM，对话类型={conversation_type}, 消息数={len(messages)}"
            )

            system_prompt = await self.prompt_builder.build_system_prompt_with_persona(
                self.context,
                persona_id,
                continuity_context=continuity_context,
                interest_profile=interest_profile,
                topic_segmentation_enabled=self._topic_segmentation_enabled,
                topic_segmentation_guidance=self._topic_guidance,
            )

            llm_response_text = await self.llm_client.call_llm_with_retry(
                prompt=prompt,
                system_prompt=system_prompt,
            )

            logger.info(
                f"[MemoryProcessor] LLM 响应成功，响应长度={len(llm_response_text)}"
            )

            structured_data = self._parse_llm_response(llm_response_text, is_group_chat)

            quality = self.quality.validate_summary_quality(structured_data)
            if quality == "low":
                logger.warning(
                    "[MemoryProcessor] 总结质量不达标（low），将标记但仍写入"
                )
            structured_data["_quality"] = quality

            fallback_excerpt = (
                conversation_text[:200] + "..."
                if len(conversation_text) > 200
                else conversation_text
            )
            content, metadata = self.storage.build_storage_format(
                fallback_excerpt, structured_data, is_group_chat
            )
            metadata["summary_quality"] = structured_data.get("_quality", "normal")
            if structured_data.get("_guardrails_validated"):
                metadata["guardrails_validated"] = True
            if structured_data.get("_guardrail_fallback"):
                metadata["guardrail_fallback"] = True

            # 情感标签：优先使用 LLM 输出，外部传入值仅作为后备。
            llm_emotion_tags = structured_data.get("emotion_tags") or []
            llm_emotion_tags = [
                t for t in llm_emotion_tags if isinstance(t, str) and t.strip()
            ][:3]
            if llm_emotion_tags:
                metadata["emotion_tags"] = llm_emotion_tags
                metadata["emotional_intensity"] = max(
                    0.0, min(1.0, emotional_intensity)
                )
            elif emotion_tags:
                metadata["emotion_tags"] = list(emotion_tags)
                metadata["emotional_intensity"] = max(
                    0.0, min(1.0, emotional_intensity)
                )

            # G2: 提取 LLM 输出的因果关系到 metadata
            causal_relations = structured_data.get("causal_relations") or []
            causal_relations = [
                cr
                for cr in causal_relations
                if isinstance(cr, dict) and cr.get("cause") and cr.get("effect")
            ][:3]
            if causal_relations:
                metadata["causal_relations"] = causal_relations

            # M1: 记忆溯源 — 存储原始对话片段摘要 (~100 字)
            snippet = conversation_text.strip()[:150]
            if len(conversation_text) > 150:
                snippet = snippet.rsplit("\n", 1)[0]  # 在换行处截断，保持句子完整
                if len(snippet) < 80:
                    snippet = conversation_text.strip()[:150]
            metadata["source_snippet"] = snippet[:150].strip()

            # 提取 memories 数组（新格式）或包装旧格式
            if "memories" in structured_data:
                memories_raw = structured_data["memories"]
            else:
                memories_raw = [
                    {
                        "summary": structured_data.get("summary", ""),
                        "key_facts": structured_data.get("key_facts", []),
                        "topics": structured_data.get("topics", []),
                        "importance": structured_data.get("importance", 0.5),
                        "sentiment": structured_data.get("sentiment", "neutral"),
                        "emotion_tags": structured_data.get("emotion_tags"),
                        "causal_relations": structured_data.get("causal_relations"),
                        "participants": structured_data.get("participants"),
                    }
                ]

            results: list[dict[str, Any]] = []
            for mem in memories_raw:
                if isinstance(mem, str):
                    continue
                mem_summary = str(mem.get("summary", "") or "")
                mem_facts = [str(f) for f in (mem.get("key_facts") or []) if f]
                mem_topics = [str(t) for t in (mem.get("topics") or []) if t]
                if not mem_summary and not mem_facts:
                    continue

                mem_importance = float(mem.get("importance", 0.5))
                intensity = max(0.0, min(1.0, emotional_intensity))
                if intensity > 0.5:
                    mem_importance = min(1.0, mem_importance + (intensity - 0.5) * 0.3)

                if serial_position_hint in ("first", "first_and_last"):
                    mem_importance = min(1.0, mem_importance + 0.15)
                if serial_position_hint in ("last", "first_and_last"):
                    mem_importance = min(1.0, mem_importance + 0.10)

                if interest_profile and mem_topics:
                    topic_text = " ".join(t.lower() for t in mem_topics)
                    matched = [i for i in interest_profile if i.lower() in topic_text]
                    if matched:
                        mem_importance = min(
                            1.0, mem_importance + min(0.35, len(matched) * 0.12)
                        )

                mem_emotion_tags = mem.get("emotion_tags") or []
                if not mem_emotion_tags:
                    mem_emotion_tags = emotion_tags or []

                mem_content, mem_metadata = self.storage.build_storage_format(
                    fallback_excerpt, mem, is_group_chat
                )
                mem_metadata["summary_quality"] = quality
                if structured_data.get("_guardrails_validated"):
                    mem_metadata["guardrails_validated"] = True
                if structured_data.get("_guardrail_fallback"):
                    mem_metadata["guardrail_fallback"] = True
                mem_metadata["source_snippet"] = snippet[:150].strip()
                mem_metadata["schema_version"] = "v3"
                if mem_emotion_tags:
                    mem_metadata["emotion_tags"] = [
                        t for t in mem_emotion_tags if isinstance(t, str) and t.strip()
                    ][:3]
                causal = (mem.get("causal_relations") or [])[:3]
                if causal:
                    mem_metadata["causal_relations"] = [
                        c
                        for c in causal
                        if isinstance(c, dict) and c.get("cause") and c.get("effect")
                    ]
                if identity_metadata:
                    mem_metadata.update(identity_metadata)
                elif is_group_chat and mem.get("participants"):
                    mem_metadata["participants"] = mem["participants"]

                # 记录首因与近因位置效应。
                if serial_position_hint in ("first", "first_and_last"):
                    mem_metadata["serial_position"] = "primacy"
                if serial_position_hint in ("last", "first_and_last"):
                    mem_metadata["serial_position"] = (
                        "recency"
                        if mem_metadata.get("serial_position") != "primacy"
                        else "primacy+recency"
                    )

                # 记录兴趣主题匹配及其重要性增益。
                if interest_profile and mem_topics:
                    topic_text = " ".join(t.lower() for t in mem_topics)
                    matched = [i for i in interest_profile if i.lower() in topic_text]
                    if matched:
                        boost = min(0.35, len(matched) * 0.12)
                        mem_metadata["interest_match"] = matched
                        mem_metadata["interest_boost"] = round(boost, 4)

                atoms = self.classify_atoms_from_metadata(
                    metadata=mem_metadata,
                    parent_importance=mem_importance,
                    session_id=None,
                    persona_id=persona_id,
                )

                results.append(
                    {
                        "content": mem_content,
                        "metadata": mem_metadata,
                        "importance": mem_importance,
                        "atoms": atoms,
                    }
                )

            logger.info(
                f"[MemoryProcessor] 成功生成 {len(results)} 条记忆, "
                f"类型={conversation_type}"
            )
            return results

        except Exception as e:
            logger.error(f"[MemoryProcessor] 处理对话历史失败: {e}", exc_info=True)
            raise

    def _parse_llm_response(
        self,
        response_text: str,
        is_group_chat: bool,
    ) -> dict[str, Any]:
        """优先通过结构护栏解析 LLM 输出，失败后使用旧解析器。"""
        if self.config.get("security.guardrails_enabled", True):
            try:
                guarded = validate_llm_response(
                    response_text,
                    MemoryExtractionResult,
                    fallback_return_none=True,
                )
                if guarded is not None and guarded.memories:
                    logger.info("[MemoryProcessor] guardrails 结构验证通过")
                    return self._guarded_result_to_structured_data(guarded)
                logger.warning(
                    "[MemoryProcessor] guardrails 结构验证失败，回退旧 JSON 解析器"
                )
            except Exception:
                logger.warning(
                    "[MemoryProcessor] guardrails 解析异常，回退旧 JSON 解析器",
                    exc_info=True,
                )

        data = self.json_parser.parse_llm_response(response_text, is_group_chat)
        data["_guardrail_fallback"] = self.config.get(
            "security.guardrails_enabled",
            True,
        )
        return data

    @staticmethod
    def _guarded_result_to_structured_data(
        guarded: MemoryExtractionResult,
    ) -> dict[str, Any]:
        """把通过护栏的结构转换为处理器既有字段契约。"""
        memories: list[dict[str, Any]] = []
        for atom in guarded.memories:
            content = atom.content.strip()
            if not content:
                continue
            key_facts = [fact for fact in atom.key_facts if fact.strip()]
            memories.append(
                {
                    "summary": content,
                    "key_facts": key_facts or [content],
                    "topics": list(atom.topics or atom.entities),
                    "importance": atom.importance,
                    "sentiment": atom.sentiment,
                    "emotion_tags": list(atom.emotion_tags),
                    "participants": list(atom.participants),
                    "causal_relations": list(atom.causal_relations),
                    "confidence": atom.confidence,
                    "atom_type": atom.atom_type,
                }
            )

        first = memories[0] if memories else {}
        return {
            "summary": first.get("summary", ""),
            "topics": first.get("topics", []),
            "key_facts": first.get("key_facts", []),
            "sentiment": first.get("sentiment", "neutral"),
            "importance": first.get("importance", 0.5),
            "memories": memories,
            "confidence": guarded.confidence,
            "extraction_quality": guarded.extraction_quality,
            "_guardrails_validated": True,
        }

    def build_memory_from_structured_data(
        self,
        structured_data: dict[str, Any],
        is_group_chat: bool = False,
        fallback_excerpt: str = "",
    ) -> dict[str, Any]:
        """从结构化数据构建包含 Atom 分类结果的记忆字典。"""
        quality = self.quality.validate_summary_quality(structured_data)
        normalized = self.quality.normalize_parsed_data(structured_data, is_group_chat)
        normalized["_quality"] = quality

        content, metadata = self.storage.build_storage_format(
            fallback_excerpt or normalized.get("summary", ""),
            normalized,
            is_group_chat,
        )
        metadata["summary_quality"] = quality
        metadata["schema_version"] = "v3"
        if fallback_excerpt and fallback_excerpt.strip():
            metadata["source_snippet"] = fallback_excerpt.strip()[:150]

        importance = self.quality.validate_importance(normalized.get("importance"))
        atoms = self.classify_atoms_from_metadata(
            metadata=metadata,
            parent_importance=importance,
        )
        return {
            "content": content,
            "metadata": metadata,
            "importance": importance,
            "atoms": atoms,
        }

    def classify_atoms_from_metadata(
        self,
        metadata: dict[str, Any],
        parent_importance: float = 0.5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[MemoryAtom]:
        if not self.config.get("atom_enabled", True):
            return []
        key_facts: list[str] = metadata.get("key_facts", [])
        if not key_facts:
            return []
        topics = metadata.get("topics", [])
        participants = metadata.get("participants", [])
        emotion_tags = metadata.get("emotion_tags")
        emotional_intensity = float(metadata.get("emotional_intensity", 0.5))
        return classify_atoms(
            key_facts=key_facts,
            topics=topics,
            participants=participants,
            parent_importance=parent_importance,
            session_id=session_id,
            persona_id=persona_id,
            emotion_tags=emotion_tags,
            emotional_intensity=emotional_intensity,
        )

    async def generate_persona_interpretations(
        self,
        content: str,
        conversation_text: str,
        primary_persona_id: str | None,
        secondary_persona_ids: list[str],
        persona_contexts: dict[str, str],
    ) -> dict[str, str]:
        """为多个角色生成同一记忆的不同解读。

        同一事实对不同角色有不同意义。例如：
        - 侦探 persona: "线索：嫌疑人A在案发时出现在现场"
        - 医生 persona: "患者A在症状出现前的活动轨迹"

        参数:
            content: 已生成的记忆内容
            conversation_text: 原始对话文本
            primary_persona_id: 主角色 ID（已用于主 LLM 调用，跳过）
            secondary_persona_ids: 需要生成解读的次要角色 ID 列表
            persona_contexts: {persona_id: persona_description} 角色描述字典

        返回:
            {persona_id: interpretation_text, ...}
        """
        if not secondary_persona_ids or not self.config.get(
            "persona_interpretation.enabled", False
        ):
            return {}

        interpretations: dict[str, str] = {}
        for pid in secondary_persona_ids:
            persona_desc = persona_contexts.get(pid, "")
            if not persona_desc:
                continue

            prompt = (
                f"你正在扮演以下角色：\n{persona_desc}\n\n"
                f"原始对话：\n{conversation_text[:800]}\n\n"
                f"已生成的记忆摘要：\n{content[:300]}\n\n"
                f"请从你角色的视角，用一句话（不超过60字）解读这段记忆对你意味着什么。"
                f"只输出解读文本，不要加任何前缀或引号。"
            )

            try:
                result = await self.llm_client.call_llm_with_retry(
                    prompt=prompt,
                    system_prompt=persona_desc[:500],
                )
                text = str(result).strip()[:120]
                if text and len(text) >= 3:
                    interpretations[pid] = text
                    logger.debug(
                        f"[MemoryProcessor]persona={pid[:20]} interpretation={text[:50]}"
                    )
            except Exception as e:
                logger.warning(
                    f"[MemoryProcessor]interpretation failed for {pid[:20]}: {e}"
                )

        return interpretations
