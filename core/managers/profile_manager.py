"""用户画像生命周期 — 标签积累、偏好学习、衰减。"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

from ..base.entity_editing import EntityValidationError, compute_entity_revision
from ..models.user_profile import TagCategory, UserPreferences, UserProfile, UserTag
from ..storage.profile_store import ProfileStore

_EDITABLE_PREFERENCE_FIELDS = frozenset(
    {"reply_style", "preferred_topics", "avoided_topics", "active_hours"}
)


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

    async def create_profile_manual(
        self,
        user_id: Any,
        display_name: Any = "",
        preferences: Any = None,
        tags: Any = None,
    ) -> UserProfile:
        """校验并严格创建管理员画像。"""
        return await self._store.create_profile_strict(
            user_id=self._normalize_text(user_id, "user_id", allow_empty=False),
            display_name=self._normalize_text(
                display_name, "display_name", allow_empty=True
            ),
            preferences=self._normalize_preferences(preferences),
            tags=self._normalize_manual_tags(tags),
        )

    async def update_profile_manual(
        self,
        user_id: Any,
        *,
        display_name: Any,
        preferences: Any,
        tags: Any,
        expected_revision: Any,
    ) -> UserProfile:
        """校验并按修订版本替换管理员可写画像字段。"""
        return await self._store.replace_editable_fields(
            self._normalize_text(user_id, "user_id", allow_empty=False),
            display_name=self._normalize_text(
                display_name, "display_name", allow_empty=True
            ),
            preferences=self._normalize_preferences(preferences),
            tags=self._normalize_manual_tags(tags),
            expected_revision=self._normalize_revision(expected_revision),
        )

    async def delete_profile_manual(
        self,
        user_id: Any,
        *,
        expected_revision: Any,
    ) -> bool:
        """校验并按修订版本删除管理员画像。"""
        return await self._store.delete_profile_if_revision(
            self._normalize_text(user_id, "user_id", allow_empty=False),
            expected_revision=self._normalize_revision(expected_revision),
        )

    @staticmethod
    def revision_for(profile: UserProfile) -> str:
        return compute_entity_revision(profile.to_dict())

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

    @staticmethod
    def _normalize_text(
        value: Any,
        field: str,
        *,
        allow_empty: bool,
        maximum: int = 128,
    ) -> str:
        if not isinstance(value, str):
            raise EntityValidationError({field: "必须为字符串"})
        normalized = value.strip()
        if not normalized and not allow_empty:
            raise EntityValidationError({field: "不能为空"})
        if len(normalized) > maximum:
            raise EntityValidationError({field: "文本过长"})
        return normalized

    @classmethod
    def _normalize_preferences(cls, value: Any) -> UserPreferences:
        if not isinstance(value, Mapping):
            raise EntityValidationError({"preferences": "必须为对象"})
        if any(not isinstance(key, str) for key in value):
            raise EntityValidationError({"preferences": "字段名称必须为字符串"})
        unknown = sorted(set(value) - _EDITABLE_PREFERENCE_FIELDS)
        if unknown:
            raise EntityValidationError(
                {"preferences." + key: "字段不可写" for key in unknown}
            )

        normalized: dict[str, Any] = {}
        if "reply_style" in value:
            normalized["reply_style"] = cls._normalize_text(
                value["reply_style"],
                "preferences.reply_style",
                allow_empty=False,
            )
        for field in ("preferred_topics", "avoided_topics"):
            if field in value:
                normalized[field] = cls._normalize_string_list(
                    value[field], "preferences." + field
                )
        if "active_hours" in value:
            normalized["active_hours"] = cls._normalize_active_hours(
                value["active_hours"]
            )
        return UserPreferences.from_dict(normalized)

    @staticmethod
    def _normalize_active_hours(value: Any) -> list[int]:
        if not isinstance(value, list):
            raise EntityValidationError(
                {"preferences.active_hours": "必须为整数数组"}
            )
        normalized: list[int] = []
        for index, hour in enumerate(value):
            field = "preferences.active_hours." + str(index)
            if isinstance(hour, bool) or not isinstance(hour, int):
                raise EntityValidationError({field: "必须为整数"})
            if not 0 <= hour <= 23:
                raise EntityValidationError({field: "必须在 0 到 23 之间"})
            if hour not in normalized:
                normalized.append(hour)
        return normalized

    @classmethod
    def _normalize_manual_tags(cls, value: Any) -> list[UserTag]:
        if not isinstance(value, list):
            raise EntityValidationError({"tags": "必须为数组"})
        normalized: list[UserTag] = []
        for index, item in enumerate(value):
            prefix = "tags." + str(index)
            if not isinstance(item, Mapping):
                raise EntityValidationError({prefix: "必须为对象"})
            category = item.get("category", TagCategory.CUSTOM.value)
            if not isinstance(category, str):
                raise EntityValidationError({prefix + ".category": "不支持的标签分类"})
            try:
                normalized_category = TagCategory(category.strip())
            except ValueError as exc:
                raise EntityValidationError(
                    {prefix + ".category": "不支持的标签分类"}
                ) from exc
            tag_value = cls._normalize_text(
                item.get("value"),
                prefix + ".value",
                allow_empty=False,
            )
            confidence = item.get("confidence", 0.5)
            if isinstance(confidence, bool) or not isinstance(
                confidence, (int, float)
            ):
                raise EntityValidationError({prefix + ".confidence": "必须为数字"})
            normalized_confidence = float(confidence)
            if not math.isfinite(normalized_confidence):
                raise EntityValidationError(
                    {prefix + ".confidence": "必须为有限数字"}
                )
            if not 0.0 <= normalized_confidence <= 1.0:
                raise EntityValidationError(
                    {prefix + ".confidence": "必须在 0.0 到 1.0 之间"}
                )
            normalized.append(
                UserTag.from_dict(
                    {
                        "category": normalized_category.value,
                        "value": tag_value,
                        "confidence": normalized_confidence,
                    }
                )
            )
        return normalized

    @staticmethod
    def _normalize_string_list(value: Any, field: str) -> list[str]:
        if not isinstance(value, list):
            raise EntityValidationError({field: "必须为字符串数组"})
        normalized: list[str] = []
        for index, item in enumerate(value):
            item_field = field + "." + str(index)
            if not isinstance(item, str):
                raise EntityValidationError({item_field: "必须为字符串"})
            text = item.strip()
            if len(text) > 64:
                raise EntityValidationError({item_field: "文本过长"})
            if text and text not in normalized:
                normalized.append(text)
        if len(normalized) > 32:
            raise EntityValidationError({field: "项目过多"})
        return normalized

    @staticmethod
    def _normalize_revision(value: Any) -> str:
        if not isinstance(value, str):
            raise EntityValidationError({"expected_revision": "必须为字符串"})
        normalized = value.strip()
        if not normalized:
            raise EntityValidationError({"expected_revision": "不能为空"})
        if len(normalized) > 256:
            raise EntityValidationError({"expected_revision": "文本过长"})
        return normalized


__all__ = ["ProfileManager"]
