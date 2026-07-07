"""用户画像生命周期 — 标签积累、偏好学习、衰减。"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from ..models.user_profile import UserPreferences, UserProfile, UserTag
from ..storage.profile_store import ProfileStore


class ProfileManager:
    """管理用户画像: 创建、更新、标签积累、衰减。"""

    def __init__(self, profile_store: ProfileStore) -> None:
        self._store = profile_store

    async def ensure_profile(self, user_id: str) -> UserProfile:
        return await self._store.get_or_create_profile(user_id)

    async def get_profile(self, user_id: str) -> UserProfile | None:
        return await self._store.get_profile(user_id)

    async def touch(self, user_id: str) -> None:
        await self._store.touch(user_id)

    async def update_profile_fields(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        preferences: dict[str, Any] | UserPreferences | None = None,
    ) -> UserProfile | None:
        profile = await self.get_profile(user_id)
        if profile is None:
            return None
        if display_name is not None:
            profile.display_name = display_name.strip()
        if preferences is not None:
            if isinstance(preferences, UserPreferences):
                profile.preferences = preferences
            else:
                profile.preferences = UserPreferences.from_dict(preferences)
        await self._store.update_profile(profile)
        return await self.get_profile(user_id) or profile

    async def delete_profile(self, user_id: str) -> bool:
        return await self._store.delete_profile(user_id)

    async def add_tag(self, user_id: str, tag: UserTag) -> UserProfile | None:
        profile = await self.get_profile(user_id)
        if profile is None:
            return None
        await self._store.add_tag(user_id, tag)
        return await self.get_profile(user_id)

    async def remove_tag(
        self, user_id: str, category: str, value: str
    ) -> UserProfile | None:
        profile = await self.get_profile(user_id)
        if profile is None:
            return None
        await self._store.remove_tag(user_id, category, value)
        return await self.get_profile(user_id)

    async def ingest_tags(self, user_id: str, tags: list[UserTag]) -> UserProfile:
        profile = await self.ensure_profile(user_id)
        new_count = 0
        for tag in tags:
            if profile.upsert_tag(tag):
                new_count += 1
            await self._store.add_tag(user_id, tag)
        if new_count:
            logger.debug(
                f"[Profile] {user_id}: +{new_count} new tags, total={len(profile.tags)}"
            )
        await self._store.update_profile(profile)
        return profile

    async def get_tag_weights(self, user_id: str) -> dict[str, float]:
        profile = await self.get_profile(user_id)
        if profile is None:
            return {}
        return profile.get_weight_vector()

    async def decay_and_clean(self, user_id: str) -> int:
        profile = await self.get_profile(user_id)
        if profile is None:
            return 0
        profile.decay_tags()
        removed = profile.remove_stale_tags(min_confidence=0.1)
        if removed:
            await self._store.update_profile(profile)
            logger.debug(f"[Profile] {user_id}: removed {removed} stale tags")
        return removed

    async def decay_and_clean_all(self, batch_size: int = 100) -> dict[str, int]:
        """在有限批次中对所有画像应用标签衰减。"""
        offset = 0
        scanned = 0
        removed_total = 0
        failed = 0

        while True:
            profiles, _total = await self.list_profiles(limit=batch_size, offset=offset)
            if not profiles:
                break

            for profile in profiles:
                scanned += 1
                try:
                    removed_total += await self.decay_and_clean(profile.user_id)
                except Exception as e:
                    failed += 1
                    logger.warning(
                        f"[Profile] {profile.user_id}: decay_and_clean failed: {e}"
                    )

            if len(profiles) < batch_size:
                break
            offset += len(profiles)

        return {"scanned": scanned, "removed": removed_total, "failed": failed}

    async def record_message(self, user_id: str, message_length: int = 0) -> None:
        profile = await self.ensure_profile(user_id)
        profile.total_messages += 1
        profile.last_seen_at = time.time()
        if message_length > 0 and profile.preferences.avg_reply_length > 0:
            alpha = 0.1
            profile.preferences.avg_reply_length = int(
                (1 - alpha) * profile.preferences.avg_reply_length
                + alpha * message_length
            )
        else:
            profile.preferences.avg_reply_length = message_length
        await self._store.update_profile(profile)

    async def update_preferences(
        self, user_id: str, preferences_update: dict[str, Any]
    ) -> None:
        profile = await self.ensure_profile(user_id)
        prefs = profile.preferences
        if "reply_style" in preferences_update:
            prefs.reply_style = str(preferences_update["reply_style"])
        if "preferred_topics" in preferences_update:
            for t in preferences_update["preferred_topics"] or []:
                if t not in prefs.preferred_topics:
                    prefs.preferred_topics.append(t)
        if "avoided_topics" in preferences_update:
            for t in preferences_update["avoided_topics"] or []:
                if t not in prefs.avoided_topics:
                    prefs.avoided_topics.append(t)
        await self._store.update_profile(profile)

    async def get_profile_count(self) -> int:
        _, total = await self._store.list_profiles(limit=1)
        return total

    async def list_profiles(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[UserProfile], int]:
        return await self._store.list_profiles(limit=limit, offset=offset)


__all__ = ["ProfileManager"]
