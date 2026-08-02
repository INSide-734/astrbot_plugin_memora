"""再巩固候选的 SQLite 持久化与低敏动作审计。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_STATUSES = frozenset({"pending", "approved", "rejected", "failed", "rolled_back"})
_ACTIONS = frozenset({"stage", "apply", "reject", "rollback"})


class ReconsolidationCandidateNotFoundError(LookupError):
    """目标再巩固候选不存在。"""


class ReconsolidationCandidateConflictError(RuntimeError):
    """候选状态或 revision 已变化，调用方必须重新读取。"""


class ReconsolidationStore:
    """保存再巩固候选、来源 revision 与旧正文，支持 CAS 状态迁移。"""

    def __init__(self, db_path: str | Path) -> None:
        """初始化 Store，但不创建数据库文件。"""

        self.db_path = Path(db_path)

    async def initialize(self) -> None:
        """创建候选表、动作审计表与稳定索引。"""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reconsolidation_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    memory_id INTEGER NOT NULL,
                    source_revision TEXT NOT NULL,
                    old_content TEXT NOT NULL,
                    old_metadata TEXT NOT NULL,
                    proposed_content TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reconsolidation_candidates_status
                ON reconsolidation_candidates(status, updated_at, candidate_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reconsolidation_candidates_memory
                ON reconsolidation_candidates(memory_id, status)
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reconsolidation_actions (
                    action_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reconsolidation_actions_candidate
                ON reconsolidation_actions(candidate_id, created_at, action_id)
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reconsolidation_rollback_ops (
                    candidate_id TEXT PRIMARY KEY,
                    expected_revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reconsolidation_apply_ops (
                    candidate_id TEXT PRIMARY KEY,
                    expected_revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reconsolidation_rollback_ops_status
                ON reconsolidation_rollback_ops(status, updated_at, candidate_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reconsolidation_apply_ops_status
                ON reconsolidation_apply_ops(status, updated_at, candidate_id)
                """
            )
            # 旧版本没有唯一约束，先合并历史重复 pending 行，再建立约束。
            await db.execute(
                """
                DELETE FROM reconsolidation_actions
                WHERE candidate_id IN (
                    SELECT candidate_id
                    FROM reconsolidation_candidates
                    WHERE status='pending'
                      AND rowid NOT IN (
                          SELECT MAX(rowid)
                          FROM reconsolidation_candidates
                          WHERE status='pending'
                          GROUP BY memory_id, source_revision, proposed_content
                      )
                )
                """
            )
            await db.execute(
                """
                DELETE FROM reconsolidation_candidates
                WHERE status='pending'
                  AND rowid NOT IN (
                      SELECT MAX(rowid)
                      FROM reconsolidation_candidates
                      WHERE status='pending'
                      GROUP BY memory_id, source_revision, proposed_content
                  )
                """
            )
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_reconsolidation_candidates_pending_key
                ON reconsolidation_candidates(
                    memory_id, source_revision, proposed_content
                )
                WHERE status='pending'
                """
            )
            await db.commit()

    async def stage_candidate(
        self,
        *,
        memory_id: int,
        source_revision: str,
        old_content: str,
        old_metadata: dict[str, Any],
        proposed_content: str,
        change_summary: str,
        evidence_type: str,
    ) -> dict[str, Any]:
        """幂等插入一条 pending 候选；同 revision 同输出重复提案复用旧行。"""

        now = self._now()
        candidate_id = uuid.uuid4().hex
        metadata_text = json.dumps(old_metadata, ensure_ascii=False)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT * FROM reconsolidation_candidates
                WHERE memory_id=? AND source_revision=? AND proposed_content=?
                  AND status='pending'
                ORDER BY created_at DESC, candidate_id DESC LIMIT 1
                """,
                (memory_id, source_revision, proposed_content),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                existing = self._row_to_candidate(row)
                await db.commit()
                return existing
            await db.execute(
                """
                INSERT INTO reconsolidation_candidates (
                    candidate_id, memory_id, source_revision, old_content,
                    old_metadata, proposed_content, change_summary,
                    evidence_type, status, reason_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'proposed', ?, ?)
                """,
                (
                    candidate_id,
                    memory_id,
                    source_revision,
                    old_content,
                    metadata_text,
                    proposed_content,
                    change_summary,
                    evidence_type,
                    now,
                    now,
                ),
            )
            await db.execute(
                """
                INSERT INTO reconsolidation_actions (
                    action_id, candidate_id, action, reason_code, created_at
                ) VALUES (?, ?, 'stage', 'proposed', ?)
                """,
                (uuid.uuid4().hex, candidate_id, now),
            )
            await db.commit()
        return {
            "candidate_id": candidate_id,
            "memory_id": memory_id,
            "source_revision": source_revision,
            "old_content": old_content,
            "old_metadata": old_metadata,
            "proposed_content": proposed_content,
            "change_summary": change_summary,
            "evidence_type": evidence_type,
            "status": "pending",
            "reason_code": "proposed",
            "created_at": now,
            "updated_at": now,
        }

    async def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """按候选 ID 读取完整候选。"""

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return self._row_to_candidate(row) if row is not None else None

    async def list_candidates(
        self,
        status: str = "pending",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按状态列出候选，限制条数并保持稳定排序。"""

        status_value = status if status in _STATUSES else "pending"
        page = await self.list_candidates_page(
            status=status_value,
            offset=0,
            limit=limit,
        )
        return page["items"]

    async def list_candidates_page(
        self,
        *,
        status: str | None = "pending",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """返回真实总数和稳定排序的候选分页。

        Args:
            status: 固定候选状态；传入 ``None`` 时查询全部状态。
            offset: 从零开始的服务端偏移量。
            limit: 单页上限，Store 内部最多返回 200 条。

        Returns:
            包含 ``items``、``total``、``offset`` 和 ``limit`` 的分页。

        Raises:
            ValueError: 状态、偏移量或页大小无效。
        """

        if status is not None and status not in _STATUSES:
            raise ValueError("再巩固候选状态无效")
        if isinstance(offset, bool) or isinstance(limit, bool):
            raise ValueError("再巩固候选分页参数无效")
        try:
            safe_offset = int(offset)
            safe_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("再巩固候选分页参数无效") from exc
        if safe_offset < 0 or safe_limit < 1:
            raise ValueError("再巩固候选分页参数无效")
        safe_limit = min(200, safe_limit)
        where_sql = "WHERE status=?" if status is not None else ""
        params: tuple[Any, ...] = (status,) if status is not None else ()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            count_cursor = await db.execute(
                f"SELECT COUNT(*) FROM reconsolidation_candidates {where_sql}",
                params,
            )
            count_row = await count_cursor.fetchone()
            await count_cursor.close()
            cursor = await db.execute(
                f"""
                SELECT * FROM reconsolidation_candidates
                {where_sql}
                ORDER BY updated_at DESC, candidate_id DESC LIMIT ?
                OFFSET ?
                """,
                (*params, safe_limit, safe_offset),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            await db.commit()
        return {
            "items": [self._row_to_candidate(row) for row in rows],
            "total": int(count_row[0]) if count_row is not None else 0,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    async def transition(
        self,
        candidate_id: str,
        *,
        expected_status: str,
        new_status: str,
        reason_code: str,
        action: str,
    ) -> dict[str, Any]:
        """按预期状态执行 CAS 迁移并追加动作审计。"""

        if new_status not in _STATUSES or action not in _ACTIONS:
            raise ReconsolidationCandidateConflictError("invalid_transition")
        now = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            if expected_status == "pending":
                cursor = await db.execute(
                    """
                    SELECT 1 FROM reconsolidation_apply_ops
                    WHERE candidate_id=? AND status IN ('pending', 'blocked')
                    """,
                    (candidate_id,),
                )
                apply_intent = await cursor.fetchone()
                await cursor.close()
                if apply_intent is not None:
                    raise ReconsolidationCandidateConflictError("apply_in_progress")
            cursor = await db.execute(
                """
                UPDATE reconsolidation_candidates
                SET status=?, reason_code=?, updated_at=?
                WHERE candidate_id=? AND status=?
                """,
                (new_status, reason_code, now, candidate_id, expected_status),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed == 0:
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            await db.execute(
                """
                INSERT INTO reconsolidation_actions (
                    action_id, candidate_id, action, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, candidate_id, action, reason_code, now),
            )
            cursor = await db.execute(
                "SELECT * FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise ReconsolidationCandidateNotFoundError(candidate_id)
            await db.commit()
        return self._row_to_candidate(row)

    async def begin_rollback(
        self,
        candidate_id: str,
        *,
        expected_revision: str,
    ) -> dict[str, Any]:
        """在 canonical 更新前持久化回滚意图。

        Args:
            candidate_id: 已批准候选 ID。
            expected_revision: 开始回滚时读取到的 canonical revision。

        Returns:
            只含候选 ID、revision、状态与稳定 reason code 的操作摘要。

        Raises:
            ReconsolidationCandidateConflictError: 候选不是 approved 或已有操作。
        """

        now = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT status FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            candidate = await cursor.fetchone()
            await cursor.close()
            if candidate is None:
                raise ReconsolidationCandidateNotFoundError(candidate_id)
            if str(candidate["status"]) != "approved":
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            cursor = await db.execute(
                "SELECT 1 FROM reconsolidation_rollback_ops WHERE candidate_id=?",
                (candidate_id,),
            )
            exists = await cursor.fetchone()
            await cursor.close()
            if exists is not None:
                raise ReconsolidationCandidateConflictError("rollback_in_progress")
            await db.execute(
                """
                INSERT INTO reconsolidation_rollback_ops (
                    candidate_id, expected_revision, status, reason_code,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', 'rollback_started', ?, ?)
                """,
                (candidate_id, str(expected_revision), now, now),
            )
            await db.commit()
        return {
            "candidate_id": candidate_id,
            "expected_revision": str(expected_revision),
            "status": "pending",
            "reason_code": "rollback_started",
        }

    async def list_incomplete_rollbacks(self) -> list[dict[str, Any]]:
        """按创建顺序读取待恢复的回滚操作。"""

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT candidate_id, expected_revision, status, reason_code,
                       created_at, updated_at
                FROM reconsolidation_rollback_ops
                WHERE status='pending'
                ORDER BY created_at ASC, candidate_id ASC
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    async def cancel_rollback(self, candidate_id: str) -> None:
        """在 canonical 明确未提交时删除回滚意图，允许调用方重试。"""

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute(
                "DELETE FROM reconsolidation_rollback_ops WHERE candidate_id=?",
                (candidate_id,),
            )
            await db.commit()

    async def mark_rollback_blocked(
        self,
        candidate_id: str,
        *,
        reason_code: str,
    ) -> None:
        """把无法安全重放的回滚标记为 blocked，避免覆盖后续编辑。"""

        now = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 5000")
            cursor = await db.execute(
                """
                UPDATE reconsolidation_rollback_ops
                SET status='blocked', reason_code=?, updated_at=?
                WHERE candidate_id=? AND status='pending'
                """,
                (str(reason_code), now, candidate_id),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed == 0:
                raise ReconsolidationCandidateConflictError(
                    "rollback_operation_changed"
                )
            await db.commit()

    async def complete_rollback(self, candidate_id: str) -> dict[str, Any]:
        """原子完成候选状态、动作审计和回滚操作清理。"""

        now = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE reconsolidation_candidates
                SET status='rolled_back', reason_code='restored', updated_at=?
                WHERE candidate_id=? AND status='approved'
                """,
                (now, candidate_id),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed == 0:
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            cursor = await db.execute(
                """
                DELETE FROM reconsolidation_rollback_ops
                WHERE candidate_id=? AND status='pending'
                """,
                (candidate_id,),
            )
            deleted = cursor.rowcount
            await cursor.close()
            if deleted == 0:
                raise ReconsolidationCandidateConflictError(
                    "rollback_operation_changed"
                )
            await db.execute(
                """
                INSERT INTO reconsolidation_actions (
                    action_id, candidate_id, action, reason_code, created_at
                ) VALUES (?, ?, 'rollback', 'restored', ?)
                """,
                (uuid.uuid4().hex, candidate_id, now),
            )
            cursor = await db.execute(
                "SELECT * FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise ReconsolidationCandidateNotFoundError(candidate_id)
            await db.commit()
        return self._row_to_candidate(row)

    async def begin_apply(
        self,
        candidate_id: str,
        *,
        expected_revision: str,
    ) -> dict[str, Any]:
        """在 canonical CAS 前原子声明 apply intent，并返回候选副本。"""

        now = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT * FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            candidate = await cursor.fetchone()
            await cursor.close()
            if candidate is None:
                raise ReconsolidationCandidateNotFoundError(candidate_id)
            if str(candidate["status"]) != "pending":
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            cursor = await db.execute(
                "SELECT 1 FROM reconsolidation_apply_ops WHERE candidate_id=?",
                (candidate_id,),
            )
            exists = await cursor.fetchone()
            await cursor.close()
            if exists is not None:
                raise ReconsolidationCandidateConflictError("apply_in_progress")
            if str(candidate["source_revision"]) != str(expected_revision):
                raise ReconsolidationCandidateConflictError("source_revision_mismatch")
            await db.execute(
                """
                INSERT INTO reconsolidation_apply_ops (
                    candidate_id, expected_revision, status, reason_code,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', 'apply_started', ?, ?)
                """,
                (candidate_id, str(expected_revision), now, now),
            )
            await db.commit()
        return self._row_to_candidate(candidate)

    async def complete_apply(
        self,
        candidate_id: str,
        *,
        applied: bool,
        reason_code: str,
    ) -> dict[str, Any]:
        """原子收口 apply intent、候选状态和动作审计。"""

        now = self._now()
        new_status = "approved" if applied else "rejected"
        action = "apply" if applied else "reject"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT 1 FROM reconsolidation_apply_ops
                WHERE candidate_id=? AND status='pending'
                """,
                (candidate_id,),
            )
            intent = await cursor.fetchone()
            await cursor.close()
            if intent is None:
                raise ReconsolidationCandidateConflictError("apply_intent_changed")
            cursor = await db.execute(
                """
                UPDATE reconsolidation_candidates
                SET status=?, reason_code=?, updated_at=?
                WHERE candidate_id=? AND status='pending'
                """,
                (new_status, reason_code, now, candidate_id),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed == 0:
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            await db.execute(
                """
                DELETE FROM reconsolidation_apply_ops
                WHERE candidate_id=? AND status='pending'
                """,
                (candidate_id,),
            )
            await db.execute(
                """
                INSERT INTO reconsolidation_actions (
                    action_id, candidate_id, action, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, candidate_id, action, reason_code, now),
            )
            cursor = await db.execute(
                "SELECT * FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise ReconsolidationCandidateNotFoundError(candidate_id)
            await db.commit()
        return self._row_to_candidate(row)

    async def list_incomplete_applies(self) -> list[dict[str, Any]]:
        """按创建顺序读取尚未收口的 apply intent。"""

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT candidate_id, expected_revision, status, reason_code,
                       created_at, updated_at
                FROM reconsolidation_apply_ops
                WHERE status='pending'
                ORDER BY created_at ASC, candidate_id ASC
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    async def mark_apply_blocked(
        self,
        candidate_id: str,
        *,
        reason_code: str,
    ) -> None:
        """将无法安全恢复的 apply 标记为失败并保留阻断 intent。"""

        now = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE reconsolidation_candidates
                SET status='failed', reason_code=?, updated_at=?
                WHERE candidate_id=? AND status='pending'
                """,
                (reason_code, now, candidate_id),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed == 0:
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            cursor = await db.execute(
                """
                UPDATE reconsolidation_apply_ops
                SET status='blocked', reason_code=?, updated_at=?
                WHERE candidate_id=? AND status='pending'
                """,
                (reason_code, now, candidate_id),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed == 0:
                raise ReconsolidationCandidateConflictError("apply_intent_changed")
            await db.execute(
                """
                INSERT INTO reconsolidation_actions (
                    action_id, candidate_id, action, reason_code, created_at
                ) VALUES (?, ?, 'apply', ?, ?)
                """,
                (uuid.uuid4().hex, candidate_id, reason_code, now),
            )
            await db.commit()

    async def list_actions(self, candidate_id: str) -> list[dict[str, Any]]:
        """按时间顺序读取候选动作审计。"""

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT action_id, candidate_id, action, reason_code, created_at
                FROM reconsolidation_actions
                WHERE candidate_id=?
                ORDER BY created_at ASC, action_id ASC
                """,
                (candidate_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_candidate(row: aiosqlite.Row) -> dict[str, Any]:
        """把数据库行转换为可序列化候选字典。"""

        return {
            "candidate_id": str(row["candidate_id"]),
            "memory_id": int(row["memory_id"]),
            "source_revision": str(row["source_revision"]),
            "old_content": str(row["old_content"]),
            "old_metadata": _loads_metadata(row["old_metadata"]),
            "proposed_content": str(row["proposed_content"]),
            "change_summary": str(row["change_summary"]),
            "evidence_type": str(row["evidence_type"]),
            "status": str(row["status"]),
            "reason_code": str(row["reason_code"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _now() -> str:
        """返回当前 UTC ISO 时间。"""

        return datetime.now(timezone.utc).isoformat()


def _loads_metadata(value: Any) -> dict[str, Any]:
    """解析旧 metadata JSON，非法时返回空字典。"""

    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "ReconsolidationCandidateConflictError",
    "ReconsolidationCandidateNotFoundError",
    "ReconsolidationStore",
]
