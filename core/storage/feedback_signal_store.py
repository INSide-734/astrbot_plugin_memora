"""反馈排序实验的隔离 SQLite 事件与聚合 Store。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.feedback_signal import (
    FeedbackSignalAggregate,
    TrustedFeedbackEvent,
)

_UNSET = object()


class FeedbackSignalStore:
    """只接受评测任务显式路径的事务 Store，不连接生产数据库。"""

    def __init__(self, db_path: str | Path) -> None:
        """创建隔离连接；调用方负责在评测结束后关闭。"""

        self.db_path = str(db_path)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._initialized = False

    def initialize(self) -> None:
        """创建带 dedupe 唯一约束的最小事件和聚合表。"""

        self._connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY,
                adapter_kind TEXT NOT NULL,
                decision_key TEXT NOT NULL,
                variant_key TEXT NOT NULL,
                outcome TEXT NOT NULL,
                scope_domain TEXT NOT NULL,
                persona_domain TEXT,
                observed_at TEXT NOT NULL,
                window_key TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_domain_time
                ON feedback_events(scope_domain, persona_domain, observed_at);
            CREATE TABLE IF NOT EXISTS feedback_aggregates (
                scope_domain TEXT NOT NULL,
                persona_domain TEXT,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                accepted_count INTEGER NOT NULL,
                independent_window_count INTEGER NOT NULL,
                decayed_support REAL NOT NULL,
                proposed_document_weight REAL NOT NULL,
                proposed_graph_weight REAL NOT NULL,
                delta_from_baseline REAL NOT NULL,
                status TEXT NOT NULL,
                policy_version INTEGER NOT NULL,
                PRIMARY KEY(scope_domain, persona_domain, window_start, policy_version)
            );
            """
        )
        self._connection.commit()
        self._initialized = True

    def insert_events(self, events: Iterable[TrustedFeedbackEvent]) -> dict[str, int]:
        """事务写入事件并以稳定计数区分 accepted/duplicate。"""

        self._ensure_initialized()
        accepted = 0
        duplicates = 0
        try:
            with self._connection:
                for event in events:
                    cursor = self._connection.execute(
                        """
                        INSERT OR IGNORE INTO feedback_events
                        (adapter_kind, decision_key, variant_key, outcome,
                         scope_domain, persona_domain, observed_at, window_key,
                         dedupe_key, schema_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.adapter_kind.value,
                            event.decision_key,
                            event.variant_key,
                            event.outcome.value,
                            event.scope_domain,
                            event.persona_domain,
                            _serialize_time(event.observed_at),
                            event.window_key,
                            event.dedupe_key,
                            event.schema_version,
                        ),
                    )
                    if cursor.rowcount == 1:
                        accepted += 1
                    else:
                        duplicates += 1
        except sqlite3.Error as exc:
            raise RuntimeError("feedback_store_write_failed") from exc
        return {"accepted": accepted, "duplicate_event": duplicates}

    def list_events(
        self,
        *,
        scope_domain: str | None = None,
        persona_domain: str | None | object = _UNSET,
    ) -> list[TrustedFeedbackEvent]:
        """读取内部聚合所需事件；不提供报告级原始字段出口。"""

        self._ensure_initialized()
        clauses: list[str] = []
        params: list[Any] = []
        if scope_domain is not None:
            clauses.append("scope_domain = ?")
            params.append(scope_domain)
        if persona_domain is None:
            clauses.append("persona_domain IS NULL")
        elif persona_domain is not _UNSET:
            clauses.append("persona_domain = ?")
            params.append(persona_domain)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT * FROM feedback_events {where} ORDER BY observed_at, id",
            params,
        ).fetchall()
        from ..models.feedback_signal import FeedbackAdapterKind, FeedbackOutcome

        return [
            TrustedFeedbackEvent(
                adapter_kind=FeedbackAdapterKind(row["adapter_kind"]),
                decision_key=row["decision_key"],
                variant_key=row["variant_key"],
                outcome=FeedbackOutcome(row["outcome"]),
                scope_domain=row["scope_domain"],
                persona_domain=row["persona_domain"],
                observed_at=_parse_time(row["observed_at"]),
                window_key=row["window_key"],
                dedupe_key=row["dedupe_key"],
                schema_version=row["schema_version"],
            )
            for row in rows
        ]

    def replace_aggregates(self, aggregates: Iterable[FeedbackSignalAggregate]) -> None:
        """原子替换当前 policy 下的聚合快照。"""

        self._ensure_initialized()
        rows = list(aggregates)
        policy_versions = {item.policy_version for item in rows}
        try:
            with self._connection:
                if not rows:
                    self._connection.execute("DELETE FROM feedback_aggregates")
                    return
                for policy_version in policy_versions:
                    self._connection.execute(
                        "DELETE FROM feedback_aggregates WHERE policy_version = ?",
                        (policy_version,),
                    )
                for aggregate in rows:
                    self._connection.execute(
                        """
                        INSERT INTO feedback_aggregates
                        (scope_domain, persona_domain, window_start, window_end,
                         accepted_count, independent_window_count, decayed_support,
                         proposed_document_weight, proposed_graph_weight,
                         delta_from_baseline, status, policy_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            aggregate.scope_domain,
                            aggregate.persona_domain,
                            _serialize_time(aggregate.window_start),
                            _serialize_time(aggregate.window_end),
                            aggregate.accepted_count,
                            aggregate.independent_window_count,
                            aggregate.decayed_support,
                            aggregate.proposed_document_weight,
                            aggregate.proposed_graph_weight,
                            aggregate.delta_from_baseline,
                            aggregate.status,
                            aggregate.policy_version,
                        ),
                    )
        except sqlite3.Error as exc:
            raise RuntimeError("feedback_store_aggregate_failed") from exc

    def list_aggregates(
        self, *, policy_version: int | None = None
    ) -> list[sqlite3.Row]:
        """读取内部聚合行供 Manager 重放，不返回原始事件内容。"""

        self._ensure_initialized()
        if policy_version is None:
            return self._connection.execute(
                "SELECT * FROM feedback_aggregates ORDER BY window_start"
            ).fetchall()
        return self._connection.execute(
            "SELECT * FROM feedback_aggregates WHERE policy_version = ? ORDER BY window_start",
            (policy_version,),
        ).fetchall()

    def clear_aggregates(self) -> None:
        """删除派生聚合而保留事件，供完整重建验证。"""

        self._ensure_initialized()
        with self._connection:
            self._connection.execute("DELETE FROM feedback_aggregates")

    def safe_summary(self) -> dict[str, int]:
        """返回不含 key、domain、事件内容的安全计数。"""

        self._ensure_initialized()
        event_count = self._connection.execute(
            "SELECT COUNT(*) FROM feedback_events"
        ).fetchone()[0]
        aggregate_count = self._connection.execute(
            "SELECT COUNT(*) FROM feedback_aggregates"
        ).fetchone()[0]
        return {
            "event_count": int(event_count),
            "aggregate_count": int(aggregate_count),
        }

    def close(self) -> None:
        """关闭隔离 SQLite 连接。"""

        self._connection.close()
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """拒绝在 schema 初始化前使用 Store。"""

        if not self._initialized:
            raise RuntimeError("feedback_store_not_initialized")


def _serialize_time(value: datetime) -> str:
    """规范化为 UTC ISO 时间。"""

    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat()


def _parse_time(value: str) -> datetime:
    """恢复 UTC ISO 时间。"""

    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc)


__all__ = ["FeedbackSignalStore"]
