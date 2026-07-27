"""检索评测报告的持久化存储。"""

from __future__ import annotations

import json
import math
import os
import secrets
import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import aiosqlite

_PERF_PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 30000",
    "PRAGMA cache_size = -65536",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 268435456",
)

_SAFE_CASE_NUMERIC_FIELDS: tuple[str, ...] = (
    "recall_at_k",
    "precision_at_k",
    "reciprocal_rank",
    "ndcg_at_k",
    "latency_ms",
)
_SAFE_ADVANCED_METRICS: frozenset[str] = frozenset(
    {
        "multi_hop_recall",
        "single_hop_recall",
        "noise_negative_false_hit",
        "temporal_consistency",
        "conflict_accuracy",
        "source_supported_projection_rate",
        "answer_faithfulness",
        "answer_relevancy",
        "provider_calls",
        "token_cost",
    }
)


async def _apply_perf_pragmas(conn: aiosqlite.Connection) -> None:
    """设置 SQLite 性能参数，且不导入依赖 AstrBot 的存储模块。

    `statement` 只来自上方硬编码元组，不接收配置、请求或夹具输入。
    """
    for statement in _PERF_PRAGMAS:
        await conn.execute(statement)


class EvaluationReportStore:
    """将评测报告和样本级结果持久化到 SQLite。"""

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
        """创建报告持久化所需的数据表和索引。"""
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
        """持久化报告并返回生成的报告标识。"""
        normalized_report = self._normalize_json_value(report)
        if not isinstance(normalized_report, dict):
            raise TypeError("Evaluation report must normalize to a mapping")

        created_at = float(normalized_report.get("created_at") or time.time())
        report_id = self._generate_report_id(created_at)
        cases = [
            self.safe_case_payload(case)
            for case in list(normalized_report.get("cases") or [])
        ]

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
        """读取完整报告，包括样本级结果。"""
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
        report["cases"] = [
            self.safe_case_payload(self._from_json(case["payload_json"]))
            for case in case_rows
        ]
        return report

    async def list_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        """返回最新报告的元数据，不展开完整样本内容。"""
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
            "precision_at_k",
            "mrr",
            "ndcg_at_k",
            "multi_hop_recall",
            "single_hop_recall",
            "noise_negative_false_hit",
            "temporal_consistency",
            "conflict_accuracy",
            "source_supported_projection_rate",
            "answer_faithfulness",
            "answer_relevancy",
            "p50_latency_ms",
            "p95_latency_ms",
            "provider_calls",
            "token_cost",
            "reason_code_aggregates",
            "dataset_breakdown",
        )
        return {key: report[key] for key in summary_fields if key in report}

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
                str(key): cls._normalize_json_value(item) for key, item in value.items()
            }

        if isinstance(value, (set, frozenset)):
            normalized_items = [cls._normalize_json_value(item) for item in value]
            return sorted(normalized_items, key=cls._json_sort_key)

        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return [cls._normalize_json_value(item) for item in value]

        return str(value)

    @classmethod
    def safe_case_payload(cls, case: Any) -> dict[str, Any]:
        """把样本结果收敛到无 query、业务 ID 和身份字段的数值 allowlist。"""

        normalized = cls._normalize_json_value(case)
        if not isinstance(normalized, Mapping):
            return {}
        payload: dict[str, Any] = {}
        case_id = normalized.get("case_id")
        if case_id is not None:
            payload["case_id"] = str(case_id)[:128]
        for key in _SAFE_CASE_NUMERIC_FIELDS:
            value = cls._finite_number(normalized.get(key))
            if value is not None:
                payload[key] = value

        advanced = normalized.get("advanced_metrics")
        if isinstance(advanced, Mapping):
            safe_advanced: dict[str, int | float] = {}
            for key, value in advanced.items():
                key_text = str(key)
                number = cls._finite_number(value)
                if key_text in _SAFE_ADVANCED_METRICS and number is not None:
                    safe_advanced[key_text] = number
            if safe_advanced:
                payload["advanced_metrics"] = safe_advanced
        return payload

    @staticmethod
    def _finite_number(value: Any) -> int | float | None:
        """只接受有限数值，避免把自由文本或非标准 JSON 数写入报告。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if math.isfinite(float(value)) else None

    @staticmethod
    def _json_sort_key(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = ["EvaluationReportStore"]
