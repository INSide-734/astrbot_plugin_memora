"""Read-only Page APIs for adaptive memory injection strategies."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from astrbot.api import logger
from quart import request

from ....features.injection.application.injection_adapter import InjectionAdapter
from ....features.injection.application.presets import PRESETS
from ....features.injection.domain.models import (
    DeliveryMode,
    InjectionOutcome,
    PresetName,
    RoutingMode,
)
from ....features.injection.infrastructure.injection_decision_store import (
    DecisionPage,
    DecisionQuery,
    InjectionDecisionStore,
)
from ....features.retrieval.trace_privacy import normalize_trace_id
from .response_utils import error_response, ok_response

_WINDOWS = frozenset({"1h", "24h", "7d", "30d"})
_RETENTION_OPTIONS = [7, 30, 90, 180, 0]
_LIST_QUERY_FIELDS = frozenset(
    {
        "offset",
        "limit",
        "from_ms",
        "to_ms",
        "routing_mode",
        "resolved_preset",
        "provider_type",
        "primary_reason",
        "fallback_applied",
        "outcome",
        "sort_by",
        "sort_order",
    }
)
_RECENT_EVENT_FIELDS = (
    "decision_id",
    "created_at_ms",
    "trace_id",
    "routing_mode",
    "resolved_preset",
    "outcome",
    "primary_reason",
    "fallback_applied",
    "actual_payload_chars",
)
_LIST_ITEM_FIELDS = _RECENT_EVENT_FIELDS + (
    "configured_preset",
    "recommended_preset",
    "preferred_delivery",
    "resolved_delivery",
    "provider_type",
    "provider_model",
    "error_code",
    "candidate_count",
    "selected_count",
    "dropped_count",
    "truncated_count",
    "configured_budget_chars",
    "effective_budget_chars",
    "context_headroom_chars",
    "decision_ms",
    "format_ms",
    "inject_ms",
)

# 值级校验闭集：只允许生产代码当前会写入的取值；其它历史/自由文本值投影为 None。
_ROUTING_MODE_VALUES = frozenset(mode.value for mode in RoutingMode)
_PRESET_VALUES = frozenset(name.value for name in PresetName)
_DELIVERY_VALUES = frozenset(mode.value for mode in DeliveryMode)
_OUTCOME_VALUES = frozenset(item.value for item in InjectionOutcome)
# 与生产路由一致：router 产出的原因码、recall_routing 追加的降级码与缺省主因。
_DECISION_REASON_CODES = frozenset(
    {
        "AUTO_FALLBACK",
        "AUTO_HISTORY_INTENT",
        "AUTO_LOW_CONTEXT_HEADROOM",
        "AUTO_MEMORY_UNCERTAIN",
        "HYBRID_CLAMPED_MAX",
        "HYBRID_CLAMPED_MIN",
        "INVALID_CONFIG_FALLBACK",
        "MANUAL_SELECTED",
        "NO_USEFUL_CANDIDATES",
        "PROVIDER_DELIVERY_DOWNGRADED",
        "PROVIDER_TOOL_UNAVAILABLE",
    }
)
# InjectionExecutor / recall_routing 写入的固定错误码。
_DECISION_ERROR_CODES = frozenset(
    {
        "FORMAT_FAILED",
        "MUTATION_FAILED",
        "PROTECTION_FAILED",
        "PROTECTION_SCOPE_FAILED",
    }
)
# Provider 类型是配置标识符；模型名只允许常见的 vendor/model:tag 字符集。
_PROVIDER_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROVIDER_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}$")
_MAX_COUNT = 10**12
_MAX_TIMESTAMP_MS = 2**53 - 1
_MAX_DURATION_MS = 10**9
_MAX_RATIO = 1_000.0
_MAX_REASON_CODES = len(_DECISION_REASON_CODES)


def _safe_decision_id(value: Any) -> str | None:
    """仅保留规范小写 UUID；其它形式无法用于详情查询，也不应回显。"""
    if not isinstance(value, str):
        return None
    try:
        canonical = str(UUID(value))
    except ValueError:
        return None
    return value if value == canonical else None


def _safe_trace_id(value: Any) -> str | None:
    return normalize_trace_id(value) if isinstance(value, str) else None


def _closed(choices: frozenset[str]) -> Callable[[Any], str | None]:
    def validate(value: Any) -> str | None:
        return value if isinstance(value, str) and value in choices else None

    return validate


def _pattern(pattern: re.Pattern[str]) -> Callable[[Any], str | None]:
    def validate(value: Any) -> str | None:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            return None
        return None if ".." in value or "//" in value else value

    return validate


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def _bounded_int(maximum: int) -> Callable[[Any], int | None]:
    def validate(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            value = int(value)
        if type(value) is not int or not 0 <= value <= maximum:
            return None
        return value

    return validate


def _safe_duration_ms(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= _MAX_DURATION_MS:
        return None
    return parsed


_COUNT = _bounded_int(_MAX_COUNT)
_DECISION_FIELD_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "decision_id": _safe_decision_id,
    "created_at_ms": _bounded_int(_MAX_TIMESTAMP_MS),
    "trace_id": _safe_trace_id,
    "routing_mode": _closed(_ROUTING_MODE_VALUES),
    "configured_preset": _closed(_PRESET_VALUES),
    "recommended_preset": _closed(_PRESET_VALUES),
    "resolved_preset": _closed(_PRESET_VALUES),
    "preferred_delivery": _closed(_DELIVERY_VALUES),
    "resolved_delivery": _closed(_DELIVERY_VALUES),
    "outcome": _closed(_OUTCOME_VALUES),
    "primary_reason": _closed(_DECISION_REASON_CODES),
    "error_code": _closed(_DECISION_ERROR_CODES),
    "fallback_applied": _safe_bool,
    "provider_type": _pattern(_PROVIDER_TYPE_PATTERN),
    "provider_model": _pattern(_PROVIDER_MODEL_PATTERN),
    "candidate_count": _COUNT,
    "selected_count": _COUNT,
    "dropped_count": _COUNT,
    "truncated_count": _COUNT,
    "configured_budget_chars": _COUNT,
    "effective_budget_chars": _COUNT,
    "actual_payload_chars": _COUNT,
    "context_headroom_chars": _COUNT,
    "decision_ms": _safe_duration_ms,
    "format_ms": _safe_duration_ms,
    "inject_ms": _safe_duration_ms,
}


def _project_decision(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """字段 allowlist + 值级复核；缺失字段保持缺失，非法值投影为 None。

    模块级函数，避免 PluginPageApi 多 mixin 组合时私有方法被 MRO 遮蔽。
    """
    if not isinstance(value, dict):
        return {}
    return {
        field: _DECISION_FIELD_VALIDATORS[field](value[field])
        for field in fields
        if field in value
    }


def _project_listed_decisions(
    items: Any, fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    """列表/近期事件：丢弃 decision_id 无法复核的行，保持前端非空行键契约。"""
    if not isinstance(items, (list, tuple)):
        return []
    projected = (_project_decision(item, fields) for item in items)
    return [item for item in projected if item.get("decision_id") is not None]


def _safe_reason_codes(value: Any) -> list[str]:
    """只保留生产路由闭集内的原因码，按首次出现去重。"""
    if not isinstance(value, (list, tuple)):
        return []
    codes: list[str] = []
    for code in value:
        if (
            isinstance(code, str)
            and code in _DECISION_REASON_CODES
            and code not in codes
        ):
            codes.append(code)
    return codes[:_MAX_REASON_CODES]


def _bounded_ratio(maximum: float) -> Callable[[Any], float | None]:
    def validate(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        parsed = float(value)
        if not math.isfinite(parsed) or not 0.0 <= parsed <= maximum:
            return None
        return parsed

    return validate


# 回退率是 [0,1] 概率；预算利用率由 payload/budget 得出，截断误差可略超 1，
# 因此只设宽松上界，拒绝 NaN/inf/负值与明显损坏的旧聚合。
_FALLBACK_RATE = _bounded_ratio(1.0)
_UTILIZATION = _bounded_ratio(_MAX_RATIO)
_SUMMARY_SCALAR_FIELDS: dict[str, tuple[Callable[[Any], Any], Any]] = {
    "retrieved_count": (_COUNT, 0),
    "injected_count": (_COUNT, 0),
    "decision_count": (_COUNT, 0),
    "payload_chars_p95": (_COUNT, 0),
    "provider_fallback_rate": (_FALLBACK_RATE, 0.0),
    "memory_present_count": (_COUNT, 0),
    "payload_injected_count": (_COUNT, 0),
    "selected_count_total": (_COUNT, 0),
    "dropped_count_total": (_COUNT, 0),
    "truncated_count_total": (_COUNT, 0),
    "effective_budget_chars_avg": (_COUNT, 0),
    "budget_utilization_avg": (_UTILIZATION, 0.0),
    "budget_utilization_p95": (_UTILIZATION, 0.0),
}
_COST_POINT_VALIDATORS: dict[str, tuple[Callable[[Any], Any], Any]] = {
    "decision_count": (_COUNT, 0),
    "payload_chars_p95": (_COUNT, 0),
    "provider_fallback_rate": (_FALLBACK_RATE, 0.0),
    "selected_count_total": (_COUNT, 0),
    "dropped_count_total": (_COUNT, 0),
    "budget_utilization_avg": (_UTILIZATION, 0.0),
}


def _safe_cost_point(value: Any) -> dict[str, Any] | None:
    """趋势点以 bucket_ms 为横轴键：键非法则丢弃整点；其余非法值回落为 0。"""
    if not isinstance(value, dict):
        return None
    bucket_ms = _bounded_int(_MAX_TIMESTAMP_MS)(value.get("bucket_ms"))
    if bucket_ms is None:
        return None
    point: dict[str, Any] = {"bucket_ms": bucket_ms}
    for field, (validate, default) in _COST_POINT_VALIDATORS.items():
        parsed = validate(value.get(field, default))
        point[field] = default if parsed is None else parsed
    return point


class InjectionStrategyApiMixin:
    """Expose the strategy catalog and sanitized decision telemetry."""

    async def get_injection_strategy_catalog(self):
        return await self.get_injection_strategy_catalog_payload(dict(request.args))

    async def get_injection_strategy_summary(self):
        return await self.get_injection_strategy_summary_payload(dict(request.args))

    async def list_injection_decisions(self):
        return await self.list_injection_decisions_payload(dict(request.args))

    async def get_injection_decision_detail(self):
        return await self.get_injection_decision_detail_payload(dict(request.args))

    async def get_injection_strategy_catalog_payload(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        adapter = InjectionAdapter()
        provider = await self._current_injection_provider()
        _, _, provider_tools_supported = adapter.capabilities(provider)
        configured_delivery = self._injection_config_value(
            "recall_engine.injection_delivery_override",
            DeliveryMode.AUTO.value,
        )
        try:
            effective_delivery, _ = adapter.resolve(provider, configured_delivery)
        except (TypeError, ValueError):
            effective_delivery = DeliveryMode.EXTRA_USER_CONTENT

        presets = []
        for preset in PRESETS.values():
            presets.append(
                {
                    "name": preset.name.value,
                    "rank": preset.rank,
                    "auto_inject": preset.auto_inject,
                    "memory_budget_chars": preset.memory_budget_chars,
                    "max_memories": preset.max_memories,
                    "content_level": preset.content_level.value,
                    "cost_penalty_weight": preset.cost_penalty_weight,
                    "minimum_utility": preset.minimum_utility,
                    "allow_tool_fallback": preset.allow_tool_fallback,
                    "preferred_delivery": preset.preferred_delivery.value,
                }
            )

        recall_tool_enabled = bool(
            self._injection_config_value("agent_tools.enable_recall_tool", True)
        )
        return ok_response(
            {
                "routing_modes": [mode.value for mode in RoutingMode],
                "presets": presets,
                "deliveries": [mode.value for mode in DeliveryMode],
                "retention_options": list(_RETENTION_OPTIONS),
                "provider_tools_supported": provider_tools_supported,
                "memory_tool_available": bool(
                    getattr(self.plugin, "_llm_tools_registered", False)
                    and recall_tool_enabled
                ),
                "recall_trace_available": True,
                "effective_default_delivery": effective_delivery.value,
            }
        )

    async def get_injection_strategy_summary_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        window = str(payload.get("window", "24h"))
        if window not in _WINDOWS:
            return error_response("window must be one of 1h, 24h, 7d, 30d")
        store = self._injection_store()
        if store is None:
            return error_response("Injection decision store unavailable")
        try:
            summary = await store.summary(window)
        except Exception as exc:
            logger.error(
                "[InjectionStrategyApi] operation=%s failed error_type=%s",
                "summary",
                type(exc).__name__,
            )
            return error_response("Unable to load injection strategy summary")
        return ok_response(self._safe_summary(summary))

    async def list_injection_decisions_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._injection_store()
        if store is None:
            return error_response("Injection decision store unavailable")
        try:
            query = self._decision_query(payload)
        except ValueError as exc:
            return error_response(str(exc))
        try:
            page = await store.list_decisions(query)
        except Exception as exc:
            logger.error(
                "[InjectionStrategyApi] operation=%s failed error_type=%s",
                "list_decisions",
                type(exc).__name__,
            )
            return error_response("Unable to load injection decisions")
        return ok_response(self._safe_page(page))

    async def get_injection_decision_detail_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._injection_store()
        if store is None:
            return error_response("Injection decision store unavailable")
        try:
            decision_id = str(UUID(str(payload.get("decision_id", ""))))
        except (AttributeError, TypeError, ValueError):
            return error_response("decision_id must be a valid UUID")
        try:
            detail = await store.get_decision(decision_id)
        except Exception as exc:
            logger.error(
                "[InjectionStrategyApi] operation=%s failed error_type=%s",
                "get_decision",
                type(exc).__name__,
            )
            return error_response("Unable to load injection decision detail")
        if detail is None:
            return error_response("Injection decision not found")
        safe = self._decision_projection(detail, _LIST_ITEM_FIELDS)
        # 详情以请求中已校验的 UUID 为身份，而不是回显存储行里的值。
        safe["decision_id"] = decision_id
        safe["reason_codes"] = _safe_reason_codes(detail.get("reason_codes", []))
        return ok_response(safe)

    def _injection_store(self) -> InjectionDecisionStore | None:
        initializer = getattr(self.plugin, "initializer", None)
        return getattr(initializer, "injection_decision_store", None)

    async def _current_injection_provider(self) -> Any | None:
        context = getattr(self.plugin, "context", None)
        getter = getattr(context, "get_using_provider", None)
        if not callable(getter):
            return None
        try:
            provider = getter()
            return await provider if inspect.isawaitable(provider) else provider
        except Exception:
            return None

    def _injection_config_value(self, path: str, default: Any) -> Any:
        manager = getattr(self.plugin, "config_manager", None)
        getter = getattr(manager, "get", None)
        if not callable(getter):
            return default
        try:
            return getter(path, default)
        except Exception:
            return default

    @staticmethod
    def _decision_query(payload: dict[str, Any]) -> DecisionQuery:
        unknown = sorted(set(payload) - _LIST_QUERY_FIELDS)
        if unknown:
            raise ValueError(f"unknown query field: {unknown[0]}")

        offset, limit = InjectionStrategyApiMixin._pagination(payload)
        from_ms = InjectionStrategyApiMixin._optional_integer(payload, "from_ms")
        to_ms = InjectionStrategyApiMixin._optional_integer(payload, "to_ms")
        if from_ms is not None and to_ms is not None and from_ms > to_ms:
            raise ValueError("from_ms must not exceed to_ms")

        routing_mode = InjectionStrategyApiMixin._optional_enum(
            payload,
            "routing_mode",
            {mode.value for mode in RoutingMode},
        )
        resolved_preset = InjectionStrategyApiMixin._optional_enum(
            payload,
            "resolved_preset",
            {name.value for name in PRESETS},
        )
        outcome = InjectionStrategyApiMixin._optional_enum(
            payload,
            "outcome",
            {item.value for item in InjectionOutcome},
        )
        provider_type = InjectionStrategyApiMixin._strategy_optional_text(
            payload, "provider_type"
        )
        primary_reason = InjectionStrategyApiMixin._strategy_optional_text(
            payload, "primary_reason"
        )
        fallback_applied = InjectionStrategyApiMixin._optional_bool(
            payload, "fallback_applied"
        )
        sort_by = str(payload.get("sort_by", "created_at_ms"))
        if sort_by not in {
            "created_at_ms",
            "routing_mode",
            "resolved_preset",
            "provider_type",
            "outcome",
            "actual_payload_chars",
            "decision_ms",
        }:
            raise ValueError("sort_by is invalid")
        sort_order = payload.get("sort_order", "desc")
        if not isinstance(sort_order, str) or sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be asc or desc")
        return DecisionQuery(
            offset=offset,
            limit=limit,
            from_ms=from_ms,
            to_ms=to_ms,
            routing_mode=routing_mode,
            resolved_preset=resolved_preset,
            provider_type=provider_type,
            primary_reason=primary_reason,
            fallback_applied=fallback_applied,
            outcome=outcome,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    @staticmethod
    def _pagination(payload: dict[str, Any]) -> tuple[int, int]:
        offset = InjectionStrategyApiMixin._integer(payload.get("offset", 0), "offset")
        limit = InjectionStrategyApiMixin._integer(payload.get("limit", 50), "limit")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return offset, limit

    @staticmethod
    def _integer(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"{field} must be an integer")
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc

    @staticmethod
    def _optional_integer(payload: dict[str, Any], field: str) -> int | None:
        if field not in payload:
            return None
        return InjectionStrategyApiMixin._integer(payload[field], field)

    @staticmethod
    def _optional_enum(
        payload: dict[str, Any],
        field: str,
        allowed: set[str],
    ) -> str | None:
        if field not in payload:
            return None
        value = payload[field]
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"{field} is invalid")
        return value

    @staticmethod
    def _strategy_optional_text(payload: dict[str, Any], field: str) -> str | None:
        if field not in payload:
            return None
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_bool(payload: dict[str, Any], field: str) -> bool | None:
        if field not in payload:
            return None
        value = payload[field]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise ValueError(f"{field} must be true or false")

    @classmethod
    def _decision_projection(
        cls,
        value: Any,
        fields: tuple[str, ...],
    ) -> dict[str, Any]:
        """Allowlist a decision row and revalidate every value."""
        return _project_decision(value, fields)

    @classmethod
    def _safe_page(cls, page: DecisionPage) -> dict[str, Any]:
        return {
            "items": _project_listed_decisions(page.items, _LIST_ITEM_FIELDS),
            "total": page.total,
            "offset": page.offset,
            "limit": page.limit,
        }

    @classmethod
    def _safe_summary(cls, summary: Any) -> dict[str, Any]:
        if not isinstance(summary, dict):
            return {
                "window": "24h",
                "retrieved_count": 0,
                "injected_count": 0,
                "decision_count": 0,
                "payload_chars_p95": 0,
                "provider_fallback_rate": 0.0,
                "memory_present_count": 0,
                "payload_injected_count": 0,
                "selected_count_total": 0,
                "dropped_count_total": 0,
                "truncated_count_total": 0,
                "effective_budget_chars_avg": 0,
                "budget_utilization_avg": 0.0,
                "budget_utilization_p95": 0.0,
                "preset_distribution": {},
                "cost_trend": [],
                "recent_events": [],
            }
        distribution = summary.get("preset_distribution", {})
        safe_distribution: dict[str, int] = {}
        if isinstance(distribution, dict):
            for name in PRESETS:
                count = _COUNT(distribution.get(name.value))
                if count is not None:
                    safe_distribution[name.value] = count
        window = summary.get("window", "24h")
        safe: dict[str, Any] = {
            "window": window
            if isinstance(window, str) and window in _WINDOWS
            else "24h",
        }
        for field, (validate, default) in _SUMMARY_SCALAR_FIELDS.items():
            value = validate(summary.get(field, default))
            safe[field] = default if value is None else value
        cost_trend = summary.get("cost_trend", [])
        safe["preset_distribution"] = safe_distribution
        safe["cost_trend"] = (
            [
                point
                for point in (_safe_cost_point(item) for item in cost_trend)
                if point is not None
            ]
            if isinstance(cost_trend, (list, tuple))
            else []
        )
        safe["recent_events"] = _project_listed_decisions(
            summary.get("recent_events", []), _RECENT_EVENT_FIELDS
        )
        return safe


__all__ = ["InjectionStrategyApiMixin"]
