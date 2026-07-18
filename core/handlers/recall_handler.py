"""记忆召回处理器 — LLM 请求前检索并注入长期记忆"""

from __future__ import annotations

import asyncio
import hashlib
import math
import uuid
import random
from dataclasses import dataclass, replace
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ..base.constants import FAKE_TOOL_CALL_NAME
from ..base.config_manager import ConfigManager
from ..cleaners.injection_cleaner import InjectionCleaner
from ..extractors.message_content_extractor import MessageContentExtractor
from ..managers.conversation_manager import ConversationManager
from ..managers.memory_engine import MemoryEngine
from ..monitoring import monitored
from ..retrieval.query_rewriter import QueryRewriter
from ..injection.executor import InjectionExecutionContext, InjectionExecutor
from ..injection.models import (
    DeliveryMode,
    InjectionDecision,
    InjectionDecisionRecord,
    InjectionExecutionResult,
    InjectionOutcome,
    PresetName,
    RequestSignals,
    RoutingMode,
)
from ..injection.router import InjectionRoutingConfig, InjectionStrategyRouter
from ..security.prompt_sanitizer import (
    PROMPT_PROTECTION_REQUIRED_ATTR,
    PROMPT_PROTECTION_REQUIRED_EXTRA_KEY,
    PROMPT_PROTECTION_SCOPE_ATTR,
    PROMPT_PROTECTION_SCOPE_EXTRA_KEY,
)
from ..utils import OperationContext, get_persona_id

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import ProviderRequest
    from ..injection.recorder import InjectionDecisionRecorder
    from ..security.prompt_sanitizer import PromptProtectionService
    from ..utils.injection_adapter import InjectionAdapter


@dataclass(frozen=True, slots=True)
class _RecallExecutionInput:
    req: Any
    decision: InjectionDecision
    signals: RequestSignals
    memories: list[dict[str, Any]]
    prospective: list[Any]
    cognitive_context: str
    query: str
    session_filtered: bool
    persona_filtered: bool
    decision_ms: float
    provider: Any
    event: Any
    preflight_short_circuit: bool
    cognitive_format_ms: float = 0.0


