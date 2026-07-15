"""SQLite persistence for safe, non-sensitive injection decision telemetry."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..injection.models import InjectionDecisionRecord
from ..managers.write_coordinator import write_transaction
from .base_store import BaseStore

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_WINDOW_MS = {
    "1h": _HOUR_MS,
    "24h": _DAY_MS,
    "7d": 7 * _DAY_MS,
    "30d": 30 * _DAY_MS,
}

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

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.from_ms is not None and self.to_ms is not None and self.from_ms > self.to_ms:
            raise ValueError("from_ms must not exceed to_ms")


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

    async def insert_many(self, records: list[InjectionDecisionRecord]) -> int:
        """Insert a batch atomically, ignoring already-persisted decision IDs."""
        if not records:
            return 0

        values = [self._record_values(record) for record in records]

        async def operation() -> int:
            if self.connection is None:
                raise RuntimeError("InjectionDecisionStore is not initialized")
            before = self.connection.total_changes
            try:
                await self.connection.executemany(self._INSERT_SQL, values)
                await self.connection.commit()
            except Exception:
                await self.connection.rollback()
                raise
            return self.connection.total_changes - before

        return await write_transaction(operation)

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
        """Return a filtered page in deterministic newest-first order."""
        where, params = self._where(query)
        total = int(await self._fetch_scalar(
            "SELECT COUNT(*) FROM injection_decisions" + where,
            params,
        ) or 0)
        rows = await self._fetch_all(
            f"SELECT {_SELECT_LIST_COLUMNS} FROM injection_decisions{where} "
            "ORDER BY created_at_ms DESC, decision_id DESC LIMIT ? OFFSET ?",
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

    async def summary(self, window: str = "24h", now_ms: int | None = None) -> dict[str, Any]:
        """Build the deterministic aggregate and hourly cost trend for a window."""
        if window not in _WINDOW_MS:
            raise ValueError("window must be one of 1h, 24h, 7d, 30d")
        if now_ms is None:
            import time

            now_ms = int(time.time() * 1000)
        rows = await self._fetch_all(
            "SELECT decision_id, created_at_ms, trace_id, routing_mode, resolved_preset, "
            "outcome, primary_reason, fallback_applied, actual_payload_chars "
            "FROM injection_decisions WHERE created_at_ms >= ? "
            "ORDER BY created_at_ms DESC, decision_id DESC",
            (now_ms - _WINDOW_MS[window],),
        )
        if not rows:
            return {
                "window": window,
                "decision_count": 0,
                "payload_chars_p95": 0,
                "provider_fallback_rate": 0.0,
                "preset_distribution": {},
                "cost_trend": [],
                "recent_events": [],
            }

        for row in rows:
            self._normalize_row(row)
        count = len(rows)
        preset_distribution: dict[str, int] = {}
        buckets: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            preset = row["resolved_preset"]
            preset_distribution[preset] = preset_distribution.get(preset, 0) + 1
            bucket = row["created_at_ms"] // _HOUR_MS * _HOUR_MS
            buckets.setdefault(bucket, []).append(row)

        cost_trend = []
        for bucket_ms in sorted(buckets):
            items = buckets[bucket_ms]
            cost_trend.append({
                "bucket_ms": bucket_ms,
                "decision_count": len(items),
                "payload_chars_p95": self._p95(
                    [item["actual_payload_chars"] for item in items]
                ),
                "provider_fallback_rate": sum(
                    item["fallback_applied"] for item in items
                ) / len(items),
            })
        return {
            "window": window,
            "decision_count": count,
            "payload_chars_p95": self._p95(
                [row["actual_payload_chars"] for row in rows]
            ),
            "provider_fallback_rate": sum(
                row["fallback_applied"] for row in rows
            ) / count,
            "preset_distribution": dict(sorted(preset_distribution.items())),
            "cost_trend": cost_trend,
            "recent_events": rows[:15],
        }

    async def cleanup(
        self,
        retention_days: int,
        max_rows: int,
        now_ms: int | None = None,
    ) -> CleanupResult:
        """Delete expired decisions first, then rows outside the stable newest cap."""
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
                if retention_days:
                    cursor = await self.connection.execute(
                        "DELETE FROM injection_decisions WHERE created_at_ms < ?",
                        (now_ms - retention_days * _DAY_MS,),
                    )
                    deleted_expired = cursor.rowcount
                cursor = await self.connection.execute(
                    "DELETE FROM injection_decisions WHERE decision_id IN ("
                    "SELECT decision_id FROM injection_decisions "
                    "ORDER BY created_at_ms DESC, decision_id DESC LIMIT -1 OFFSET ?"
                    ")",
                    (max_rows,),
                )
                deleted_overflow = cursor.rowcount
                await self.connection.commit()
            except Exception:
                await self.connection.rollback()
                raise
            return CleanupResult(deleted_expired, deleted_overflow)

        return await write_transaction(operation)


__all__ = [
    "CleanupResult",
    "DecisionPage",
    "DecisionQuery",
    "InjectionDecisionStore",
]
