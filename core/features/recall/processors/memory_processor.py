"""
记忆处理器 - 使用LLM将对话历史处理为结构化记忆
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ....platform.security.guardrails import (
    MemoryExtractionResult,
    validate_llm_response,
)
from ....shared.contracts.conversation import Message
from ....shared.cost_control import CostControl
from ....shared.summary_llm_limiter import SummaryLlmLimiter
from ...identity.application.enricher import build_memory_identity_context
from ...memory.domain.memory_atom import MemoryAtom
from ...quality.domain.gate_config import BUILTIN_GENERIC_TERMS, GateProfile
from .atom_classifier import classify_atoms
from .conversation_formatter import ConversationFormatter
from .gate_context import resolve_reflection_gate
from .grounding_judge import GroundingJudgeCallable, GroundingJudgeMixin
from .json_parser import JsonParser, SummaryParseError
from .llm_client import LLMClient
from .memory_fact_evidence import (
    admit_candidate_facts,
    apply_admission_metadata,
    apply_fact_admission,
    guarded_result_to_structured_data,
    has_trusted_fact_evidence,
)
from .memory_grounding import MemoryGroundingValidator
from .memory_processor_candidate_mixin import MemoryProcessorCandidateMixin
from .prompt_builder import (
    PromptBuilder,
    load_prompt_file,
    render_extraction_prompt,
)
from .quality_validator import QualityValidator
from .reflection_generation_observability import (
    report_generation_stage as _report_generation_stage,
)
from .reflection_generation_observability import (
    report_parse_attempt as _report_parse_attempt,
)
from .reflection_generation_observability import (
    report_parse_failure as _report_parse_failure,
)
from .reflection_generation_observability import (
    report_parse_success as _report_parse_success,
)
from .storage_builder import StorageBuilder
from .topic_segmentation_pipeline import (
    TOPIC_SEGMENTATION_OBSERVABILITY_FIELDS,
    TopicSegmentationPipeline,
)

if TYPE_CHECKING:
    from ....shared.contracts import PromptProtectionPort
    from ...reflection.domain.summary_models import TopicCandidateSelection


class MemoryProcessor(MemoryProcessorCandidateMixin, GroundingJudgeMixin):
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
        gate_runtime: Any | None = None,
        grounding_judge: GroundingJudgeCallable | None = None,
        topic_embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]]
        | None = None,
        limiter: SummaryLlmLimiter | None = None,
    ):
        """初始化结构化抽取、话题分段、质量校验与存储格式协作对象。

        参数:
            context: AstrBot 运行时上下文。
            llm_provider: 固定 Provider 或由上下文解析的 Provider 标识。
            config: 处理器运行时配置快照。
            cost_control: 共享的请求级成本门。
            gate_runtime: 门禁运行时；缺省时使用内置默认快照。
            grounding_judge: 可选的来源可信度 Judge。
            topic_embed_fn: 策略 B 使用的批量 Embedding 入口。
            limiter: 可选的进程内总结 LLM 并发限流器，仅转发给 LLMClient。
        """

        self.context = context
        self.config = config or {}
        self.cost_control = cost_control or CostControl()
        self._gate_runtime = gate_runtime
        self.llm_client = LLMClient(context, llm_provider, limiter=limiter)
        # core 包内 prompts 目录（core/prompts）：本文件位于 core/features/recall/processors，
        # parents[3] 即 core 包根，规避按相对层级推算目录的漂移。
        prompt_dir = Path(__file__).resolve().parents[3] / "prompts"
        prompt_config = {
            k: self.config.get(k, "")
            for k in ("group_chat_template", "private_chat_template")
        }
        try:
            self._judge_prompt_default = load_prompt_file(
                "grounding_judge_prompt.txt", prompt_dir
            )
        except Exception:
            logger.warning("[MemoryProcessor] Judge 默认模板加载失败，使用空模板")
            self._judge_prompt_default = ""
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
        group_id: str | None = None,
        gate_snapshot_json: str | None = None,
        strict_summary: bool = False,
        candidate_selection: "TopicCandidateSelection | None" = None,
        message_seqs: Sequence[int | None] | None = None,
    ) -> list[dict[str, Any]]:
        """处理对话并生成结构化记忆。

        ``strict_summary`` 只供统一总结 Worker 使用；结构无效时抛出
        :class:`SummaryParseError`，合法空候选返回空列表。

        ``message_seqs`` 是窗口稳定序号，必须与 ``messages`` 同序等长；
        缺失时无法生成可脱离窗口重定位的事实证据，候选保守隔离。
        """
        if not messages:
            raise ValueError("消息列表不能为空")
        if message_seqs is not None and len(message_seqs) != len(messages):
            raise ValueError("grounding_message_sequence_invalid")
        profile, gate_enabled = resolve_reflection_gate(
            self._gate_runtime,
            is_group_chat=is_group_chat,
            group_id=group_id,
            persona_id=persona_id,
            gate_snapshot_json=gate_snapshot_json,
        )

        total_started = time.perf_counter()
        stage_started = total_started
        current_stage = "prompt_build"
        conversation_text = self.formatter.format_conversation(messages)
        grounded_conversation_text = (
            self.formatter.format_conversation_with_source_refs(messages)
        )
        identity_context = build_memory_identity_context(messages)

        current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_type = "群聊" if is_group_chat else "私聊"
        template = (
            self.prompt_builder.group_chat_prompt
            if is_group_chat
            else self.prompt_builder.private_chat_prompt
        )
        prompt = render_extraction_prompt(
            template,
            conversation=grounded_conversation_text,
            current_date=current_date,
            chat_type=conversation_type,
            continuity_topics=continuity_context or "",
            interests="、".join((interest_profile or [])[:5]),
            emotion_tags="、".join(emotion_tags or []),
            emotional_intensity=f"{emotional_intensity:.2f}",
        )
        prompt += identity_context.prompt_constraint()
        prompt += self.grounding_validator.prompt_contract(
            len(messages), profile.references.max_references
        )
        # 注入候选块（如果有）
        prompt = self._inject_topic_candidates(prompt, candidate_selection)

        identity_metadata = identity_context.metadata()

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
                operation="summary_extraction",
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
            _report_parse_attempt()
            try:
                structured_data = self._parse_llm_response(
                    llm_response_text,
                    is_group_chat,
                    strict_summary=strict_summary,
                )
            except asyncio.CancelledError:
                raise
            except SummaryParseError as error:
                _report_parse_failure(error.reason)
                raise
            except Exception:
                _report_parse_failure("unknown")
                raise
            else:
                _report_parse_success()

            quality = (
                "normal"
                if not gate_enabled or not profile.checks.quality_low_check
                else self.quality.validate_summary_quality(
                    structured_data,
                    min_summary_chars=profile.quality.min_summary_chars,
                    generic_terms=_resolved_generic_terms(profile),
                )
            )
            if quality == "low":
                logger.warning(
                    "[MemoryProcessor] 总结质量不达标（low），候选将进入隔离队列"
                )
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

            fallback_excerpt = ""
            if not strict_summary:
                fallback_excerpt = (
                    conversation_text[:200] + "..."
                    if len(conversation_text) > 200
                    else conversation_text
                )
            current_stage = "grounding"
            stage_started = time.perf_counter()
            results: list[dict[str, Any]] = []
            for mem in memories_raw:
                if isinstance(mem, str):
                    continue
                mem_summary = str(mem.get("summary", "") or "")
                mem_facts = mem.get("key_facts") or []
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

                admission = await admit_candidate_facts(
                    validator=self.grounding_validator,
                    resolve_judge=self.resolve_grounding_judge,
                    mem=mem,
                    facts=mem_facts,
                    messages=messages,
                    is_group_chat=is_group_chat,
                    profile=profile,
                    message_seqs=message_seqs,
                    gate_enabled=gate_enabled,
                    topics=mem_topics,
                    importance=mem_importance,
                )
                mem, mem_topics = apply_fact_admission(mem, admission, mem_topics)
                rejected_candidate = admission.quarantine_candidate

                mem_content, mem_metadata = self.storage.build_storage_format(
                    fallback_excerpt, mem, is_group_chat
                )
                mem_metadata["summary_quality"] = quality
                if structured_data.get("_guardrails_validated"):
                    mem_metadata["guardrails_validated"] = True
                if structured_data.get("_guardrail_fallback"):
                    mem_metadata["guardrail_fallback"] = True
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

                should_quarantine = apply_admission_metadata(
                    mem_metadata,
                    admission,
                    quality=quality,
                    messages=messages,
                    identity_metadata=identity_metadata,
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
                if rejected_candidate is not None:
                    rejected_candidate["metadata"].update(identity_metadata)
                    rejected_candidate["metadata"]["privacy_level"] = mem_metadata[
                        "privacy_level"
                    ]
                    results.append(rejected_candidate)

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
        except SummaryParseError as error:
            _report_generation_stage(
                current_stage,
                "failed",
                "summary_invalid",
                stage_started,
            )
            _report_generation_stage(
                "window_total",
                "failed",
                "summary_invalid",
                total_started,
            )
            logger.error(
                "[MemoryProcessor] 总结结构无效，sub_reason=%s，detail=%s，异常类型=%s",
                error.reason,
                error.detail or "none",
                error.__class__.__name__,
                extra={"reason_code": "summary_invalid", "sub_reason": error.reason},
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

    def _parse_llm_response(
        self,
        response_text: str,
        is_group_chat: bool,
        *,
        strict_summary: bool = False,
    ) -> dict[str, Any]:
        """按调用边界选择严格总结或既有兼容解析。"""
        if strict_summary:
            guarded = self.json_parser.parse_summary_response(response_text)
            return guarded_result_to_structured_data(guarded)
        if self.config.get("security.guardrails_enabled", True):
            try:
                guarded = validate_llm_response(
                    response_text,
                    MemoryExtractionResult,
                    fallback_return_none=True,
                )
                if guarded is not None and guarded.memories:
                    logger.info("[MemoryProcessor] guardrails 结构验证通过")
                    return guarded_result_to_structured_data(guarded)
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

    def build_memory_from_structured_data(
        self,
        structured_data: dict[str, Any],
        is_group_chat: bool = False,
        fallback_excerpt: str = "",
    ) -> dict[str, Any]:
        """从结构化数据构建包含 Atom 分类结果的记忆字典。"""
        quality = self.quality.validate_summary_quality(structured_data)
        normalized = self.quality.normalize_parsed_data(structured_data, is_group_chat)
        facts = normalized.get("key_facts")
        evidence = normalized.get("fact_source_evidence")
        trusted = has_trusted_fact_evidence(facts, evidence)
        if trusted and isinstance(facts, list):
            # This entry point has no source window to independently verify a summary.
            normalized["summary"] = "；".join(facts)

        content, metadata = self.storage.build_storage_format(
            fallback_excerpt or normalized.get("summary", ""),
            normalized,
            is_group_chat,
        )
        metadata["summary_quality"] = quality
        metadata["schema_version"] = "v3"
        metadata["grounding_status"] = "grounded" if trusted else "quarantine"
        metadata["grounding_reason_codes"] = (
            [] if trusted else ["grounding_fact_evidence_mismatch"]
        )
        metadata["quality_gate_action"] = "allow" if trusted else "quarantine"
        metadata["emotional_intensity"] = max(
            0.0,
            min(1.0, float(structured_data.get("emotional_intensity", 0.5))),
        )
        if structured_data.get("atom_type"):
            metadata["atom_type"] = str(structured_data["atom_type"])

        importance = self.quality.validate_importance(normalized.get("importance"))
        atoms = (
            self.classify_atoms_from_metadata(
                metadata=metadata,
                parent_importance=importance,
            )
            if trusted and quality != "low"
            else []
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
        evidence = metadata.get("fact_source_evidence")
        if not isinstance(evidence, list) or len(evidence) != len(key_facts):
            return []
        topics = metadata.get("topics", [])
        participants = metadata.get("participants", [])
        emotion_tags = metadata.get("emotion_tags")
        emotional_intensity = float(metadata.get("emotional_intensity", 0.5))
        return classify_atoms(
            key_facts=key_facts,
            fact_source_evidence=evidence,
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


def _resolved_generic_terms(profile: GateProfile) -> tuple[str, ...]:
    """按词表模式合并内置泛化词；replace 模式完全由配置掌控。"""

    config = profile.word_lists.generic_terms
    if config.mode == "replace":
        return tuple(config.items)
    return BUILTIN_GENERIC_TERMS + tuple(config.items)
