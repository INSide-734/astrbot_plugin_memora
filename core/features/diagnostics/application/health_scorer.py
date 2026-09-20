"""运行时诊断健康评分应用服务。"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

_MAX_SAFE_COUNT = 2**63 - 1


class HealthScorer:
    """把运行时健康快照转换为可安全序列化的诊断摘要。"""

    def __init__(self) -> None:
        """初始化实例级写失败累计值基线。"""
        self._last_write_failures_total: int | None = None

    def score(
        self,
        snapshot: Any,
        *,
        previous_write_failures_total: int | None = None,
    ) -> dict[str, Any]:
        """根据固定阈值计算健康分、领域状态和建议动作。

        参数:
            snapshot: 包含 Provider、召回、写入、任务和索引状态的快照。
            previous_write_failures_total: 可选的上次写失败累计值；缺省时使用
                当前实例保存的基线。

        返回:
            JSON 安全的健康分、等级、领域明细和建议动作。

        副作用:
            快照包含合法写失败累计值时，更新当前实例的比较基线。
        """
        data = snapshot if isinstance(snapshot, dict) else {}
        score = 100
        domains: list[dict[str, Any]] = []
        recommended_actions: list[str] = []

        provider = self._as_dict(data.get("provider"))
        provider_status = str(provider.get("status", "")).lower()
        attempts = self._to_number(provider.get("attempts")) or 0.0
        max_attempts = self._to_number(provider.get("max_attempts")) or 0.0
        retry_active = provider.get("retry_active") is True
        provider_failed = provider_status == "failed"
        if provider_failed:
            score -= 35
            domains.append(
                self._domain(
                    "provider",
                    0,
                    "critical",
                    "Provider is failed.",
                )
            )
            recommended_actions.append(
                "Restore or reconfigure the provider, then retry initialization."
            )
        elif provider_status == "waiting" and (
            retry_active or self._retry_active(attempts, max_attempts)
        ):
            score -= 10
            domains.append(
                self._domain(
                    "provider",
                    60,
                    "watch",
                    "Provider is waiting and retry attempts remain active.",
                )
            )
            recommended_actions.append(
                "Monitor provider startup and verify upstream availability."
            )

        recall = self._as_dict(data.get("recall"))
        p95_total_ms = self._to_number(recall.get("p95_total_ms"))
        if p95_total_ms is not None and p95_total_ms > 1000:
            score -= 15
            domains.append(
                self._domain(
                    "recall",
                    40,
                    "degraded",
                    "Recall p95 latency is above 1000 ms.",
                )
            )
            recommended_actions.append(
                "Inspect retrieval latency, reranking, and provider response time."
            )

        write = self._as_dict(data.get("write_coordinator"))
        failures_total = self._to_int(write.get("failures_total"))
        if failures_total is not None:
            prior_failures_total = (
                previous_write_failures_total
                if previous_write_failures_total is not None
                else self._last_write_failures_total
            )
            if (
                prior_failures_total is not None
                and failures_total > prior_failures_total
            ):
                score -= 15
                domains.append(
                    self._domain(
                        "write",
                        50,
                        "degraded",
                        "Write failures increased since the last health event.",
                    )
                )
                recommended_actions.append(
                    "Review write coordinator errors and storage availability."
                )
            self._last_write_failures_total = failures_total

        background_tasks = self._as_dict(data.get("background_tasks"))
        failed_tasks = self._to_int(background_tasks.get("failed"))
        if failed_tasks is not None and failed_tasks > 0:
            score -= 10
            domains.append(
                self._domain(
                    "scheduler",
                    55,
                    "watch",
                    "Background tasks have recorded failures.",
                )
            )
            recommended_actions.append(
                "Check scheduler failure details and rerun failed maintenance jobs."
            )

        index = self._as_dict(data.get("index"))
        rebuild_errors = self._to_number(index.get("last_rebuild_errors"))
        rebuild_total = self._to_number(index.get("last_rebuild_total"))
        if (
            rebuild_errors is not None
            and rebuild_total is not None
            and rebuild_total > 0
            and rebuild_errors / rebuild_total > 0.10
        ):
            score -= 10
            domains.append(
                self._domain(
                    "index",
                    55,
                    "watch",
                    "Index rebuild error ratio is above 10 percent.",
                )
            )
            recommended_actions.append(
                "Inspect index validation output and rebuild failed entries."
            )

        anomaly = self._as_dict(data.get("anomaly"))
        anomaly_reason = str(anomaly.get("reason_code", "")).lower()
        if anomaly.get("available") is True and anomaly_reason == "memory_rate_anomaly":
            score -= 10
            domains.append(
                self._domain(
                    "anomaly",
                    55,
                    "watch",
                    "Memory creation rate anomaly detected.",
                )
            )
            recommended_actions.append(
                "Review recent memory creation volume and upstream ingestion."
            )
        elif (
            anomaly.get("available") is True
            and anomaly_reason == "insufficient_history"
        ):
            domains.append(
                self._domain(
                    "anomaly",
                    100,
                    "info",
                    "Anomaly detector has insufficient history; no alert.",
                )
            )

        prometheus = self._as_dict(data.get("prometheus"))
        if prometheus and prometheus.get("available") is False:
            domains.append(
                self._domain(
                    "prometheus",
                    100,
                    "info",
                    "Prometheus collector is unavailable; score is unchanged.",
                )
            )

        summary_value = data.get("summary_tasks")
        summary_tasks = self._as_dict(summary_value)
        summary_available = isinstance(summary_value, dict)
        summary_projection: dict[str, Any] = {}
        if "summary_tasks" in data:
            blocked = self._safe_count(summary_tasks.get("blocked"))
            unknown = self._safe_count(summary_tasks.get("unknown"))
            candidate_total = self._safe_count(summary_tasks.get("candidate_total"))
            canonical_total = self._safe_count(summary_tasks.get("canonical_total"))
            quarantine_total = self._safe_count(summary_tasks.get("quarantine_total"))
            accepted_total = canonical_total + quarantine_total
            unresolved = blocked + unknown
            reason_counts = summary_tasks.get("unresolved_reason_counts")
            safe_reasons: dict[str, int] = {}
            if isinstance(reason_counts, dict):
                for reason, count in reason_counts.items():
                    if not isinstance(reason, str) or not reason or len(reason) > 64:
                        continue
                    safe_count = self._safe_count(count)
                    if safe_count > 0:
                        safe_reasons[reason] = safe_count

            has_evidence = candidate_total > 0 or accepted_total > 0 or unresolved > 0
            write_status = (
                "unknown"
                if not has_evidence
                else "available"
                if accepted_total > 0
                else "blocked"
            )
            summary_projection = {
                "blocked": blocked,
                "unknown": unknown,
                "oldest_unresolved_age_seconds": self._safe_count(
                    summary_tasks.get("oldest_unresolved_age_seconds")
                ),
                "unresolved_reason_counts": safe_reasons,
                "write_availability": {
                    "candidate_total": candidate_total,
                    "canonical_total": canonical_total,
                    "quarantine_total": quarantine_total,
                    "accepted_total": accepted_total,
                    "status": write_status,
                },
                "evidence_status": (
                    "available"
                    if has_evidence
                    else "insufficient"
                    if summary_available
                    else "unknown"
                ),
            }
            if unknown:
                score -= 10
                domains.append(
                    self._domain(
                        "summary_tasks",
                        55,
                        "degraded",
                        "Summary jobs remain unresolved and need investigation.",
                    )
                )
                recommended_actions.append(
                    "Review unresolved summary jobs and their stable reason categories."
                )
            elif blocked:
                domains.append(
                    self._domain(
                        "summary_tasks",
                        70,
                        "watch",
                        "Summary jobs are blocked pending safety review.",
                    )
                )
            elif not has_evidence:
                domains.append(
                    self._domain(
                        "summary_tasks",
                        0,
                        "unknown",
                        "Summary task evidence has no samples yet.",
                    )
                )
            if candidate_total > 0 and accepted_total == 0:
                score -= 10
                domains.append(
                    self._domain(
                        "write_availability",
                        50,
                        "degraded",
                        "Candidates exist but no canonical or quarantined writes are recorded.",
                    )
                )
                recommended_actions.append(
                    "Inspect the write path; candidate and accepted-write totals are shown below."
                )
            elif quarantine_total > 0:
                domains.append(
                    self._domain(
                        "write_availability",
                        100,
                        "info",
                        "Quarantined candidates are awaiting safety review; this is not an infrastructure failure.",
                    )
                )

        if provider_failed:
            score = min(score, 44)
        score = max(0, min(100, int(score)))
        result = {
            "score": score,
            "level": self.level_for_score(score),
            "domains": deepcopy(domains),
            "recommended_actions": list(recommended_actions),
        }
        if summary_projection:
            result["summary_tasks"] = summary_projection
        return result

    def level_for_score(self, score: Any) -> str:
        """把任意分值钳制到 0～100 后映射为固定健康等级。"""
        value = self._to_int(score)
        if value is None:
            value = 0
        value = max(0, min(100, value))
        if value >= 85:
            return "healthy"
        if value >= 65:
            return "watch"
        if value >= 45:
            return "degraded"
        return "critical"

    @staticmethod
    def _domain(name: str, score: int, status: str, message: str) -> dict[str, Any]:
        """构造单个健康领域的 JSON 安全明细。"""
        return {
            "name": name,
            "score": score,
            "status": status,
            "message": message,
        }

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        """仅接受字典快照片段，其余输入安全退化为空字典。"""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _to_number(value: Any, default: float | None = None) -> float | None:
        """把非布尔整数或浮点数转换为浮点数，否则返回默认值。"""
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        return default

    @classmethod
    def _safe_count(cls, value: Any) -> int:
        """把诊断计数限制为有限、非负且有界整数。"""
        number = cls._to_number(value)
        if number is None or not math.isfinite(number) or number <= 0:
            return 0
        try:
            return min(int(number), _MAX_SAFE_COUNT)
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _to_int(cls, value: Any) -> int | None:
        """把合法有限数值转换为整数，非法输入返回空值。"""
        number = cls._to_number(value)
        if number is None or not math.isfinite(number):
            return None
        return int(number)

    @staticmethod
    def _retry_active(attempts: float, max_attempts: float) -> bool:
        """判断 Provider 重试次数是否仍处于活动窗口。"""
        if max_attempts <= 0:
            return attempts > 0
        return attempts < max_attempts


__all__ = ["HealthScorer"]
