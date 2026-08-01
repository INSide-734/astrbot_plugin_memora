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

        safe_limit = max(1, min(200, int(limit)))
        status_value = status if status in _STATUSES else "pending"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM reconsolidation_candidates
                WHERE status=?
                ORDER BY updated_at DESC, candidate_id DESC LIMIT ?
                """,
                (status_value, safe_limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._row_to_candidate(row) for row in rows]

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
            cursor = await db.execute(
                """
                UPDATE reconsolidation_candidates
                SET status=?, reason_code=?, updated_at=?
                WHERE candidate_id=? AND status=?
                """,
                (new_status, reason_code, now, candidate_id, expected_status),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ReconsolidationCandidateConflictError("candidate_status_changed")
            await db.execute(
                """
                INSERT INTO reconsolidation_actions (
                    action_id, candidate_id, action, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, candidate_id, action, reason_code, now),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT * FROM reconsolidation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise ReconsolidationCandidateNotFoundError(candidate_id)
        return self._row_to_candidate(row)

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
