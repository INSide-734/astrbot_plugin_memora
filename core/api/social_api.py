"""控制台社交关系浏览 API。"""

from __future__ import annotations

import inspect
from typing import Any

from astrbot.api import logger
from quart import request

from .response_utils import error_response, ok_response


def _relation_to_dict(rel: Any) -> dict[str, Any]:
    """将 SocialRelation 转换为 JSON 响应所需字典。"""
    category = "unknown"
    try:
        from ..social.models import get_relation_category
        category = (
            get_relation_category(getattr(rel, "relation_type", "stranger"))
            or "unknown"
        )
    except Exception as exc:
        logger.debug("[社交关系 API] 关系分类查询失败：%s", exc, exc_info=True)
    return {
        "from_user": getattr(rel, "from_user", ""),
        "to_user": getattr(rel, "to_user", ""),
        "relation_type": getattr(rel, "relation_type", "stranger"),
        "strength": getattr(rel, "strength", 0.0),
        "frequency": getattr(rel, "frequency", 0),
        "last_interaction": getattr(rel, "last_interaction", 0.0),
        "group_id": getattr(rel, "group_id", ""),
        "tags": getattr(rel, "tags", []) or [],
        "category": category,
    }


def _safe_relation_to_dict(rel: Any) -> dict[str, Any] | None:
    try:
        return _relation_to_dict(rel)
    except Exception:
        return None


def _safe_relation_list(relations: Any) -> list[Any]:
    try:
        return list(relations or [])
    except Exception:
        return []


class SocialApiMixin:
    """为 Memora 控制台提供社交关系 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_relation_manager(self) -> Any | None:
        """按需从插件属性中解析 RelationManager。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_relation_manager", "relation_manager"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("relation_manager", "_relation_manager"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    # ------------------------------------------------------------------
    # GET /social/relations
    # ------------------------------------------------------------------

    async def get_social_relations(self):
        """返回社交关系数据，并可按群组和分类过滤。

        查询参数：
            group_id (str, optional): 群组标识。
            category (str, optional): 按关系分类过滤，
                可选值包括 blood/geographic/career/emotional/interest/intimacy。
        """
        manager = self._get_relation_manager()
        if manager is None:
            return error_response("关系管理器不可用")

        args = request.args
        group_id = (args.get("group_id", "") or "").strip()
        category = (args.get("category", "") or "").strip().lower()

        try:
            if group_id:
                relations = manager.get_relations_by_group(group_id)
            else:
                relations = manager.list_all() if hasattr(manager, "list_all") else []
            if inspect.isawaitable(relations):
                relations = await relations
            relations = _safe_relation_list(relations)

            result_relations = [
                item
                for item in (_safe_relation_to_dict(r) for r in relations)
                if item is not None
            ]

            # 可选的分类过滤
            if category:
                result_relations = [r for r in result_relations if r.get("category") == category]

            return ok_response({
                "relations": result_relations,
                "total": len(result_relations),
            })
        except Exception as e:
            logger.error(f"[社交关系 API] 获取社交关系失败：{e}", exc_info=True)
            return error_response(f"获取社交关系失败: {e}")


__all__ = ["SocialApiMixin"]
