"""Bounded storage for recent recall traces."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from .trace_models import json_safe


class RecallTraceStore:
    """Keep recent recall traces in memory and optionally persist them."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        retention_count: int = 200,
    ) -> None:
        self.db_path = str(db_path) if db_path is not None else None
        self.retention_count = max(1, int(retention_count))
        self._traces: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @asynccontextmanager
    async def _connect(self):
        if self.db_path is None:
            raise RuntimeError("RecallTraceStore has no db_path")

        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        if self.db_path is None:
            return

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS recall_traces (
                    trace_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recall_traces_created_at
                ON recall_traces(created_at DESC, trace_id DESC)
                """
            )
            await db.commit()

            await self._trim_sqlite(db)
            await db.commit()
            cursor = await db.execute(
                """
                SELECT payload_json
                FROM recall_traces
                ORDER BY created_at ASC, trace_id ASC
                LIMIT ?
                """,
                (self.retention_count,),
            )
            rows = await cursor.fetchall()

        self._replace_cache(self._from_json(row["payload_json"]) for row in rows)

    async def save_trace(self, trace: Mapping[str, Any] | Any) -> None:
        payload = self._normalize_trace(trace)

        if self.db_path is None:
            self._remember(payload)
            return

        async with self._connect() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO recall_traces (
                    trace_id,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?)
                """,
                (
                    payload["trace_id"],
                    float(payload["created_at"]),
                    self._to_json(payload),
                ),
            )
            await self._trim_sqlite(db)
            retained_payloads = await self._load_retained_payloads(db)
            await db.commit()
        self._replace_cache(retained_payloads)

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        if self.db_path is None:
            cached = self._traces.get(trace_id)
            if cached is not None:
                return self._json_copy(cached)
            return None

        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT payload_json
                FROM recall_traces
                WHERE trace_id = ?
                """,
                (trace_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        payload = self._from_json(row["payload_json"])
        self._remember(payload)
        return self._json_copy(payload)

    async def list_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))

        if self.db_path is not None:
            async with self._connect() as db:
                cursor = await db.execute(
                    """
                    SELECT payload_json
                    FROM recall_traces
                    ORDER BY created_at DESC, trace_id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                )
                rows = await cursor.fetchall()
            return [self._from_json(row["payload_json"]) for row in rows]

        return [
            self._json_copy(payload)
            for payload in reversed(self._traces.values())
        ][:safe_limit]

    def _remember(self, payload: dict[str, Any]) -> None:
        trace_id = str(payload["trace_id"])
        if trace_id in self._traces:
            del self._traces[trace_id]

        self._traces[trace_id] = self._json_copy(payload)
        while len(self._traces) > self.retention_count:
            self._traces.popitem(last=False)

    def _replace_cache(self, payloads: Any) -> None:
        self._traces.clear()
        for payload in payloads:
            self._remember(payload)

    async def _trim_sqlite(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            DELETE FROM recall_traces
            WHERE trace_id IN (
                SELECT trace_id
                FROM recall_traces
                ORDER BY created_at DESC, trace_id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.retention_count,),
        )

    async def _load_retained_payloads(
        self,
        db: aiosqlite.Connection,
    ) -> list[dict[str, Any]]:
        cursor = await db.execute(
            """
            SELECT payload_json
            FROM recall_traces
            ORDER BY created_at ASC, trace_id ASC
            LIMIT ?
            """,
            (self.retention_count,),
        )
        rows = await cursor.fetchall()
        return [self._from_json(row["payload_json"]) for row in rows]

    @classmethod
    def _normalize_trace(cls, trace: Mapping[str, Any] | Any) -> dict[str, Any]:
        if hasattr(trace, "to_dict") and callable(trace.to_dict):
            payload = trace.to_dict()
        elif isinstance(trace, Mapping):
            payload = dict(trace)
        else:
            raise TypeError("trace must be a RecallTrace-like object or mapping")

        payload = json_safe(payload)
        if not isinstance(payload, dict):
            raise TypeError("trace payload must normalize to a mapping")
        if not payload.get("trace_id"):
            raise ValueError("trace payload must include trace_id")

        payload["trace_id"] = str(payload["trace_id"])
        payload.setdefault("created_at", time.time())
        return payload

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json_safe(value)

    @staticmethod
    def _json_copy(value: Any) -> Any:
        return json.loads(json.dumps(json_safe(value), ensure_ascii=False))

    @staticmethod
    def _to_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _from_json(value: str) -> dict[str, Any]:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise TypeError("stored recall trace payload is not a mapping")
        return payload


__all__ = ["RecallTraceStore"]
