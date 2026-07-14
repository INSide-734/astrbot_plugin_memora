"""基于 SQLite 的用户画像与标签存储。"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

from ..base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    compute_entity_revision,
)
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

    @staticmethod
    async def _rollback_safely(db: aiosqlite.Connection) -> None:
        """尽力回滚事务，但绝不以清理错误替换原始失败。"""
        for _ in range(2):
            try:
                await db.rollback()
                return
            except BaseException:
                try:
                    if not db.in_transaction:
                        return
                except BaseException:
                    pass
        try:
            if db.in_transaction:
                await db.execute("ROLLBACK")
        except BaseException:
            pass

    async def init_table(self) -> None:
        async with self._connect() as db:
            await db.execute(_CREATE_PROFILES)
            await db.execute(_CREATE_TAGS)
            await db.execute(_CREATE_TAG_INDEX)
            await db.commit()

    # ---- profile CRUD --------------------------------------------

    async def get_profile(self, user_id: str) -> UserProfile | None:
        async with self._connect() as db:
            return await self._get_profile_with_db(db, user_id)

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

    async def create_profile_strict(
        self,
        user_id: str,
        display_name: str = "",
        preferences: UserPreferences | None = None,
        tags: list[UserTag] | None = None,
    ) -> UserProfile:
        """在单个事务中严格创建画像及其管理员标签。"""
        now = time.time()
        normalized_preferences = preferences or UserPreferences()
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT 1 FROM user_profiles WHERE user_id = ?", (user_id,)
                )
                if await cursor.fetchone() is not None:
                    raise EntityAlreadyExistsError("用户画像已存在")
                await db.execute(
                    """INSERT INTO user_profiles
                       (user_id, display_name, preferences_json,
                        total_messages, total_sessions, first_seen_at,
                        last_seen_at, created_at, updated_at)
                       VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?)""",
                    (
                        user_id,
                        display_name,
                        self._to_json(normalized_preferences.to_dict()),
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                await self._replace_tags_with_db(db, user_id, tags or [], now)
                created = await self._get_profile_with_db(db, user_id)
                if created is None:
                    raise EntityNotFoundError("画像不存在")
                await db.commit()
                return created
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def replace_editable_fields(
        self,
        user_id: str,
        *,
        display_name: str,
        preferences: UserPreferences,
        tags: list[UserTag],
        expected_revision: str,
    ) -> UserProfile:
        """按修订版本原子替换画像的全部管理员可写字段。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    raise EntityNotFoundError("画像不存在")
                current_revision = compute_entity_revision(current.to_dict())
                if current_revision != expected_revision:
                    raise EditConflictError(current.to_dict(), current_revision)

                now = time.time()
                await db.execute(
                    """UPDATE user_profiles
                       SET display_name = ?, preferences_json = ?, updated_at = ?
                       WHERE user_id = ?""",
                    (
                        display_name,
                        self._to_json(preferences.to_dict()),
                        now,
                        user_id,
                    ),
                )
                await self._replace_tags_with_db(db, user_id, tags, now)
                updated = await self._get_profile_with_db(db, user_id)
                if updated is None:
                    raise EntityNotFoundError("画像不存在")
                await db.commit()
                return updated
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def delete_profile_if_revision(
        self,
        user_id: str,
        *,
        expected_revision: str,
    ) -> bool:
        """按修订版本在单个事务中删除画像及其标签。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    raise EntityNotFoundError("画像不存在")
                current_revision = compute_entity_revision(current.to_dict())
                if current_revision != expected_revision:
                    raise EditConflictError(current.to_dict(), current_revision)

                await db.execute(
                    "DELETE FROM user_tags WHERE user_id = ?", (user_id,)
                )
                cursor = await db.execute(
                    "DELETE FROM user_profiles WHERE user_id = ?", (user_id,)
                )
                await db.commit()
                return cursor.rowcount > 0
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def update_profile_fields_atomic(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        preferences: UserPreferences | None = None,
    ) -> UserProfile | None:
        """仅更新显式提供的画像字段，不写回旧统计快照。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    await db.commit()
                    return None
                now = time.time()
                if display_name is not None and preferences is not None:
                    await db.execute(
                        """UPDATE user_profiles
                           SET display_name = ?, preferences_json = ?, updated_at = ?
                           WHERE user_id = ?""",
                        (
                            display_name,
                            self._to_json(preferences.to_dict()),
                            now,
                            user_id,
                        ),
                    )
                elif display_name is not None:
                    await db.execute(
                        """UPDATE user_profiles
                           SET display_name = ?, updated_at = ?
                           WHERE user_id = ?""",
                        (display_name, now, user_id),
                    )
                elif preferences is not None:
                    await db.execute(
                        """UPDATE user_profiles
                           SET preferences_json = ?, updated_at = ?
                           WHERE user_id = ?""",
                        (
                            self._to_json(preferences.to_dict()),
                            now,
                            user_id,
                        ),
                    )
                updated = await self._get_profile_with_db(db, user_id)
                await db.commit()
                return updated
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def upsert_tags_atomic(
        self,
        user_id: str,
        tags: list[UserTag],
    ) -> tuple[UserProfile | None, int]:
        """在一个写事务中合并自动标签，不修改画像行。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    await db.commit()
                    return None, 0
                new_count = 0
                for tag in tags:
                    if await self._upsert_tag_with_db(db, user_id, tag):
                        new_count += 1
                updated = await self._get_profile_with_db(db, user_id)
                await db.commit()
                return updated, new_count
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def record_message_atomic(
        self,
        user_id: str,
        *,
        message_length: int = 0,
    ) -> UserProfile | None:
        """基于最新持久化值递增消息计数并更新平均回复长度。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    await db.commit()
                    return None
                preferences = current.preferences
                if message_length > 0 and preferences.avg_reply_length > 0:
                    preferences.avg_reply_length = int(
                        0.9 * preferences.avg_reply_length + 0.1 * message_length
                    )
                else:
                    preferences.avg_reply_length = message_length
                now = time.time()
                await db.execute(
                    """UPDATE user_profiles
                       SET preferences_json = ?, total_messages = ?,
                           last_seen_at = ?, updated_at = ?
                       WHERE user_id = ?""",
                    (
                        self._to_json(preferences.to_dict()),
                        current.total_messages + 1,
                        now,
                        now,
                        user_id,
                    ),
                )
                updated = await self._get_profile_with_db(db, user_id)
                await db.commit()
                return updated
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def merge_preferences_atomic(
        self,
        user_id: str,
        preferences_update: dict[str, Any],
    ) -> UserProfile | None:
        """把显式学习结果合并到最新偏好，而非覆盖旧快照。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    await db.commit()
                    return None
                preferences = current.preferences
                if "reply_style" in preferences_update:
                    preferences.reply_style = str(preferences_update["reply_style"])
                if "preferred_topics" in preferences_update:
                    for topic in preferences_update["preferred_topics"] or []:
                        if topic not in preferences.preferred_topics:
                            preferences.preferred_topics.append(topic)
                if "avoided_topics" in preferences_update:
                    for topic in preferences_update["avoided_topics"] or []:
                        if topic not in preferences.avoided_topics:
                            preferences.avoided_topics.append(topic)
                now = time.time()
                await db.execute(
                    """UPDATE user_profiles
                       SET preferences_json = ?, updated_at = ?
                       WHERE user_id = ?""",
                    (
                        self._to_json(preferences.to_dict()),
                        now,
                        user_id,
                    ),
                )
                updated = await self._get_profile_with_db(db, user_id)
                await db.commit()
                return updated
            except BaseException:
                await self._rollback_safely(db)
                raise

    async def decay_and_clean_tags_atomic(
        self,
        user_id: str,
        *,
        reference_time: float | None = None,
        min_confidence: float = 0.1,
    ) -> int:
        """在写锁内重读、衰减并清理标签，不覆盖画像字段。"""
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await self._get_profile_with_db(db, user_id)
                if current is None:
                    await db.commit()
                    return 0
                current.decay_tags(reference_time)
                stale = [
                    tag for tag in current.tags if tag.confidence < min_confidence
                ]
                retained = [
                    tag for tag in current.tags if tag.confidence >= min_confidence
                ]
                for tag in stale:
                    await db.execute(
                        """DELETE FROM user_tags
                           WHERE user_id = ? AND category = ? AND value = ?""",
                        (user_id, tag.category.value, tag.value),
                    )
                for tag in retained:
                    await db.execute(
                        """UPDATE user_tags SET confidence = ?
                           WHERE user_id = ? AND category = ? AND value = ?""",
                        (
                            tag.confidence,
                            user_id,
                            tag.category.value,
                            tag.value,
                        ),
                    )
                await db.commit()
                return len(stale)
            except BaseException:
                await self._rollback_safely(db)
                raise

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
                """SELECT * FROM user_tags WHERE user_id = ?
                   ORDER BY confidence DESC, category ASC, value ASC""",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_tag(row) for row in rows]

    async def _get_profile_with_db(
        self, db: aiosqlite.Connection, user_id: str
    ) -> UserProfile | None:
        """使用调用方连接加载画像及其标签。"""
        cursor = await db.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        profile = self._row_to_profile(row)
        cursor = await db.execute(
            """SELECT * FROM user_tags WHERE user_id = ?
               ORDER BY confidence DESC, category ASC, value ASC""",
            (user_id,),
        )
        profile.tags = [self._row_to_tag(tag_row) for tag_row in await cursor.fetchall()]
        return profile

    async def _replace_tags_with_db(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        tags: list[UserTag],
        now: float,
    ) -> None:
        """使用调用方连接替换管理员标签，并强制服务端元数据。"""
        await db.execute("DELETE FROM user_tags WHERE user_id = ?", (user_id,))
        for tag in tags:
            await db.execute(
                """INSERT INTO user_tags
                   (user_id, category, value, confidence, source,
                    created_at, last_seen_at, occurrence_count)
                   VALUES (?, ?, ?, ?, 'manual', ?, ?, 1)""",
                (
                    user_id,
                    tag.category.value,
                    tag.value,
                    tag.confidence,
                    now,
                    now,
                ),
            )

    async def _upsert_tag_with_db(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        tag: UserTag,
    ) -> bool:
        cursor = await db.execute(
            """SELECT id, occurrence_count, confidence FROM user_tags
               WHERE user_id = ? AND category = ? AND value = ?""",
            (user_id, tag.category.value, tag.value),
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                """UPDATE user_tags SET confidence = ?, last_seen_at = ?,
                   occurrence_count = ? WHERE id = ?""",
                (
                    max(existing[2], tag.confidence),
                    tag.last_seen_at,
                    existing[1] + 1,
                    existing[0],
                ),
            )
            return False
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
        return True

    async def add_tag(self, user_id: str, tag: UserTag) -> bool:
        async with self._connect() as db:
            inserted = await self._upsert_tag_with_db(db, user_id, tag)
            await db.commit()
            return inserted

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
