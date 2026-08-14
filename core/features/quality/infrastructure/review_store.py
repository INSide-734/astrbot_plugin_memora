"""SQLite-backed review queue store."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from ..domain.models import (
    ReviewAction,
    ReviewItem,
    ReviewReason,
    ReviewSeverity,
    ReviewStatus,
    json_copy,
    normalize_reason,
    normalize_severity,
    normalize_status,
)


class ReviewStore:
    """Persist review items and action history without AstrBot dependencies."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    @asynccontextmanager
    async def _connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS review_items (
                    item_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_preview TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS review_actions (
                    action_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES review_items(item_id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_items_newest
                ON review_items(updated_at DESC, item_id DESC)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_items_status
                ON review_items(status, updated_at DESC, item_id DESC)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_actions_item
                ON review_actions(item_id, created_at ASC, action_id ASC)
                """
            )
            await db.commit()

    async def upsert_item(self, item: ReviewItem | Mapping[str, Any]) -> dict[str, Any]:
        payload = self._normalize_item(item)

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                rows = await self._find_open_items(
                    db,
                    payload["memory_id"],
                    payload["reasons"],
                )
                if rows:
                    winner = rows[0]
                    payload["item_id"] = winner["item_id"]
                    payload["created_at"] = float(winner["created_at"])
                    payload["updated_at"] = max(
                        float(payload["updated_at"]), time.time()
                    )
                    await db.execute(
                        """
                        UPDATE review_items
                        SET reasons_json = ?,
                            severity = ?,
                            status = ?,
                            content_preview = ?,
                            metadata_json = ?,
                            updated_at = ?
                        WHERE item_id = ?
                        """,
                        self._item_params_for_update(payload),
                    )
                    await self._mark_superseded_open_items_safe(
                        db,
                        rows[1:],
                        payload["updated_at"],
                    )
                else:
                    await db.execute(
                        """
                        INSERT INTO review_items (
                            item_id,
                            memory_id,
                            reasons_json,
                            severity,
                            status,
                            content_preview,
                            metadata_json,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._item_params_for_insert(payload),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        stored = await self.get_item(payload["item_id"])
        if stored is None:
            raise RuntimeError("review item was not persisted")
        return stored

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM review_items WHERE item_id = ?",
                (str(item_id),),
            )
            row = await cursor.fetchone()
        return self._row_to_item(row) if row is not None else None

    async def list_items(
        self,
        *,
        status: ReviewStatus | str | None = None,
        reason: ReviewReason | str | None = None,
        severity: ReviewSeverity | str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = self._normalize_limit(limit)
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("status = ?")
            params.append(normalize_status(status).value)
        if severity is not None:
            where.append("severity = ?")
            params.append(normalize_severity(severity).value)
        if reason is not None:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(reasons_json) WHERE value = ?)"
            )
            params.append(normalize_reason(reason).value)
        if cursor is not None:
            cursor_item = await self.get_item(str(cursor))
            if cursor_item is None:
                return []
            where.append("(updated_at < ? OR (updated_at = ? AND item_id < ?))")
            params.extend(
                [
                    cursor_item["updated_at"],
                    cursor_item["updated_at"],
                    cursor_item["item_id"],
                ]
            )

        sql = "SELECT * FROM review_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, item_id DESC LIMIT ?"
        params.append(safe_limit)

        async with self._connect() as db:
            cursor_obj = await db.execute(sql, tuple(params))
            rows = await cursor_obj.fetchall()
        return [self._row_to_item(row) for row in rows]

    async def record_action(
        self,
        action: ReviewAction | Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = self._normalize_action(action)
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM review_items WHERE item_id = ?",
                (payload["item_id"],),
            )
            row = await cursor.fetchone()
            if row is None:
                return {
                    "item": None,
                    "action": payload,
                    "success": False,
                    "message": "review item not found",
                }

            await db.execute(
                """
                INSERT INTO review_actions (
                    action_id,
                    item_id,
                    action,
                    actor_id,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["action_id"],
                    payload["item_id"],
                    payload["action"],
                    payload["actor_id"],
                    self._to_json(payload["payload"]),
                    payload["created_at"],
                ),
            )
            await db.execute(
                """
                UPDATE review_items
                SET status = ?, updated_at = ?
                WHERE item_id = ?
                """,
                (payload["action"], payload["created_at"], payload["item_id"]),
            )
            await db.commit()

        item = await self.get_item(payload["item_id"])
        return {
            "item": item,
            "action": json_copy(payload),
            "success": True,
            "message": "",
        }

    async def list_actions(self, item_id: str) -> list[dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT *
                FROM review_actions
                WHERE item_id = ?
                ORDER BY created_at ASC, action_id ASC
                """,
                (str(item_id),),
            )
            rows = await cursor.fetchall()
        return [self._row_to_action(row) for row in rows]

    async def _find_open_items(
        self,
        db: aiosqlite.Connection,
        memory_id: str,
        reasons: list[str],
    ) -> list[aiosqlite.Row]:
        cursor = await db.execute(
            """
            SELECT *
            FROM review_items
            WHERE memory_id = ?
              AND status = ?
              AND EXISTS (
                  SELECT 1
                  FROM json_each(reasons_json)
                  WHERE value IN (SELECT value FROM json_each(?))
            )
            ORDER BY updated_at DESC, item_id DESC
            """,
            (memory_id, ReviewStatus.OPEN.value, json.dumps(reasons)),
        )
        return await cursor.fetchall()

    async def _mark_superseded_open_items_safe(
        self,
        db: aiosqlite.Connection,
        rows: list[aiosqlite.Row],
        updated_at: float,
    ) -> None:
        if not rows:
            return
        await db.executemany(
            """
            UPDATE review_items
            SET status = ?, updated_at = ?
            WHERE item_id = ?
            """,
            [
                (ReviewStatus.SAFE.value, float(updated_at), row["item_id"])
                for row in rows
            ],
        )

    @staticmethod
    def _normalize_item(item: ReviewItem | Mapping[str, Any]) -> dict[str, Any]:
        payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        reasons = payload.get("reasons") or []
        if not reasons:
            raise ValueError("review item must include at least one reason")
        normalized = {
            "item_id": str(payload["item_id"]),
            "memory_id": str(payload["memory_id"]),
            "reasons": [normalize_reason(reason).value for reason in reasons],
            "severity": normalize_severity(payload["severity"]).value,
            "status": normalize_status(payload.get("status", ReviewStatus.OPEN)).value,
            "content_preview": str(payload.get("content_preview", "")),
            "metadata": json_copy(payload.get("metadata", {})),
            "created_at": float(payload.get("created_at", time.time())),
            "updated_at": float(payload.get("updated_at", time.time())),
        }
        return normalized

    @staticmethod
    def _normalize_action(action: ReviewAction | Mapping[str, Any]) -> dict[str, Any]:
        payload = action.to_dict() if hasattr(action, "to_dict") else dict(action)
        return {
            "action_id": str(payload["action_id"]),
            "item_id": str(payload["item_id"]),
            "action": normalize_status(payload["action"]).value,
            "actor_id": payload.get("actor_id"),
            "payload": json_copy(payload.get("payload", {})),
            "created_at": float(payload.get("created_at", time.time())),
        }

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        if limit is None or isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        return min(200, max(1, limit))

    def _item_params_for_insert(self, payload: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            payload["item_id"],
            payload["memory_id"],
            self._to_json(payload["reasons"]),
            payload["severity"],
            payload["status"],
            payload["content_preview"],
            self._to_json(payload["metadata"]),
            payload["created_at"],
            payload["updated_at"],
        )

    def _item_params_for_update(self, payload: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            self._to_json(payload["reasons"]),
            payload["severity"],
            payload["status"],
            payload["content_preview"],
            self._to_json(payload["metadata"]),
            payload["updated_at"],
            payload["item_id"],
        )

    @classmethod
    def _row_to_item(cls, row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "item_id": row["item_id"],
            "memory_id": row["memory_id"],
            "reasons": cls._from_json(row["reasons_json"]),
            "severity": row["severity"],
            "status": row["status"],
            "content_preview": row["content_preview"],
            "metadata": cls._from_json(row["metadata_json"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    @classmethod
    def _row_to_action(cls, row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "action_id": row["action_id"],
            "item_id": row["item_id"],
            "action": row["action"],
            "actor_id": row["actor_id"],
            "payload": cls._from_json(row["payload_json"]),
            "created_at": float(row["created_at"]),
        }

    @staticmethod
    def _to_json(value: Any) -> str:
        return json.dumps(json_copy(value), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _from_json(value: str) -> Any:
        return json.loads(value)


__all__ = ["ReviewStore"]
