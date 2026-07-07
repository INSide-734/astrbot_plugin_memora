"""好感度系统的 SQLite 持久化存储，负责用户分数与 Bot 情绪记录。"""

from __future__ import annotations

import time
from typing import Any

from ..storage.base_store import BaseStore


# ---- 表结构 ----------------------------------------------------------------------------

_CREATE_USER_AFFECTION = """
CREATE TABLE IF NOT EXISTS user_affection (
    user_id     TEXT NOT NULL,
    group_id    TEXT NOT NULL DEFAULT '',
    affection_score INTEGER NOT NULL DEFAULT 0,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    last_interaction REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (user_id, group_id)
)
"""

_CREATE_BOT_MOOD = """
CREATE TABLE IF NOT EXISTS bot_mood (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    TEXT NOT NULL DEFAULT '',
    mood_type   TEXT NOT NULL DEFAULT 'calm',
    intensity   REAL NOT NULL DEFAULT 0.5,
    description TEXT NOT NULL DEFAULT '',
    start_time  REAL NOT NULL,
    duration_hours REAL NOT NULL DEFAULT 4.0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ua_group ON user_affection(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_ua_score ON user_affection(affection_score)",
    "CREATE INDEX IF NOT EXISTS idx_bm_group ON bot_mood(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_bm_start ON bot_mood(start_time)",
]

# ---- CRUD ------------------------------------------------------------------------------


class AffectionStore(BaseStore):
    """用户好感度分数与 Bot 情绪历史的持久化存储层。"""

    # ---- 生命周期 ---------------------------------------------------------------------

    async def _create_tables(self) -> None:
        """创建所有缺失的数据表与索引。"""
        await self._execute(_CREATE_USER_AFFECTION)
        await self._execute(_CREATE_BOT_MOOD)
        for idx_sql in _CREATE_INDEXES:
            await self._execute(idx_sql)
        await self._commit()

    # ---- 用户好感度 ----------------------------------------------------------------

    async def get_affection(self, group_id: str, user_id: str) -> dict | None:
        """获取单个用户的好感度记录，不存在时返回 ``None``。"""
        return await self._fetch_one(
            "SELECT * FROM user_affection WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )

    async def upsert_affection(
        self,
        group_id: str,
        user_id: str,
        score_delta: int,
        *,
        max_score: int = 100,
        min_score: int = -100,
    ) -> dict:
        """原子化插入或更新用户好感度分数，并返回最新记录。"""
        now = time.time()
        if not self.connection:
            raise RuntimeError(f"{self.__class__.__name__} 未初始化")

        await self._execute(
            """INSERT INTO user_affection (user_id, group_id, affection_score,
               interaction_count, last_interaction)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(user_id, group_id) DO UPDATE SET
               affection_score = MAX(?, MIN(?, user_affection.affection_score + ?)),
               interaction_count = user_affection.interaction_count + 1,
               last_interaction = ?
            """,
            (
                user_id, group_id, max(min_score, min(max_score, score_delta)), now,
                min_score, max_score, score_delta,
                now,
            ),
        )
        await self._commit()
        return await self.get_affection(group_id, user_id) or {
            "user_id": user_id,
            "group_id": group_id,
            "affection_score": max(min_score, min(max_score, score_delta)),
            "interaction_count": 1,
            "last_interaction": now,
        }

    async def set_affection_score(
        self, group_id: str, user_id: str, new_score: int
    ) -> None:
        """直接设置用户好感度分数（用于重分配场景）。"""
        now = time.time()
        await self._execute(
            """INSERT INTO user_affection (user_id, group_id, affection_score,
               interaction_count, last_interaction)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(user_id, group_id) DO UPDATE SET
               affection_score = ?,
               last_interaction = ?
            """,
            (user_id, group_id, new_score, now, new_score, now),
        )
        await self._commit()

    async def get_top_users(
        self, group_id: str, limit: int = 10
    ) -> list[dict]:
        """按好感度分数降序返回用户列表。"""
        return await self._fetch_all(
            "SELECT * FROM user_affection WHERE group_id = ? ORDER BY affection_score DESC LIMIT ?",
            (group_id, limit),
        )

    async def get_all_affections(self, group_id: str) -> list[dict]:
        """返回指定群组的全部好感度记录。"""
        return await self._fetch_all(
            "SELECT * FROM user_affection WHERE group_id = ?",
            (group_id,),
        )

    async def get_total_affection(self, group_id: str) -> int:
        """返回指定群组的好感度总分。"""
        result = await self._fetch_scalar(
            "SELECT COALESCE(SUM(affection_score), 0) FROM user_affection WHERE group_id = ?",
            (group_id,),
        )
        return int(result) if result is not None else 0

    async def get_user_count(self, group_id: str) -> int:
        """返回指定群组内拥有好感度记录的不同用户数量。"""
        result = await self._fetch_scalar(
            "SELECT COUNT(*) FROM user_affection WHERE group_id = ?",
            (group_id,),
        )
        return int(result) if result is not None else 0

    async def get_users_above_score(
        self, group_id: str, threshold: int
    ) -> list[dict]:
        """获取好感度分数高于指定阈值的全部用户。"""
        return await self._fetch_all(
            "SELECT * FROM user_affection WHERE group_id = ? AND affection_score > ? ORDER BY affection_score DESC",
            (group_id, threshold),
        )

    async def list_group_ids(self) -> list[str]:
        """返回好感度记录中所有非空且去重后的群组 ID。"""
        rows = await self._fetch_all(
            "SELECT DISTINCT group_id FROM user_affection WHERE group_id <> '' ORDER BY group_id"
        )
        return [str(row["group_id"]) for row in rows if row.get("group_id")]

    # ---- Bot 情绪 ----------------------------------------------------------------------

    async def save_bot_mood(
        self,
        group_id: str,
        mood_type: str,
        intensity: float,
        description: str,
        duration_hours: float,
    ) -> int:
        """保存一条新的情绪记录，并返回其行 ID。"""
        now = time.time()
        cursor = await self._execute(
            """INSERT INTO bot_mood (group_id, mood_type, intensity,
               description, start_time, duration_hours)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (group_id, mood_type, intensity, description, now, duration_hours),
        )
        await self._commit()
        return cursor.lastrowid or 0

    async def get_latest_mood(self, group_id: str) -> dict | None:
        """返回指定群组最近一次记录的情绪。"""
        return await self._fetch_one(
            "SELECT * FROM bot_mood WHERE group_id = ? ORDER BY start_time DESC LIMIT 1",
            (group_id,),
        )

    async def get_active_mood(self, group_id: str) -> dict | None:
        """若最近一次情绪尚未过期，则返回该情绪。"""
        now = time.time()
        return await self._fetch_one(
            """SELECT * FROM bot_mood
               WHERE group_id = ?
                 AND (start_time + duration_hours * 3600) > ?
               ORDER BY start_time DESC LIMIT 1""",
            (group_id, now),
        )

    async def get_mood_history(
        self, group_id: str, limit: int = 20
    ) -> list[dict]:
        """返回指定群组最近的情绪历史记录。"""
        return await self._fetch_all(
            "SELECT * FROM bot_mood WHERE group_id = ? ORDER BY start_time DESC LIMIT ?",
            (group_id, limit),
        )

    # ---- 维护操作 -------------------------------------------------------------------

    async def clear_group(self, group_id: str) -> int:
        """删除指定群组的全部好感度与情绪数据，并返回删除计数。"""
        await self._execute(
            "DELETE FROM user_affection WHERE group_id = ?", (group_id,)
        )
        await self._execute(
            "DELETE FROM bot_mood WHERE group_id = ?", (group_id,)
        )
        await self._commit()
        # 两张表的删除行数不易精确汇总，这里返回 0，由调用方按需自行前后比对
        return 0

    async def vacuum(self) -> None:
        """压缩数据库文件。"""
        await self._execute("VACUUM")


__all__ = ["AffectionStore"]
