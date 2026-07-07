"""Persistent storage for retrieval evaluation reports."""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import aiosqlite


_PERF_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("foreign_keys", "ON"),
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "30000"),
    ("cache_size", "-65536"),
    ("temp_store", "MEMORY"),
    ("mmap_size", "268435456"),
)


async def _apply_perf_pragmas(conn: aiosqlite.Connection) -> None:
    """Apply SQLite pragmas without importing AstrBot-dependent storage modules."""
    for key, value in _PERF_PRAGMAS:
        await conn.execute(f"PRAGMA {key} = {value}")


class EvaluationReportStore:
    """Persist evaluation reports and case-level results in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    @asynccontextmanager
    async def _connect(self):
        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            await _apply_perf_pragmas(db)
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        """Create report persistence tables and indexes."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_reports (
                    report_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    baseline TEXT,
                    summary_json TEXT NOT NULL,
                    datasets_json TEXT NOT NULL,
                    variants_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    case_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_cases (
                    report_id TEXT NOT NULL,
                    case_index INTEGER NOT NULL,
                    case_id TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (report_id, case_index),
                    FOREIGN KEY (report_id) REFERENCES evaluation_reports(report_id)
                        ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evaluation_reports_created_at
                ON evaluation_reports(created_at DESC)
                """
            )
            await db.commit()

    async def save_report(self, report: Mapping[str, Any] | Any) -> str:
        """Persist a report and return its generated report ID."""
        normalized_report = self._normalize_json_value(report)
        if not isinstance(normalized_report, dict):
            raise TypeError("Evaluation report must normalize to a mapping")

        created_at = float(normalized_report.get("created_at") or time.time())
        report_id = self._generate_report_id(created_at)
        cases = list(normalized_report.get("cases") or [])

        payload = dict(normalized_report)
        payload.pop("cases", None)
        summary = self._report_summary(normalized_report)

        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO evaluation_reports (
                    report_id,
                    created_at,
                    baseline,
                    summary_json,
                    datasets_json,
                    variants_json,
                    payload_json,
                    case_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    created_at,
                    normalized_report.get("baseline"),
                    self._to_json(summary),
                    self._to_json(normalized_report.get("datasets", [])),
                    self._to_json(normalized_report.get("variants", [])),
                    self._to_json(payload),
                    len(cases),
                ),
            )
            await db.executemany(
                """
                INSERT INTO evaluation_cases (
                    report_id,
                    case_index,
                    case_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        report_id,
                        index,
                        case.get("case_id") if isinstance(case, Mapping) else None,
                        self._to_json(case),
                    )
                    for index, case in enumerate(cases)
                ],
            )
            await db.commit()

        return report_id

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        """Load a full report, including case-level results."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT *
                FROM evaluation_reports
                WHERE report_id = ?
                """,
                (report_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            case_cursor = await db.execute(
                """
                SELECT payload_json
                FROM evaluation_cases
                WHERE report_id = ?
                ORDER BY case_index ASC
                """,
                (report_id,),
            )
            case_rows = await case_cursor.fetchall()

        report = self._from_json(row["payload_json"])
        report["report_id"] = row["report_id"]
        report["created_at"] = row["created_at"]
        report["baseline"] = row["baseline"]
        report["summary"] = self._from_json(row["summary_json"])
        report["datasets"] = self._from_json(row["datasets_json"])
        report["variants"] = self._from_json(row["variants_json"])
        report["cases"] = [self._from_json(case["payload_json"]) for case in case_rows]
        return report

    async def list_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return newest report metadata without full case payloads."""
        safe_limit = max(1, int(limit))
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT
                    report_id,
                    created_at,
                    baseline,
                    summary_json,
                    datasets_json,
                    variants_json,
                    case_count
                FROM evaluation_reports
                ORDER BY created_at DESC, report_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
            rows = await cursor.fetchall()

        return [
            {
                "report_id": row["report_id"],
                "created_at": row["created_at"],
                "baseline": row["baseline"],
                "summary": self._from_json(row["summary_json"]),
                "datasets": self._from_json(row["datasets_json"]),
                "variants": self._from_json(row["variants_json"]),
                "case_count": row["case_count"],
            }
            for row in rows
        ]

    @staticmethod
    def _generate_report_id(created_at: float) -> str:
        timestamp_ms = int(created_at * 1000)
        return f"eval-{timestamp_ms}-{secrets.token_hex(4)}"

    @staticmethod
    def _to_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _from_json(value: str) -> Any:
        return json.loads(value)

    @classmethod
    def _report_summary(cls, report: dict[str, Any]) -> dict[str, Any]:
        summary = report.get("summary")
        if isinstance(summary, Mapping):
            return dict(summary)

        summary_fields = (
            "total_cases",
            "k",
            "recall_at_k",
            "mrr",
            "ndcg_at_k",
            "p95_latency_ms",
            "dataset_breakdown",
        )
        return {
            key: report[key]
            for key in summary_fields
            if key in report
        }

    @classmethod
    def _normalize_json_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, os.PathLike):
            return os.fspath(value)

        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: cls._normalize_json_value(getattr(value, field.name))
                for field in fields(value)
            }

        if isinstance(value, Mapping):
            return {
                str(key): cls._normalize_json_value(item)
                for key, item in value.items()
            }

        if isinstance(value, (set, frozenset)):
            normalized_items = [cls._normalize_json_value(item) for item in value]
            return sorted(normalized_items, key=cls._json_sort_key)

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [cls._normalize_json_value(item) for item in value]

        return str(value)

    @staticmethod
    def _json_sort_key(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = ["EvaluationReportStore"]
