"""用户画像生命周期 — 标签积累、偏好学习、衰减。"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ....shared.domain_provenance import DomainObjectOrigin, DomainProvenance
from ....shared.entity_editing import (
    EntityNotFoundError,
    EntityValidationError,
    compute_entity_revision,
)
from ....shared.list_sorting import SortQuery
from ..contracts import ProfileStorePort
from ..domain.models import (
    TagCategory,
    UserPreferences,
    UserProfile,
    UserTag,
)

_EDITABLE_PREFERENCE_FIELDS = frozenset(
    {"reply_style", "preferred_topics", "avoided_topics", "active_hours"}
)
_LOGGER = logging.getLogger(__name__)


class ProfileManager:
    """管理用户画像: 创建、更新、标签积累、衰减。"""

    def __init__(self, profile_store: ProfileStorePort) -> None:
        """保存画像 Store 依赖。"""

        self._store = profile_store

    async def ensure_profile(self, user_id: str) -> UserProfile:
        """返回已有画像，缺失时创建空画像。

        Raises:
            RuntimeError: Store 未能返回已创建的画像。
        """

        profile = await self._store.get_or_create_profile(user_id)
        if profile is None:
            raise RuntimeError("画像创建后仍不可用")
        return profile

    async def get_profile(self, user_id: str) -> UserProfile | None:
        """按可信用户 ID 读取画像。"""

        return await self._store.get_profile(user_id)

    async def touch(self, user_id: str) -> None:
        """更新画像最近活动时间。"""

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
        """计算管理员编辑使用的稳定画像 revision。"""

        return compute_entity_revision(profile.to_dict())

    async def update_profile_fields(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        preferences: dict[str, Any] | UserPreferences | None = None,
    ) -> UserProfile | None:
        """更新显式人工字段，并为偏好标记人工权威。"""

        normalized_preferences: UserPreferences | None = None
        if preferences is not None:
            if isinstance(preferences, UserPreferences):
                normalized_preferences = replace(
                    preferences,
                    provenance=DomainProvenance(DomainObjectOrigin.MANUAL),
                )
            else:
                normalized_preferences = replace(
                    UserPreferences.from_dict(preferences),
                    provenance=DomainProvenance(DomainObjectOrigin.MANUAL),
                )
        return await self._store.update_profile_fields_atomic(
            user_id,
            display_name=display_name.strip() if display_name is not None else None,
            preferences=normalized_preferences,
        )

    async def delete_profile(self, user_id: str) -> bool:
        """删除指定画像及其标签。"""

        return await self._store.delete_profile(user_id)

    async def add_tag(self, user_id: str, tag: UserTag) -> UserProfile | None:
        """向画像写入显式人工标签。"""

        profile = await self.get_profile(user_id)
        if profile is None:
            return None
        tag.source = "manual"
        tag.provenance = DomainProvenance(DomainObjectOrigin.MANUAL)
        await self._store.add_tag(user_id, tag)
        return await self.get_profile(user_id)

    async def remove_tag(
        self, user_id: str, category: str, value: str
    ) -> UserProfile | None:
        """按分类和值删除单个标签。"""

        profile = await self.get_profile(user_id)
        if profile is None:
            return None
        await self._store.remove_tag(user_id, category, value)
        return await self.get_profile(user_id)

    async def ingest_tags(
        self,
        user_id: str,
        tags: list[UserTag],
        *,
        provenance: DomainProvenance | None = None,
    ) -> UserProfile:
        """应用带 canonical 证据的自动标签 proposal。"""

        provenance = self._require_derived_provenance(provenance)
        await self.ensure_profile(user_id)
        for tag in tags:
            tag.provenance = provenance
        profile, new_count = await self._store.upsert_tags_atomic(
            user_id,
            tags,
        )
        if profile is None:
            raise EntityNotFoundError("画像不存在")
        if new_count:
            _LOGGER.debug(
                f"[画像] {user_id}: 新增 {new_count} 个标签，合计 {len(profile.tags)} 个"
            )
        return profile

    async def get_tag_weights(self, user_id: str) -> dict[str, float]:
        """返回个性化排序使用的标签权重。"""

        profile = await self.get_profile(user_id)
        if profile is None:
            return {}
        return profile.get_weight_vector()

    async def decay_and_clean(self, user_id: str) -> int:
        """衰减并删除指定画像中的低置信度标签。"""

        removed = await self._store.decay_and_clean_tags_atomic(user_id)
        if removed:
            _LOGGER.debug(f"[画像] {user_id}: 删除 {removed} 个过期标签")
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
                    _LOGGER.warning(
                        f"[画像] {profile.user_id}: 标签衰减失败，异常类型={e.__class__.__name__}"
                    )

            if len(profiles) < batch_size:
                break
            offset += len(profiles)

        return {"scanned": scanned, "removed": removed_total, "failed": failed}

    async def record_message(self, user_id: str, message_length: int = 0) -> None:
        """记录一次用户消息并更新画像统计。"""

        await self.ensure_profile(user_id)
        await self._store.record_message_atomic(
            user_id,
            message_length=message_length,
        )

    async def update_preferences(
        self,
        user_id: str,
        preferences_update: dict[str, Any],
        *,
        provenance: DomainProvenance | None = None,
    ) -> None:
        """应用带 canonical 证据的自动偏好 proposal。"""

        provenance = self._require_derived_provenance(provenance)
        await self.ensure_profile(user_id)
        await self._store.merge_preferences_atomic(
            user_id,
            preferences_update,
            provenance=provenance,
        )

    async def get_profile_count(self) -> int:
        """返回画像总数。"""

        _, total = await self._store.list_profiles(limit=1)
        return total

    async def list_profiles(
        self,
        limit: int = 50,
        offset: int = 0,
        sort: SortQuery = SortQuery("last_seen_at", "desc"),
    ) -> tuple[list[UserProfile], int]:
        """按稳定排序分页列出画像。"""

        return await self._store.list_profiles(limit=limit, offset=offset, sort=sort)

    @staticmethod
    def _normalize_text(
        value: Any,
        field: str,
        *,
        allow_empty: bool,
        maximum: int = 128,
    ) -> str:
        """规范化有限长度文本字段。"""

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
        """规范化管理员可写偏好并标记人工权威。"""

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
        return replace(
            UserPreferences.from_dict(normalized),
            provenance=DomainProvenance(DomainObjectOrigin.MANUAL),
        )

    @staticmethod
    def _normalize_active_hours(value: Any) -> list[int]:
        """规范化 0 到 23 的去重小时数组。"""

        if not isinstance(value, list):
            raise EntityValidationError({"preferences.active_hours": "必须为整数数组"})
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
        """规范化管理员标签列表并拒绝重复项。"""

        if not isinstance(value, list):
            raise EntityValidationError({"tags": "必须为数组"})
        normalized: list[UserTag] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(value):
            tag = cls._normalize_manual_tag(item, index)
            identity = (tag.category.value, tag.value)
            if identity in seen:
                raise EntityValidationError(
                    {"tags." + str(index) + ".value": "标签重复"}
                )
            seen.add(identity)
            normalized.append(tag)
        return normalized

    @classmethod
    def _normalize_manual_tag(cls, item: Any, index: int) -> UserTag:
        """规范化一条管理员标签。"""

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
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise EntityValidationError({prefix + ".confidence": "必须为数字"})
        normalized_confidence = float(confidence)
        if not math.isfinite(normalized_confidence):
            raise EntityValidationError({prefix + ".confidence": "必须为有限数字"})
        if not 0.0 <= normalized_confidence <= 1.0:
            raise EntityValidationError(
                {prefix + ".confidence": "必须在 0.0 到 1.0 之间"}
            )
        return UserTag.from_dict(
            {
                "category": normalized_category.value,
                "value": tag_value,
                "confidence": normalized_confidence,
                "source": "manual",
                "provenance": DomainProvenance(DomainObjectOrigin.MANUAL).to_dict(),
            }
        )

    @staticmethod
    def _normalize_string_list(value: Any, field: str) -> list[str]:
        """规范化有限、去重的字符串列表。"""

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
        """规范化非空 revision 文本。"""

        if not isinstance(value, str):
            raise EntityValidationError({"expected_revision": "必须为字符串"})
        normalized = value.strip()
        if not normalized:
            raise EntityValidationError({"expected_revision": "不能为空"})
        if len(normalized) > 256:
            raise EntityValidationError({"expected_revision": "文本过长"})
        return normalized

    @staticmethod
    def _require_derived_provenance(
        provenance: DomainProvenance | None,
    ) -> DomainProvenance:
        """要求自动写入携带完整 derived provenance。"""

        if provenance is None or provenance.origin is not DomainObjectOrigin.DERIVED:
            raise ValueError("source_provenance_required")
        return provenance


__all__ = ["ProfileManager"]
