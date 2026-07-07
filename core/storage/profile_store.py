"""基于 SQLite 的用户画像与标签存储。"""

from __future__ import annotations

import time
from typing import Any

from ..models.user_profile import TagCategory, UserPreferences, UserProfile, UserTag
from .base import BaseStore

_CREATE_PROFILES = """
CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE NOT NULL,
    display_name TEXT DEFAULT '',
    preferences_json TEXT DEFAULT '{}',
    total_messages INTEGER DEFAULT 0,
    total_sessions INTEGER DEFAULT 0,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""

_CREATE_TAGS = """
CREATE TABLE IF NOT EXISTS user_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'custom',
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    source TEXT DEFAULT 'auto',
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    occurrence_count INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES user_profiles(user_id),
    UNIQUE(user_id, category, value)
)
"""

_CREATE_TAG_INDEX = """
CREATE INDEX IF NOT EXISTS idx_user_tags_user_id ON user_tags(user_id)
"""


class ProfileStore(BaseStore):
    """SQLite 中用户画像的 CRUD 操作。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_table(self) -> None:
        async with self._connect() as db:
            await db.execute(_CREATE_PROFILES)
            await db.execute(_CREATE_TAGS)
            await db.execute(_CREATE_TAG_INDEX)
            await db.commit()

    # ---- profile CRUD --------------------------------------------

    async def get_profile(self, user_id: str) -> UserProfile | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            profile = self._row_to_profile(row)
        profile.tags = await self._get_tags(user_id)
        return profile

    async def get_or_create_profile(self, user_id: str) -> UserProfile:
        profile = await self.get_profile(user_id)
        if profile is not None:
            return profile
        return await self.create_profile(user_id)

    async def create_profile(self, user_id: str, display_name: str = "") -> UserProfile:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO user_profiles
                   (user_id, display_name, first_seen_at, last_seen_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, display_name, now, now, now, now),
            )
            await db.commit()
        return UserProfile(
            user_id=user_id,
            display_name=display_name,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )

    async def update_profile(self, profile: UserProfile) -> None:
        profile.updated_at = time.time()
        async with self._connect() as db:
            await db.execute(
                """UPDATE user_profiles SET
                   display_name = ?, preferences_json = ?,
                   total_messages = ?, total_sessions = ?,
                   last_seen_at = ?, updated_at = ?
                   WHERE user_id = ?""",
                (
                    profile.display_name,
                    self._to_json(profile.preferences.to_dict()),
                    profile.total_messages,
                    profile.total_sessions,
                    profile.last_seen_at,
                    profile.updated_at,
                    profile.user_id,
                ),
            )
            await db.commit()

    async def touch(self, user_id: str) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "UPDATE user_profiles SET last_seen_at = ? WHERE user_id = ?",
                (now, user_id),
            )
            await db.commit()

    async def list_profiles(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[UserProfile], int]:
        async with self._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM user_profiles")
            total = (await cursor.fetchone())[0]
            cursor = await db.execute(
                "SELECT * FROM user_profiles ORDER BY last_seen_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        profiles: list[UserProfile] = []
        for row in rows:
            profile = self._row_to_profile(row)
            profile.tags = await self._get_tags(profile.user_id)
            profiles.append(profile)
        return profiles, total

    async def delete_profile(self, user_id: str) -> bool:
        async with self._connect() as db:
            await db.execute("DELETE FROM user_tags WHERE user_id = ?", (user_id,))
            cursor = await db.execute(
                "DELETE FROM user_profiles WHERE user_id = ?", (user_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ---- tag CRUD ------------------------------------------------

    async def _get_tags(self, user_id: str) -> list[UserTag]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM user_tags WHERE user_id = ? ORDER BY confidence DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_tag(row) for row in rows]

    async def add_tag(self, user_id: str, tag: UserTag) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT id, occurrence_count, confidence FROM user_tags
                   WHERE user_id = ? AND category = ? AND value = ?""",
                (user_id, tag.category.value, tag.value),
            )
            existing = await cursor.fetchone()
            if existing:
                new_count = existing[1] + 1
                new_conf = max(existing[2], tag.confidence)
                await db.execute(
                    """UPDATE user_tags SET confidence = ?, last_seen_at = ?,
                       occurrence_count = ? WHERE id = ?""",
                    (new_conf, tag.last_seen_at, new_count, existing[0]),
                )
                await db.commit()
                return False
            else:
                await db.execute(
                    """INSERT INTO user_tags
                       (user_id, category, value, confidence, source,
                        created_at, last_seen_at, occurrence_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        tag.category.value,
                        tag.value,
                        tag.confidence,
                        tag.source,
                        tag.created_at,
                        tag.last_seen_at,
                        tag.occurrence_count,
                    ),
                )
                await db.commit()
                return True

    async def remove_tag(self, user_id: str, category: str, value: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM user_tags WHERE user_id = ? AND category = ? AND value = ?",
                (user_id, category, value),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ---- row conversion ------------------------------------------

    @staticmethod
    def _row_to_profile(row: Any) -> UserProfile:
        prefs = BaseStore._from_json(row[3]) if len(row) > 3 else {}
        return UserProfile(
            user_id=str(row[1]),
            display_name=str(row[2] or ""),
            preferences=UserPreferences.from_dict(prefs),
            total_messages=int(row[4] or 0) if len(row) > 4 else 0,
            total_sessions=int(row[5] or 0) if len(row) > 5 else 0,
            first_seen_at=float(row[6] or 0) if len(row) > 6 else 0.0,
            last_seen_at=float(row[7] or 0) if len(row) > 7 else 0.0,
            created_at=float(row[8] or 0) if len(row) > 8 else 0.0,
            updated_at=float(row[9] or 0) if len(row) > 9 else 0.0,
        )

    @staticmethod
    def _row_to_tag(row: Any) -> UserTag:
        return UserTag(
            category=TagCategory(str(row[2] or "custom")),
            value=str(row[3] or ""),
            confidence=float(row[4] or 0.5),
            source=str(row[5] or "auto"),
            created_at=float(row[6] or 0),
            last_seen_at=float(row[7] or 0),
            occurrence_count=int(row[8] or 1),
        )


__all__ = ["ProfileStore"]
