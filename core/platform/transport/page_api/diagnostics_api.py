"""诊断健康、事件历史和有限恢复动作 API。"""

import inspect
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from astrbot.api import logger
from quart import request

from ....features.diagnostics import DiagnosticEventStore, HealthScorer
from ....features.diagnostics.infrastructure.event_store import (
    _DOMAINS as _EVENT_DOMAINS,
)
from ....features.diagnostics.infrastructure.event_store import (
    _SEVERITIES as _EVENT_SEVERITIES,
)
from .response_utils import error_response, ok_response

# 委托维护接口自带的稳定错误码；其余错误一律收敛为 `<action>_failed`。
_DELEGATE_ERROR_CODES = frozenset(
    {
        "component_lookup_error",
        "maintenance_blocked",
        "maintenance_guard_failed",
        "plugin_not_ready",
        "plugin_readiness_error",
    }
)
_REBUILD_NUMBER_FIELDS = frozenset(
    {
        "bm25_errors",
        "bm25_processed",
        "duration_seconds",
        "errors",
        "failure_ratio",
        "processed",
        "total",
        "vector_errors",
        "vector_processed",
    }
)
_REBUILD_FLAG_FIELDS = frozenset({"partial", "switched"})
_VECTOR_MODES = frozenset({"full", "repair", "skip"})
_MAX_NUMBER = 10**12

_DIAGNOSTICS_METRIC_DOMAINS = (
    "recall",
    "provider",
    "index",
    "write_coordinator",
    "background_tasks",
    "anomaly",
    "learning",
    "prometheus",
    "summary_tasks",
)
_METRIC_STATUSES = frozenset(
    {
        "active",
        "available",
        "blocked",
        "cancelled",
        "completed",
        "degraded",
        "error",
        "failed",
        "healthy",
        "info",
        "no_samples",
        "queued",
        "ready",
        "running",
        "unknown",
        "unavailable",
        "waiting",
    }
)
_RESTORE_STATUSES = frozenset(
    {
        "staged",
        "reload_scheduled",
        "applying",
        "validating",
        "succeeded",
        "failed_before_apply",
        "rollback_pending",
        "rolling_back",
        "rolled_back",
        "cancelled",
    }
)


