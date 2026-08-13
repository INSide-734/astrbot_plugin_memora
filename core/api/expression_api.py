"""控制台的表达模式浏览 API。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from quart import request

from ..features.cognition.expression.pattern_store import EXPRESSION_SORT_COLUMNS
from ..shared.list_sorting import SortQuery, parse_sort_query
from .response_utils import error_response, ok_response


def _parse_limit(raw_value: Any, *, default: int = 20, maximum: int = 100) -> int:
    """解析 limit 查询参数，非法或非正数时回退到默认值。"""
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, maximum)


def _pattern_to_dict(p: Any) -> dict[str, Any]:
    """将 ``ExpressionPattern`` 转换为 JSON 响应字典。"""
    return {
        "pattern_id": getattr(p, "pattern_id", 0),
        "situation": getattr(p, "situation", ""),
        "expression": getattr(p, "expression", ""),
        "group_id": getattr(p, "group_id", ""),
        "persona_id": getattr(p, "persona_id", ""),
        "user_id": getattr(p, "user_id", None),
        "weight": getattr(p, "weight", 0.0),
        "usage_count": getattr(p, "usage_count", 0),
        "created_at": getattr(p, "created_at", 0.0),
        "last_used_at": getattr(p, "last_used_at", 0.0),
    }


def _safe_pattern_to_dict(pattern: Any) -> dict[str, Any] | None:
    try:
        return _pattern_to_dict(pattern)
    except Exception:
        return None


def _safe_pattern_list(patterns: Any) -> list[Any]:
    try:
        return list(patterns or [])
    except Exception:
        return []


def _safe_total(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sort_query_error(exc: ValueError) -> dict[str, Any]:
    message = str(exc)
    field = "sort_order" if message == "sort_order must be asc or desc" else "sort_by"
    return error_response(
        message,
        code="invalid_query",
        field_errors={field: message},
    )


class ExpressionApiMixin:
    """为 Memora 控制台提供表达模式 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_expression_store(self) -> Any | None:
        """从插件属性中惰性解析 ``ExpressionPatternStore``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_expression_store", "expression_store"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("expression_store", "_expression_store"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    def _get_expression_learner(self) -> Any | None:
        """从插件属性中惰性解析 ``ExpressionPatternLearner``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_expression_learner", "expression_learner"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("expression_learner", "_expression_learner"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    # ------------------------------------------------------------------
    # GET /expression/patterns
    # ------------------------------------------------------------------

    async def get_expression_patterns(self):
        """返回表达模式列表，可按作用域筛选。

        查询参数:
            group_id (str, 可选): 群组标识。
            persona_id (str, 可选): 人设标识。
            limit (int, 可选): 返回上限，默认 20，最大 100。
        """
        args = request.args
        group_id = (args.get("group_id", "") or "").strip()
        persona_id = (args.get("persona_id", "") or "").strip()
        limit = _parse_limit(args.get("limit", 20))
        try:
            sort = parse_sort_query(
                args,
                allowed=EXPRESSION_SORT_COLUMNS,
                default_by="weight",
                default_order="desc",
            )
        except ValueError as exc:
            return _sort_query_error(exc)

        # 既有 learner 路径仅支持权重降序；其他排序必须由 store 在 LIMIT 前完成。
        learner = self._get_expression_learner()
        if (
            sort == SortQuery("weight", "desc")
            and learner is not None
            and hasattr(
                learner,
                "get_patterns_for_injection",
            )
        ):
            try:
                patterns = await learner.get_patterns_for_injection(
                    group_id=group_id or "default",
                    persona_id=persona_id or "default",
                    user_id=None,
                    limit=limit,
                )
                patterns = _safe_pattern_list(patterns)
                if patterns:
                    serialized_patterns = [
                        item
                        for item in (_safe_pattern_to_dict(p) for p in patterns)
                        if item is not None
                    ]
                    return ok_response(
                        {
                            "patterns": serialized_patterns,
                            "total": len(patterns),
                            "group_patterns": len(patterns),
                            "group_id": group_id or "default",
                        }
                    )
            except Exception as e:
                logger.warning(
                    f"[ExpressionApi] 调用 learner.get_patterns_for_injection 失败: {e}"
                )

        store = self._get_expression_store()
        if store is None:
            return error_response("表达模式存储不可用")

        try:
            from ..features.cognition.expression.models import PatternScope

            scope = PatternScope(
                group_id=group_id or "default",
                persona_id=persona_id or "default",
                user_id=None,
            )
            patterns = await store.get_top_by_weight(
                scope,
                limit=limit,
                sort=sort,
            )
            patterns = _safe_pattern_list(patterns)
            serialized_patterns = [
                item
                for item in (_safe_pattern_to_dict(p) for p in patterns)
                if item is not None
            ]
            total = (
                _safe_total(await store.count_by_scope(scope))
                if hasattr(store, "count_by_scope")
                else len(patterns)
            )
            return ok_response(
                {
                    "patterns": serialized_patterns,
                    "total": total,
                    "group_patterns": len(patterns),
                    "group_id": group_id or "default",
                }
            )
        except Exception as e:
            logger.error(f"[ExpressionApi] 获取表达模式失败: {e}", exc_info=True)
            return error_response(f"获取表达模式失败: {e}")


__all__ = ["ExpressionApiMixin"]
