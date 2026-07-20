"""有界保存并在写入、读取两侧脱敏的 Recall Trace Store。"""

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
from .trace_privacy import sanitize_trace_payload


class RecallTraceStore:
    """在内存及可选 SQLite 中保存最近的安全 Recall Trace。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        retention_count: int = 200,
    ) -> None:
        """初始化 Store 配置，不立即创建数据库。"""
        self.db_path = str(db_path) if db_path is not None else None
        self.retention_count = max(1, int(retention_count))
        self._traces: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @asynccontextmanager
    async def _connect(self):
        """建立一个自动关闭的 SQLite 连接。"""
        if self.db_path is None:
            raise RuntimeError("RecallTraceStore has no db_path")

        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        """初始化表、裁剪历史，并把脱敏后的保留记录载入缓存。"""
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
        """脱敏并保存一条 trace；相同关联码执行替换。"""
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
        """按观测关联码读取一份独立的安全 DTO 副本。"""
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
        """按新到旧列出有界数量的安全 DTO。"""
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
        """把一份安全 DTO 写入有界内存缓存。"""
        trace_id = str(payload["trace_id"])
        if trace_id in self._traces:
            del self._traces[trace_id]

        self._traces[trace_id] = self._json_copy(payload)
        while len(self._traces) > self.retention_count:
            self._traces.popitem(last=False)

    def _replace_cache(self, payloads: Any) -> None:
        """用已脱敏的持久化记录整体替换缓存。"""
        self._traces.clear()
        for payload in payloads:
            self._remember(payload)

    async def _trim_sqlite(self, db: aiosqlite.Connection) -> None:
        """删除超过保留数量的最旧 SQLite 记录。"""
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
        """按旧到新读取当前应保留的脱敏记录。"""
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
        """把模型或映射统一转换成安全 DTO。"""
        if hasattr(trace, "to_dict") and callable(trace.to_dict):
            payload = trace.to_dict()
        elif isinstance(trace, Mapping):
            payload = dict(trace)
        else:
            raise TypeError("trace_payload_not_supported")

        payload = json_safe(payload)
        if not isinstance(payload, dict):
            raise TypeError("trace_payload_not_mapping")
        if not payload.get("trace_id"):
            raise ValueError("trace_id_required")

        payload.setdefault("created_at", time.time())
        return sanitize_trace_payload(payload)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """提供兼容的 JSON 可序列化内部副本。"""
        return json_safe(value)

    @staticmethod
    def _json_copy(value: Any) -> Any:
        """通过 JSON 往返创建与缓存隔离的深副本。"""
        return json.loads(json.dumps(json_safe(value), ensure_ascii=False))

    @staticmethod
    def _to_json(value: Any) -> str:
        """生成确定性 SQLite JSON 文本。"""
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _from_json(value: str) -> dict[str, Any]:
        """解析并重新脱敏历史 SQLite JSON。"""
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise TypeError("stored_trace_payload_not_mapping")
        return sanitize_trace_payload(payload)


__all__ = ["RecallTraceStore"]
