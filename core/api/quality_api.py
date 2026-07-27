"""控制台的记忆质量评分统计、历史与告警 API。"""

from __future__ import annotations

from collections import deque
from typing import Any

from astrbot.api import logger
from quart import request

from .response_utils import error_response, ok_response


def _parse_bounded_limit(
    raw_value: Any,
    *,
    default: int,
    maximum: int,
) -> int:
    """解析 limit 查询参数，并限制在安全的正整数范围内。"""
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, maximum)


def _alert_to_dict(alert: Any, idx: int) -> dict[str, Any]:
    """将 ``QualityAlert`` 转换为 JSON 响应字典。"""
    level = _safe_alert_level(alert)
    if level is None:
        raise ValueError("alert level unavailable")
    return {
        "id": idx,
        "level": level,
        "dimension": alert.dimension,
        "score": alert.score,
        "threshold": alert.threshold,
        "message": alert.message,
        "suggestion": alert.suggestion,
        "timestamp": alert.timestamp,
    }


def _score_to_dict(score: Any) -> dict[str, Any]:
    """将 ``QualityScore`` 转换为 JSON 响应字典。"""
    return {
        "atom_id": score.atom_id,
        "overall": score.overall,
        "consistency": score.consistency,
        "coherence": score.coherence,
        "relevance": score.relevance,
        "freshness": score.freshness,
        "accuracy": score.accuracy,
        "timestamp": getattr(score, "timestamp", 0.0),
    }


def _safe_score_to_dict(score: Any) -> dict[str, Any] | None:
    try:
        return _score_to_dict(score)
    except Exception:
        return None


def _safe_alert_to_dict(alert: Any, idx: int) -> dict[str, Any] | None:
    try:
        return _alert_to_dict(alert, idx)
    except Exception:
        return None


def _safe_alert_level(alert: Any) -> str | None:
    try:
        level = getattr(alert, "level")
        return level.value if hasattr(level, "value") else str(level)
    except Exception:
        return None


def _safe_history_list(history: Any) -> list[Any]:
    try:
        return list(history or [])
    except Exception:
        return []


def _reset_history_container(history: Any, *, maxlen: int | None = None) -> deque[Any]:
    if isinstance(history, deque):
        try:
            history.clear()
            return history
        except Exception:
            pass
    return deque(maxlen=maxlen)


class QualityApiMixin:
    """为 Memora 控制台提供质量评分 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_quality_scorer(self) -> Any | None:
        """从插件属性中惰性解析 ``MemoryQualityScorer`` 实例。

        评分器可能挂载在插件的 ``_quality_scorer``、``quality_scorer``，
        或 ``self.plugin.initializer`` 上。若尚未实例化，则按需创建默认实例。
        """
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None

        # 先尝试直接从插件属性读取
        for attr_name in ("_quality_scorer", "quality_scorer"):
            scorer = getattr(plugin, attr_name, None)
            if scorer is not None:
                return scorer

        # 再尝试从 initializer 读取
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            scorer = getattr(initializer, "quality_scorer", None)
            if scorer is not None:
                return scorer

        # 若不存在则惰性创建默认实例，并缓存到插件对象
        from ..monitoring.quality_scorer import MemoryQualityScorer

        scorer = MemoryQualityScorer(window_size=100)
        plugin._quality_scorer = scorer
        logger.info("[质量接口] 已惰性创建 MemoryQualityScorer 实例")
        return scorer

    # ------------------------------------------------------------------
    # GET /stats
    # ------------------------------------------------------------------

    async def get_quality_stats(self):
        """返回聚合后的质量评分统计信息。"""
        scorer = self._get_quality_scorer()
        if scorer is None:
            return error_response("质量评分器不可用")
        try:
            stats = scorer.get_stats()
            if not isinstance(stats, dict):
                stats = {}
            elif stats.get("total_scored") == 0 and "status" not in stats:
                stats = {**stats, "status": "no_samples"}
            return ok_response(stats)
        except Exception as e:
            logger.error(f"[质量接口] 获取质量统计失败: {e}", exc_info=True)
            return error_response(f"获取质量统计失败: {e}")

    # ------------------------------------------------------------------
    # GET /recent
    # ------------------------------------------------------------------

    async def get_quality_recent(self):
        """返回最近的质量评分详情。"""
        scorer = self._get_quality_scorer()
        if scorer is None:
            return error_response("质量评分器不可用")
        try:
            args = request.args
            limit = _parse_bounded_limit(args.get("limit", 20), default=20, maximum=100)

            scores = _safe_history_list(getattr(scorer, "_score_history", []))
            recent = scores[-limit:] if len(scores) > limit else scores
            serialized_scores = [
                item
                for item in (_safe_score_to_dict(s) for s in reversed(recent))
                if item is not None
            ]
            return ok_response(
                {
                    "scores": serialized_scores,
                    "total_scores": len(scores),
                }
            )
        except Exception as e:
            logger.error(f"[质量接口] 获取最近评分记录失败: {e}", exc_info=True)
            return error_response(f"获取最近评分记录失败: {e}")

    # ------------------------------------------------------------------
    # GET /alerts
    # ------------------------------------------------------------------

    async def get_quality_alerts(self):
        """返回最近的质量告警，可按级别筛选。"""
        scorer = self._get_quality_scorer()
        if scorer is None:
            return error_response("质量评分器不可用")
        try:
            args = request.args
            level_filter = args.get("level", "").strip().lower()
            limit = _parse_bounded_limit(args.get("limit", 50), default=50, maximum=200)

            all_alerts = _safe_history_list(getattr(scorer, "_alert_history", []))
            filtered = all_alerts
            if level_filter:
                valid_levels = frozenset({"critical", "high", "medium", "info"})
                if level_filter not in valid_levels:
                    return error_response(
                        f"invalid level: 无效的告警级别 '{level_filter}'；"
                        f"必须为以下之一：{', '.join(sorted(valid_levels))}"
                    )
                filtered = [
                    a
                    for a in all_alerts
                    if (_safe_alert_level(a) or "").lower() == level_filter
                ]

            recent = filtered[-limit:] if len(filtered) > limit else filtered
            serialized_alerts = [
                item
                for i, a in enumerate(reversed(recent))
                for item in [_safe_alert_to_dict(a, i)]
                if item is not None
            ]
            return ok_response(
                {
                    "alerts": serialized_alerts,
                    "total_alerts": len(all_alerts),
                    "filtered_count": len(filtered),
                }
            )
        except Exception as e:
            logger.error(f"[质量接口] 获取质量告警失败: {e}", exc_info=True)
            return error_response(f"获取告警记录失败: {e}")

    # ------------------------------------------------------------------
    # POST /reset
    # ------------------------------------------------------------------

    async def reset_quality(self):
        """重置质量评分器及其告警历史。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        scorer = self._get_quality_scorer()
        if scorer is None:
            return error_response("质量评分器不可用")
        try:
            scorer._score_history = _reset_history_container(
                getattr(scorer, "_score_history", None),
                maxlen=100,
            )
            scorer._alert_history = _reset_history_container(
                getattr(scorer, "_alert_history", None),
                maxlen=200,
            )
            scorer._paused = False
            scorer._pause_reason = ""
            logger.info("[质量接口] 已重置质量评分器及告警历史")
            return ok_response({"message": "reset: 质量评分器及告警历史已重置"})
        except Exception as e:
            logger.error(f"[质量接口] 重置质量评分器失败: {e}", exc_info=True)
            return error_response(f"failed to reset quality scorer: {e}")


__all__ = ["QualityApiMixin"]
