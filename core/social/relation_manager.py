"""社交关系管理器：负责关系类型判定与强度更新的集中协调。"""

from __future__ import annotations

import math
import time
from typing import Any

from astrbot.api import logger

from ..base.entity_editing import EntityValidationError, compute_entity_revision
from .models import (
    RELATION_CATEGORIES,
    RELATION_DIFFICULTY,
    RelationChange,
    SocialRelation,
    get_difficulty,
    get_relation_category,
)
from .relation_store import RelationStore

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

_DEFAULT_RELATION_TYPE = "stranger"
_DEFAULT_STRENGTH = 0.1
_DEFAULT_TAGS: list[str] = []


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class RelationManager:
    """社交关系类型化的中心协调器。"""

    def __init__(self, store: RelationStore) -> None:
        self._store = store

    # ---- 对外 API ------------------------------------------------------

    @staticmethod
    def revision_for(rel: SocialRelation) -> str:
        """返回关系当前完整序列化形状的规范修订版本。"""
        return compute_entity_revision(rel.to_dict())

    async def create_manual_relation(
        self,
        *,
        from_user: str,
        to_user: str,
        group_id: str,
        relation_type: str,
        strength: float,
        tags: list[str],
    ) -> SocialRelation:
        """创建不伪造互动次数或时间的管理员关系记录。"""
        errors = self._validate_manual_fields(
            from_user,
            to_user,
            group_id,
            relation_type,
            strength,
            tags,
        )
        if errors:
            raise EntityValidationError(errors)
        return await self._store.create_relation_strict(
            SocialRelation(
                from_user=from_user.strip(),
                to_user=to_user.strip(),
                relation_type=relation_type,
                strength=float(strength),
                frequency=0,
                last_interaction=0.0,
                group_id=group_id.strip(),
                tags=self._normalize_tags(tags),
            )
        )

    async def update_manual_relation(
        self,
        *,
        identity: tuple[str, str, str, str],
        relation_type: str,
        strength: float,
        tags: list[str],
        expected_revision: str,
    ) -> SocialRelation:
        """按修订版本绝对更新管理员可写字段。"""
        normalized_identity, identity_errors = self._normalize_identity(identity)
        errors = dict(identity_errors)
        if normalized_identity is not None:
            errors.update(
                self._validate_editable_fields(
                    relation_type,
                    strength,
                    tags,
                )
            )
        self._validate_expected_revision(expected_revision, errors)
        if errors or normalized_identity is None:
            raise EntityValidationError(errors)
        return await self._store.update_relation_if_revision(
            normalized_identity,
            relation_type=relation_type,
            strength=float(strength),
            tags=self._normalize_tags(tags),
            expected_revision=expected_revision,
        )

    async def delete_manual_relation(
        self,
        *,
        identity: tuple[str, str, str, str],
        expected_revision: str,
    ) -> bool:
        """按修订版本删除管理员指定的关系。"""
        normalized_identity, errors = self._normalize_identity(identity)
        self._validate_expected_revision(expected_revision, errors)
        if errors or normalized_identity is None:
            raise EntityValidationError(errors)
        return await self._store.delete_relation_if_revision(
            normalized_identity,
            expected_revision=expected_revision,
        )

    async def get_or_create(
        self,
        from_user: str,
        to_user: str,
        group_id: str,
        *,
        relation_type: str = _DEFAULT_RELATION_TYPE,
    ) -> SocialRelation:
        """获取现有关系；若不存在则创建默认关系。"""
        existing = await self._store.get_relation(
            from_user, to_user, relation_type, group_id,
        )
        if existing is not None:
            return existing

        rel = SocialRelation(
            from_user=from_user,
            to_user=to_user,
            relation_type=relation_type,
            strength=_DEFAULT_STRENGTH,
            frequency=0,
            last_interaction=time.time(),
            group_id=group_id,
            tags=list(_DEFAULT_TAGS),
        )
        return await self._store.get_or_create_relation(rel)

    async def update_relation(self, change: RelationChange) -> SocialRelation:
        """应用带难度门控的关系强度更新。"""
        existing = await self._store.get_relation(
            change.from_user, change.to_user, change.relation_type, "",
        )

        if existing is None:
            # 自动补建默认关系记录，再应用变化量
            existing = await self.get_or_create(
                change.from_user,
                change.to_user,
                group_id="",
                relation_type=change.relation_type,
            )

        return await self._apply_delta(
            existing,
            change.delta,
            change.reason,
        )

    async def apply_delta(
        self,
        from_user: str,
        to_user: str,
        group_id: str,
        delta: float,
        reason: str,
        *,
        relation_type: str = _DEFAULT_RELATION_TYPE,
    ) -> SocialRelation:
        """便捷封装：获取或创建关系、计算门控增量并持久化。"""
        rel = await self.get_or_create(
            from_user, to_user, group_id, relation_type=relation_type,
        )
        return await self._apply_delta(rel, delta, reason)

    async def get_relations_by_group(
        self, group_id: str
    ) -> list[SocialRelation]:
        """返回指定群组内的全部关系，按强度降序排列。"""
        return await self._store.get_group_relations(group_id)

    async def get_user_network(
        self, user_id: str
    ) -> list[SocialRelation]:
        """返回涉及指定用户的全部关系（不限群组）。"""
        return await self._store.get_user_network(user_id)

    async def get_user_relations_in_group(
        self, user_id: str, group_id: str
    ) -> list[SocialRelation]:
        """返回指定群组内涉及该用户的全部关系。"""
        return await self._store.get_user_relations_in_group(user_id, group_id)

    async def delete_relation(
        self,
        from_user: str,
        to_user: str,
        relation_type: str,
        group_id: str,
    ) -> bool:
        """删除单条带类型的关系，成功时返回 ``True``。"""
        return await self._store.delete_relation(
            from_user, to_user, relation_type, group_id,
        )

    async def update_tags(
        self,
        from_user: str,
        to_user: str,
        relation_type: str,
        group_id: str,
        tags: list[str],
    ) -> SocialRelation | None:
        """替换现有关系上的标签列表。"""
        rel = await self._store.get_relation(
            from_user, to_user, relation_type, group_id,
        )
        if rel is None:
            return None
        rel.tags = list(tags)
        await self._store.upsert_relation(rel)
        return rel

    async def list_all(self) -> list[SocialRelation]:
        """返回全部关系记录。"""
        return await self._store.list_all()

    async def list_group_ids(self) -> list[str]:
        """返回已持久化关系中所有非空且去重的群组 ID。"""
        return await self._store.list_group_ids()

    # ---- 内部实现 --------------------------------------------------------

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            text = tag.strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @classmethod
    def _validate_manual_fields(
        cls,
        from_user: Any,
        to_user: Any,
        group_id: Any,
        relation_type: Any,
        strength: Any,
        tags: Any,
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        cls._validate_identifier(from_user, "from_user", errors)
        cls._validate_identifier(to_user, "to_user", errors)
        cls._validate_bounded_string(
            group_id,
            "group_id",
            errors,
            allow_empty=True,
        )

        if (
            isinstance(from_user, str)
            and isinstance(to_user, str)
            and from_user.strip()
            and from_user.strip() == to_user.strip()
        ):
            errors["to_user"] = "不能与 from_user 相同"

        errors.update(
            cls._validate_editable_fields(
                relation_type,
                strength,
                tags,
            )
        )
        return errors

    @staticmethod
    def _validate_editable_fields(
        relation_type: Any,
        strength: Any,
        tags: Any,
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        if (
            not isinstance(relation_type, str)
            or get_relation_category(relation_type) is None
        ):
            errors["relation_type"] = "不支持的关系类型"

        if isinstance(strength, bool) or not isinstance(strength, (int, float)):
            errors["strength"] = "必须为数字"
        elif not math.isfinite(float(strength)):
            errors["strength"] = "必须为有限数字"
        elif not 0.0 <= float(strength) <= 1.0:
            errors["strength"] = "必须在 0.0 到 1.0 之间"

        if not isinstance(tags, list):
            errors["tags"] = "必须为字符串数组"
        else:
            normalized_count = 0
            seen: set[str] = set()
            for index, tag in enumerate(tags):
                if not isinstance(tag, str):
                    errors[f"tags.{index}"] = "必须为字符串"
                    continue
                text = tag.strip()
                if len(text) > 64:
                    errors[f"tags.{index}"] = "文本过长"
                if text and text not in seen:
                    seen.add(text)
                    normalized_count += 1
            if normalized_count > 32:
                errors["tags"] = "项目过多"
        return errors

    @staticmethod
    def _validate_identifier(
        value: Any,
        field: str,
        errors: dict[str, str],
    ) -> None:
        RelationManager._validate_bounded_string(
            value,
            field,
            errors,
            allow_empty=False,
        )

    @staticmethod
    def _validate_bounded_string(
        value: Any,
        field: str,
        errors: dict[str, str],
        *,
        allow_empty: bool,
    ) -> None:
        if not isinstance(value, str):
            errors[field] = "必须为字符串"
            return
        normalized = value.strip()
        if not normalized and not allow_empty:
            errors[field] = "不能为空"
        elif len(normalized) > 128:
            errors[field] = "文本过长"

    @classmethod
    def _normalize_identity(
        cls,
        identity: Any,
    ) -> tuple[tuple[str, str, str, str] | None, dict[str, str]]:
        errors: dict[str, str] = {}
        if not isinstance(identity, tuple) or len(identity) != 4:
            return None, {"identity": "必须包含四个标识字段"}
        from_user, to_user, relation_type, group_id = identity
        cls._validate_bounded_string(
            from_user,
            "identity.from_user",
            errors,
            allow_empty=False,
        )
        cls._validate_bounded_string(
            to_user,
            "identity.to_user",
            errors,
            allow_empty=False,
        )
        cls._validate_bounded_string(
            relation_type,
            "identity.relation_type",
            errors,
            allow_empty=False,
        )
        cls._validate_bounded_string(
            group_id,
            "identity.group_id",
            errors,
            allow_empty=True,
        )
        if errors or not all(isinstance(value, str) for value in identity):
            return None, errors
        return (
            from_user.strip(),
            to_user.strip(),
            relation_type,
            group_id.strip(),
        ), errors

    @staticmethod
    def _validate_expected_revision(
        expected_revision: Any,
        errors: dict[str, str],
    ) -> None:
        if not isinstance(expected_revision, str) or not expected_revision.strip():
            errors["expected_revision"] = "不能为空"

    async def _apply_delta(
        self,
        rel: SocialRelation,
        delta: float,
        reason: str,
    ) -> SocialRelation:
        """计算门控增量、裁剪强度、持久化并返回更新后的关系。"""
        result = await self._store.apply_automatic_delta_if_exists(
            (
                rel.from_user,
                rel.to_user,
                rel.relation_type,
                rel.group_id,
            ),
            delta=delta,
            difficulty=get_difficulty(rel.relation_type),
        )
        if result is None:
            logger.debug(
                "[RelationManager] 跳过已不存在的关系自动更新: %s → %s [%s]",
                rel.from_user,
                rel.to_user,
                rel.relation_type,
            )
            return rel

        current, updated = result
        actual_delta = delta * (1.0 - get_difficulty(rel.relation_type))
        old_strength = current.strength
        new_strength = updated.strength

        if abs(new_strength - old_strength) > 0.001:
            logger.debug(
                "[RelationManager] %s → %s [%s] strength %.3f → %.3f "
                "(raw_delta=%.3f, actual=%.3f, reason=%s)",
                updated.from_user,
                updated.to_user,
                updated.relation_type,
                old_strength,
                new_strength,
                delta,
                actual_delta,
                reason,
            )

        return updated


__all__ = ["RelationManager"]
