"""记忆召回处理器 — LLM 请求前检索并注入长期记忆"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ..base.config_manager import ConfigManager
from ..cleaners.injection_cleaner import InjectionCleaner
from ..extractors.message_content_extractor import MessageContentExtractor
from ..features.observability.application import runtime as observability
from ..features.observability.domain import recall_timing as rt
from ..identity.models import IdentityTrust, ResolvedIdentity
from ..injection.executor import InjectionExecutionContext, InjectionExecutor
from ..injection.headroom import estimate_context_headroom_chars
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
from ..managers.conversation_manager import ConversationManager
from ..managers.memory_engine import MemoryEngine
from ..retrieval.query_planner import QueryPlanner
from ..retrieval.query_rewriter import QueryRewriter, resolve_reference_time
from ..shared.constants import FAKE_TOOL_CALL_NAME
from ..shared.contracts.prompt_protection import (
    PROMPT_PROTECTION_REQUIRED_ATTR,
    PROMPT_PROTECTION_REQUIRED_EXTRA_KEY,
    PROMPT_PROTECTION_SCOPE_ATTR,
    PROMPT_PROTECTION_SCOPE_EXTRA_KEY,
)
from ..shared.cost_control import CostControl
from ..utils import OperationContext, get_persona_id
from .auxiliary_recall import AuxiliaryRecall
from .continuity_hooks import build_continuity_context
from .recall_observability import RecallTimingContext
from .reconsolidation_dispatch import schedule_reconsolidation_proposal

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import ProviderRequest

    from ..identity.memory import MemoryIdentityEnricher
    from ..injection.recorder import InjectionDecisionRecorder
    from ..shared.contracts import PromptProtectionPort
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
    required_facets: tuple[str, ...] = ()
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
        prompt_protection_service: PromptProtectionPort | None = None,
        perf_tracker: Any | None = None,
        injection_recorder: InjectionDecisionRecorder | None = None,
        memory_tool_available: bool = False,
        identity_enricher: MemoryIdentityEnricher | None = None,
        query_rewrite_llm_caller: Any | None = None,
        cost_control: CostControl | None = None,
    ) -> None:
        """装配召回依赖与可选的历史别名只读增强器。"""

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
        self._identity_enricher = identity_enricher
        self._router = InjectionStrategyRouter()
        self._executor = InjectionExecutor(injection_adapter, prompt_protection_service)
        self._cleaner = InjectionCleaner()
        self._extractor = MessageContentExtractor()
        self._query_rewriter = QueryRewriter(
            llm_caller=query_rewrite_llm_caller,
            enabled=config_manager.get("recall_engine.query_rewrite_enabled", True),
            cost_control=cost_control,
        )
        self._auxiliary_recall = AuxiliaryRecall(config_manager, memory_engine)

    @observability.monitored
    async def handle_memory_recall(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        identity: ResolvedIdentity | None = None,
        timing_context: RecallTimingContext | None = None,
    ) -> None:
        """在 LLM 请求前查询并注入长期记忆，可使用已解析协议身份。"""
        recall_started = time.perf_counter()
        if timing_context is None:
            timing_context = RecallTimingContext.start(
                self._config_manager.get(
                    "recall_engine.pre_llm_soft_budget_ms",
                    800,
                ),
                started_monotonic=recall_started,
            )
        injected_count = 0
        filtered_count = 0
        candidate_count = 0
        injection_format_ms: float = 0.0
        injection_inject_ms: float = 0.0
        injection_chars: int = 0
        injection_selected_count: int = 0
        recall_status = "completed"
        recall_reason = "recall_completed"
        observability.report_debug_event(
            "recall_stage",
            component="recall",
            stage="request",
            status="started",
            reason_code="request_received",
        )
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
                    recall_status = "skipped"
                    recall_reason = "empty_request"
                    observability.report_debug_event(
                        "recall_stage",
                        component="recall",
                        stage="request",
                        status="skipped",
                        reason_code="empty_request",
                    )
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
                        observability.report_debug_event(
                            "recall_stage",
                            component="recall",
                            stage="context_cleanup",
                            status="completed",
                            reason_code="history_injection_removed",
                            count=removed,
                        )
                        logger.info(
                            f"[{session_id}] 已清理 {removed} 处历史记忆注入片段"
                        )

                query_analysis_started = time.perf_counter()
                actual_query = await self._extractor.get_event_message_str(event)
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="query",
                    status="completed" if actual_query else "degraded",
                    reason_code="message_query_ready"
                    if actual_query
                    else "message_query_empty",
                    count=1 if actual_query else 0,
                )

                request_query = (
                    prompt_text.strip() if isinstance(prompt_text, str) else ""
                )

                is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
                should_store_private_user = (
                    identity is None
                    or identity.trust_status
                    in {IdentityTrust.TRUSTED, IdentityTrust.UNSUPPORTED}
                )
                if not is_group and actual_query and should_store_private_user:
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
                        identity=identity,
                    )
                    await self._enforce_limit_cb(session_id)

                top_k = self._config_manager.get("recall_engine.top_k", 5)
                if top_k <= 0:
                    recall_status = "skipped"
                    recall_reason = "top_k_disabled"
                    observability.report_debug_event(
                        "recall_stage",
                        component="recall",
                        stage="retrieval",
                        status="skipped",
                        reason_code="top_k_disabled",
                    )
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
                        recall_status = "skipped"
                        recall_reason = "empty_query"
                        observability.report_debug_event(
                            "recall_stage",
                            component="recall",
                            stage="query",
                            status="skipped",
                            reason_code="empty_query",
                        )
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
                # R1.5：查询计划构建 — 生成多查询计划用于跨查询RRF融合
                query_plan = QueryPlanner.build(query=actual_query, intent=query_intent)
                timing_context.record_elapsed(
                    "query_analysis_ms",
                    query_analysis_started,
                )
                # 使用改写后的第一条查询（或原始查询）作为主检索词
                rewritten_queries = query_intent.rewritten_queries
                primary_query = (
                    rewritten_queries[0] if rewritten_queries else query_for_search
                )
                memory_type_filter = query_intent.memory_types or None
                reference_time = resolve_reference_time(query_intent)
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="query_rewrite",
                    status="completed",
                    reason_code="query_rewritten",
                    count=len(rewritten_queries),
                )

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

                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="provider",
                    status="completed" if provider is not None else "degraded",
                    reason_code="provider_available"
                    if provider is not None
                    else "provider_unavailable",
                    count=1 if provider is not None else 0,
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
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="preflight",
                    status="skipped" if preflight.skip_passive_recall else "completed",
                    reason_code="passive_recall_skipped"
                    if preflight.skip_passive_recall
                    else "preflight_completed",
                    duration_ms=max(0.0, preflight_ms),
                    route=preflight.resolved_preset.value,
                    delivery=preflight.resolved_delivery.value,
                )

                if preflight.skip_passive_recall:
                    prospective = await self._maybe_prospective_recall(
                        session_id=recall_session_id,
                        persona_id=recall_persona_id,
                        chat_type=chat_type,
                        deadline_monotonic=timing_context.deadline_monotonic,
                    )
                    result = await self._execute_and_record(
                        _RecallExecutionInput(
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
                        )
                    )
                    injected_count = result.selected_count
                    injection_format_ms = result.format_ms
                    injection_inject_ms = result.inject_ms
                    injection_chars = result.actual_payload_chars
                    injection_selected_count = result.selected_count
                    recall_reason = "passive_recall_only"
                    return

                user_id = self._get_event_sender_id(event, identity)
                retrieval_started = time.perf_counter()
                recalled_memories = await self._memory_engine.search_memories(
                    query=primary_query,
                    k=top_k,
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                    query_intent=query_intent,
                    memory_types=memory_type_filter,
                    user_id=user_id,
                    reference_time=reference_time,
                    query_plan=query_plan,
                    timing_sink=timing_context.retrieval,
                    deadline_monotonic=timing_context.deadline_monotonic,
                )
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="retrieval",
                    status="completed",
                    reason_code="memory_search_completed",
                    duration_ms=max(
                        0.0, (time.perf_counter() - retrieval_started) * 1000.0
                    ),
                    count=len(recalled_memories or []),
                )

                spontaneous = await self._maybe_spontaneous_recall(
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                    deadline_monotonic=timing_context.deadline_monotonic,
                )
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="spontaneous",
                    status="completed",
                    reason_code="spontaneous_recall_completed",
                    count=len(spontaneous or []),
                )
                ordinary_candidates = list(recalled_memories or [])
                if spontaneous:
                    ordinary_candidates.extend(spontaneous)
                candidate_finalize_started = time.perf_counter()
                ordinary_candidates = self._finalize_recall_candidates(
                    ordinary_candidates,
                    top_k=top_k,
                )
                timing_context.record_elapsed(
                    "candidate_finalize_ms",
                    candidate_finalize_started,
                )

                prospective = await self._maybe_prospective_recall(
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                    deadline_monotonic=timing_context.deadline_monotonic,
                )
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="prospective",
                    status="completed",
                    reason_code="prospective_recall_completed",
                    count=len(prospective or []),
                )
                memories = self._safe_candidates(ordinary_candidates)
                if self._identity_enricher is not None:
                    memories = await self._identity_enricher.enrich(
                        memories,
                        identity=identity,
                        session_id=session_id,
                    )
                final_signals = self._final_signals(preflight_signals, memories)
                candidate_count = final_signals.candidate_count
                decision_started = time.perf_counter()
                decision = self._router.route_final(routing_config, final_signals)
                decision_ms = (
                    preflight_ms + (time.perf_counter() - decision_started) * 1000.0
                )
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="decision",
                    status="completed",
                    reason_code="routing_decision_completed",
                    duration_ms=max(0.0, decision_ms),
                    candidate_count=candidate_count,
                    route=decision.resolved_preset.value,
                    delivery=decision.resolved_delivery.value,
                )
                format_started = time.perf_counter()
                cognitive_context = await self._build_cognitive_context(
                    text=actual_query,
                    group_id=session_id or "default",
                    persona_id=persona_id or "default",
                )
                format_ms = (time.perf_counter() - format_started) * 1000.0
                observability.report_debug_event(
                    "recall_stage",
                    component="recall",
                    stage="format",
                    status="completed",
                    reason_code="cognitive_context_formatted",
                    duration_ms=max(0.0, format_ms),
                    payload_chars=len(cognitive_context),
                )
                result = await self._execute_and_record(
                    _RecallExecutionInput(
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
                        required_facets=query_plan.required_facets,
                        cognitive_format_ms=format_ms,
                    )
                )
                await self._maybe_propose_reconsolidation(memories, actual_query)
                injected_count = result.selected_count
                injection_format_ms = result.format_ms
                injection_inject_ms = result.inject_ms
                injection_chars = result.actual_payload_chars
                injection_selected_count = result.selected_count

        except asyncio.CancelledError:
            recall_status = "cancelled"
            recall_reason = "recall_cancelled"
            observability.report_debug_event(
                "recall_stage",
                component="recall",
                stage="recall",
                status="cancelled",
                reason_code="recall_cancelled",
            )
            raise
        except Exception as e:
            recall_status = "failed"
            recall_reason = "recall_error"
            observability.report_debug_exception(
                "recall_failed",
                e,
                component="recall",
                stage="recall",
                status="failed",
                reason_code="recall_error",
            )
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)
        finally:
            recall_total_ms = (time.perf_counter() - recall_started) * 1000.0
            timing_context.record("recall_hook_total_ms", recall_total_ms)
            self._record_recall_observability(
                total_ms=recall_total_ms,
                injected_count=injected_count,
                filtered_count=filtered_count,
                candidate_count=candidate_count,
                status=recall_status,
                reason_code=recall_reason,
                format_ms=injection_format_ms,
                inject_ms=injection_inject_ms,
                injection_chars=injection_chars,
                selected_count=injection_selected_count,
                timing_context=timing_context,
            )

    def _routing_config(self) -> InjectionRoutingConfig:
        get = self._config_manager.get
        return InjectionRoutingConfig(
            mode=RoutingMode(get("recall_engine.injection_routing_mode", "manual")),
            manual_preset=PresetName(
                get("recall_engine.injection_manual_preset", "balanced")
            ),
            auto_fallback=PresetName(
                get("recall_engine.injection_auto_fallback_preset", "balanced")
            ),
            hybrid_base=PresetName(
                get("recall_engine.injection_hybrid_base_preset", "balanced")
            ),
            hybrid_min=PresetName(
                get("recall_engine.injection_hybrid_min_preset", "low_cost")
            ),
            hybrid_max=PresetName(
                get("recall_engine.injection_hybrid_max_preset", "quality")
            ),
            delivery_override=DeliveryMode(
                get("recall_engine.injection_delivery_override", "auto")
            ),
            preset_overrides_enabled=bool(
                get("recall_engine.injection_preset_overrides_enabled", False)
            ),
            budget_chars=int(get("recall_engine.injection_budget_chars", 0)),
            memory_max_chars=int(get("recall_engine.injection_memory_max_chars", 0)),
            metadata_max_chars=int(
                get("recall_engine.injection_metadata_max_chars", 0)
            ),
            include_key_facts=bool(
                get("recall_engine.injection_include_key_facts", True)
            ),
            include_topics=bool(get("recall_engine.injection_include_topics", True)),
            include_participants=bool(
                get("recall_engine.injection_include_participants", False)
            ),
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
            "relationship",
            "relational",
            "temporal",
            "preference",
            "contextual",
        }
        return RequestSignals(
            query_intent=intent,
            explicit_history_request=explicit,
            provider_type=str(provider_type or ""),
            provider_model=str(provider_model or ""),
            tools_supported=tools_supported is True,
            memory_tool_available=self._request_has_memory_tool(req),
            context_headroom_chars=estimate_context_headroom_chars(provider, req),
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
                metadata.pop("identity_reference_lines", None)
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
            # 仅把固定 facet 信号复制到注入选择的请求局部字段。
            score_breakdown = getattr(candidate, "score_breakdown", None)
            if isinstance(score_breakdown, dict):
                matched_facets: dict[str, float] = {}
                for k in ("entity", "role", "time", "event", "focus", "relation"):
                    v = score_breakdown.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        matched_facets[k] = float(v)
                if matched_facets:
                    safe_candidate["_matched_facets"] = matched_facets
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
            for second in token_sets[index + 1 :]:
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
        cognitive_budget = int(
            self._config_manager.get(
                "recall_engine.cognitive_context_budget_chars", 300
            )
        )
        prospective_budget = int(
            self._config_manager.get("recall_engine.proactive_plan_budget_chars", 240)
        )
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
                    self._report_injection_result(decision, signals, result)
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
                required_facets=execution.required_facets,
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
        self._report_injection_result(decision, signals, result)
        return result

    @staticmethod
    def _report_injection_result(
        decision: InjectionDecision,
        signals: RequestSignals,
        result: InjectionExecutionResult,
    ) -> None:
        """记录不含 Provider 或记忆内容的注入结果摘要。"""
        actual_delivery = result.actual_resolved_delivery or decision.resolved_delivery
        outcome = result.outcome.value
        observability.report_debug_event(
            "injection_completed",
            component="injection",
            stage="injection",
            status="completed" if outcome in {"injected", "fallback"} else "skipped",
            reason_code=result.error_code or outcome,
            route=decision.resolved_preset.value,
            delivery=actual_delivery.value,
            outcome=outcome,
            candidate_count=max(0, int(signals.candidate_count)),
            selected_count=max(0, int(result.selected_count)),
            configured_budget_chars=max(0, int(result.configured_budget_chars)),
            effective_budget_chars=max(0, int(result.effective_budget_chars)),
            payload_chars=max(0, int(result.actual_payload_chars)),
        )
        logger.info(
            "[召回流程] 注入结果：outcome=%s, route=%s, delivery=%s, "
            "candidates=%d, selected=%d, budget=%d/%d",
            outcome,
            decision.resolved_preset.value,
            actual_delivery.value,
            max(0, int(signals.candidate_count)),
            max(0, int(result.selected_count)),
            max(0, int(result.effective_budget_chars)),
            max(0, int(result.configured_budget_chars)),
        )

    def _record_injection_decision(
        self,
        decision: InjectionDecision,
        signals: RequestSignals,
        result: InjectionExecutionResult,
    ) -> None:
        if self._injection_recorder is None:
            return
        actual_delivery = result.actual_resolved_delivery or decision.resolved_delivery
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
                if decision.reason_codes
                else "NO_USEFUL_CANDIDATES"
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
        candidate_count: int,
        status: str = "completed",
        reason_code: str = "recall_completed",
        format_ms: float = 0.0,
        inject_ms: float = 0.0,
        injection_chars: int = 0,
        selected_count: int = 0,
        timing_context: RecallTimingContext | None = None,
    ) -> None:
        """记录一次召回的安全总量、阶段计时和结果计数。"""

        observability.report_debug_event(
            "recall_completed",
            component="recall",
            stage="recall",
            status=status,
            reason_code=reason_code,
            duration_ms=max(0.0, float(total_ms)),
            candidate_count=max(0, int(candidate_count)),
            injected_count=max(0, int(injected_count)),
            filtered_count=max(0, int(filtered_count)),
        )
        try:
            from ..features.observability.infrastructure.metrics import (
                RECALL_DURATION,
                RECALL_REQUESTS,
            )

            RECALL_REQUESTS.inc()
            RECALL_DURATION.labels(stage="total").observe(max(0.0, total_ms) / 1000.0)
        except Exception:
            logger.debug("[召回流程] 指标记录失败", exc_info=True)

        if self._perf_tracker is None:
            return
        timing = timing_context.snapshot() if timing_context is not None else {}
        try:
            sample = {
                key: timing[key]
                for key in (*rt.TIMING_KEYS, *rt.COUNT_KEYS, *rt.BOOL_KEYS)
                if key in timing
            }
            sample.update(
                {
                    "total_ms": max(0.0, total_ms),
                    "recall_hook_total_ms": max(0.0, total_ms),
                    "format_ms": max(0.0, format_ms),
                    "injection_ms": max(0.0, inject_ms),
                    "injection_chars": max(0, injection_chars),
                    "selected_count": max(0, selected_count),
                    "candidate_count": max(0, candidate_count),
                }
            )
            self._perf_tracker.record(sample)
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
    def _get_event_sender_id(
        event: AstrMessageEvent,
        identity: ResolvedIdentity | None = None,
    ) -> str | None:
        """优先返回可信 canonical ID，未注册协议沿用事件发送者。"""

        if identity is not None:
            if identity.trust_status is IdentityTrust.TRUSTED:
                return identity.canonical_user_id
            if identity.trust_status is not IdentityTrust.UNSUPPORTED:
                return None
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
        """对多来源召回候选去重，并执行注入数量上限。"""
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
        continuity_context = build_continuity_context(
            self._memory_engine,
            group_id,
        )
        if continuity_context:
            parts.append(continuity_context)
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

    async def _maybe_propose_reconsolidation(
        self,
        memories: list[Any],
        query: str,
    ) -> None:
        """把最高分记忆的再巩固候选交给引擎生命周期任务所有者。"""

        await schedule_reconsolidation_proposal(
            self._memory_engine,
            memories,
            query,
        )

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

    @observability.monitored
    async def _maybe_spontaneous_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        deadline_monotonic: float | None = None,
    ) -> list[Any]:
        """兼容旧调用边界，并委托独立组件执行受预算约束的自发回忆。"""

        return await self._auxiliary_recall.maybe_spontaneous_recall(
            session_id=session_id,
            persona_id=persona_id,
            chat_type=chat_type,
            deadline_monotonic=deadline_monotonic,
        )

    def _prospective_recall_enabled(self) -> bool:
        """读取标准前瞻召回开关，并兼容旧版回退配置。"""

        return self._auxiliary_recall.prospective_enabled()

    @observability.monitored
    async def _maybe_prospective_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        deadline_monotonic: float | None = None,
    ) -> list[Any]:
        """兼容旧调用边界，并委托独立组件执行受预算约束的前瞻召回。"""

        return await self._auxiliary_recall.maybe_prospective_recall(
            session_id=session_id,
            persona_id=persona_id,
            chat_type=chat_type,
            deadline_monotonic=deadline_monotonic,
        )
