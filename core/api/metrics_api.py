"""Runtime observability summary API for the dashboard."""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger
from quart import request

from .response_utils import error_response, ok_response


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MetricsApiMixin:
    """Expose a compact, JSON-serializable runtime observability snapshot."""

    async def get_metrics_summary(self):
        """Return recall, quality, background task, and metric registry summary."""
        try:
            return ok_response(
                {
                    "recall": self._build_recall_summary(),
                    "quality": self._build_quality_summary(),
                    "background_tasks": self._build_background_task_summary(),
                    "provider": self._build_provider_summary(),
                    "index": self._build_index_summary(),
                    "write_coordinator": self._build_write_coordinator_summary(),
                    "prometheus": self._build_prometheus_summary(),
                }
            )
        except Exception as exc:
            logger.error("[指标接口] 获取运行观测摘要失败: %s", exc, exc_info=True)
            return error_response(f"获取运行观测摘要失败: {exc}")

    def _build_recall_summary(self) -> dict[str, Any]:
        tracker = self._get_existing_perf_tracker()
        if tracker is None:
            return {
                "sample_count": 0,
                "avg_total_ms": 0.0,
                "p50_total_ms": None,
                "p95_total_ms": None,
                "recent": [],
            }

        try:
            data = tracker.get_perf_data(recent_limit=10)
        except TypeError:
            data = tracker.get_perf_data()
        except Exception as exc:
            logger.warning("[指标接口] 读取 PerfTracker 失败: %s", exc, exc_info=True)
            return {
                "sample_count": 0,
                "avg_total_ms": 0.0,
                "p50_total_ms": None,
                "p95_total_ms": None,
                "recent": [],
                "error": exc.__class__.__name__,
            }

        if not isinstance(data, dict):
            data = {}
        sample_count = _safe_int(
            data.get(
                "count_total_ms", len(tracker) if hasattr(tracker, "__len__") else 0
            )
        )
        return {
            "sample_count": sample_count,
            "avg_total_ms": _safe_float(data.get("avg_total_ms")) or 0.0,
            "avg_bm25_ms": _safe_float(data.get("avg_bm25_ms")) or 0.0,
            "avg_vector_ms": _safe_float(data.get("avg_vector_ms")) or 0.0,
            "avg_graph_ms": _safe_float(data.get("avg_graph_ms")) or 0.0,
            "avg_rerank_ms": _safe_float(data.get("avg_rerank_ms")) or 0.0,
            "p50_total_ms": self._get_percentile(tracker, "total_ms", 50),
            "p95_total_ms": self._get_percentile(tracker, "total_ms", 95),
            "recent": self._json_safe_recent(data.get("recent", [])),
        }

    def _build_quality_summary(self) -> dict[str, Any]:
        scorer = self._get_existing_quality_scorer()
        if scorer is None or not hasattr(scorer, "get_stats"):
            return {"status": "unavailable", "total_scored": 0}
        try:
            stats = scorer.get_stats()
        except Exception as exc:
            logger.warning("[指标接口] 读取质量统计失败: %s", exc, exc_info=True)
            return {
                "status": "error",
                "total_scored": 0,
                "error": exc.__class__.__name__,
            }
        if not isinstance(stats, dict):
            return {"status": "unavailable", "total_scored": 0}
        return {
            "status": stats.get("status", "ok"),
            "total_scored": _safe_int(stats.get("total_scored")),
            "avg_overall": _safe_float(stats.get("avg_overall")),
            "paused": bool(stats.get("paused", False)),
            "alert_counts": dict(stats.get("alert_counts", {}) or {}),
        }

    def _build_background_task_summary(self) -> dict[str, Any]:
        tasks = getattr(getattr(self, "plugin", None), "_pending_tasks", None) or set()
        try:
            task_list = list(tasks)
        except TypeError:
            task_list = []
        completed = 0
        active = 0
        failed = 0
        cancelled = 0
        failed_tasks: list[dict[str, str]] = []
        for task in task_list:
            try:
                is_done = bool(task.done())
            except Exception:
                is_done = False
            if not is_done:
                active += 1
                continue

            try:
                is_cancelled = bool(task.cancelled())
            except Exception:
                is_cancelled = False
            if is_cancelled:
                cancelled += 1
                continue

            task_exc = None
            try:
                task_exc = task.exception()
            except Exception:
                task_exc = None
            if task_exc is not None:
                failed += 1
                failed_tasks.append(
                    {
                        "name": self._task_name(task),
                        "error": task_exc.__class__.__name__,
                        "message": str(task_exc),
                        "suggestion": self._failure_recovery_suggestion(
                            self._task_name(task),
                            task_exc.__class__.__name__,
                        ),
                    }
                )
            else:
                completed += 1
        return {
            "tracked": len(task_list),
            "active": active,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "failed_tasks": failed_tasks[:10],
            "schedulers": self._build_scheduler_summary(),
        }

    def _build_provider_summary(self) -> dict[str, Any]:
        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        if initializer is None:
            return {
                "status": "unknown",
                "providers_ready": False,
                "attempts": 0,
                "max_attempts": 0,
                "retry_active": False,
                "missing_provider": [],
                "is_initialized": False,
                "is_failed": False,
                "error_message": None,
                "components_ready": {},
            }

        snapshot: dict[str, Any] = {}
        get_snapshot = getattr(initializer, "get_readiness_snapshot", None)
        if callable(get_snapshot):
            try:
                raw_snapshot = get_snapshot()
                if isinstance(raw_snapshot, dict):
                    snapshot = raw_snapshot
            except Exception as exc:
                logger.warning(
                    "[指标接口] 读取 readiness snapshot 失败: %s", exc, exc_info=True
                )

        waiter = getattr(initializer, "_provider_waiter", None)
        attempts = _safe_int(
            snapshot.get(
                "provider_attempts",
                getattr(waiter, "attempts", 0) if waiter is not None else 0,
            )
        )
        max_attempts = _safe_int(getattr(waiter, "_max_attempts", 0), 0)
        providers_ready = bool(getattr(waiter, "providers_ready", False))
        retry_task = getattr(waiter, "_retry_task", None)
        retry_active = self._task_active(retry_task)
        is_initialized = bool(
            snapshot.get(
                "is_initialized", getattr(initializer, "is_initialized", False)
            )
        )
        is_failed = bool(
            snapshot.get("is_failed", getattr(initializer, "is_failed", False))
        )
        missing_provider = snapshot.get("missing_provider", [])
        if not isinstance(missing_provider, list):
            missing_provider = []
        components_ready = snapshot.get("components_ready", {})
        if not isinstance(components_ready, dict):
            components_ready = {}

        if is_failed:
            status = "failed"
        elif is_initialized or providers_ready:
            status = "ready"
        elif attempts > 0 or missing_provider or retry_active:
            status = "waiting"
        else:
            status = "unknown"

        return {
            "status": status,
            "providers_ready": providers_ready,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "retry_active": retry_active,
            "missing_provider": [str(item) for item in missing_provider],
            "is_initialized": is_initialized,
            "is_failed": is_failed,
            "error_message": snapshot.get(
                "error_message", getattr(initializer, "error_message", None)
            ),
            "components_ready": dict(components_ready),
        }

    def _build_index_summary(self) -> dict[str, Any]:
        plugin = getattr(self, "plugin", None)
        initializer = getattr(plugin, "initializer", None)
        validator = getattr(initializer, "index_validator", None)
        state = (
            getattr(plugin, "_index_observability", None)
            or getattr(initializer, "_index_observability", None)
            or getattr(validator, "_observability", None)
            or {}
        )
        if not isinstance(state, dict):
            state = {}
        summary = {"validator_available": validator is not None}
        for key in (
            "last_rebuild_success",
            "last_rebuild_duration_seconds",
            "last_rebuild_errors",
            "last_rebuild_total",
            "last_rebuild_message",
            "last_check_consistent",
            "last_check_needs_rebuild",
            "last_check_reason",
            "last_check_documents_count",
            "last_check_bm25_count",
            "last_check_vector_count",
        ):
            if key in state:
                summary[key] = state[key]
        return summary

    def _build_scheduler_summary(self) -> dict[str, Any]:
        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        if initializer is None:
            return {}

        summary: dict[str, Any] = {}
        backfill = getattr(initializer, "backfill_scheduler", None)
        if backfill is not None:
            progress = getattr(backfill, "progress", {})
            if not isinstance(progress, dict):
                progress = {}
            summary["backfill"] = {
                "job_id": progress.get("job_id"),
                "status": str(progress.get("status", "unknown")),
                "running": bool(getattr(backfill, "is_running", False)),
                "errors": _safe_int(progress.get("errors")),
                "processed": _safe_int(progress.get("processed")),
                "total": _safe_int(progress.get("total")),
                "last_error": progress.get("error"),
                "started_at": _safe_float(progress.get("started_at")),
                "completed_at": _safe_float(progress.get("completed_at")),
                "cancelled_at": _safe_float(progress.get("cancelled_at")),
                "last_finished_at": (
                    _safe_float(progress.get("completed_at"))
                    or _safe_float(progress.get("cancelled_at"))
                ),
                "retry_count": _safe_int(progress.get("retry_count")),
            }
            if summary["backfill"]["errors"] > 0 or summary["backfill"]["status"] in {
                "failed",
                "completed_with_errors",
            }:
                summary["backfill"]["suggestion"] = (
                    "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。"
                )

        decay = getattr(initializer, "decay_scheduler", None)
        if decay is not None:
            startup_task = getattr(decay, "_startup_task", None)
            startup_error = self._task_exception_summary(startup_task)
            decay_state = self._read_decay_state(decay)
            summary["decay"] = {
                "running": bool(getattr(decay, "_running", False)),
                "loop_active": self._task_active(getattr(decay, "_task", None)),
                "startup_active": self._task_active(startup_task),
                "startup_failed": self._task_failed(startup_task),
                "check_hour": _safe_int(getattr(decay, "check_hour", 0)),
                "check_minute": _safe_int(getattr(decay, "check_minute", 0)),
                "next_run_in_seconds": self._next_decay_run_seconds(decay),
                "last_decay_date": decay_state.get("last_decay_date"),
                "last_completed_at": _safe_float(
                    decay_state.get("last_decay_timestamp")
                ),
                "retry_count": _safe_int(getattr(decay, "_retry_count", 0)),
            }
            if startup_error is not None:
                summary["decay"]["startup_error"] = startup_error["error"]
                summary["decay"]["startup_message"] = startup_error["message"]
                summary["decay"]["suggestion"] = (
                    "检查衰减调度器启动日志；修复异常后重启插件以恢复定期衰减。"
                )
        return summary

    def _build_anomaly_summary(self) -> dict[str, Any]:
        """返回异常检测器最近状态与脱敏标量统计。"""

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        engine = getattr(initializer, "memory_engine", None)
        detector = getattr(engine, "anomaly_detector", None)
        if detector is None:
            return {"available": False, "reason_code": "unavailable"}
        try:
            stats = detector.stats if isinstance(detector.stats, dict) else {}
        except Exception as exc:
            logger.warning(
                "[指标接口] 读取异常检测统计失败，异常类型=%s",
                type(exc).__name__,
            )
            return {
                "available": True,
                "reason_code": "error",
                "error_type": type(exc).__name__,
            }
        return {
            "available": True,
            "reason_code": str(stats.get("reason_code", "ok")),
            "window_size": _safe_int(stats.get("window_size")),
            "mean_7d": _safe_float(stats.get("mean")),
            "stdev_7d": _safe_float(stats.get("stdev")),
            "alerts": _safe_int(stats.get("alerts")),
            "latest_count": _safe_int(stats.get("latest_count")),
            "sigma_threshold": _safe_float(stats.get("sigma_threshold")),
        }

    def _build_learning_summary(self) -> dict[str, Any]:
        """返回自主学习 shadow 候选与统一反馈管线的只读摘要。"""

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        engine = getattr(initializer, "memory_engine", None)
        auto_learning = getattr(engine, "auto_learning", None)
        feedback = getattr(engine, "feedback_signal_manager", None)
        feedback_summary: dict[str, Any] = {}
        if feedback is not None:
            try:
                feedback_summary = feedback.safe_summary()
            except Exception as exc:
                logger.warning(
                    "[指标接口] 读取反馈摘要失败，异常类型=%s",
                    type(exc).__name__,
                )
        summary: dict[str, Any] = {
            "available": auto_learning is not None,
            "candidate_count": 0,
            "ready_count": 0,
            "rejected_count": 0,
            "published_count": 0,
            "reasons": [],
            "feedback": feedback_summary,
        }
        if auto_learning is not None:
            try:
                summary.update(auto_learning.safe_summary())
            except Exception as exc:
                logger.warning(
                    "[指标接口] 读取自主学习摘要失败，异常类型=%s",
                    type(exc).__name__,
                )
                summary["status"] = "error"
        return summary

    @staticmethod
    def _read_decay_state(decay: Any) -> dict[str, Any]:
        state_file = getattr(decay, "_state_file", None)
        if state_file is None:
            return {}
        try:
            content = state_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _next_decay_run_seconds(decay: Any) -> float | None:
        get_seconds = getattr(decay, "_seconds_until_next_run", None)
        if not callable(get_seconds):
            return None
        try:
            return _safe_float(get_seconds())
        except Exception:
            return None

    @staticmethod
    def _task_name(task: Any) -> str:
        get_name = getattr(task, "get_name", None)
        if callable(get_name):
            try:
                name = str(get_name())
                if name:
                    return name
            except Exception:
                pass
        return task.__class__.__name__

    @staticmethod
    def _task_active(task: Any) -> bool:
        if task is None:
            return False
        try:
            return not bool(task.done())
        except Exception:
            return False

    @staticmethod
    def _task_failed(task: Any) -> bool:
        if task is None:
            return False
        try:
            if not bool(task.done()) or bool(task.cancelled()):
                return False
            return task.exception() is not None
        except Exception:
            return False

    @staticmethod
    def _task_exception_summary(task: Any) -> dict[str, str] | None:
        if task is None:
            return None
        try:
            if not bool(task.done()) or bool(task.cancelled()):
                return None
            exc = task.exception()
        except Exception:
            return None
        if exc is None:
            return None
        return {"error": exc.__class__.__name__, "message": str(exc)}

    @staticmethod
    def _failure_recovery_suggestion(name: str, error: str) -> str:
        normalized = name.lower()
        if "provider" in normalized:
            return "检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。"
        if "backfill" in normalized:
            return "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。"
        if "decay" in normalized:
            return "检查衰减调度器启动日志；修复异常后重启插件以恢复定期衰减。"
        if error in {"TimeoutError", "ConnectionError"}:
            return "检查外部依赖、网络和超时配置；恢复后可重试该后台任务。"
        return "查看插件日志中的完整堆栈；修复根因后重启插件或重新触发相关维护任务。"

    @staticmethod
    def _build_write_coordinator_summary() -> dict[str, Any]:
        try:
            from ..managers.write_coordinator import get_write_metrics_snapshot

            snapshot = get_write_metrics_snapshot()
        except Exception as exc:
            logger.warning("[指标接口] 读取写协调器指标失败: %s", exc, exc_info=True)
            return {
                "operations_total": 0,
                "lock_retries_total": 0,
                "failures_total": 0,
                "retry_exhausted_total": 0,
                "fatal_failures_total": 0,
                "non_retryable_failures_total": 0,
                "last_error": exc.__class__.__name__,
            }
        return {
            "operations_total": _safe_int(snapshot.get("operations_total")),
            "lock_retries_total": _safe_int(snapshot.get("lock_retries_total")),
            "failures_total": _safe_int(snapshot.get("failures_total")),
            "retry_exhausted_total": _safe_int(snapshot.get("retry_exhausted_total")),
            "fatal_failures_total": _safe_int(snapshot.get("fatal_failures_total")),
            "non_retryable_failures_total": _safe_int(
                snapshot.get("non_retryable_failures_total")
            ),
            "last_error": snapshot.get("last_error"),
        }

    @staticmethod
    def _build_prometheus_summary() -> dict[str, Any]:
        try:
            from ..monitoring import metrics

            collectors = list(metrics.REGISTRY.collect())
            return {
                "available": bool(metrics.is_prometheus_available()),
                "collector_count": len(collectors),
                "metric_names": [getattr(item, "name", "") for item in collectors],
            }
        except Exception as exc:
            logger.warning(
                "[指标接口] 读取 Prometheus registry 失败: %s", exc, exc_info=True
            )
            return {
                "available": False,
                "collector_count": 0,
                "metric_names": [],
                "error": exc.__class__.__name__,
            }

    def _get_existing_perf_tracker(self) -> Any | None:
        plugin = getattr(self, "plugin", None)
        for owner in (plugin, getattr(plugin, "initializer", None)):
            if owner is None:
                continue
            for attr in ("_perf_tracker", "perf_tracker"):
                tracker = getattr(owner, attr, None)
                if tracker is not None:
                    return tracker
        return None

    def _get_existing_quality_scorer(self) -> Any | None:
        plugin = getattr(self, "plugin", None)
        for owner in (plugin, getattr(plugin, "initializer", None)):
            if owner is None:
                continue
            for attr in ("_quality_scorer", "quality_scorer"):
                scorer = getattr(owner, attr, None)
                if scorer is not None:
                    return scorer
        return None

    @staticmethod
    def _get_percentile(tracker: Any, key: str, p: float) -> float | None:
        if not hasattr(tracker, "get_percentile"):
            return None
        try:
            value = tracker.get_percentile(key, p)
        except Exception:
            return None
        return _safe_float(value)

    @staticmethod
    def _json_safe_recent(recent: Any) -> list[dict[str, float]]:
        if not isinstance(recent, list):
            return []
        sanitized: list[dict[str, float]] = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            sanitized_item: dict[str, float] = {}
            for key, value in item.items():
                parsed = _safe_float(value)
                if parsed is not None:
                    sanitized_item[str(key)] = parsed
            if sanitized_item:
                sanitized.append(sanitized_item)
        return sanitized

    async def get_recall_samples(self):
        """从查询参数读取召回样本游标。"""
        return await self.get_recall_samples_payload(dict(request.args))

    async def get_recall_samples_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """返回游标之后的隐私安全召回标量样本。"""
        try:
            after_sequence = max(0, int(payload.get("after_sequence", 0)))
            limit = min(max(1, int(payload.get("limit", 50))), 200)
        except (TypeError, ValueError):
            return error_response("recall_samples_invalid_query")
        tracker = self._get_existing_perf_tracker()
        if tracker is None or not hasattr(tracker, "get_samples"):
            return ok_response({"items": [], "next_sequence": 0, "latest_sequence": 0})
        try:
            return ok_response(
                tracker.get_samples(after_sequence=after_sequence, limit=limit)
            )
        except Exception:
            logger.warning("[指标接口] 读取召回样本失败", exc_info=True)
            return error_response("recall_samples_unavailable")


__all__ = ["MetricsApiMixin"]
