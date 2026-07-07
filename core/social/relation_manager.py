"""社交关系管理器：负责关系类型判定与强度更新的集中协调。"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

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
        await self._store.upsert_relation(rel)
        return rel

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

    async def _apply_delta(
        self,
        rel: SocialRelation,
        delta: float,
        reason: str,
    ) -> SocialRelation:
        """计算门控增量、裁剪强度、持久化并返回更新后的关系。"""
        difficulty = get_difficulty(rel.relation_type)
        actual_delta = delta * (1.0 - difficulty)

        old_strength = rel.strength
        new_strength = max(0.0, min(1.0, old_strength + actual_delta))

        rel.strength = new_strength
        rel.frequency += 1
        rel.last_interaction = time.time()

        await self._store.upsert_relation(rel)

        if abs(new_strength - old_strength) > 0.001:
            logger.debug(
                "[RelationManager] %s → %s [%s] strength %.3f → %.3f "
                "(raw_delta=%.3f, actual=%.3f, reason=%s)",
                rel.from_user,
                rel.to_user,
                rel.relation_type,
                old_strength,
                new_strength,
                delta,
                actual_delta,
                reason,
            )

        return rel


__all__ = ["RelationManager"]
