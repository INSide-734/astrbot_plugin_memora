"""只读运行诊断与召回追踪命令。"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.platform import MessageType

from ..i18n_backend import t


DiagnosticProvider = Callable[..., Awaitable[Mapping[str, Any]]]

_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_HEALTH_LEVELS = frozenset({"healthy", "watch", "degraded", "critical", "info"})
_HEALTH_DOMAINS = frozenset(
    {"provider", "recall", "write", "scheduler", "index", "prometheus"}
)
_PROVIDER_STATUSES = frozenset({"ready", "waiting", "failed", "unknown"})
_TRACE_STAGES = frozenset({"search_memories", "injection_decision"})
_ROUTING_MODES = frozenset({"auto", "manual", "hybrid"})
_PRESETS = frozenset({"tool_first", "low_cost", "balanced", "quality"})
_TRACE_REASON_CODES = frozenset(
    {
        "AUTO_FALLBACK",
        "AUTO_HISTORY_INTENT",
        "AUTO_LOW_CONTEXT_HEADROOM",
        "AUTO_MEMORY_UNCERTAIN",
        "HYBRID_CLAMPED_MAX",
        "HYBRID_CLAMPED_MIN",
        "INVALID_CONFIG_FALLBACK",
        "MANUAL_SELECTED",
        "PROVIDER_TOOL_UNAVAILABLE",
    }
)

_ACTION_KEYS = {
    ("provider", "critical"): "command_diagnostics.health.actions.provider_critical",
    ("provider", "watch"): "command_diagnostics.health.actions.provider_watch",
    ("recall", "degraded"): "command_diagnostics.health.actions.recall_degraded",
    ("write", "degraded"): "command_diagnostics.health.actions.write_degraded",
    ("scheduler", "watch"): "command_diagnostics.health.actions.scheduler_watch",
    ("index", "watch"): "command_diagnostics.health.actions.index_watch",
}


class DiagnosticCommandMixin:
    """为命令处理器提供只读运行诊断能力。"""

    _diagnostics_health_provider: DiagnosticProvider | None = None
    _diagnostics_metrics_provider: DiagnosticProvider | None = None
    _recall_trace_provider: DiagnosticProvider | None = None

    async def handle_health(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 `/memora health` 命令。"""
        provider = self._diagnostics_health_provider
        if not callable(provider):
            yield event.plain_result(
                self._diagnostic_component_not_ready("/memora health")
            )
            return

        try:
            payload = self._unwrap_provider_payload(await provider())
            if payload is None:
                yield event.plain_result(t("command_diagnostics.health.failed"))
                return
            yield event.plain_result(self._format_health(payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[诊断命令] 获取健康摘要失败，异常类型=%s",
                exc.__class__.__name__,
            )
            yield event.plain_result(t("command_diagnostics.health.failed"))

    async def handle_diagnostics(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 `/memora diagnostics` 命令。"""
        provider = self._diagnostics_metrics_provider
        if not callable(provider):
            yield event.plain_result(
                self._diagnostic_component_not_ready("/memora diagnostics")
            )
            return

        try:
            payload = self._unwrap_provider_payload(await provider())
            if payload is None:
                yield event.plain_result(t("command_diagnostics.snapshot.failed"))
                return
            yield event.plain_result(self._format_diagnostics(payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[诊断命令] 获取实时诊断快照失败，异常类型=%s",
                exc.__class__.__name__,
            )
            yield event.plain_result(t("command_diagnostics.snapshot.failed"))

    async def handle_trace(
        self,
        event: AstrMessageEvent,
        query: str,
        k: int = 5,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 `/memora trace` 命令。"""
        provider = self._recall_trace_provider
        if not callable(provider):
            yield event.plain_result(
                self._diagnostic_component_not_ready("/memora trace")
            )
            return

        normalized_query = str(query or "").strip()
        if not normalized_query:
            yield event.plain_result(t("command_diagnostics.trace.query_empty"))
            return

        safe_k = self._clamp_int(k, minimum=1, maximum=20, default=5)
        session_id = str(event.unified_msg_origin)
        request_payload = {
            "query": normalized_query,
            "k": safe_k,
            "session_id": session_id,
            "chat_type": self._event_chat_type(event, session_id),
        }

        try:
            payload = self._unwrap_provider_payload(await provider(request_payload))
            if payload is None:
                yield event.plain_result(t("command_diagnostics.trace.failed"))
                return
            yield event.plain_result(self._format_trace(payload, safe_k))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[诊断命令] 执行召回追踪失败，异常类型=%s",
                exc.__class__.__name__,
            )
            yield event.plain_result(t("command_diagnostics.trace.failed"))

    @staticmethod
    def _diagnostic_component_not_ready(command: str) -> str:
        return t(
            "error.component_not_ready",
            component=t("command_diagnostics.component"),
            command=command,
        )

    @staticmethod
    def _unwrap_provider_payload(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping) or value.get("status") != "ok":
            return None
        data = value.get("data")
        return dict(data) if isinstance(data, Mapping) else None

    @classmethod
    def _format_health(cls, payload: Mapping[str, Any]) -> str:
        score = cls._clamp_int(payload.get("score"), 0, 100, 0)
        level = cls._safe_choice(payload.get("level"), _HEALTH_LEVELS, "critical")
        lines = [
            t("command_diagnostics.health.header"),
            t("command_diagnostics.health.score", score=score),
            t(
                "command_diagnostics.health.level",
                level=t(f"command_diagnostics.health.levels.{level}"),
            ),
        ]
        domains = cls._health_domains(payload.get("domains"))
        lines.extend(cls._health_domain_lines(domains))
        lines.extend(cls._health_action_lines(domains))
        return "\n".join(lines)

    @classmethod
    def _health_domains(cls, value: Any) -> list[tuple[str, str, int]]:
        domains: list[tuple[str, str, int]] = []
        if not isinstance(value, list):
            return domains
        for item in value:
            if not isinstance(item, Mapping):
                continue
            name = cls._safe_choice(item.get("name"), _HEALTH_DOMAINS, "")
            status = cls._safe_choice(item.get("status"), _HEALTH_LEVELS, "")
            if name and status:
                domains.append(
                    (name, status, cls._clamp_int(item.get("score"), 0, 100, 0))
                )
        return domains

    @staticmethod
    def _health_domain_lines(domains: list[tuple[str, str, int]]) -> list[str]:
        if not domains:
            return [t("command_diagnostics.health.domains_none")]
        lines = [t("command_diagnostics.health.domains_header")]
        lines.extend(
            t(
                "command_diagnostics.health.domain_item",
                domain=t(f"command_diagnostics.health.domains.{name}"),
                status=t(f"command_diagnostics.health.levels.{status}"),
                score=score,
            )
            for name, status, score in domains
        )
        return lines

    @staticmethod
    def _health_action_lines(domains: list[tuple[str, str, int]]) -> list[str]:
        action_keys: list[str] = []
        for name, status, _ in domains:
            action_key = _ACTION_KEYS.get((name, status))
            if action_key and action_key not in action_keys:
                action_keys.append(action_key)
        if not action_keys:
            return []
        return [t("command_diagnostics.health.actions_header"), *(
            t("command_diagnostics.health.action_item", action=t(key))
            for key in action_keys
        )]

    @classmethod
    def _format_diagnostics(cls, payload: Mapping[str, Any]) -> str:
        provider = cls._safe_mapping(payload.get("provider"))
        recall = cls._safe_mapping(payload.get("recall"))
        tasks = cls._safe_mapping(payload.get("background_tasks"))
        index = cls._safe_mapping(payload.get("index"))
        write = cls._safe_mapping(payload.get("write_coordinator"))
        prometheus = cls._safe_mapping(payload.get("prometheus"))

        return "\n".join(
            [
                t("command_diagnostics.snapshot.header"),
                cls._format_snapshot_provider(provider),
                t(
                    "command_diagnostics.snapshot.recall",
                    samples=cls._safe_int(recall.get("sample_count")),
                    avg=cls._number_text(recall.get("avg_total_ms")),
                    p50=cls._number_text(recall.get("p50_total_ms")),
                    p95=cls._number_text(recall.get("p95_total_ms")),
                ),
                t(
                    "command_diagnostics.snapshot.tasks",
                    tracked=cls._safe_int(tasks.get("tracked")),
                    active=cls._safe_int(tasks.get("active")),
                    completed=cls._safe_int(tasks.get("completed")),
                    failed=cls._safe_int(tasks.get("failed")),
                    cancelled=cls._safe_int(tasks.get("cancelled")),
                ),
                t(
                    "command_diagnostics.snapshot.index",
                    available=cls._bool_text(index.get("validator_available")),
                    consistent=cls._bool_text(index.get("last_check_consistent")),
                    needs_rebuild=cls._bool_text(index.get("last_check_needs_rebuild")),
                    total=cls._safe_int(index.get("last_rebuild_total")),
                    errors=cls._safe_int(index.get("last_rebuild_errors")),
                ),
                t(
                    "command_diagnostics.snapshot.write",
                    operations=cls._safe_int(write.get("operations_total")),
                    retries=cls._safe_int(write.get("lock_retries_total")),
                    failures=cls._safe_int(write.get("failures_total")),
                    fatal=cls._safe_int(write.get("fatal_failures_total")),
                    non_retryable=cls._safe_int(write.get("non_retryable_failures_total")),
                ),
                t(
                    "command_diagnostics.snapshot.prometheus",
                    available=cls._bool_text(prometheus.get("available")),
                    collectors=cls._safe_int(prometheus.get("collector_count")),
                ),
            ]
        )

    @classmethod
    def _format_snapshot_provider(cls, provider: Mapping[str, Any]) -> str:
        status = cls._safe_choice(
            provider.get("status"), _PROVIDER_STATUSES, "unknown"
        )
        return t(
            "command_diagnostics.snapshot.provider",
            status=t(f"command_diagnostics.snapshot.provider_status.{status}"),
            attempts=cls._safe_int(provider.get("attempts")),
            max_attempts=cls._safe_int(provider.get("max_attempts")),
            retry=cls._bool_text(provider.get("retry_active")),
        )

    @classmethod
    def _format_trace(cls, payload: Mapping[str, Any], limit: int) -> str:
        trace_id = cls._safe_trace_id(payload.get("trace_id"))
        lines = [
            t("command_diagnostics.trace.header"),
            t("command_diagnostics.trace.id", trace_id=trace_id),
            t(
                "command_diagnostics.trace.total",
                duration=cls._number_text(payload.get("total_ms")),
            ),
        ]
        lines.extend(cls._format_trace_stages(payload.get("stages")))

        filtered = payload.get("filtered")
        filtered_count = len(filtered) if isinstance(filtered, list) else 0
        lines.append(t("command_diagnostics.trace.filtered", count=filtered_count))
        formatted_results = cls._format_trace_results(payload.get("results"), limit)
        lines.append(
            t("command_diagnostics.trace.results_header")
            if formatted_results
            else t("command_diagnostics.trace.results_none")
        )
        lines.extend(formatted_results)
        return "\n".join(lines)

    @classmethod
    def _format_trace_stages(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        stages = [
            item
            for item in value
            if isinstance(item, Mapping) and item.get("name") in _TRACE_STAGES
        ]
        if not stages:
            return []
        lines = [t("command_diagnostics.trace.stages_header")]
        for stage in stages:
            name = str(stage["name"])
            lines.append(
                t(
                    "command_diagnostics.trace.stage_item",
                    stage=t(f"command_diagnostics.trace.stages.{name}"),
                    duration=cls._number_text(stage.get("duration_ms")),
                    count=cls._safe_int(stage.get("candidate_count")),
                )
            )
            if name == "injection_decision":
                lines.append(cls._format_trace_route(stage.get("metadata")))
        return lines

    @classmethod
    def _format_trace_route(cls, value: Any) -> str:
        """格式化固定枚举的路由预览，不透传任意 metadata。"""
        metadata = cls._safe_mapping(value)
        routing_mode = cls._safe_choice(metadata.get("routing_mode"), _ROUTING_MODES, "")
        preset = cls._safe_choice(metadata.get("resolved_preset"), _PRESETS, "")
        reason_code = cls._safe_reason_code(metadata.get("reason_code"))
        return t(
            "command_diagnostics.trace.route",
            mode=routing_mode or t("common.none"),
            preset=preset or t("common.none"),
            reasons=reason_code or t("common.none"),
        )

    @classmethod
    def _format_trace_results(cls, value: Any, limit: int) -> list[str]:
        """格式化无 canonical memory ID 的排名和分数。"""
        results = value if isinstance(value, list) else []
        formatted_results: list[str] = []
        for index, item in enumerate(results[:limit], start=1):
            if not isinstance(item, Mapping):
                continue
            formatted_results.append(
                t(
                    "command_diagnostics.trace.result_item",
                    rank=cls._clamp_int(item.get("rank"), 1, limit, index),
                    initial=cls._number_text(item.get("initial_score")),
                    final=cls._number_text(item.get("final_score")),
                )
            )
        return formatted_results

    @staticmethod
    def _event_chat_type(event: AstrMessageEvent, session_id: str) -> str:
        try:
            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                return "group"
        except Exception:
            return "group" if "GroupMessage" in session_id else "private"
        return "private"

    @staticmethod
    def _safe_mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _safe_choice(value: Any, choices: frozenset[str], default: str) -> str:
        text = str(value or "").strip().lower()
        return text if text in choices else default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _clamp_int(
        cls,
        value: Any,
        minimum: int,
        maximum: int,
        default: int,
    ) -> int:
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _number_text(value: Any) -> str:
        if isinstance(value, bool):
            return t("common.none")
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return t("common.none")
        if not math.isfinite(parsed):
            return t("common.none")
        return f"{parsed:.2f}"

    @staticmethod
    def _bool_text(value: Any) -> str:
        if value is True:
            return t("common.yes")
        if value is False:
            return t("common.no")
        return t("common.none")

    @staticmethod
    def _safe_trace_id(value: Any) -> str:
        trace_id = str(value or "").strip()
        return trace_id if _TRACE_ID_PATTERN.fullmatch(trace_id) else t("common.none")

    @staticmethod
    def _safe_reason_code(value: Any) -> str:
        """只返回允许列表中的单个路由原因码。"""
        return value if isinstance(value, str) and value in _TRACE_REASON_CODES else ""


__all__ = ["DiagnosticCommandMixin", "DiagnosticProvider"]
