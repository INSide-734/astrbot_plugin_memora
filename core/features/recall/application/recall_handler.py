"""记忆召回处理器 — LLM 请求前检索并注入长期记忆"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ....platform.config.manager import ConfigManager
from ....platform.context_helpers import get_persona_id
from ....shared.contracts import RecallPort
from ....shared.cost_control import CostControl
from ....shared.data_helpers import OperationContext
from ...conversation.application.conversation_manager import ConversationManager
from ...conversation.application.message_content_extractor import (
    MessageContentExtractor,
)
from ...identity.domain.models import IdentityTrust, ResolvedIdentity
from ...injection.application.executor import InjectionExecutor
from ...injection.application.router import InjectionStrategyRouter
from ...observability.application import runtime as observability
from ...retrieval.query_planner import QueryPlanner
from ...retrieval.query_rewriter import QueryRewriter, resolve_reference_time
from .auxiliary_recall import AuxiliaryRecall
from .injection_cleaner import InjectionCleaner
from .recall_context import RecallContextMixin
from .recall_observability import RecallTimingContext
from .recall_routing import RecallRoutingMixin, _RecallExecutionInput

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import ProviderRequest

    from ....features.injection.application.injection_adapter import InjectionAdapter
    from ....shared.contracts import PromptProtectionPort
    from ...identity.application.enricher import MemoryIdentityEnricher
    from ...injection.infrastructure.recorder import InjectionDecisionRecorder


class RecallHandler(RecallRoutingMixin, RecallContextMixin):
    """LLM 请求前记忆召回 + 注入"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: RecallPort,
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