class RecallHandler:
    """LLM 请求前记忆召回 + 注入"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        conversation_manager: ConversationManager,
        injection_adapter: InjectionAdapter,
        enforce_limit_cb: Callable,
        jargon_query_service: Any | None = None,
        expression_learner: Any | None = None,
        affection_manager: Any | None = None,
        relation_manager: Any | None = None,
        prompt_protection_service: PromptProtectionService | None = None,
        perf_tracker: Any | None = None,
        injection_recorder: InjectionDecisionRecorder | None = None,
        memory_tool_available: bool = False,
    ) -> None:
        self._context = context
        self._config_manager = config_manager
        self._memory_engine = memory_engine
        self._conversation_manager = conversation_manager
        self._injection_adapter = injection_adapter
        self._enforce_limit_cb = enforce_limit_cb
        self._jargon_query_service = jargon_query_service
        self._expression_learner = expression_learner
        self._affection_manager = affection_manager
        self._relation_manager = relation_manager
        self._prompt_protection = prompt_protection_service
        self._perf_tracker = perf_tracker
        self._injection_recorder = injection_recorder
        self._memory_tool_available = memory_tool_available
        self._router = InjectionStrategyRouter()
        self._executor = InjectionExecutor(injection_adapter, prompt_protection_service)
        self._cleaner = InjectionCleaner()
        self._extractor = MessageContentExtractor()
        # R1：查询改写器（无 LLM 调用方时使用关键词回退，后续再注入 LLM 调用方）
        self._query_rewriter = QueryRewriter(
            enabled=config_manager.get("recall_engine.query_rewrite_enabled", True),
        )

    @monitored
    async def handle_memory_recall(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在 LLM 请求前查询并注入长期记忆。"""
        recall_started = time.perf_counter()
        injected_count = 0
        filtered_count = 0
        try:
            session_id = event.unified_msg_origin
            logger.debug(f"[召回流程] 获取到 unified_msg_origin: {session_id}")

            if session_id and (
                "Error:" in session_id or "error:" in session_id.lower()
            ):
                logger.warning(
                    f"[{session_id}] 检测到异常的会话 ID，这可能导致记忆功能异常。"
                )

            async with OperationContext("记忆召回", session_id):
                prompt_text = getattr(req, "prompt", "")
                extra_parts = getattr(req, "extra_user_content_parts", [])
                has_prompt_text = isinstance(prompt_text, str) and bool(
                    prompt_text.strip()
                )
                has_extra_parts = bool(extra_parts)

                if not has_prompt_text and not has_extra_parts:
                    logger.debug(f"[{session_id}] 请求中无可用用户内容，跳过记忆召回")
                    return

                if self._config_manager.get("recall_engine.auto_remove_injected", True):
                    removed = self._cleaner.remove_injected_memories_from_context(
                        req,
                        session_id,
                    )
                    removed += self._cleaner.remove_fake_tool_call_from_context(
                        req,
                        session_id,
                    )
                    if removed > 0:
                        logger.info(
                            f"[{session_id}] 已清理 {removed} 处历史记忆注入片段"
                        )

                actual_query = await self._extractor.get_event_message_str(event)

                request_query = (
                    prompt_text.strip() if isinstance(prompt_text, str) else ""
                )

                is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
                if not is_group and actual_query:
                    message_to_store = request_query
                    if not message_to_store:
                        message_to_store = (
                            await self._extractor.extract_message_content(event, req)
                        )
                    if not message_to_store:
                        message_to_store = actual_query.strip()
                    await self._conversation_manager.add_message_from_event(
                        event=event,
                        role="user",
                        content=message_to_store,
                    )
                    await self._enforce_limit_cb(session_id)

                top_k = self._config_manager.get("recall_engine.top_k", 5)
                if top_k <= 0:
                    logger.info(
                        f"[{session_id}] top_k={top_k} <= 0，跳过记忆检索和注入"
                    )
                    return

                if not actual_query:
                    # 空消息（如纯 @mention）时回退到历史上下文
                    fallback_query = await self._build_fallback_query(session_id)
                    if fallback_query:
                        logger.info(
                            f"[{session_id}] 原始消息为空，使用历史上下文作为回退查询"
                        )
                        actual_query = fallback_query
                    else:
                        logger.warning(f"[{session_id}] 原始用户消息为空，跳过记忆召回")
                        return

                filtering_config = self._config_manager.filtering_settings
                use_persona_filtering = filtering_config.get(
                    "use_persona_filtering", True
                )
                use_session_filtering = filtering_config.get(
                    "use_session_filtering", True
                )

                persona_id = await get_persona_id(self._context, event)

                recall_session_id = session_id if use_session_filtering else None
                recall_persona_id = persona_id if use_persona_filtering else None

                query_for_search = actual_query

                if self._config_manager.get(
                    "recall_engine.inject_with_recent_context", False
                ):
                    try:
                        recent_messages = await self._conversation_manager.get_context(
                            session_id,
                            max_messages=5,
                        )
                        if recent_messages and len(recent_messages) > 1:
                            context_parts = []
                            for msg in reversed(recent_messages[1:]):
                                content = msg.get("content", "")
                                if content and content.strip():
                                    context_parts.append(content.strip())
                            if context_parts:
                                expanded = " | ".join(context_parts)
                                query_for_search = expanded + " " + actual_query
                                logger.info(
                                    f"[{session_id}] 上下文扩展查询: "
                                    f"{len(context_parts)}条历史消息 + 当前消息"
                                )
                    except Exception as e:
                        logger.warning(f"[{session_id}] 获取上下文扩展失败: {e}")

                # R1：语义查询改写 —— 展开模糊指代
                query_intent = await self._query_rewriter.rewrite(
                    query=actual_query,
                    recent_context=query_for_search,
                )
                # 使用改写后的第一条查询（或原始查询）作为主检索词
                rewritten_queries = query_intent.rewritten_queries
                primary_query = (
                    rewritten_queries[0] if rewritten_queries else query_for_search
                )
                memory_type_filter = query_intent.memory_types or None

                logger.info(
                    f"[{session_id}] 开始记忆召回: intent={query_intent.intent}, "
                    f"rewritten_count={len(rewritten_queries)}, "
                    f"entity_count={len(query_intent.extracted_entities)}"
                )

                chat_type = "group" if is_group else "private"
                provider = getattr(req, "provider", None)
                if provider is None:
                    provider_getter = getattr(self._context, "get_using_provider", None)
                    if callable(provider_getter):
                        try:
                            provider = provider_getter(session_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            provider = None
                            logger.warning(
                                "[召回流程] Provider 获取失败，使用保守注入能力",
                                exc_info=True,
                            )

                routing_config = self._routing_config()
                preflight_signals = self._preflight_signals(
                    query_intent, provider, req, chat_type
                )
                decision_started = time.perf_counter()
                preflight = self._router.route_preflight(
                    routing_config, preflight_signals
                )
                preflight_ms = (time.perf_counter() - decision_started) * 1000.0

                if preflight.skip_passive_recall:
                    prospective = await self._maybe_prospective_recall(
                        session_id=recall_session_id,
                        persona_id=recall_persona_id,
                        chat_type=chat_type,
                    )
                    result = await self._execute_and_record(_RecallExecutionInput(
                        req=req,
                        decision=preflight,
                        signals=preflight_signals,
                        memories=[],
                        prospective=prospective,
                        cognitive_context="",
                        query=actual_query,
                        session_filtered=use_session_filtering,
                        persona_filtered=use_persona_filtering,
                        decision_ms=preflight_ms,
                        provider=provider,
                        preflight_short_circuit=True,
                        event=event,
                    ))
                    injected_count = result.selected_count
                    return

                user_id = self._get_event_sender_id(event)
                recalled_memories = await self._memory_engine.search_memories(
                    query=primary_query,
                    k=top_k,
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                    query_intent=query_intent,
                    memory_types=memory_type_filter,
                    user_id=user_id,
                )

                spontaneous = await self._maybe_spontaneous_recall(
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                )
                ordinary_candidates = list(recalled_memories or [])
                if spontaneous:
                    ordinary_candidates.extend(spontaneous)
                ordinary_candidates = self._finalize_recall_candidates(
                    ordinary_candidates,
                    top_k=top_k,
                )

                prospective = await self._maybe_prospective_recall(
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                )
                memories = self._safe_candidates(ordinary_candidates)
                final_signals = self._final_signals(preflight_signals, memories)
                decision_started = time.perf_counter()
                decision = self._router.route_final(routing_config, final_signals)
                decision_ms = (
                    preflight_ms
                    + (time.perf_counter() - decision_started) * 1000.0
                )
                format_started = time.perf_counter()
                cognitive_context = await self._build_cognitive_context(
                    text=actual_query,
                    group_id=session_id or "default",
                    persona_id=persona_id or "default",
                )
                format_ms = (time.perf_counter() - format_started) * 1000.0
                result = await self._execute_and_record(_RecallExecutionInput(
                    req=req,
                    decision=decision,
                    signals=final_signals,
                    memories=memories,
                    prospective=prospective,
                    cognitive_context=cognitive_context,
                    query=actual_query,
                    session_filtered=use_session_filtering,
                    persona_filtered=use_persona_filtering,
                    decision_ms=decision_ms,
                    provider=provider,
                    preflight_short_circuit=False,
                    event=event,
                    cognitive_format_ms=format_ms,
                ))
                injected_count = result.selected_count

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)
        finally:
            self._record_recall_observability(
                total_ms=(time.perf_counter() - recall_started) * 1000.0,
                injected_count=injected_count,
                filtered_count=filtered_count,
            )

    def _routing_config(self) -> InjectionRoutingConfig:
        get = self._config_manager.get
        return InjectionRoutingConfig(
            mode=RoutingMode(get("recall_engine.injection_routing_mode", "manual")),
            manual_preset=PresetName(get("recall_engine.injection_manual_preset", "balanced")),
            auto_fallback=PresetName(get("recall_engine.injection_auto_fallback_preset", "balanced")),
            hybrid_base=PresetName(get("recall_engine.injection_hybrid_base_preset", "balanced")),
            hybrid_min=PresetName(get("recall_engine.injection_hybrid_min_preset", "low_cost")),
            hybrid_max=PresetName(get("recall_engine.injection_hybrid_max_preset", "quality")),
            delivery_override=DeliveryMode(get("recall_engine.injection_delivery_override", "auto")),
            preset_overrides_enabled=bool(get("recall_engine.injection_preset_overrides_enabled", False)),
            budget_chars=int(get("recall_engine.injection_budget_chars", 0)),
            memory_max_chars=int(get("recall_engine.injection_memory_max_chars", 0)),
            metadata_max_chars=int(get("recall_engine.injection_metadata_max_chars", 0)),
            include_key_facts=bool(get("recall_engine.injection_include_key_facts", True)),
            include_topics=bool(get("recall_engine.injection_include_topics", True)),
            include_participants=bool(get("recall_engine.injection_include_participants", False)),
            compact_header=bool(get("recall_engine.injection_compact_header", True)),
            invalid_config_fallback=bool(
                getattr(self._config_manager, "runtime_injection_fallback", False)
            ),
        )

    def _preflight_signals(
        self, query_intent: Any, provider: Any, req: Any, chat_type: str
    ) -> RequestSignals:
        try:
            provider_type, provider_model, tools_supported = (
                self._injection_adapter.capabilities(provider)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            provider_type, provider_model, tools_supported = "", "", False
            logger.warning(
                "[召回流程] Provider 能力探测失败，使用保守能力",
                exc_info=True,
            )
        intent = str(getattr(query_intent, "intent", "default") or "default")
        explicit = intent in {
            "relationship", "relational", "temporal", "preference", "contextual"
        }
        return RequestSignals(
            query_intent=intent,
            explicit_history_request=explicit,
            provider_type=str(provider_type or ""),
            provider_model=str(provider_model or ""),
            tools_supported=tools_supported is True,
            memory_tool_available=self._request_has_memory_tool(req),
            context_headroom_chars=self._context_headroom_chars(provider, req),
            chat_type=chat_type,
        )

    def _request_has_memory_tool(self, req: Any) -> bool:
        if self._memory_tool_available is not True:
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        try:
            get_tool = getattr(tool_set, "get_tool", None)
            if callable(get_tool):
                tool = get_tool(FAKE_TOOL_CALL_NAME)
                return tool is not None and bool(getattr(tool, "active", True))
            return any(
                getattr(tool, "name", None) == FAKE_TOOL_CALL_NAME
                and bool(getattr(tool, "active", True))
                for tool in getattr(tool_set, "tools", ())
            )
        except Exception:
            return False

    @classmethod
    def _context_headroom_chars(cls, provider: Any, req: Any) -> int:
        raw_override = getattr(req, "context_headroom_chars", None)
        if isinstance(raw_override, (int, float, str)) and not isinstance(
            raw_override, bool
        ):
            try:
                return max(0, int(raw_override))
            except (OverflowError, TypeError, ValueError):
                pass

        config = getattr(provider, "provider_config", None)
        if not isinstance(config, Mapping):
            return 0
        max_context_tokens = cls._nonnegative_int(config.get("max_context_tokens"))
        if max_context_tokens <= 0:
            return 0
        output_reserve = max(
            cls._nonnegative_int(config.get("max_tokens")),
            cls._nonnegative_int(config.get("max_completion_tokens")),
        )
        request_chars = sum(
            cls._text_chars(getattr(req, field, None))
            for field in (
                "prompt",
                "system_prompt",
                "contexts",
                "extra_user_content_parts",
                "tool_calls_result",
                "image_urls",
                "audio_urls",
            )
        )
        tool_set = getattr(req, "func_tool", None)
        for tool in getattr(tool_set, "tools", ()):
            request_chars += cls._text_chars(
                {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "parameters": getattr(tool, "parameters", {}),
                }
            )
        # Charge one token per text character: conservative for the
        # character-denominated injection budget without a Provider tokenizer.
        return max(0, max_context_tokens - output_reserve - request_chars)

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (OverflowError, TypeError, ValueError):
            return 0

    @classmethod
    def _text_chars(cls, value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, Mapping):
            return sum(
                len(str(key)) + cls._text_chars(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return sum(cls._text_chars(item) for item in value)
        text = getattr(value, "text", None)
        return len(text) if isinstance(text, str) else 0

    @staticmethod
    def _safe_projection_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """只保留可进入模型上下文的 projection 三字段。"""

        raw = metadata.get("derived_projections")
        if not isinstance(raw, list):
            return {}
        allowed_types = {
            "episode_summary",
            "preference_state",
            "relationship_state",
            "conflict_set",
        }
        safe: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            projection_type = item.get("type")
            summary = item.get("summary")
            if projection_type not in allowed_types or not isinstance(summary, str):
                continue
            summary = summary.strip()
            if not summary:
                continue
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(confidence):
                continue
            safe.append(
                {
                    "type": projection_type,
                    "summary": summary,
                    "confidence": max(0.0, min(1.0, confidence)),
                }
            )
        return {"derived_projections": safe} if safe else {}

    @staticmethod
    def _safe_candidates(candidates: list[Any]) -> list[dict[str, Any]]:
        safe: list[dict[str, Any]] = []
        for candidate in candidates:
            content = str(getattr(candidate, "content", "") or "")
            metadata = getattr(candidate, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}
            else:
                metadata = dict(metadata)
                safe_projection = RecallHandler._safe_projection_metadata(metadata)
                metadata.pop("derived_projections", None)
                metadata.update(safe_projection)
            raw_score = getattr(candidate, "final_score", 0.0)
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            stable_id = getattr(candidate, "doc_id", None)
            safe_candidate = {
                "content": content,
                "score": max(0.0, min(1.0, score)),
                "metadata": dict(metadata),
                "timestamp": metadata.get("create_time"),
            }
            if isinstance(stable_id, (str, int)):
                safe_candidate["id"] = stable_id
            safe.append(safe_candidate)
        return safe

    @staticmethod
    def _final_signals(
        preflight: RequestSignals, candidates: list[dict[str, Any]]
    ) -> RequestSignals:
        scores = sorted(
            (float(candidate.get("score", 0.0)) for candidate in candidates),
            reverse=True,
        )
        top_confidence = scores[0] if scores else 0.0
        score_gap = top_confidence - scores[1] if len(scores) > 1 else top_confidence
        token_sets = [
            set(str(candidate.get("content", "")).casefold().split())
            for candidate in candidates
        ]
        similarities: list[float] = []
        for index, first in enumerate(token_sets):
            for second in token_sets[index + 1:]:
                union = first | second
                similarities.append(len(first & second) / len(union) if union else 0.0)
        redundancy = sum(similarities) / len(similarities) if similarities else 0.0
        temporal_conflict = any(
            (candidate.get("metadata") or {}).get("temporal_conflict") is True
            for candidate in candidates
        )
        estimated_chars = sum(
            len(str(candidate.get("content", "")))
            + len(str(candidate.get("metadata", "")))
            for candidate in candidates
        )
        return replace(
            preflight,
            candidate_count=len(candidates),
            top_confidence=max(0.0, min(1.0, top_confidence)),
            score_gap=max(0.0, min(1.0, score_gap)),
            candidate_redundancy=max(0.0, min(1.0, redundancy)),
            temporal_conflict=temporal_conflict,
            estimated_payload_chars=estimated_chars,
        )

    @staticmethod
    def _prospective_context(prospective: list[Any]) -> str:
        if not prospective:
            return ""
        lines = ["[Upcoming Plans]"]
        for candidate in prospective:
            content = str(getattr(candidate, "content", "") or "")
            metadata = getattr(candidate, "metadata", None) or {}
            event_time = metadata.get("event_time")
            suffix = f" (at {event_time})" if event_time else ""
            lines.append(f"- {content}{suffix}")
        return "\n".join(lines)

    async def _execute_and_record(
        self, execution: _RecallExecutionInput
    ) -> InjectionExecutionResult:
        decision = execution.decision
        signals = execution.signals
        cognitive_budget = int(self._config_manager.get(
            "recall_engine.cognitive_context_budget_chars", 300
        ))
        prospective_budget = int(self._config_manager.get(
            "recall_engine.proactive_plan_budget_chars", 240
        ))
        prospective_context = self._prospective_context(execution.prospective)
        if execution.preflight_short_circuit and not prospective_context:
            configured_budget = (
                max(0, decision.memory_budget_chars)
                + max(0, cognitive_budget)
                + max(0, prospective_budget)
            )
            result = InjectionExecutionResult(
                outcome=InjectionOutcome.SKIPPED,
                configured_budget_chars=configured_budget,
                effective_budget_chars=min(
                    signals.context_headroom_chars, configured_budget
                ),
                decision_ms=execution.decision_ms,
                format_ms=execution.cognitive_format_ms,
            )
        else:
            scope_id: str | None = None
            if self._prompt_protection is not None:
                scope_id = self._associate_prompt_protection_scope(execution.event)
                if scope_id is None:
                    configured_budget = (
                        max(0, decision.memory_budget_chars)
                        + max(0, cognitive_budget)
                        + max(0, prospective_budget)
                    )
                    result = InjectionExecutionResult(
                        outcome=InjectionOutcome.ERROR,
                        configured_budget_chars=configured_budget,
                        effective_budget_chars=min(
                            signals.context_headroom_chars, configured_budget
                        ),
                        error_code="PROTECTION_SCOPE_FAILED",
                    )
                    self._record_injection_decision(decision, signals, result)
                    return result
            context = InjectionExecutionContext(
                query=execution.query,
                memories=execution.memories,
                cognitive_context=execution.cognitive_context,
                prospective_context=prospective_context,
                cognitive_budget_chars=cognitive_budget,
                prospective_budget_chars=prospective_budget,
                session_filtered=execution.session_filtered,
                persona_filtered=execution.persona_filtered,
                context_headroom_chars=signals.context_headroom_chars,
                provider=execution.provider,
                scope_id=scope_id,
            )
            result = await self._executor.execute(execution.req, decision, context)
            if self._prompt_protection is not None:
                if result.outcome in {
                    InjectionOutcome.INJECTED,
                    InjectionOutcome.FALLBACK,
                }:
                    self._mark_prompt_protection_required(execution.event, True)
                else:
                    self._clear_prompt_protection_scope(execution.event, scope_id)
            result = replace(
                result,
                decision_ms=execution.decision_ms,
                format_ms=result.format_ms + execution.cognitive_format_ms,
            )
        self._record_injection_decision(decision, signals, result)
        return result

    def _record_injection_decision(
        self,
        decision: InjectionDecision,
        signals: RequestSignals,
        result: InjectionExecutionResult,
    ) -> None:
        if self._injection_recorder is None:
            return
        actual_delivery = (
            result.actual_resolved_delivery or decision.resolved_delivery
        )
        reason_codes = decision.reason_codes
        if (
            result.fallback_applied
            and "PROVIDER_DELIVERY_DOWNGRADED" not in reason_codes
        ):
            reason_codes = (*reason_codes, "PROVIDER_DELIVERY_DOWNGRADED")
        record = InjectionDecisionRecord(
            decision_id=str(uuid.uuid4()),
            created_at_ms=time.time_ns() // 1_000_000,
            routing_mode=decision.routing_mode.value,
            configured_preset=decision.configured_preset.value,
            recommended_preset=decision.recommended_preset.value,
            resolved_preset=decision.resolved_preset.value,
            preferred_delivery=decision.preferred_delivery.value,
            resolved_delivery=actual_delivery.value,
            fallback_applied=result.fallback_applied,
            outcome=result.outcome.value,
            primary_reason=(
                decision.reason_codes[0]
                if decision.reason_codes else "NO_USEFUL_CANDIDATES"
            ),
            reason_codes=reason_codes,
            trace_id=None,
            error_code=result.error_code,
            provider_type=signals.provider_type,
            provider_model=signals.provider_model,
            candidate_count=signals.candidate_count,
            selected_count=result.selected_count,
            dropped_count=result.dropped_count,
            truncated_count=result.truncated_count,
            configured_budget_chars=result.configured_budget_chars,
            effective_budget_chars=result.effective_budget_chars,
            actual_payload_chars=result.actual_payload_chars,
            context_headroom_chars=signals.context_headroom_chars,
            decision_ms=result.decision_ms,
            format_ms=result.format_ms,
            inject_ms=result.inject_ms,
        )
        try:
            self._injection_recorder.record(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[召回流程] 注入决策记录失败", exc_info=True)

    def _record_recall_observability(
        self,
        *,
        total_ms: float,
        injected_count: int,
        filtered_count: int,
    ) -> None:
        try:
            from ..monitoring.metrics import RECALL_DURATION, RECALL_REQUESTS

            RECALL_REQUESTS.inc()
            RECALL_DURATION.labels(stage="total").observe(max(0.0, total_ms) / 1000.0)
        except Exception:
            logger.debug("[召回流程] 指标记录失败", exc_info=True)

        if self._perf_tracker is None:
            return
        # 从 MemoryEngine 读取实际阶段耗时
        timing = getattr(self._memory_engine, "_last_search_timing", None) or {}
        try:
            self._perf_tracker.record({
                "total_ms": max(0.0, total_ms),
                "cache_hit": timing.get("cache_hit", False),
                "cache_lookup_ms": timing.get("cache_lookup_ms", 0.0),
                "bm25_ms": timing.get("bm25_ms", 0.0),
                "vector_ms": timing.get("vector_ms", 0.0),
                "graph_ms": timing.get("graph_ms", 0.0),
                "rerank_ms": timing.get("rerank_ms", 0.0),
                "merge_ms": timing.get("merge_ms", 0.0),
                "boost_ms": timing.get("boost_ms", 0.0),
                "chain_expand_ms": timing.get("chain_expand_ms", 0.0),
                "injected_count": float(injected_count),
                "filtered_count": float(filtered_count),
            })
        except Exception:
            logger.debug("[召回流程] 性能样本记录失败", exc_info=True)

    @staticmethod
    def _associate_prompt_protection_scope(event: AstrMessageEvent) -> str | None:
        scope_id = uuid.uuid4().hex
        official = False
        setter = getattr(event, "set_extra", None)
        getter = getattr(event, "get_extra", None)
        if callable(setter) and callable(getter):
            try:
                setter(PROMPT_PROTECTION_SCOPE_EXTRA_KEY, scope_id)
                setter(PROMPT_PROTECTION_REQUIRED_EXTRA_KEY, False)
                official = getter(PROMPT_PROTECTION_SCOPE_EXTRA_KEY) == scope_id
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "[召回流程] 请求安全关联官方通道写入失败",
                    exc_info=True,
                )
        private = False
        try:
            setattr(event, PROMPT_PROTECTION_SCOPE_ATTR, scope_id)
            setattr(event, PROMPT_PROTECTION_REQUIRED_ATTR, False)
            private = getattr(event, PROMPT_PROTECTION_SCOPE_ATTR, None) == scope_id
        except Exception:
            logger.warning(
                "[召回流程] 请求安全关联私有通道写入失败",
                exc_info=True,
            )
        return scope_id if official or private else None

    @staticmethod
    def _mark_prompt_protection_required(
        event: AstrMessageEvent,
        required: bool,
    ) -> None:
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter(PROMPT_PROTECTION_REQUIRED_EXTRA_KEY, required)
            except Exception:
                logger.warning("[召回流程] 请求安全标记官方通道写入失败", exc_info=True)
        try:
            setattr(event, PROMPT_PROTECTION_REQUIRED_ATTR, required)
        except Exception:
            logger.warning("[召回流程] 请求安全标记私有通道写入失败", exc_info=True)

    def _clear_prompt_protection_scope(
        self,
        event: AstrMessageEvent,
        scope_id: str | None,
    ) -> None:
        if self._prompt_protection is not None:
            discard = getattr(self._prompt_protection, "discard_scope", None)
            if callable(discard):
                try:
                    discard(scope_id)
                except Exception:
                    logger.warning("[召回流程] 请求安全注册清理失败", exc_info=True)
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter(PROMPT_PROTECTION_SCOPE_EXTRA_KEY, None)
                setter(PROMPT_PROTECTION_REQUIRED_EXTRA_KEY, False)
            except Exception:
                pass
        for attr in (PROMPT_PROTECTION_SCOPE_ATTR, PROMPT_PROTECTION_REQUIRED_ATTR):
            try:
                delattr(event, attr)
            except (AttributeError, TypeError):
                pass

    @staticmethod
    def _get_event_sender_id(event: AstrMessageEvent) -> str | None:
        getter = getattr(event, "get_sender_id", None)
        if not callable(getter):
            return None
        try:
            sender_id = getter()
        except Exception:
            return None
        if sender_id is None:
            return None
        sender_id = str(sender_id).strip()
        return sender_id or None


    @staticmethod
    def _finalize_recall_candidates(
        candidates: list[Any],
        top_k: int,
    ) -> list[Any]:
        """De-duplicate multi-source recall candidates and enforce inject budget."""
        if top_k <= 0 or not candidates:
            return []

        source_priority = {
            "prospective": 3,
            "main": 2,
            "spontaneous": 1,
        }

        def candidate_key(item: Any) -> tuple[str, Any]:
            doc_id = getattr(item, "doc_id", None)
            if doc_id is not None:
                return ("doc", doc_id)
            metadata = getattr(item, "metadata", None) or {}
            source = metadata.get("recall_source", "main")
            content = str(getattr(item, "content", "") or "")
            digest = hashlib.sha256(f"{source}\0{content}".encode("utf-8")).hexdigest()
            return ("content", digest)

        def rank_tuple(item: Any) -> tuple[int, float]:
            metadata = getattr(item, "metadata", None) or {}
            source = metadata.get("recall_source", "main")
            return (
                source_priority.get(str(source), source_priority["main"]),
                float(getattr(item, "final_score", 0.0) or 0.0),
            )

        deduped: dict[tuple[str, Any], Any] = {}
        for item in candidates:
            key = candidate_key(item)
            existing = deduped.get(key)
            if existing is None or rank_tuple(item) > rank_tuple(existing):
                deduped[key] = item

        finalized = list(deduped.values())
        finalized.sort(key=rank_tuple, reverse=True)
        return finalized[:top_k]


    async def _build_cognitive_context(
        self,
        text: str,
        group_id: str,
        persona_id: str,
    ) -> str:
        """构建来自 v1.0+ 认知模块的可选只读上下文。"""
        parts: list[str] = []
        try:
            if self._jargon_query_service is not None:
                explanation = await self._jargon_query_service.check_and_explain(
                    text, group_id
                )
                if explanation:
                    parts.append(explanation)
        except Exception:
            logger.debug("[召回流程] 黑话解释构建失败", exc_info=True)

        try:
            if self._expression_learner is not None:
                patterns = await self._expression_learner.format_patterns_for_prompt(
                    group_id=group_id,
                    persona_id=persona_id,
                    limit=3,
                )
                if patterns:
                    parts.append(patterns)
        except Exception:
            logger.debug("[召回流程] 表达模式格式化失败", exc_info=True)

        try:
            if self._affection_manager is not None:
                status = await self._affection_manager.get_group_affection_status(
                    group_id
                )
                mood = status.get("current_mood") if isinstance(status, dict) else None
                if mood:
                    parts.append(
                        "[互动状态]\n"
                        f"- 当前情绪: {mood.get('type', 'calm')} "
                        f"({mood.get('description', '')})"
                    )
        except Exception:
            logger.debug("[召回流程] 好感度上下文构建失败", exc_info=True)

        return "\n".join(parts)

    async def _build_fallback_query(self, session_id: str) -> str | None:
        """从最近历史消息构建回退查询，用于空消息场景（如纯 @mention）。

        获取最近 5 条消息，取最近 3 条非空内容拼接为查询字符串。
        """
        try:
            recent = await self._conversation_manager.get_context(
                session_id,
                max_messages=5,
            )
            if not recent or len(recent) <= 1:
                return None
            parts: list[str] = []
            for msg in reversed(recent[1:]):
                content = msg.get("content", "")
                if content and content.strip():
                    parts.append(content.strip())
            return " ".join(parts[:3]) if parts else None
        except Exception:
            logger.debug(
                f"[{session_id}] 构建回退查询失败",
                exc_info=True,
            )
            return None

    @monitored
    async def _maybe_spontaneous_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
    ) -> list[Any]:
        """自发回忆 — 以低概率主动浮现非查询驱动的关联记忆。

        约 6% 的请求会触发，使用低阈值宽泛检索，模拟人类"突然想起来"的体验。
        """
        enabled = self._config_manager.get(
            "recall_engine.spontaneous_recall_enabled", True
        )
        if not enabled:
            return []

        probability = float(
            self._config_manager.get(
                "recall_engine.spontaneous_recall_probability", 0.06
            )
        )
        if random.random() >= probability:
            return []

        try:
            # 使用宽泛的通用查询词进行低阈值检索
            seed_queries = [
                "重要的事情",
                "开心的回忆",
                "最近发生的事",
                "之前的对话",
                "难忘的经历",
            ]
            seed_query = random.choice(seed_queries)
            spontaneous_k = int(
                self._config_manager.get("recall_engine.spontaneous_recall_k", 2)
            )

            results = await self._memory_engine.search_memories(
                query=seed_query,
                k=spontaneous_k,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
            )
            # 标记为自发回忆来源
            for r in results:
                meta = r.metadata or {}
                meta["recall_source"] = "spontaneous"
                r.metadata = meta

            if results:
                logger.debug(
                    f"[{session_id}] 自发回忆触发 (p={probability:.0%}): "
                    f"seed='{seed_query}', {len(results)} 条记忆"
                )
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("自发回忆检索失败", exc_info=True)
            return []

    def _prospective_recall_enabled(self) -> bool:
        """读取标准前瞻召回开关，并兼容旧版回退配置。"""
        enabled = self._config_manager.get(
            "recall_engine.prospective_recall_enabled",
            None,
        )
        if enabled is None:
            enabled = self._config_manager.get("prospective.enabled", True)
        return bool(enabled)

    @monitored
    async def _maybe_prospective_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
    ) -> list[Any]:
        """前瞻记忆 — 扫描 24h 内到期的 PLANNED 原子并注入上下文。

        认知原理：人类会自动想起"今天要做 X"，PLANNED 原子承载此功能。
        每次 LLM 请求前扫描，将即将到期的计划注入当前上下文。
        """
        if not self._prospective_recall_enabled():
            return []

        try:
            lookahead_hours = float(
                self._config_manager.get(
                    "recall_engine.prospective_lookahead_hours", 24.0
                )
            )
            lookahead_sec = lookahead_hours * 3600.0
            prospective_k = int(
                self._config_manager.get("recall_engine.prospective_recall_k", 3)
            )

            # 使用 memory_engine 的 atom_store 查询
            engine = self._memory_engine
            if not hasattr(engine, "atom_store") or engine.atom_store is None:
                return []

            planned_atoms = await engine.atom_store.query_upcoming_planned(
                lookahead_sec=lookahead_sec,
                session_id=session_id,
                persona_id=persona_id,
                limit=prospective_k,
            )

            if not planned_atoms:
                return []

            # 将 PLANNED 原子转为 HybridResult 格式
            from ..retrieval.rrf_fusion import HybridResult

            results: list[HybridResult] = []
            for atom in planned_atoms:
                meta = atom.metadata or {}
                meta["recall_source"] = "prospective"
                meta["atom_type"] = "planned"
                meta["event_time"] = atom.event_time
                results.append(
                    HybridResult(
                        doc_id=atom.parent_memory_id,
                        final_score=0.9,  # 高优先级
                        content=f"[待办] {atom.content}",
                        metadata=meta,
                    )
                )

            if results:
                logger.info(
                    f"[{session_id}] 前瞻记忆: {len(results)} 条 PLANNED 原子在 "
                    f"{lookahead_hours:.0f}h 内到期"
                )
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(f"[{session_id}] 前瞻记忆扫描失败", exc_info=True)
            return []
