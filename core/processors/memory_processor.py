"""
记忆处理器 - 使用LLM将对话历史处理为结构化记忆
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ..features.identity.application.enricher import build_memory_identity_context
from ..features.memory.domain.memory_atom import MemoryAtom
from ..models.conversation_models import Message
from ..platform.security.guardrails import (
    MemoryExtractionResult,
    validate_and_clean_json,
    validate_llm_response,
)
from ..shared.cost_control import CostControl
from ..shared.extra_llm_budget import budgeted_extra_llm_call
from .atom_classifier import classify_atoms
from .conversation_formatter import ConversationFormatter
from .json_parser import JsonParser
from .llm_client import LLMClient
from .memory_grounding import GroundingResult, MemoryGroundingValidator
from .prompt_builder import PromptBuilder
from .quality_validator import QualityValidator
from .reflection_generation_observability import (
    report_generation_stage as _report_generation_stage,
)
from .storage_builder import StorageBuilder
from .topic_segmentation_pipeline import (
    TOPIC_SEGMENTATION_OBSERVABILITY_FIELDS,
    TopicSegmentationPipeline,
)

if TYPE_CHECKING:
    from ..shared.contracts import PromptProtectionPort


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
        cost_control: CostControl | None = None,
        grounding_judge: Callable[[dict[str, Any]], Awaitable[bool | Mapping[str, Any]]]
        | None = None,
        topic_embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]]
        | None = None,
    ):
        """初始化结构化抽取、话题分段、质量校验与存储格式协作对象。

        参数:
            context: AstrBot 运行时上下文。
            llm_provider: 固定 Provider 或由上下文解析的 Provider 标识。
            config: 处理器运行时配置快照。
            cost_control: 共享的请求级成本门。
            grounding_judge: 可选的来源可信度 Judge。
            topic_embed_fn: 策略 B 使用的批量 Embedding 入口。
        """

        self.context = context
        self.config = config or {}
        self.cost_control = cost_control or CostControl()

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
        self.grounding_validator = MemoryGroundingValidator()
        self._grounding_judge = grounding_judge or self._call_grounding_judge
        self.prompt_protection_service: "PromptProtectionPort | None" = None
        self._topic_guidance = self._load_topic_guidance(prompt_dir)
        self._topic_segmentation_enabled = self.config.get(
            "topic_segmentation.enabled", True
        )
        self.topic_segmentation = TopicSegmentationPipeline(
            self.config,
            embed_fn=topic_embed_fn,
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
        llm_max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        """处理对话批次并生成结构化记忆（可能返回多条独立话题记忆）。

        返回:
            list[dict]: 每条 dict = {content, metadata, importance, atoms}
        """
        if not messages:
            raise ValueError("消息列表不能为空")

        total_started = time.perf_counter()
        stage_started = total_started
        current_stage = "prompt_build"
        conversation_text = self.formatter.format_conversation(messages)
        grounded_conversation_text = (
            self.formatter.format_conversation_with_source_refs(messages)
        )
        identity_context = build_memory_identity_context(messages)

        current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        if is_group_chat:
            prompt = self.prompt_builder.group_chat_prompt.replace(
                "{conversation}", grounded_conversation_text
            )
        else:
            prompt = self.prompt_builder.private_chat_prompt.replace(
                "{conversation}", grounded_conversation_text
            )
        prompt = prompt.replace("{current_date}", current_date)
        prompt += identity_context.prompt_constraint()
        prompt += self.grounding_validator.prompt_contract(len(messages))
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
            _report_generation_stage(
                "prompt_build",
                "completed",
                "reflection_prompt_built",
                stage_started,
                prompt_chars=len(prompt),
                message_count=len(messages),
            )

            current_stage = "provider"
            stage_started = time.perf_counter()
            generation_result = await self.llm_client.call_llm_with_retry_result(
                prompt=prompt,
                system_prompt=system_prompt,
                max_retries=max(1, int(llm_max_retries)),
            )
            llm_response_text = generation_result.text
            _report_generation_stage(
                "provider",
                "completed",
                "reflection_provider_completed",
                stage_started,
                prompt_chars=len(prompt),
                response_chars=len(llm_response_text),
                prompt_tokens=generation_result.prompt_tokens,
                completion_tokens=generation_result.completion_tokens,
            )

            logger.info(
                f"[MemoryProcessor] LLM 响应成功，响应长度={len(llm_response_text)}"
            )

            current_stage = "parse"
            stage_started = time.perf_counter()
            structured_data = self._parse_llm_response(llm_response_text, is_group_chat)

            quality = self.quality.validate_summary_quality(structured_data)
            if quality == "low":
                logger.warning(
                    "[MemoryProcessor] 总结质量不达标（low），候选将进入隔离队列"
                )
            structured_data["_quality"] = quality
            raw_candidates = structured_data.get("memories")
            _report_generation_stage(
                "parse",
                "completed",
                "reflection_parse_completed",
                stage_started,
                candidate_count=(
                    len(raw_candidates) if isinstance(raw_candidates, list) else 0
                ),
            )
            current_stage = "segmentation"
            stage_started = time.perf_counter()
            memories_raw = await self.topic_segmentation.prepare_candidates(
                structured_data,
                messages,
                is_group_chat=is_group_chat,
            )
            _report_generation_stage(
                "segmentation",
                "completed",
                "reflection_segmentation_completed",
                stage_started,
                candidate_count=len(memories_raw),
            )

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

            current_stage = "grounding"
            stage_started = time.perf_counter()
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
                mem_metadata["emotional_intensity"] = intensity
                if mem_emotion_tags:
                    mem_metadata["emotion_tags"] = [
                        t for t in mem_emotion_tags if isinstance(t, str) and t.strip()
                    ][:3]
                if mem.get("atom_type"):
                    mem_metadata["atom_type"] = str(mem["atom_type"])
                if mem.get("confidence") is not None:
                    mem_metadata["atom_confidence"] = float(mem["confidence"])
                for field in TOPIC_SEGMENTATION_OBSERVABILITY_FIELDS:
                    if field in mem:
                        mem_metadata[field] = mem[field]
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

                grounding = self.grounding_validator.validate(
                    mem,
                    messages,
                    is_group_chat=is_group_chat,
                )
                if grounding.requires_judge:
                    grounding = await self._resolve_grounding_with_judge(
                        grounding,
                        is_group_chat=is_group_chat,
                    )
                mem_metadata["grounding_status"] = grounding.status
                mem_metadata["grounding_reason_codes"] = list(grounding.reason_codes)
                mem_metadata["source_evidence"] = grounding.evidence
                subject_ids = _referenced_subject_ids(
                    grounding.evidence,
                    messages,
                    identity_metadata,
                )
                if subject_ids:
                    mem_metadata["subject_ids"] = list(subject_ids)
                should_quarantine = quality == "low" or not grounding.allowed
                mem_metadata["quality_gate_action"] = (
                    "quarantine" if should_quarantine else "allow"
                )

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

                atoms = []
                if not should_quarantine:
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

            _report_generation_stage(
                "grounding",
                "completed",
                "reflection_grounding_completed",
                stage_started,
                candidate_count=len(results),
            )
            logger.info(
                f"[MemoryProcessor] 成功生成 {len(results)} 条记忆, "
                f"类型={conversation_type}"
            )
            _report_generation_stage(
                "window_total",
                "completed",
                "reflection_window_completed",
                total_started,
                candidate_count=len(results),
                prompt_chars=len(prompt),
                response_chars=len(llm_response_text),
            )
            return results

        except asyncio.CancelledError:
            _report_generation_stage(
                current_stage,
                "cancelled",
                "reflection_generation_cancelled",
                stage_started,
            )
            raise
        except Exception as e:
            _report_generation_stage(
                current_stage,
                "failed",
                "reflection_generation_failed",
                stage_started,
            )
            _report_generation_stage(
                "window_total",
                "failed",
                "reflection_generation_failed",
                total_started,
            )
            logger.error(
                "[MemoryProcessor] 处理对话历史失败，异常类型=%s",
                e.__class__.__name__,
                exc_info=True,
            )
            raise

    async def _resolve_grounding_with_judge(
        self,
        grounding: GroundingResult,
        *,
        is_group_chat: bool,
    ) -> GroundingResult:
        """用请求预算保护可选 Judge，并把普通失败降级为隔离。"""

        payload = {
            "claim_text": grounding.claim_text,
            "source_text": grounding.source_text,
            "is_group_chat": bool(is_group_chat),
        }
        try:
            async with budgeted_extra_llm_call(
                self.cost_control,
                "memory_grounding_judge",
            ) as allowed:
                if not allowed:
                    return grounding.with_unavailable_judge()
                judged = await self._grounding_judge(payload)
            if isinstance(judged, Mapping):
                supported = judged.get("supported") is True
            else:
                supported = judged is True
            return grounding.with_judge_result(supported)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MemoryProcessor] 来源忠实性 Judge 失败，候选进入隔离，异常类型=%s",
                exc.__class__.__name__,
            )
            return grounding.with_unavailable_judge()

    async def _call_grounding_judge(
        self,
        payload: dict[str, Any],
    ) -> Mapping[str, Any]:
        """只向 Provider 发送当前候选声明和已引用片段。"""

        prompt = (
            "判断候选记忆是否完全由给定来源支持。只输出 JSON："
            '{"supported": true} 或 {"supported": false}。\n'
            f"候选声明：{str(payload.get('claim_text') or '')[:1200]}\n"
            f"来源片段：{str(payload.get('source_text') or '')[:2400]}"
        )
        response_text = await self.llm_client.call_llm_with_retry(
            prompt=prompt,
            system_prompt="只做来源忠实性判断，不补充来源之外的事实。",
            max_retries=1,
        )
        parsed = validate_and_clean_json(
            response_text,
            fallback_return_none=True,
        )
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("supported"), bool
        ):
            raise ValueError("grounding_judge_invalid_response")
        return json.loads(json.dumps(parsed))

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
                    "source_refs": [
                        reference.model_dump() for reference in atom.source_refs
                    ],
                    "confidence": atom.confidence,
                    "atom_type": (
                        atom.atom_type if "atom_type" in atom.model_fields_set else None
                    ),
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
        metadata["emotional_intensity"] = max(
            0.0,
            min(1.0, float(structured_data.get("emotional_intensity", 0.5))),
        )
        if structured_data.get("atom_type"):
            metadata["atom_type"] = str(structured_data["atom_type"])
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
        """按父记忆 metadata 和运行时质量配置生成 MemoryAtom 列表。"""

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
            min_confidence=float(self.config.get("atom_min_confidence", 0.65)),
            min_importance=float(self.config.get("atom_min_importance", 0.3)),
            min_content_length=int(self.config.get("atom_min_content_length", 5)),
            enable_info_check=bool(self.config.get("atom_info_check_enabled", True)),
            enable_quality_filter=bool(
                self.config.get("atom_quality_filter_enabled", True)
            ),
            enable_negation_detection=bool(
                self.config.get(
                    "atom_classifier.negation_detection_enabled",
                    True,
                )
            ),
            atom_type_hint=metadata.get("atom_type"),
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
                async with budgeted_extra_llm_call(
                    self.cost_control,
                    "persona_interpretation",
                ) as allowed:
                    if not allowed:
                        continue
                    result = await self.llm_client.call_llm_with_retry(
                        prompt=prompt,
                        system_prompt=persona_desc[:500],
                        max_retries=1,
                    )
                text = str(result).strip()[:120]
                if text and len(text) >= 3:
                    interpretations[pid] = text
                    logger.debug("[MemoryProcessor] 人格解读生成成功")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[MemoryProcessor] 人格解读生成失败，异常类型=%s",
                    exc.__class__.__name__,
                )

        return interpretations


def _referenced_subject_ids(
    evidence: list[dict[str, Any]],
    messages: list[Message],
    identity_metadata: dict[str, Any],
) -> tuple[str, ...]:
    """从候选实际引用的消息提取可信 canonical 参与者。"""

    raw_trusted = identity_metadata.get("participant_ids")
    if not isinstance(raw_trusted, list):
        return ()
    trusted = {
        item.strip() for item in raw_trusted if isinstance(item, str) and item.strip()
    }
    if not trusted:
        return ()
    subjects: list[str] = []
    for item in evidence:
        index = item.get("message_index") if isinstance(item, dict) else None
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if index < 0 or index >= len(messages):
            continue
        sender_id = messages[index].sender_id
        if sender_id in trusted and sender_id not in subjects:
            subjects.append(sender_id)
    return tuple(subjects)
