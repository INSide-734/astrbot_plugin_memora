"""Explainable recall trace API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quart import request

from astrbot.api import logger
from ..injection.models import DeliveryMode, PresetName, RoutingMode
from ..injection.router import InjectionRoutingConfig

from ..models.recall_strategy import RecallStrategy
from ..retrieval.explainable_recall import capture_explainable_recall
from ..retrieval.trace_store import RecallTraceStore


class RecallTraceApiMixin:
    """Mixin for recall trace capture and lookup endpoints."""

    async def test_recall_with_trace(self):
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须为 JSON 对象")
        return await self.test_recall_with_trace_payload(payload)

    async def test_recall_with_trace_payload(self, payload: dict[str, Any]):
        engine = self._get_trace_memory_engine()
        if engine is None:
            return self._error("MemoryEngine unavailable")

        query = str(payload.get("query", "") or "").strip()
        if not query:
            return self._error("查询内容不能为空")

        params = self._build_trace_request_params(payload, query)
        try:
            store = await self._get_recall_trace_store()
            trace = await capture_explainable_recall(
                engine,
                params,
                store=store,
                routing_config=self._trace_routing_config(),
            )
        except Exception as exc:
            logger.error("[RecallTraceApi] traced recall failed: %s", exc, exc_info=True)
            return self._error(str(exc))
        return self._ok(trace)

    async def get_recall_trace_detail(self):
        return await self.get_recall_trace_detail_payload(dict(request.args or {}))

    async def get_recall_trace_detail_payload(self, payload: dict[str, Any]):
        trace_id = str(payload.get("trace_id", "") or "").strip()
        if not trace_id:
            return self._error("trace_id is required")
        try:
            store = await self._get_recall_trace_store()
            trace = await store.get_trace(trace_id)
        except Exception as exc:
            logger.error("[RecallTraceApi] get trace detail failed: %s", exc, exc_info=True)
            return self._error(str(exc))
        if trace is None:
            return self._error("Recall trace not found")
        return self._ok(trace)

    def _get_trace_memory_engine(self):
        try:
            initializer = getattr(self.plugin, "initializer", None)
            return getattr(initializer, "memory_engine", None)
        except Exception:
            return None

    def _build_trace_request_params(
        self,
        payload: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "query": query,
            "k": self._coerce_trace_k(payload.get("k", 5)),
            "session_id": payload.get("session_id"),
            "persona_id": payload.get("persona_id"),
            "user_id": payload.get("user_id"),
            "chat_type": str(payload.get("chat_type") or "private"),
            "memory_types": self._coerce_optional_list(payload.get("memory_types")),
            "emotion_context": self._coerce_optional_list(
                payload.get("emotion_context")
            ),
            "recall_type": payload.get("recall_type") or "manual_trace",
            "chain_depth": self._coerce_nonnegative_int(
                payload.get("chain_depth", 0), 0
            ),
            "query_intent": payload.get("query_intent"),
            "recall_strategy": self._coerce_recall_strategy(
                payload.get("recall_strategy")
            ),
        }
        return params

    @staticmethod
    def _coerce_trace_k(value: Any) -> int:
        if isinstance(value, bool):
            return 5
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 5
        return min(20, max(1, parsed))

    @staticmethod
    def _coerce_nonnegative_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, parsed)

    @staticmethod
    def _coerce_optional_list(value: Any) -> list[Any] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, tuple | set):
            return list(value)
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return None

    @staticmethod
    def _coerce_recall_strategy(value: Any) -> RecallStrategy | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, RecallStrategy):
            return value
        try:
            return RecallStrategy(str(value).strip())
        except ValueError:
            return None

    def _trace_routing_config(self) -> InjectionRoutingConfig:
        config_manager = getattr(self.plugin, "config_manager", None)
        get = getattr(config_manager, "get", None)
        if not callable(get):
            return InjectionRoutingConfig()
        try:
            return InjectionRoutingConfig(
                mode=RoutingMode(
                    get("recall_engine.injection_routing_mode", "manual")
                ),
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
                memory_max_chars=int(
                    get("recall_engine.injection_memory_max_chars", 0)
                ),
                metadata_max_chars=int(
                    get("recall_engine.injection_metadata_max_chars", 0)
                ),
                include_key_facts=bool(
                    get("recall_engine.injection_include_key_facts", True)
                ),
                include_topics=bool(
                    get("recall_engine.injection_include_topics", True)
                ),
                include_participants=bool(
                    get("recall_engine.injection_include_participants", False)
                ),
                compact_header=bool(
                    get("recall_engine.injection_compact_header", True)
                ),
                invalid_config_fallback=bool(
                    getattr(config_manager, "runtime_injection_fallback", False)
                ),
            )
        except (TypeError, ValueError):
            return InjectionRoutingConfig(invalid_config_fallback=True)

    async def _get_recall_trace_store(self) -> RecallTraceStore:
        store = getattr(self, "_recall_trace_store", None)
        if store is not None:
            return store

        db_path = self._recall_trace_db_path()
        store = (
            RecallTraceStore(db_path=db_path)
            if db_path is not None
            else RecallTraceStore()
        )
        await store.initialize()
        self._recall_trace_store = store
        return store

    def _recall_trace_db_path(self) -> Path | None:
        initializer = getattr(self.plugin, "initializer", None)
        data_dir = getattr(initializer, "data_dir", None)
        if data_dir:
            return Path(data_dir) / "recall_traces.db"
        return None


__all__ = ["RecallTraceApiMixin"]
