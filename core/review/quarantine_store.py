"""持久化 canonical 写入前的记忆质量隔离候选。"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

_TERMINAL_STATUSES = {"approved", "rejected"}


class MemoryQuarantineStore:
    """用独立 SQLite 状态机保存 pre-canonical 记忆候选。"""

    def __init__(self, db_path: str | Path) -> None:
        """记录隔离数据库路径；每次操作使用独立短连接。"""

        self.db_path = str(db_path)

    @asynccontextmanager
    async def _connect(self):
        """创建启用外键和行对象的短生命周期连接。"""

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        """幂等创建候选表、动作历史表及查询索引。"""

        async with self._connect() as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_quarantine_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    candidate_key TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    importance REAL NOT NULL,
                    session_id TEXT NOT NULL,
                    persona_id TEXT,
                    source_window_json TEXT NOT NULL,
                    is_group_chat INTEGER NOT NULL,
                    canonical_memory_id INTEGER,
                    approval_token_hash TEXT,
                    failure_reason TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_quarantine_actions (
                    action_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(candidate_id)
                        REFERENCES memory_quarantine_candidates(candidate_id)
                );

                CREATE INDEX IF NOT EXISTS idx_memory_quarantine_status
                ON memory_quarantine_candidates(
                    status, updated_at DESC, candidate_id DESC
                );

                CREATE INDEX IF NOT EXISTS idx_memory_quarantine_actions
                ON memory_quarantine_actions(candidate_id, created_at, action_id);
                """
            )
            cursor = await db.execute("PRAGMA table_info(memory_quarantine_candidates)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            await cursor.close()
            if "approval_token_hash" not in columns:
                await db.execute(
                    """
                    ALTER TABLE memory_quarantine_candidates
                    ADD COLUMN approval_token_hash TEXT
                    """
                )
            await db.commit()

    async def stage_candidate(
        self,
        *,
        candidate_key: str,
        reason_codes: list[str] | tuple[str, ...],
        content: str,
        metadata: Mapping[str, Any],
        importance: float,
        session_id: str,
        persona_id: str | None,
        source_window: Mapping[str, Any],
        is_group_chat: bool,
    ) -> dict[str, Any]:
        """按稳定候选键幂等写入 pending 候选并返回权威记录。"""

        normalized_key = str(candidate_key).strip()
        if not normalized_key:
            raise ValueError("candidate_key 不能为空")
        normalized_content = str(content).strip()
        if not normalized_content:
            raise ValueError("候选记忆正文不能为空")
        now = time.time()
        candidate_id = f"qc_{uuid.uuid4().hex}"
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO memory_quarantine_candidates (
                        candidate_id, candidate_key, revision, status,
                        reason_codes_json, content, metadata_json, importance,
                        session_id, persona_id, source_window_json,
                        is_group_chat, canonical_memory_id, failure_reason,
                        created_at, updated_at
                    ) VALUES (?, ?, 1, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                              NULL, ?, ?)
                    """,
                    (
                        candidate_id,
                        normalized_key,
                        self._to_json(sorted(set(reason_codes))),
                        normalized_content,
                        self._to_json(metadata),
                        max(0.0, min(1.0, float(importance))),
                        str(session_id),
                        str(persona_id) if persona_id is not None else None,
                        self._to_json(source_window),
                        int(bool(is_group_chat)),
                        now,
                        now,
                    ),
                )
                cursor = await db.execute(
                    """
                    SELECT * FROM memory_quarantine_candidates
                    WHERE candidate_key = ?
                    """,
                    (normalized_key,),
                )
                row = await cursor.fetchone()
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        if row is None:
            raise RuntimeError("隔离候选未能持久化")
        return self._row_to_candidate(row)

    async def get_candidate(self, candidate_id: str | None) -> dict[str, Any] | None:
        """按候选 ID 返回一条隔离记录；不存在时返回 ``None``。"""

        if not candidate_id:
            return None
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT * FROM memory_quarantine_candidates
                WHERE candidate_id = ?
                """,
                (str(candidate_id),),
            )
            row = await cursor.fetchone()
        return self._row_to_candidate(row) if row is not None else None

    async def list_candidates(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按更新时间倒序列出候选，并限制单次返回数量。"""

        safe_limit = min(200, max(1, int(limit)))
        sql = "SELECT * FROM memory_quarantine_candidates"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(str(status))
        sql += " ORDER BY updated_at DESC, candidate_id DESC LIMIT ?"
        params.append(safe_limit)
        async with self._connect() as db:
            cursor = await db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        return [self._row_to_candidate(row) for row in rows]

    async def claim_approval(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
        approval_token: str,
        content: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """用 revision CAS 声明批准，并可原子保存管理员修正正文。"""

        token_hash = self._approval_token_digest(approval_token)
        return await self._transition(
            candidate_id,
            expected_revision=expected_revision,
            allowed_statuses={"pending", "blocked"},
            next_status="approving",
            action="approve_claimed",
            actor_id=actor_id,
            approval_token_hash=token_hash,
            failure_reason=None,
            content=content,
            metadata=metadata,
            action_payload={"content_changed": content is not None},
        )

    async def finalize_approval(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        canonical_memory_id: int,
        actor_id: str | None,
        approval_token: str,
    ) -> dict[str, Any]:
        """把已声明候选终结为 approved 并绑定 canonical 整数 ID。"""

        token_hash = self._approval_token_digest(approval_token)
        return await self._transition(
            candidate_id,
            expected_revision=expected_revision,
            allowed_statuses={"approving"},
            next_status="approved",
            action="approved",
            actor_id=actor_id,
            canonical_memory_id=int(canonical_memory_id),
            failure_reason=None,
            verify_approval_token_hash=token_hash,
        )

    async def finalize_repaired_approval(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        canonical_memory_id: int,
        actor_id: str | None,
        approval_token: str,
    ) -> dict[str, Any]:
        """仅凭匹配 token、revision 的 canonical 事实收口 approving 候选。"""

        token_hash = self._approval_token_digest(approval_token)
        return await self._transition(
            candidate_id,
            expected_revision=expected_revision,
            allowed_statuses={"approving"},
            next_status="approved",
            action="approval_repaired",
            actor_id=actor_id,
            canonical_memory_id=int(canonical_memory_id),
            failure_reason=None,
            verify_approval_token_hash=token_hash,
        )

    async def block_approval(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
        reason_code: str,
    ) -> dict[str, Any]:
        """在批准复核失败时把候选置为 blocked 并记录稳定原因码。"""

        return await self._transition(
            candidate_id,
            expected_revision=expected_revision,
            allowed_statuses={"approving"},
            next_status="blocked",
            action="approval_blocked",
            actor_id=actor_id,
            failure_reason=str(reason_code),
            action_payload={"reason_code": str(reason_code)},
        )

    async def reject(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """用 revision CAS 拒绝候选，不触碰原始会话证据。"""

        return await self._transition(
            candidate_id,
            expected_revision=expected_revision,
            allowed_statuses={"pending", "blocked"},
            next_status="rejected",
            action="rejected",
            actor_id=actor_id,
            failure_reason=None,
        )

    async def list_actions(self, candidate_id: str) -> list[dict[str, Any]]:
        """按发生顺序返回候选的低敏动作历史。"""

        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT * FROM memory_quarantine_actions
                WHERE candidate_id = ?
                ORDER BY created_at ASC, action_id ASC
                """,
                (str(candidate_id),),
            )
            rows = await cursor.fetchall()
        return [self._row_to_action(row) for row in rows]

    async def _transition(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        allowed_statuses: set[str],
        next_status: str,
        action: str,
        actor_id: str | None,
        canonical_memory_id: int | None = None,
        failure_reason: str | None = None,
        action_payload: Mapping[str, Any] | None = None,
        content: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        approval_token_hash: str | None = None,
        verify_approval_token_hash: str | None = None,
    ) -> dict[str, Any]:
        """在单个立即事务中完成状态 CAS 与动作历史写入。"""

        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                    SELECT * FROM memory_quarantine_candidates
                    WHERE candidate_id = ?
                    """,
                    (str(candidate_id),),
                )
                current = await cursor.fetchone()
                if current is None:
                    raise KeyError("quarantine_candidate_not_found")
                if current["status"] in _TERMINAL_STATUSES:
                    if str(current["status"]) == next_status:
                        await db.commit()
                        return self._row_to_candidate(current)
                    raise ValueError("quarantine_status_conflict")
                if int(current["revision"]) != int(expected_revision):
                    raise ValueError("quarantine_revision_conflict")
                if str(current["status"]) not in allowed_statuses:
                    raise ValueError("quarantine_status_conflict")
                if verify_approval_token_hash is not None:
                    current_token_hash = str(current["approval_token_hash"] or "")
                    if not secrets.compare_digest(
                        current_token_hash,
                        str(verify_approval_token_hash),
                    ):
                        raise ValueError("quarantine_approval_token_invalid")
                next_revision = int(current["revision"]) + 1
                normalized_content = None
                if content is not None:
                    normalized_content = str(content).strip()
                    if not normalized_content:
                        raise ValueError("quarantine_content_required")
                metadata_json = (
                    self._to_json(metadata) if metadata is not None else None
                )
                await db.execute(
                    """
                    UPDATE memory_quarantine_candidates
                    SET revision = ?, status = ?, canonical_memory_id = ?,
                        approval_token_hash = COALESCE(?, approval_token_hash),
                        failure_reason = ?,
                        content = COALESCE(?, content),
                        metadata_json = COALESCE(?, metadata_json),
                        updated_at = ?
                    WHERE candidate_id = ? AND revision = ?
                    """,
                    (
                        next_revision,
                        next_status,
                        canonical_memory_id,
                        approval_token_hash,
                        failure_reason,
                        normalized_content,
                        metadata_json,
                        now,
                        str(candidate_id),
                        int(expected_revision),
                    ),
                )
                await db.execute(
                    """
                    INSERT INTO memory_quarantine_actions (
                        action_id, candidate_id, action, actor_id,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"qa_{uuid.uuid4().hex}",
                        str(candidate_id),
                        str(action),
                        str(actor_id) if actor_id is not None else None,
                        self._to_json(action_payload or {}),
                        now,
                    ),
                )
                cursor = await db.execute(
                    """
                    SELECT * FROM memory_quarantine_candidates
                    WHERE candidate_id = ?
                    """,
                    (str(candidate_id),),
                )
                updated = await cursor.fetchone()
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        if updated is None:
            raise RuntimeError("隔离候选状态未能持久化")
        return self._row_to_candidate(updated)

    @classmethod
    def _row_to_candidate(cls, row: aiosqlite.Row) -> dict[str, Any]:
        """把 SQLite 行转换为内部候选字典。"""

        return {
            "candidate_id": row["candidate_id"],
            "candidate_key": row["candidate_key"],
            "revision": int(row["revision"]),
            "status": row["status"],
            "reason_codes": cls._from_json(row["reason_codes_json"]),
            "content": row["content"],
            "metadata": cls._from_json(row["metadata_json"]),
            "importance": float(row["importance"]),
            "session_id": row["session_id"],
            "persona_id": row["persona_id"],
            "source_window": cls._from_json(row["source_window_json"]),
            "is_group_chat": bool(row["is_group_chat"]),
            "canonical_memory_id": row["canonical_memory_id"],
            "failure_reason": row["failure_reason"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    @classmethod
    def _row_to_action(cls, row: aiosqlite.Row) -> dict[str, Any]:
        """把 SQLite 行转换为低敏动作字典。"""

        return {
            "action_id": row["action_id"],
            "candidate_id": row["candidate_id"],
            "action": row["action"],
            "actor_id": row["actor_id"],
            "payload": cls._from_json(row["payload_json"]),
            "created_at": float(row["created_at"]),
        }

    @staticmethod
    def _to_json(value: Any) -> str:
        """把候选内部基本类型序列化为稳定 JSON。"""

        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _from_json(value: str) -> Any:
        """反序列化受本 Store 控制的 JSON 列。"""

        return json.loads(value)

    @staticmethod
    def _approval_token_digest(approval_token: str) -> str:
        """规范化一次性 repair token，并返回不可逆 SHA-256 摘要。"""

        token = str(approval_token).strip()
        if not token:
            raise ValueError("quarantine_approval_token_required")
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = ["MemoryQuarantineStore"]
