"""Diagnostics health, event history, and recovery action API."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from astrbot.api import logger
from quart import request

from ..diagnostics import DiagnosticEventStore, HealthScorer
from .response_utils import error_response, ok_response


class DiagnosticsApiMixin:
    """Expose runtime diagnostics built from existing observability summaries."""

    async def get_diagnostics_health(self):
        try:
            return ok_response(self._build_diagnostics_health())
        except Exception as exc:
            logger.error("[诊断接口] 获取健康摘要失败: %s", exc, exc_info=True)
            return error_response(f"获取诊断健康摘要失败: {exc}")

    async def get_diagnostics_events(self):
        return await self.get_diagnostics_events_payload(dict(request.args))

    async def get_diagnostics_event_detail(self):
        return await self.get_diagnostics_event_detail_payload(dict(request.args))

    async def run_diagnostics_action(self):
        try:
            payload = await request.get_json(silent=True)
        except Exception as exc:
            logger.debug("[诊断接口] JSON 请求体无效: %s", exc, exc_info=True)
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return await self.run_diagnostics_action_payload(payload)

    async def get_diagnostics_events_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            store = await self._get_diagnostic_event_store()
            limit = self._diagnostics_positive_int(
                payload.get("limit"),
                default=50,
                maximum=500,
            )
            domain = self._optional_text(payload.get("domain"))
            severity = self._optional_text(payload.get("severity"))
            include_resolved = self._diagnostics_bool(
                payload.get("include_resolved"),
                default=True,
            )
            events = await store.list_events(
                limit=limit,
                domain=domain,
                severity=severity,
                include_resolved=include_resolved,
            )
            return ok_response({"events": events, "total": len(events)})
        except Exception as exc:
            logger.error("[诊断接口] 获取事件列表失败: %s", exc, exc_info=True)
            return error_response(f"获取诊断事件列表失败: {exc}")

    async def get_diagnostics_event_detail_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            return error_response("缺少必填参数 event_id")
        try:
            store = await self._get_diagnostic_event_store()
            event = await store.get_event(event_id)
            if event is None:
                return error_response("诊断事件不存在")
            return ok_response({"event": event})
        except Exception as exc:
            logger.error("[诊断接口] 获取事件详情失败: %s", exc, exc_info=True)
            return error_response(f"获取诊断事件详情失败: {exc}")

    async def run_diagnostics_action_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            action = str(payload.get("action") or "").strip()
            if not action:
                return error_response("缺少必填参数 action")

            if action == "refresh_metrics":
                snapshot = self._build_diagnostics_snapshot()
                return ok_response(
                    {
                        "action": action,
                        "status": "completed",
                        "metrics": snapshot,
                        "health": self._score_diagnostics_snapshot(snapshot),
                    }
                )

            if action == "rebuild_index":
                if not self._diagnostics_bool(payload.get("confirmed"), default=False):
                    return error_response("confirmation_required")
                rebuild_index = getattr(self, "rebuild_index", None)
                if not callable(rebuild_index):
                    return error_response("rebuild_index unavailable")
                return await self._maybe_await(rebuild_index())

            if action == "restart_backfill":
                start_backfill = getattr(self, "start_backfill", None)
                if not callable(start_backfill):
                    return error_response("start_backfill unavailable")
                return await self._maybe_await(start_backfill())

            if action == "clear_completed_events":
                return await self._clear_completed_diagnostic_events()

            return error_response(f"unknown diagnostics action: {action}")
        except Exception as exc:
            logger.error("[诊断接口] 执行诊断动作失败: %s", exc, exc_info=True)
            return error_response(f"执行诊断动作失败: {exc}")

    def _build_diagnostics_snapshot(self) -> dict[str, Any]:
        snapshot = {
            "recall": self._build_recall_summary(),
            "background_tasks": self._build_background_task_summary(),
            "provider": self._build_provider_summary(),
            "index": self._build_index_summary(),
            "write_coordinator": self._build_write_coordinator_summary(),
        }
        build_prometheus = getattr(self, "_build_prometheus_summary", None)
        if callable(build_prometheus):
            snapshot["prometheus"] = build_prometheus()
        return snapshot

    def _build_diagnostics_health(self) -> dict[str, Any]:
        snapshot = self._build_diagnostics_snapshot()
        return self._score_diagnostics_snapshot(snapshot)

    def _score_diagnostics_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        scorer = self._get_diagnostics_health_scorer()
        previous_failures = getattr(self, "_diagnostics_previous_write_failures_total", None)
        health = scorer.score(
            snapshot,
            previous_write_failures_total=previous_failures,
        )
        current_failures = self._write_failures_total(snapshot)
        if current_failures is not None:
            self._diagnostics_previous_write_failures_total = current_failures
        return health

    def _get_diagnostics_health_scorer(self) -> HealthScorer:
        scorer = getattr(self, "_diagnostics_health_scorer", None)
        if scorer is None:
            scorer = HealthScorer()
            self._diagnostics_health_scorer = scorer
        return scorer

    async def _get_diagnostic_event_store(self) -> DiagnosticEventStore:
        store = getattr(self, "_diagnostic_event_store", None)
        if store is None:
            store = DiagnosticEventStore(self._diagnostics_event_db_path())
            await store.initialize()
            self._diagnostic_event_store = store
        return store

    def _diagnostics_event_db_path(self) -> Path:
        plugin = getattr(self, "plugin", None)
        initializer = getattr(plugin, "initializer", None)
        for owner in (initializer, plugin):
            if owner is None:
                continue
            data_dir = getattr(owner, "data_dir", None)
            if data_dir:
                return Path(data_dir) / "diagnostics_events.db"
        raise RuntimeError("diagnostics event store requires plugin initializer data_dir")

    async def _clear_completed_diagnostic_events(self) -> dict[str, Any]:
        store = await self._get_diagnostic_event_store()
        events = await store.list_events(limit=500, include_resolved=True)
        resolved_count = sum(1 for item in events if item.get("resolved_at"))
        return ok_response(
            {
                "action": "clear_completed_events",
                "status": "noop",
                "cleared": 0,
                "resolved": resolved_count,
            }
        )

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _write_failures_total(snapshot: dict[str, Any]) -> int | None:
        write = snapshot.get("write_coordinator")
        if not isinstance(write, dict):
            return None
        value = write.get("failures_total")
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _diagnostics_positive_int(
        value: Any,
        *,
        default: int,
        maximum: int,
    ) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if parsed <= 0:
            parsed = default
        return min(parsed, maximum)

    @staticmethod
    def _diagnostics_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            return default
        return bool(value)

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


__all__ = ["DiagnosticsApiMixin"]
