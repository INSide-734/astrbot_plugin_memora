"""
写操作日志与断点修复
负责多存储写操作的事务性日志记录和崩溃恢复
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import aiosqlite
from astrbot.api import logger

from .write_op_repair import WriteOpRepairMixin

if TYPE_CHECKING:
    pass


class WriteOpJournal(WriteOpRepairMixin):
    """写操作日志 — 记录多存储写操作状态，支持崩溃恢复"""

    def __init__(
        self,
        db_connection: aiosqlite.Connection | None,
        graph_memory_manager: Any | None,
        atom_store: Any | None,
        atom_enabled: bool = True,
        write_op_max_retries: int = 3,
        get_memory_cb: Callable | None = None,
        invalidate_cache_cb: Callable | None = None,
        delete_doc_indexes_batch_cb: Callable | None = None,
        delete_graph_atoms_batch_cb: Callable | None = None,
    ) -> None:
        self._db = db_connection
        self._graph_memory_manager = graph_memory_manager
        self._atom_store = atom_store
        self._atom_enabled = atom_enabled
        self._max_retries = write_op_max_retries
        self._get_memory = get_memory_cb
        self._invalidate_cache = invalidate_cache_cb
        self._delete_doc_indexes_batch = delete_doc_indexes_batch_cb
        self._delete_graph_atoms_batch = delete_graph_atoms_batch_cb

    # ---- 表创建 ----

    async def create_table(self) -> None:
        """创建可恢复的写操作日志表。"""
        if self._db is None:
            return
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memory_write_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type TEXT NOT NULL,
                memory_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                step TEXT NOT NULL DEFAULT 'started',
                payload TEXT DEFAULT '{}',
                error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_write_ops_status
            ON memory_write_ops(status, updated_at)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_write_ops_memory
            ON memory_write_ops(memory_id, op_type)
        """)

    # ---- 日志记录 ----

    async def start_op(
        self,
        op_type: str,
        payload: dict[str, Any] | None = None,
        memory_id: int | None = None,
    ) -> int | None:
        """记录多存储写操作的开始。"""
        if self._db is None:
            return None
        now = time.time()
        try:
            cursor = await self._db.execute(
                """
                INSERT INTO memory_write_ops(
                    op_type, memory_id, status, step, payload,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', 'started', ?, ?, ?)
                """,
                (
                    op_type,
                    memory_id,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            await self._db.commit()
            return int(cursor.lastrowid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[WriteOpJournal] 写操作日志创建失败", exc_info=True)
            return None

    async def advance_op(
        self,
        op_id: int | None,
        step: str,
        *,
        status: str = "pending",
        memory_id: int | None = None,
        error: str | None = None,
        payload_patch: dict[str, Any] | None = None,
    ) -> None:
        """推进写操作日志条目。"""
        if op_id is None or self._db is None:
            return

        try:
            if status == "completed":
                error = None
            current_payload: dict[str, Any] = {}
            if payload_patch:
                cursor = await self._db.execute(
                    "SELECT payload FROM memory_write_ops WHERE id = ?",
                    (op_id,),
                )
                row = await cursor.fetchone()
                if row and row[0]:
                    try:
                        loaded = json.loads(row[0])
                        current_payload = loaded if isinstance(loaded, dict) else {}
                    except (json.JSONDecodeError, TypeError):
                        current_payload = {}
                current_payload.update(payload_patch)

            fields = ["status = ?", "step = ?", "updated_at = ?"]
            params: list[Any] = [status, step, time.time()]
            if memory_id is not None:
                fields.append("memory_id = ?")
                params.append(memory_id)
            if error is not None:
                fields.append("error = ?")
                params.append(error[:1000])
                if status != "completed":
                    fields.append("retry_count = retry_count + 1")
            elif status == "completed":
                fields.append("error = NULL")
            if payload_patch:
                fields.append("payload = ?")
                params.append(json.dumps(current_payload, ensure_ascii=False))
            params.append(op_id)
            await self._db.execute(
                f"UPDATE memory_write_ops SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            await self._db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[WriteOpJournal] 写操作日志更新失败", exc_info=True)
