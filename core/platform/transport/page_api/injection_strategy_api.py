"""Read-only Page APIs for adaptive memory injection strategies."""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID

from astrbot.api import logger
from quart import request

from ....features.injection.application.presets import PRESETS
from ....features.injection.domain.models import (
    DeliveryMode,
    InjectionOutcome,
    RoutingMode,
)
from ....features.injection.infrastructure.injection_decision_store import (
    DecisionPage,
    DecisionQuery,
    InjectionDecisionStore,
)
from ....utils.injection_adapter import InjectionAdapter
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
_COST_POINT_FIELDS = (
    "bucket_ms",
    "decision_count",
    "payload_chars_p95",
    "provider_fallback_rate",
)


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
        except Exception:
            logger.error(
                "[InjectionStrategyApi] failed to load decision summary",
                exc_info=True,
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
        except Exception:
            logger.error(
                "[InjectionStrategyApi] failed to list injection decisions",
                exc_info=True,
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
        except Exception:
            logger.error(
                "[InjectionStrategyApi] failed to load injection decision detail",
                exc_info=True,
            )
            return error_response("Unable to load injection decision detail")
        if detail is None:
            return error_response("Injection decision not found")
        safe = self._allowlisted(detail, _LIST_ITEM_FIELDS)
        reason_codes = detail.get("reason_codes", [])
        safe["reason_codes"] = (
            [str(code) for code in reason_codes]
            if isinstance(reason_codes, (list, tuple))
            else []
        )
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
        provider_type = InjectionStrategyApiMixin._optional_text(
            payload, "provider_type"
        )
        primary_reason = InjectionStrategyApiMixin._optional_text(
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
    def _optional_text(payload: dict[str, Any], field: str) -> str | None:
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

    @staticmethod
    def _allowlisted(
        value: Any,
        fields: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {field: value[field] for field in fields if field in value}

    @classmethod
    def _safe_page(cls, page: DecisionPage) -> dict[str, Any]:
        return {
            "items": [cls._allowlisted(item, _LIST_ITEM_FIELDS) for item in page.items],
            "total": page.total,
            "offset": page.offset,
            "limit": page.limit,
        }

    @classmethod
    def _safe_summary(cls, summary: Any) -> dict[str, Any]:
        if not isinstance(summary, dict):
            return {
                "window": "24h",
                "decision_count": 0,
                "payload_chars_p95": 0,
                "provider_fallback_rate": 0.0,
                "preset_distribution": {},
                "cost_trend": [],
                "recent_events": [],
            }
        distribution = summary.get("preset_distribution", {})
        safe_distribution = (
            {
                name.value: distribution[name.value]
                for name in PRESETS
                if name.value in distribution
            }
            if isinstance(distribution, dict)
            else {}
        )
        cost_trend = summary.get("cost_trend", [])
        recent_events = summary.get("recent_events", [])
        return {
            "window": summary.get("window", "24h"),
            "decision_count": summary.get("decision_count", 0),
            "payload_chars_p95": summary.get("payload_chars_p95", 0),
            "provider_fallback_rate": summary.get("provider_fallback_rate", 0.0),
            "preset_distribution": safe_distribution,
            "cost_trend": [
                cls._allowlisted(item, _COST_POINT_FIELDS)
                for item in cost_trend
                if isinstance(item, dict)
            ],
            "recent_events": [
                cls._allowlisted(item, _RECENT_EVENT_FIELDS)
                for item in recent_events
                if isinstance(item, dict)
            ],
        }


__all__ = ["InjectionStrategyApiMixin"]