def _safe_diagnostics_count(value: Any) -> int:
    """Return a finite, non-negative, bounded diagnostic count."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return min(max(value, 0), _MAX_NUMBER)
    if isinstance(value, float) and math.isfinite(value):
        return min(max(int(value), 0), _MAX_NUMBER)
    return 0


def _safe_diagnostics_status(value: Any, default: str = "unknown") -> str:
    """Keep only the diagnostic status closed set."""
    return value if isinstance(value, str) and value in _METRIC_STATUSES else default


def _safe_diagnostics_metrics(snapshot: Any) -> dict[str, dict[str, Any]]:
    """Project a runtime snapshot to bounded status/count/boolean summaries."""
    data = snapshot if isinstance(snapshot, Mapping) else {}

    def section(name: str) -> Mapping[str, Any]:
        value = data.get(name)
        return value if isinstance(value, Mapping) else {}

    recall = section("recall")
    recall_count = _safe_diagnostics_count(recall.get("sample_count"))
    recall_status = _safe_diagnostics_status(recall.get("status"))
    if "status" not in recall:
        recall_status = "available" if recall_count else "unknown"

    provider = section("provider")
    provider_result: dict[str, Any] = {
        "status": _safe_diagnostics_status(provider.get("status")),
        "attempts": _safe_diagnostics_count(provider.get("attempts")),
        "max_attempts": _safe_diagnostics_count(provider.get("max_attempts")),
        "providers_ready": provider.get("providers_ready") is True,
        "retry_active": provider.get("retry_active") is True,
        "is_initialized": provider.get("is_initialized") is True,
        "is_failed": provider.get("is_failed") is True,
    }

    index = section("index")
    index_status = _safe_diagnostics_status(index.get("status"))
    if "status" not in index:
        if index.get("last_check_needs_rebuild") is True:
            index_status = "degraded"
        elif index.get("validator_available") is True:
            index_status = "available"
    index_result: dict[str, Any] = {
        "status": index_status,
        "validator_available": index.get("validator_available") is True,
        "last_check_consistent": index.get("last_check_consistent") is True,
        "last_check_needs_rebuild": index.get("last_check_needs_rebuild") is True,
        "last_rebuild_success": index.get("last_rebuild_success") is True,
        "last_rebuild_total": _safe_diagnostics_count(index.get("last_rebuild_total")),
        "last_rebuild_errors": _safe_diagnostics_count(
            index.get("last_rebuild_errors")
        ),
    }

    write = section("write_coordinator")
    write_status = _safe_diagnostics_status(write.get("status"))
    if "status" not in write:
        write_status = (
            "unknown" if not write or write.get("last_error") else "available"
        )
    write_result: dict[str, Any] = {
        "status": write_status,
        "operations_total": _safe_diagnostics_count(write.get("operations_total")),
        "lock_retries_total": _safe_diagnostics_count(write.get("lock_retries_total")),
        "failures_total": _safe_diagnostics_count(write.get("failures_total")),
        "retry_exhausted_total": _safe_diagnostics_count(
            write.get("retry_exhausted_total")
        ),
        "fatal_failures_total": _safe_diagnostics_count(
            write.get("fatal_failures_total")
        ),
        "non_retryable_failures_total": _safe_diagnostics_count(
            write.get("non_retryable_failures_total")
        ),
    }

    tasks = section("background_tasks")
    task_failed = _safe_diagnostics_count(tasks.get("failed"))
    task_active = _safe_diagnostics_count(tasks.get("active"))
    task_status = _safe_diagnostics_status(tasks.get("status"))
    if "status" not in tasks:
        task_status = (
            "unknown"
            if not tasks
            else "failed"
            if task_failed
            else "active"
            if task_active
            else "available"
        )
    tasks_result: dict[str, Any] = {
        "status": task_status,
        "tracked": _safe_diagnostics_count(tasks.get("tracked")),
        "active": task_active,
        "completed": _safe_diagnostics_count(tasks.get("completed")),
        "failed": task_failed,
        "cancelled": _safe_diagnostics_count(tasks.get("cancelled")),
    }

    anomaly = section("anomaly")
    anomaly_alerts = _safe_diagnostics_count(anomaly.get("alerts"))
    anomaly_status = _safe_diagnostics_status(anomaly.get("status"))
    if "status" not in anomaly:
        anomaly_status = (
            "degraded"
            if anomaly_alerts
            else "available"
            if anomaly.get("available") is True
            else "unknown"
        )
    anomaly_result: dict[str, Any] = {
        "status": anomaly_status,
        "available": anomaly.get("available") is True,
        "alerts": anomaly_alerts,
        "window_size": _safe_diagnostics_count(anomaly.get("window_size")),
        "latest_count": _safe_diagnostics_count(anomaly.get("latest_count")),
    }

    learning = section("learning")
    learning_status = _safe_diagnostics_status(learning.get("status"))
    if "status" not in learning:
        learning_status = (
            "available" if learning.get("available") is True else "unknown"
        )
    learning_result: dict[str, Any] = {
        "status": learning_status,
        "available": learning.get("available") is True,
        "candidate_count": _safe_diagnostics_count(learning.get("candidate_count")),
        "ready_count": _safe_diagnostics_count(learning.get("ready_count")),
        "rejected_count": _safe_diagnostics_count(learning.get("rejected_count")),
        "published_count": _safe_diagnostics_count(learning.get("published_count")),
    }

    prometheus = section("prometheus")
    prometheus_available = prometheus.get("available") is True
    prometheus_result: dict[str, Any] = {
        "status": (
            _safe_diagnostics_status(prometheus.get("status"))
            if "status" in prometheus
            else "available"
            if prometheus_available
            else "unavailable"
            if "available" in prometheus
            else "unknown"
        ),
        "available": prometheus_available,
        "collector_count": _safe_diagnostics_count(prometheus.get("collector_count")),
    }

    summary = section("summary_tasks")
    summary_failed = _safe_diagnostics_count(summary.get("failed"))
    summary_blocked = _safe_diagnostics_count(summary.get("blocked"))
    summary_unknown = _safe_diagnostics_count(summary.get("unknown"))
    summary_status = _safe_diagnostics_status(summary.get("status"))
    if "status" not in summary:
        summary_status = (
            "failed"
            if summary_failed
            else "blocked"
            if summary_blocked
            else "unknown"
            if summary_unknown or not summary
            else "available"
        )
    summary_result: dict[str, Any] = {
        "status": summary_status,
        "queued": _safe_diagnostics_count(summary.get("queued")),
        "running": _safe_diagnostics_count(summary.get("running")),
        "failed": summary_failed,
        "blocked": summary_blocked,
        "unknown": summary_unknown,
        "cancelled": _safe_diagnostics_count(summary.get("cancelled")),
        "abandoned": _safe_diagnostics_count(summary.get("abandoned")),
        "candidate_total": _safe_diagnostics_count(summary.get("candidate_total")),
        "canonical_total": _safe_diagnostics_count(summary.get("canonical_total")),
        "quarantine_total": _safe_diagnostics_count(summary.get("quarantine_total")),
        "failed_candidate_total": _safe_diagnostics_count(
            summary.get("failed_candidate_total")
        ),
        "skipped_idempotent_total": _safe_diagnostics_count(
            summary.get("skipped_idempotent_total")
        ),
    }

    projected = {
        "recall": {"status": recall_status, "sample_count": recall_count},
        "provider": provider_result,
        "index": index_result,
        "write_coordinator": write_result,
        "background_tasks": tasks_result,
        "anomaly": anomaly_result,
        "learning": learning_result,
        "prometheus": prometheus_result,
        "summary_tasks": summary_result,
    }
    return {name: projected[name] for name in _DIAGNOSTICS_METRIC_DOMAINS}


class DiagnosticsApiMixin:
    """基于现有安全观测摘要提供运行时诊断能力。"""

    if TYPE_CHECKING:

        def _build_recall_summary(self) -> dict[str, Any]: ...
        def _build_background_task_summary(self) -> dict[str, Any]: ...
        def _build_provider_summary(self) -> dict[str, Any]: ...
        def _build_index_summary(self) -> dict[str, Any]: ...
        def _build_write_coordinator_summary(self) -> dict[str, Any]: ...
        def _build_anomaly_summary(self) -> dict[str, Any]: ...
        def _build_learning_summary(self) -> dict[str, Any]: ...
        def _build_prometheus_summary(self) -> dict[str, Any]: ...
        async def _build_summary_task_summary(self) -> dict[str, object] | None: ...

    async def get_diagnostics_health(self):
        """返回诊断健康评分；失败时只暴露稳定错误码。"""
        try:
            return ok_response(await self._build_diagnostics_health())
        except Exception as exc:
            logger.error(
                "[诊断接口] operation=get_diagnostics_health exception_type=%s",
                exc.__class__.__name__,
            )
            return error_response("diagnostics_health_failed")

    async def get_diagnostics_events(self):
        """从当前请求参数读取诊断事件列表。"""
        return await self.get_diagnostics_events_payload(dict(request.args))

    async def get_diagnostics_event_detail(self):
        """从当前请求参数读取单条诊断事件。"""
        return await self.get_diagnostics_event_detail_payload(dict(request.args))

    async def run_diagnostics_action(self):
        """解析 JSON 请求体并执行允许列表中的诊断动作。"""
        try:
            payload = await request.get_json(silent=True)
        except Exception as exc:
            logger.debug(
                "[诊断接口] operation=parse_diagnostics_json exception_type=%s",
                exc.__class__.__name__,
            )
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return await self.run_diagnostics_action_payload(payload)

    async def get_diagnostics_events_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """按安全筛选条件返回脱敏后的诊断事件列表。"""
        try:
            store = self._get_diagnostic_event_store()
            limit = self._diagnostics_positive_int(
                payload.get("limit"),
                default=50,
                maximum=500,
            )
            domain = self._diagnostics_filter(payload.get("domain"), _EVENT_DOMAINS)
            severity = self._diagnostics_filter(
                payload.get("severity"), _EVENT_SEVERITIES
            )
            if domain == "" or severity == "":
                return error_response("invalid_diagnostics_filter")
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
            logger.error(
                "[诊断接口] operation=list_diagnostics_events exception_type=%s",
                exc.__class__.__name__,
            )
            return error_response("diagnostics_events_failed")

    async def get_diagnostics_event_detail_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """按诊断关联码返回脱敏后的事件详情。"""
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            return error_response("event_id_required")
        try:
            store = self._get_diagnostic_event_store()
            event = await store.get_event(event_id)
            if event is None:
                return error_response("diagnostics_event_not_found")
            return ok_response({"event": event})
        except Exception as exc:
            logger.error(
                "[诊断接口] operation=get_diagnostics_event exception_type=%s",
                exc.__class__.__name__,
            )
            return error_response("diagnostics_event_failed")

    async def run_diagnostics_action_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """执行固定 allowlist 中的只读或显式确认诊断动作。"""
        try:
            action = str(payload.get("action") or "").strip()
            if not action:
                return error_response("action_required")

            if action == "refresh_metrics":
                snapshot = await self._build_diagnostics_snapshot_with_summary()
                return ok_response(
                    {
                        "action": action,
                        "status": "completed",
                        "health": self._score_diagnostics_snapshot(snapshot),
                        "metrics": _safe_diagnostics_metrics(snapshot),
                    }
                )

            if action == "rebuild_index":
                if not self._diagnostics_bool(payload.get("confirmed"), default=False):
                    return error_response("confirmation_required")
                rebuild_index = getattr(self, "rebuild_index", None)
                if not callable(rebuild_index):
                    return error_response("rebuild_index_unavailable")
                return self._project_delegated_diagnostics_action(
                    action, await self._maybe_await(rebuild_index())
                )

            if action == "restart_backfill":
                start_backfill = getattr(self, "start_backfill", None)
                if not callable(start_backfill):
                    return error_response("start_backfill_unavailable")
                return self._project_delegated_diagnostics_action(
                    action, await self._maybe_await(start_backfill())
                )

            if action == "clear_completed_events":
                return await self._clear_completed_diagnostic_events()

            return error_response("unknown_diagnostics_action")
        except Exception as exc:
            logger.error(
                "[诊断接口] operation=run_diagnostics_action exception_type=%s",
                exc.__class__.__name__,
            )
            return error_response("diagnostics_action_failed")

    def _build_diagnostics_snapshot(self) -> dict[str, Any]:
        """从现有组件构造固定领域的诊断标量快照。"""
        snapshot: dict[str, Any] = {
            "recall": self._build_recall_summary(),
            "background_tasks": self._build_background_task_summary(),
            "provider": self._build_provider_summary(),
            "index": self._build_index_summary(),
            "write_coordinator": self._build_write_coordinator_summary(),
            "anomaly": self._build_anomaly_summary(),
            "learning": self._build_learning_summary(),
        }
        build_quality = getattr(self, "_build_quality_summary", None)
        if callable(build_quality):
            snapshot["quality"] = cast(dict[str, Any], build_quality())
        snapshot["restore"] = self._build_restore_summary()
        build_prometheus = getattr(self, "_build_prometheus_summary", None)
        if callable(build_prometheus):
            snapshot["prometheus"] = cast(dict[str, Any], build_prometheus())
        return snapshot

    async def _build_diagnostics_snapshot_with_summary(self) -> dict[str, Any]:
        """为 Page 诊断补充可选的安全总结任务投影。"""
        snapshot = self._build_diagnostics_snapshot()
        build_summary = getattr(self, "_build_summary_task_summary", None)
        if not callable(build_summary):
            return snapshot

        summary_tasks = await self._maybe_await(build_summary())
        if summary_tasks is not None:
            snapshot["summary_tasks"] = summary_tasks
        return snapshot

    async def _build_diagnostics_health(self) -> dict[str, Any]:
        """对包含可选总结任务投影的当前诊断快照执行健康评分。"""
        snapshot = await self._build_diagnostics_snapshot_with_summary()
        return self._score_diagnostics_snapshot(snapshot)

    def _build_restore_summary(self) -> dict[str, Any] | None:
        """Read only the backup maintenance state needed by health scoring."""
        manager = getattr(getattr(self, "plugin", None), "_backup_manager", None)
        getter = getattr(manager, "get_maintenance_state", None)
        if not callable(getter):
            return None
        try:
            state = getter()
        except Exception as exc:
            logger.warning(
                "[诊断接口] operation=read_restore_maintenance_state exception_type=%s",
                exc.__class__.__name__,
            )
            return None
        if not isinstance(state, Mapping):
            return None
        status = state.get("status")
        safe_status = (
            status if isinstance(status, str) and status in _RESTORE_STATUSES else None
        )
        return {
            "blocked": state.get("blocked") is True,
            "status": safe_status,
        }

    def _score_diagnostics_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """评分并推进写失败累计值基线。"""
        scorer = self._get_diagnostics_health_scorer()
        previous_failures = getattr(
            self, "_diagnostics_previous_write_failures_total", None
        )
        # A missing scheduler is a real unavailable signal. Keep the key in the
        # health input so the scorer can emit an explicit unknown projection,
        # rather than allowing the UI to infer a healthy default from absence.
        safe_snapshot = dict(snapshot)
        safe_snapshot.setdefault("summary_tasks", None)
        health = scorer.score(
            safe_snapshot,
            previous_write_failures_total=previous_failures,
        )
        current_failures = self._write_failures_total(safe_snapshot)
        if current_failures is not None:
            self._diagnostics_previous_write_failures_total = current_failures
        return health

    def _get_diagnostics_health_scorer(self) -> HealthScorer:
        """懒加载并复用健康评分器。"""
        scorer = getattr(self, "_diagnostics_health_scorer", None)
        if scorer is None:
            scorer = HealthScorer()
            self._diagnostics_health_scorer = scorer
        return scorer

    def _get_diagnostic_event_store(self) -> DiagnosticEventStore:
        """返回组合根发布的唯一诊断事件 Store。

        Returns:
            初始化阶段由 `ComponentFactory` 发布、`PluginInitializer` 持有的
            共享 Store 实例。

        Raises:
            RuntimeError: 组合根尚未发布 Store；请求路径不得自行建库。
        """

        plugin = getattr(self, "plugin", None)
        initializer = getattr(plugin, "initializer", None)
        store = getattr(initializer, "diagnostic_event_store", None)
        if store is None:
            raise RuntimeError("diagnostic event store is not published")
        return cast(DiagnosticEventStore, store)

    async def _clear_completed_diagnostic_events(self) -> dict[str, Any]:
        """报告已解决事件数量；当前保持无删除的 noop 语义。"""
        store = self._get_diagnostic_event_store()
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

    @classmethod
    def _project_delegated_diagnostics_action(
        cls,
        action: str,
        response: Any,
    ) -> dict[str, Any]:
        """把维护委托响应收敛为稳定错误码与有界聚合字段。

        委托接口会在 message/result 中携带异常正文、job/failed ID；诊断动作
        只公开动作名、状态、闭集错误码和有限非负计数，未知结果不视为成功。
        """
        failure_code = f"{action}_failed"
        if not isinstance(response, Mapping) or response.get("status") != "ok":
            code = response.get("code") if isinstance(response, Mapping) else None
            if isinstance(code, str) and code in _DELEGATE_ERROR_CODES:
                return error_response(code)
            return error_response(failure_code)
        if action == "restart_backfill":
            return ok_response({"action": action, "status": "started"})

        data = response.get("data")
        result = data.get("result") if isinstance(data, Mapping) else None
        if not isinstance(result, Mapping):
            result = {}
        safe_result: dict[str, Any] = {}
        for key, value in result.items():
            if key in _REBUILD_NUMBER_FIELDS and cls._diagnostics_safe_number(value):
                safe_result[key] = value
            elif key in _REBUILD_FLAG_FIELDS and isinstance(value, bool):
                safe_result[key] = value
            elif key == "vector_mode" and value in _VECTOR_MODES:
                safe_result[key] = value
        if result.get("success") is True:
            return ok_response(
                {"action": action, "status": "completed", "result": safe_result}
            )
        return error_response(
            failure_code,
            data={"action": action, "status": "failed", "result": safe_result},
        )

    @staticmethod
    def _diagnostics_safe_number(value: Any) -> bool:
        """只接受有限、非负、有界的真实数值。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and 0 <= value <= _MAX_NUMBER

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        """兼容同步返回值和 awaitable 委托结果。"""
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _write_failures_total(snapshot: dict[str, Any]) -> int | None:
        """从快照读取合法写失败累计值。"""
        write = snapshot.get("write_coordinator")
        if not isinstance(write, dict):
            return None
        value = write.get("failures_total")
        if value is None or isinstance(value, bool):
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
        """把正整数参数钳制到指定上限。"""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if parsed <= 0:
            parsed = default
        return min(parsed, maximum)

    @staticmethod
    def _diagnostics_bool(value: Any, *, default: bool) -> bool:
        """兼容布尔值和常见字符串表示。"""
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
    def _diagnostics_filter(value: Any, choices: frozenset[str]) -> str | None:
        """把可选筛选值规范化为 Store 闭集；缺省返回 None，非法返回空串。"""
        text = str(value or "").strip().lower()
        if not text:
            return None
        return text if text in choices else ""


__all__ = ["DiagnosticsApiMixin"]
