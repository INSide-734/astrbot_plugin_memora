"""隐私安全的可解释召回追踪 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import logger
from quart import request

from ..injection.models import DeliveryMode, PresetName, RoutingMode
from ..injection.router import InjectionRoutingConfig
from ..models.recall_strategy import RecallStrategy
from ..monitoring import (
    debug_operation,
    is_debug_reporting_enabled,
    report_debug_event,
    report_debug_exception,
)
from ..retrieval.explainable_recall import capture_explainable_recall
from ..retrieval.trace_store import RecallTraceStore


class RecallTraceApiMixin:
    """提供安全 Recall Trace 捕获和详情查询端点。"""

    async def test_recall_with_trace(self):
        """解析当前 JSON 请求并执行一次只读召回追踪。"""
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("invalid_request")
        return await self.test_recall_with_trace_payload(payload)

    async def test_recall_with_trace_payload(self, payload: dict[str, Any]):
        """使用已解析参数捕获并返回安全 trace DTO。"""
        engine = self._get_trace_memory_engine()
        if engine is None:
            return self._error("memory_engine_unavailable")

        query = str(payload.get("query", "") or "").strip()
        if not query:
            return self._error("query_required")

        params = self._build_trace_request_params(payload, query)
        debug_reporting_enabled = is_debug_reporting_enabled()
        with debug_operation():
            try:
                store = await self._get_recall_trace_store()
                trace = await capture_explainable_recall(
                    engine,
                    params,
                    store=store,
                    routing_config=self._trace_routing_config(),
                    debug_reporting_enabled=debug_reporting_enabled,
                )
            except Exception as exc:
                report_debug_exception(
                    "recall_failed",
                    exc,
                    component="page_api",
                    stage="recall",
                    status="failed",
                    reason_code="recall_error",
                )
                logger.error(
                    "[召回追踪接口] 执行召回追踪失败，异常类型=%s",
                    exc.__class__.__name__,
                )
                return self._error("recall_trace_failed")

            candidate_count = len(trace.get("results", []))
            filtered_count = len(trace.get("filtered", []))
            duration_ms = trace.get("total_ms", 0.0)
            report_debug_event(
                "recall_completed",
                component="page_api",
                stage="recall",
                status="completed",
                reason_code="memory_search_completed",
                duration_ms=duration_ms,
                candidate_count=candidate_count,
                filtered_count=filtered_count,
            )
            score_trace_available = bool(
                trace.get("metadata", {}).get("debug_trace_available", False)
            )
            logger.info(
                "[召回追踪接口] 追踪完成：candidates=%d, filtered=%d, "
                "debug_reporting=%s, score_trace=%s",
                candidate_count,
                filtered_count,
                debug_reporting_enabled,
                score_trace_available,
            )
        return self._ok(trace)

    async def get_recall_trace_detail(self):
        """从当前请求参数读取一条安全 trace 详情。"""
        return await self.get_recall_trace_detail_payload(dict(request.args or {}))

    async def get_recall_trace_detail_payload(self, payload: dict[str, Any]):
        """按观测关联码读取安全 trace 详情。"""
        trace_id = str(payload.get("trace_id", "") or "").strip()
        if not trace_id:
            return self._error("trace_id_required")
        try:
            store = await self._get_recall_trace_store()
            trace = await store.get_trace(trace_id)
        except Exception as exc:
            logger.error(
                "[召回追踪接口] 获取详情失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return self._error("recall_trace_detail_failed")
        if trace is None:
            return self._error("recall_trace_not_found")
        return self._ok(trace)

    def _get_trace_memory_engine(self):
        """从插件初始化器读取当前 MemoryEngine。"""
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
        """规范化只供搜索使用且不会写入 trace 的请求参数。"""
        params: dict[str, Any] = {
            "query": query,
            "k": self._coerce_trace_k(payload.get("k", 5)),
            "session_id": self._coerce_optional_text(payload.get("session_id")),
            "persona_id": self._coerce_optional_text(payload.get("persona_id")),
            "user_id": self._coerce_optional_text(payload.get("user_id")),
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
    def _coerce_optional_text(value: Any) -> str | None:
        """把空白可选标识规范化为未提供，避免生成空字符串过滤器。"""
        if value is None or isinstance(value, bool):
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _coerce_trace_k(value: Any) -> int:
        """把召回数量钳制到 1～20。"""
        if isinstance(value, bool):
            return 5
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 5
        return min(20, max(1, parsed))

    @staticmethod
    def _coerce_nonnegative_int(value: Any, default: int) -> int:
        """规范化非负整数参数。"""
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, parsed)

    @staticmethod
    def _coerce_optional_list(value: Any) -> list[Any] | None:
        """把可选集合或逗号文本规范化为列表。"""
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
        """把允许值转换为 RecallStrategy 枚举。"""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, RecallStrategy):
            return value
        try:
            return RecallStrategy(str(value).strip())
        except ValueError:
            return None

    def _trace_routing_config(self) -> InjectionRoutingConfig:
        """从现有配置构建只读路由预览参数。"""
        config_manager = getattr(self.plugin, "config_manager", None)
        get = getattr(config_manager, "get", None)
        if not callable(get):
            return InjectionRoutingConfig()
        try:
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
        """懒加载安全 Recall Trace Store。"""
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
        """返回插件隔离数据库路径；缺少数据目录时只使用内存。"""
        initializer = getattr(self.plugin, "initializer", None)
        data_dir = getattr(initializer, "data_dir", None)
        if data_dir:
            return Path(data_dir) / "recall_traces.db"
        return None


__all__ = ["RecallTraceApiMixin"]
