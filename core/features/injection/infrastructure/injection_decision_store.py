"""SQLite persistence for safe, non-sensitive injection decision telemetry."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...memory.infrastructure.base_store import BaseStore
from ..domain.models import (
    InjectionDecisionRecord,
    InjectionLifecycleRecord,
    LifecycleEventKind,
    LifecycleOrigin,
    LifecycleSource,
)

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
# Budget utilization is grouped as integer per-mille ratios so SQLite never
# aggregates floating point payload/budget divisions.
_RATIO_SCALE = 1000
_WINDOW_MS = {
    "1h": _HOUR_MS,
    "24h": _DAY_MS,
    "7d": 7 * _DAY_MS,
    "30d": 30 * _DAY_MS,
}

_MAX_SQLITE_INTEGER = 2**63 - 1


class LifecycleCommitOutcomeUnknown(RuntimeError):
    """A lifecycle-containing commit may have succeeded despite its error."""


_COLUMNS = (
    "decision_id",
    "created_at_ms",
    "trace_id",
    "routing_mode",
    "configured_preset",
    "recommended_preset",
    "resolved_preset",
    "preferred_delivery",
    "resolved_delivery",
    "fallback_applied",
    "outcome",
    "error_code",
    "primary_reason",
    "reason_codes_json",
    "provider_type",
    "provider_model",
    "candidate_count",
    "selected_count",
    "dropped_count",
    "truncated_count",
    "configured_budget_chars",
    "effective_budget_chars",
    "actual_payload_chars",
    "context_headroom_chars",
    "decision_ms",
    "format_ms",
    "inject_ms",
)
_SELECT_COLUMNS = ", ".join(_COLUMNS)
_LIST_COLUMNS = tuple(column for column in _COLUMNS if column != "reason_codes_json")
_SELECT_LIST_COLUMNS = ", ".join(_LIST_COLUMNS)
_LIFECYCLE_UPSERT_SQL = """
    INSERT INTO injection_lifecycle_counts
        (bucket_ms, event_kind, source, origin, event_count)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(bucket_ms, event_kind, source, origin)
    DO UPDATE SET event_count = event_count + excluded.event_count
"""
_BUCKET_SUMMARY_SQL = (
    "SELECT (created_at_ms / ?) * ? AS bucket_ms, "
    "COUNT(*) AS decision_count, "
    "SUM(fallback_applied) AS fallback_count, "
    "SUM(CASE WHEN selected_count > 0 THEN 1 ELSE 0 END) "
    "AS memory_present_count, "
    "SUM(CASE WHEN outcome IN ('injected','fallback') THEN 1 ELSE 0 END) "
    "AS payload_injected_count, "
    "SUM(selected_count) AS selected_count_total, "
    "SUM(dropped_count) AS dropped_count_total, "
    "SUM(truncated_count) AS truncated_count_total, "
    "SUM(effective_budget_chars) AS effective_budget_chars_total, "
    "GROUP_CONCAT(actual_payload_chars) AS payload_chars_csv, "
    "GROUP_CONCAT(CASE WHEN effective_budget_chars > 0 THEN "
    f"(actual_payload_chars * {_RATIO_SCALE}) / effective_budget_chars END) "
    "AS budget_utilization_csv "
    "FROM injection_decisions WHERE created_at_ms >= ? "
    "GROUP BY bucket_ms ORDER BY bucket_ms"
)
INJECTION_DECISION_SORT_COLUMNS = {
    "created_at_ms": "created_at_ms",
    "routing_mode": "routing_mode COLLATE NOCASE",
    "resolved_preset": "resolved_preset COLLATE NOCASE",
    "provider_type": "provider_type COLLATE NOCASE",
    "outcome": "outcome COLLATE NOCASE",
    "actual_payload_chars": "actual_payload_chars",
    "decision_ms": "decision_ms",
}


@dataclass(frozen=True, slots=True)
class DecisionQuery:
    """Validated filters and pagination for the decision list."""

    offset: int = 0
    limit: int = 50
    from_ms: int | None = None
    to_ms: int | None = None
    routing_mode: str | None = None
    resolved_preset: str | None = None
    provider_type: str | None = None
    primary_reason: str | None = None
    fallback_applied: bool | None = None
    outcome: str | None = None
    sort_by: str = "created_at_ms"
    sort_order: str = "desc"

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if (
            self.from_ms is not None
            and self.to_ms is not None
            and self.from_ms > self.to_ms
        ):
            raise ValueError("from_ms must not exceed to_ms")
        if self.sort_by not in INJECTION_DECISION_SORT_COLUMNS:
            raise ValueError("sort_by is invalid")
        if self.sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be asc or desc")


@dataclass(frozen=True, slots=True)
class DecisionPage:
    """One stable page of decisions and its unpaged total."""

    items: list[dict[str, Any]]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Counts deleted by the ordered retention and row-cap phases."""

    deleted_expired: int
    deleted_overflow: int
    deleted_lifecycle: int = 0


