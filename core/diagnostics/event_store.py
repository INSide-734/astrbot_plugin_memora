from __future__ import annotations

import json
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


class DiagnosticEventStore:
    """Async SQLite-backed store for diagnostic event history."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_list
                ON diagnostic_events (created_at DESC, event_id DESC)
                """
            )
            await db.commit()

    async def add_event(self, event: dict[str, Any] | None) -> dict[str, Any]:
        event_data = event if isinstance(event, dict) else {}
        event_id = str(event_data.get("event_id") or uuid.uuid4().hex)
        created_at = str(event_data.get("created_at") or self._now())
        domain = str(event_data.get("domain") or "unknown")
        severity = str(event_data.get("severity") or "info")
        title = str(event_data.get("title") or "")
        message = str(event_data.get("message") or "")
        source = str(event_data.get("source") or "unknown")
        payload = self._json_safe(event_data.get("payload", {}))
        resolved_at = event_data.get("resolved_at")
        resolved_at_text = str(resolved_at) if resolved_at is not None else None

        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO diagnostic_events (
                    event_id, created_at, domain, severity, title, message,
                    source, payload, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    created_at,
                    domain,
                    severity,
                    title,
                    message,
                    source,
                    payload_text,
                    resolved_at_text,
                ),
            )
            await db.commit()

        return {
            "event_id": event_id,
            "created_at": created_at,
            "domain": domain,
            "severity": severity,
            "title": title,
            "message": message,
            "source": source,
            "payload": deepcopy(payload),
            "resolved_at": resolved_at_text,
        }

    async def list_events(
        self,
        limit: int = 50,
        domain: str | None = None,
        severity: str | None = None,
        include_resolved: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if not include_resolved:
            clauses.append("resolved_at IS NULL")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = self._safe_limit(limit)
        query = f"""
            SELECT event_id, created_at, domain, severity, title, message,
                   source, payload, resolved_at
            FROM diagnostic_events
            {where}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
        """
        params.append(safe_limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._row_to_event(row) for row in rows]

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT event_id, created_at, domain, severity, title, message,
                       source, payload, resolved_at
                FROM diagnostic_events
                WHERE event_id = ?
                """,
                (event_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return self._row_to_event(row) if row is not None else None

    async def resolve_event(self, event_id: str) -> dict[str, Any] | None:
        resolved_at = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE diagnostic_events SET resolved_at = ? WHERE event_id = ?",
                (resolved_at, event_id),
            )
            await db.commit()
        return await self.get_event(event_id)

    @classmethod
    def _row_to_event(cls, row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "domain": row["domain"],
            "severity": row["severity"],
            "title": row["title"],
            "message": row["message"],
            "source": row["source"],
            "payload": cls._loads_payload(row["payload"]),
            "resolved_at": row["resolved_at"],
        }

    @staticmethod
    def _loads_payload(payload_text: str) -> Any:
        try:
            return deepcopy(json.loads(payload_text))
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            return json.loads(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _safe_limit(value: Any) -> int:
        try:
            return max(0, min(int(value), 500))
        except (TypeError, ValueError):
            return 50

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
