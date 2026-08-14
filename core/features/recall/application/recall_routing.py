"""召回请求的路由、注入执行与安全作用域辅助。"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from astrbot.api import logger

from ....shared.constants import FAKE_TOOL_CALL_NAME
from ....shared.contracts.prompt_protection import (
    PROMPT_PROTECTION_REQUIRED_ATTR,
    PROMPT_PROTECTION_REQUIRED_EXTRA_KEY,
    PROMPT_PROTECTION_SCOPE_ATTR,
    PROMPT_PROTECTION_SCOPE_EXTRA_KEY,
)
from ...identity.domain.models import IdentityTrust, ResolvedIdentity
from ...injection.application.executor import InjectionExecutionContext
from ...injection.application.headroom import estimate_context_headroom_chars
from ...injection.application.router import InjectionRoutingConfig
from ...injection.domain.models import (
    DeliveryMode,
    InjectionDecision,
    InjectionDecisionRecord,
    InjectionExecutionResult,
    InjectionOutcome,
    PresetName,
    RequestSignals,
    RoutingMode,
)
from ...observability.application import runtime as observability
from ...observability.domain import recall_timing as rt
from .recall_observability import RecallTimingContext

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


@dataclass(frozen=True, slots=True)
class _RecallExecutionInput:
    """保存一次注入执行所需的安全、计时与候选快照。"""

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


class RecallRoutingMixin:
    """为 RecallHandler 提供路由和注入边界实现。"""

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
                confidence = float(cast(Any, item.get("confidence")))
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
                safe_projection = RecallRoutingMixin._safe_projection_metadata(metadata)
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
            from ...observability.infrastructure.metrics import (
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

    _config_manager: Any
    _injection_adapter: Any
    _memory_tool_available: bool
    _prompt_protection: Any
    _executor: Any
    _injection_recorder: Any
    _perf_tracker: Any