@dataclass(frozen=True, slots=True)
class _BucketAggregate:
    """Window totals and hourly rows derived from the bucket aggregation."""

    payload_values: list[int]
    fallback_count: int
    memory_present_count: int
    payload_injected_count: int
    cost_trend: list[dict[str, Any]]
    selected_count_total: int
    dropped_count_total: int
    truncated_count_total: int
    effective_budget_chars_total: int
    budget_utilization_per_mille: list[int]


class InjectionDecisionStore(BaseStore):
    """Store injection decisions using an explicit safe schema."""

    _INSERT_SQL = f"""
        INSERT OR IGNORE INTO injection_decisions ({_SELECT_COLUMNS})
        VALUES ({", ".join("?" for _ in _COLUMNS)})
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(str(db_path))

    async def _create_tables(self) -> None:
        await self._execute("""
            CREATE TABLE IF NOT EXISTS injection_decisions (
                decision_id TEXT PRIMARY KEY,
                created_at_ms INTEGER NOT NULL,
                trace_id TEXT,
                routing_mode TEXT NOT NULL,
                configured_preset TEXT NOT NULL,
                recommended_preset TEXT NOT NULL,
                resolved_preset TEXT NOT NULL,
                preferred_delivery TEXT NOT NULL,
                resolved_delivery TEXT NOT NULL,
                fallback_applied INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                error_code TEXT,
                primary_reason TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                provider_model TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                selected_count INTEGER NOT NULL,
                dropped_count INTEGER NOT NULL,
                truncated_count INTEGER NOT NULL,
                configured_budget_chars INTEGER NOT NULL,
                effective_budget_chars INTEGER NOT NULL,
                actual_payload_chars INTEGER NOT NULL,
                context_headroom_chars INTEGER NOT NULL,
                decision_ms REAL NOT NULL,
                format_ms REAL NOT NULL,
                inject_ms REAL NOT NULL
            )
        """)
        await self._execute("""
            CREATE TABLE IF NOT EXISTS injection_lifecycle_counts (
                bucket_ms INTEGER NOT NULL CHECK(bucket_ms >= 0),
                event_kind TEXT NOT NULL CHECK(event_kind IN ('retrieved', 'injected')),
                source TEXT NOT NULL CHECK(source IN ('passive', 'agent', 'debug')),
                origin TEXT NOT NULL CHECK(origin IN ('fresh', 'cache', 'none')),
                event_count INTEGER NOT NULL
                    CHECK(event_count > 0 AND typeof(event_count) = 'integer'),
                PRIMARY KEY (bucket_ms, event_kind, source, origin)
            )
        """)
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_injection_decisions_created ON injection_decisions(created_at_ms DESC, decision_id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_injection_decisions_preset ON injection_decisions(resolved_preset, created_at_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_injection_decisions_provider ON injection_decisions(provider_type, created_at_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_injection_decisions_outcome ON injection_decisions(outcome, created_at_ms DESC)",
        ):
            await self._execute(sql)
        await self._commit()

    @staticmethod
    def _record_values(record: InjectionDecisionRecord) -> tuple[Any, ...]:
        return (
            record.decision_id,
            record.created_at_ms,
            record.trace_id,
            record.routing_mode,
            record.configured_preset,
            record.recommended_preset,
            record.resolved_preset,
            record.preferred_delivery,
            record.resolved_delivery,
            int(record.fallback_applied),
            record.outcome,
            record.error_code,
            record.primary_reason,
            json.dumps(record.reason_codes, ensure_ascii=False),
            record.provider_type,
            record.provider_model,
            record.candidate_count,
            record.selected_count,
            record.dropped_count,
            record.truncated_count,
            record.configured_budget_chars,
            record.effective_budget_chars,
            record.actual_payload_chars,
            record.context_headroom_chars,
            record.decision_ms,
            record.format_ms,
            record.inject_ms,
        )

    @staticmethod
    def _lifecycle_values(
        record: InjectionLifecycleRecord,
    ) -> tuple[int, str, str, str, int]:
        if type(record) is not InjectionLifecycleRecord:
            raise ValueError("record must be an InjectionLifecycleRecord")
        if (
            type(record.created_at_ms) is not int
            or not 0 <= record.created_at_ms <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("created_at_ms must be a non-negative SQLite integer")
        if (
            type(record.count) is not int
            or not 1 <= record.count <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("count must be a positive SQLite integer")
        if not isinstance(record.event_kind, LifecycleEventKind):
            raise ValueError("event_kind must be a LifecycleEventKind")
        if not isinstance(record.source, LifecycleSource):
            raise ValueError("source must be a LifecycleSource")
        if not isinstance(record.origin, LifecycleOrigin):
            raise ValueError("origin must be a LifecycleOrigin")
        if (
            record.event_kind is LifecycleEventKind.INJECTED
            and record.source is LifecycleSource.DEBUG
        ):
            raise ValueError("debug source cannot record injected lifecycle")
        return (
            (record.created_at_ms // _HOUR_MS) * _HOUR_MS,
            record.event_kind.value,
            record.source.value,
            record.origin.value,
            record.count,
        )

    async def insert_many(
        self,
        records: Sequence[InjectionDecisionRecord | InjectionLifecycleRecord],
    ) -> int:
        """Persist a decision/lifecycle batch in one retryable transaction."""
        if not records:
            return 0

        decision_values: list[tuple[Any, ...]] = []
        lifecycle_values: list[tuple[int, str, str, str, int]] = []
        for record in records:
            if type(record) is InjectionDecisionRecord:
                decision_values.append(self._record_values(record))
            elif type(record) is InjectionLifecycleRecord:
                lifecycle_values.append(self._lifecycle_values(record))
            else:
                raise ValueError("unsupported injection telemetry record")

        async def operation() -> int:
            if self.connection is None:
                raise RuntimeError("InjectionDecisionStore is not initialized")
            before = self.connection.total_changes
            try:
                if decision_values:
                    await self.connection.executemany(self._INSERT_SQL, decision_values)
                if lifecycle_values:
                    await self.connection.executemany(
                        _LIFECYCLE_UPSERT_SQL, lifecycle_values
                    )
                try:
                    await self.connection.commit()
                except Exception as exc:
                    if lifecycle_values:
                        # commit 后结果可能未知，阻止写协调器按 locked 重放聚合。
                        raise LifecycleCommitOutcomeUnknown(
                            "lifecycle commit outcome unknown"
                        ) from exc
                    raise
            except BaseException:
                await self.connection.rollback()
                raise
            return self.connection.total_changes - before

        from ...memory.application.write_coordinator import write_transaction

        return await write_transaction(operation)

    async def insert_lifecycle_many(
        self, records: list[InjectionLifecycleRecord]
    ) -> int:
        """Persist only lifecycle counts through the shared SQLite transaction."""
        batch: list[InjectionDecisionRecord | InjectionLifecycleRecord] = list(records)
        return await self.insert_many(batch)

    @staticmethod
    def _where(query: DecisionQuery) -> tuple[str, tuple[Any, ...]]:
        clauses: list[str] = []
        params: list[Any] = []
        filters = (
            (query.from_ms, "created_at_ms >= ?"),
            (query.to_ms, "created_at_ms <= ?"),
            (query.routing_mode, "routing_mode = ?"),
            (query.resolved_preset, "resolved_preset = ?"),
            (query.provider_type, "provider_type = ?"),
            (query.primary_reason, "primary_reason = ?"),
            (query.fallback_applied, "fallback_applied = ?"),
            (query.outcome, "outcome = ?"),
        )
        for value, fragment in filters:
            if value is not None:
                clauses.append(fragment)
                params.append(int(value) if isinstance(value, bool) else value)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", tuple(params))

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        row["fallback_applied"] = bool(row["fallback_applied"])
        return row

    async def list_decisions(self, query: DecisionQuery) -> DecisionPage:
        """Return a filtered page in deterministic, allowlisted order."""
        where, params = self._where(query)
        total = int(
            await self._fetch_scalar(
                "SELECT COUNT(*) FROM injection_decisions" + where,
                params,
            )
            or 0
        )
        order_column = INJECTION_DECISION_SORT_COLUMNS[query.sort_by]
        order_direction = query.sort_order.upper()
        rows = await self._fetch_all(
            f"SELECT {_SELECT_LIST_COLUMNS} FROM injection_decisions{where} "
            f"ORDER BY {order_column} {order_direction}, decision_id ASC LIMIT ? OFFSET ?",
            params + (query.limit, query.offset),
        )
        return DecisionPage(
            items=[self._normalize_row(row) for row in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Return the exact opaque decision ID with decoded reason codes."""
        row = await self._fetch_one(
            f"SELECT {_SELECT_COLUMNS} FROM injection_decisions WHERE decision_id = ?",
            (decision_id,),
        )
        if row is None:
            return None
        reason_codes = json.loads(row.pop("reason_codes_json"))
        row["reason_codes"] = reason_codes
        return self._normalize_row(row)

    @staticmethod
    def _p95(values: list[int]) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]

    @staticmethod
    def _per_mille_values(csv: Any) -> list[int]:
        text = str(csv or "")
        if not text:
            return []
        return [int(value) for value in text.split(",")]

    @staticmethod
    def _mean_ratio(per_mille_values: list[int]) -> float:
        if not per_mille_values:
            return 0.0
        return sum(per_mille_values) / len(per_mille_values) / _RATIO_SCALE

    @staticmethod
    def _rounded_mean(total: int, count: int) -> int:
        if count <= 0:
            return 0
        return math.floor(total / count + 0.5)

    @staticmethod
    def _empty_summary(window: str) -> dict[str, Any]:
        return {
            "window": window,
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

    async def lifecycle_summary(
        self, window: str = "24h", now_ms: int | None = None
    ) -> dict[str, int]:
        """Sum only whole hourly buckets in the closed window.

        A bucket is counted when its start is at or after the window cutoff
        and its end is at or before ``now_ms``. Thus a bucket that merely
        overlaps either boundary is excluded, while a bucket ending exactly
        at ``now_ms`` or starting exactly at the cutoff is included.
        """
        if window not in _WINDOW_MS:
            raise ValueError("window must be one of 1h, 24h, 7d, 30d")
        if now_ms is None:
            import time

            now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - _WINDOW_MS[window]
        rows = await self._fetch_all(
            "SELECT event_kind, SUM(event_count) AS event_count "
            "FROM injection_lifecycle_counts "
            "WHERE bucket_ms >= ? AND bucket_ms <= ? "
            "GROUP BY event_kind",
            (cutoff_ms, now_ms - _HOUR_MS),
        )
        result = {"retrieved_count": 0, "injected_count": 0}
        for row in rows:
            event_kind = row.get("event_kind")
            value = row.get("event_count")
            if (
                event_kind in {"retrieved", "injected"}
                and type(value) is int
                and value > 0
            ):
                result[f"{event_kind}_count"] = value
        return result

    @classmethod
    def _summarize_buckets(cls, bucket_rows: list[dict[str, Any]]) -> _BucketAggregate:
        payload_values: list[int] = []
        utilization_values: list[int] = []
        fallback_count = 0
        memory_present_count = 0
        payload_injected_count = 0
        selected_count_total = 0
        dropped_count_total = 0
        truncated_count_total = 0
        effective_budget_chars_total = 0
        cost_trend: list[dict[str, Any]] = []
        for row in bucket_rows:
            bucket_values = [
                int(value) for value in str(row["payload_chars_csv"]).split(",")
            ]
            bucket_utilization = cls._per_mille_values(row["budget_utilization_csv"])
            payload_values.extend(bucket_values)
            utilization_values.extend(bucket_utilization)
            bucket_count = int(row["decision_count"])
            bucket_fallback_count = int(row["fallback_count"] or 0)
            bucket_memory_present = int(row["memory_present_count"] or 0)
            bucket_payload_injected = int(row["payload_injected_count"] or 0)
            bucket_selected = int(row["selected_count_total"] or 0)
            bucket_dropped = int(row["dropped_count_total"] or 0)
            bucket_truncated = int(row["truncated_count_total"] or 0)
            bucket_budget = int(row["effective_budget_chars_total"] or 0)
            fallback_count += bucket_fallback_count
            memory_present_count += bucket_memory_present
            payload_injected_count += bucket_payload_injected
            selected_count_total += bucket_selected
            dropped_count_total += bucket_dropped
            truncated_count_total += bucket_truncated
            effective_budget_chars_total += bucket_budget
            cost_trend.append(
                {
                    "bucket_ms": int(row["bucket_ms"]),
                    "decision_count": bucket_count,
                    "payload_chars_p95": cls._p95(bucket_values),
                    "provider_fallback_rate": bucket_fallback_count / bucket_count,
                    "selected_count_total": bucket_selected,
                    "dropped_count_total": bucket_dropped,
                    "budget_utilization_avg": cls._mean_ratio(bucket_utilization),
                }
            )
        return _BucketAggregate(
            payload_values=payload_values,
            fallback_count=fallback_count,
            memory_present_count=memory_present_count,
            payload_injected_count=payload_injected_count,
            cost_trend=cost_trend,
            selected_count_total=selected_count_total,
            dropped_count_total=dropped_count_total,
            truncated_count_total=truncated_count_total,
            effective_budget_chars_total=effective_budget_chars_total,
            budget_utilization_per_mille=utilization_values,
        )

    async def summary(
        self, window: str = "24h", now_ms: int | None = None
    ) -> dict[str, Any]:
        """Build the deterministic aggregate and hourly cost trend for a window."""
        if window not in _WINDOW_MS:
            raise ValueError("window must be one of 1h, 24h, 7d, 30d")
        if now_ms is None:
            import time

            now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - _WINDOW_MS[window]
        bucket_rows = await self._fetch_all(
            _BUCKET_SUMMARY_SQL,
            (_HOUR_MS, _HOUR_MS, cutoff_ms),
        )
        lifecycle = await self.lifecycle_summary(window=window, now_ms=now_ms)
        if not bucket_rows:
            return {**self._empty_summary(window), **lifecycle}
        aggregate = self._summarize_buckets(bucket_rows)

        preset_rows = await self._fetch_all(
            "SELECT resolved_preset, COUNT(*) AS decision_count "
            "FROM injection_decisions WHERE created_at_ms >= ? "
            "GROUP BY resolved_preset ORDER BY resolved_preset",
            (cutoff_ms,),
        )
        recent_events = await self._fetch_all(
            "SELECT decision_id, created_at_ms, trace_id, routing_mode, resolved_preset, "
            "outcome, primary_reason, fallback_applied, actual_payload_chars "
            "FROM injection_decisions WHERE created_at_ms >= ? "
            "ORDER BY created_at_ms DESC, decision_id DESC LIMIT 15",
            (cutoff_ms,),
        )
        count = len(aggregate.payload_values)
        return {
            "window": window,
            **lifecycle,
            "decision_count": count,
            "payload_chars_p95": self._p95(aggregate.payload_values),
            "provider_fallback_rate": aggregate.fallback_count / count,
            "memory_present_count": aggregate.memory_present_count,
            "payload_injected_count": aggregate.payload_injected_count,
            "selected_count_total": aggregate.selected_count_total,
            "dropped_count_total": aggregate.dropped_count_total,
            "truncated_count_total": aggregate.truncated_count_total,
            "effective_budget_chars_avg": self._rounded_mean(
                aggregate.effective_budget_chars_total,
                count,
            ),
            "budget_utilization_avg": self._mean_ratio(
                aggregate.budget_utilization_per_mille
            ),
            "budget_utilization_p95": self._p95(aggregate.budget_utilization_per_mille)
            / _RATIO_SCALE,
            "preset_distribution": {
                str(row["resolved_preset"]): int(row["decision_count"])
                for row in preset_rows
            },
            "cost_trend": aggregate.cost_trend,
            "recent_events": [self._normalize_row(row) for row in recent_events],
        }

    async def cleanup(
        self,
        retention_days: int,
        max_rows: int,
        now_ms: int | None = None,
    ) -> CleanupResult:
        """Delete old decisions and lifecycle buckets without partial loss.

        A lifecycle bucket is retained while it intersects the retention
        cutoff; a bucket ending exactly at the cutoff is eligible for deletion.
        ``max_rows`` applies only to detailed decision rows, never buckets.
        """
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if max_rows < 0:
            raise ValueError("max_rows must be non-negative")
        if now_ms is None:
            import time

            now_ms = int(time.time() * 1000)

        async def operation() -> CleanupResult:
            if self.connection is None:
                raise RuntimeError("InjectionDecisionStore is not initialized")
            try:
                deleted_expired = 0
                deleted_lifecycle = 0
                if retention_days:
                    cutoff_ms = now_ms - retention_days * _DAY_MS
                    cursor = await self.connection.execute(
                        "DELETE FROM injection_decisions WHERE created_at_ms < ?",
                        (cutoff_ms,),
                    )
                    deleted_expired = cursor.rowcount
                    # Delete only buckets whose end is at or before cutoff;
                    # a bucket intersecting the cutoff remains intact.
                    cursor = await self.connection.execute(
                        "DELETE FROM injection_lifecycle_counts WHERE bucket_ms <= ?",
                        (cutoff_ms - _HOUR_MS,),
                    )
                    deleted_lifecycle = cursor.rowcount
                cursor = await self.connection.execute(
                    "DELETE FROM injection_decisions WHERE decision_id IN ("
                    "SELECT decision_id FROM injection_decisions "
                    "ORDER BY created_at_ms DESC, decision_id DESC LIMIT -1 OFFSET ?"
                    ")",
                    (max_rows,),
                )
                deleted_overflow = cursor.rowcount
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise
            return CleanupResult(deleted_expired, deleted_overflow, deleted_lifecycle)

        from ...memory.application.write_coordinator import write_transaction

        return await write_transaction(operation)


__all__ = [
    "CleanupResult",
    "DecisionPage",
    "DecisionQuery",
    "INJECTION_DECISION_SORT_COLUMNS",
    "InjectionDecisionStore",
]
