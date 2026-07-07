"""控制台的好感度状态与情绪概览 API。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from quart import request

from .response_utils import error_response, ok_response


def _affection_user_to_dict(user: Any) -> dict[str, Any]:
    """将 ``UserAffection`` 转换为 JSON 响应字典。"""
    return {
        "user_id": getattr(user, "user_id", ""),
        "group_id": getattr(user, "group_id", ""),
        "affection_score": getattr(user, "affection_score", 0),
        "affection_level": getattr(user, "level", None).name if hasattr(getattr(user, "level", None), "name") else str(getattr(user, "level", "NEUTRAL")),
        "level_name": getattr(user, "level_name", ""),
        "interaction_count": getattr(user, "interaction_count", 0),
        "last_interaction": getattr(user, "last_interaction", 0.0),
    }


def _mood_to_dict(mood: Any) -> dict[str, Any]:
    """将 ``BotMood`` 转换为 JSON 响应字典。"""
    return {
        "mood_type": getattr(mood, "mood_type", None).value if hasattr(getattr(mood, "mood_type", None), "value") else str(getattr(mood, "mood_type", "NEUTRAL")),
        "intensity": getattr(mood, "intensity", 0.0),
        "description": getattr(mood, "description", ""),
        "is_active": getattr(mood, "is_active", lambda: False)() if callable(getattr(mood, "is_active", None)) else False,
    }


def _safe_affection_user_to_dict(user: Any) -> dict[str, Any] | None:
    try:
        return _affection_user_to_dict(user)
    except Exception:
        return None


def _safe_mood_to_dict(mood: Any) -> dict[str, Any] | None:
    try:
        return _mood_to_dict(mood)
    except Exception:
        return None


class AffectionApiMixin:
    """为 Memora 控制台提供好感度 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_affection_manager(self) -> Any | None:
        """从插件属性中惰性解析 ``AffectionManager``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_affection_manager", "affection_manager"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("affection_manager", "_affection_manager"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    def _get_affection_store(self) -> Any | None:
        """从插件属性中惰性解析 ``AffectionStore``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_affection_store", "affection_store"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("affection_store", "_affection_store"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    # ------------------------------------------------------------------
    # GET /affection/status
    # ------------------------------------------------------------------

    async def get_affection_status(self):
        """返回指定群组的好感度汇总与 Bot 当前情绪。

        查询参数：
            group_id (str, 可选): 群组标识，默认取首个可用群组。
        """
        manager = self._get_affection_manager()
        if manager is None:
            return error_response("好感度管理器不可用")

        args = request.args
        group_id = (args.get("group_id", "") or "").strip()

        try:
            # 如果未提供 group_id，则尝试回退到任意可用群组
            if not group_id:
                store = self._get_affection_store()
                if store is not None:
                    try:
                        # 尝试从存储层取第一个群组
                        groups = await store.list_groups() if hasattr(store, "list_groups") else []
                        if groups:
                            group_id = groups[0] if isinstance(groups[0], str) else getattr(groups[0], "group_id", str(groups[0]))
                    except Exception as exc:
                        logger.debug(
                            "[AffectionApi] list_groups fallback failed: %s",
                            exc,
                            exc_info=True,
                        )
                if not group_id:
                    group_id = "default"

            status = await manager.get_group_affection_status(group_id)
            if status is None:
                return error_response(f"群组 {group_id} 没有好感度数据")

            result: dict[str, Any] = {
                "group_id": status.get("group_id", group_id),
                "total_affection": status.get("total_affection", 0),
                "max_total_affection": status.get("max_total_affection", 0),
                "user_count": status.get("user_count", 0),
                "top_users": [
                    item
                    for item in (
                        _safe_affection_user_to_dict(u)
                        for u in status.get("top_users", [])
                    )
                    if item is not None
                ],
            }

            mood = status.get("current_mood")
            if mood is not None:
                if isinstance(mood, dict):
                    result["current_mood"] = mood
                else:
                    result["current_mood"] = _safe_mood_to_dict(mood)
            else:
                # 尝试直接补取情绪信息
                try:
                    mood = await manager.get_mood(group_id)
                    if mood is not None:
                        result["current_mood"] = _safe_mood_to_dict(mood)
                    else:
                        result["current_mood"] = None
                except Exception as exc:
                    logger.debug(
                        "[AffectionApi] direct mood fetch failed for group=%s: %s",
                        group_id,
                        exc,
                        exc_info=True,
                    )
                    result["current_mood"] = None

            return ok_response(result)
        except Exception as e:
            logger.error(f"[AffectionApi] 获取好感度状态失败: {e}", exc_info=True)
            return error_response(f"获取好感度状态失败: {e}")


__all__ = ["AffectionApiMixin"]
