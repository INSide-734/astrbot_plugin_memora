"""控制台 API：自主学习的状态与历史记录。"""

from __future__ import annotations

from collections.abc import Mapping

from quart import request

from .response_utils import error_response, ok_response


def _coerce_learning_number(value, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_learning_int(value, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_learning_history_list(history):
    if isinstance(history, list):
        return history
    if history is None or isinstance(history, (str, bytes, bytearray, Mapping)):
        return []
    try:
        return list(history)
    except TypeError:
        return []


def _flatten_learning_stats(raw: dict) -> dict:
    """将 AutoLearningManager.get_stats() 的嵌套结构扁平化为前端期望的平铺字段。"""
    if not isinstance(raw, dict):
        raw = {}
    feedback = raw.get("feedback", {}) if isinstance(raw.get("feedback"), dict) else {}
    params = raw.get("params", {}) if isinstance(raw.get("params"), dict) else {}
    history = _safe_learning_history_list(raw.get("history"))

    total_hits = _coerce_learning_number(feedback.get("total_hits", 0), 0.0)
    total_recalls = max(
        _coerce_learning_int(feedback.get("total_recalls", 0), 0), 1
    )  # 避免除以零
    return {
        "hit_rate": round(total_hits / total_recalls, 4),
        "avg_quality": round(
            _coerce_learning_number(feedback.get("avg_quality", 0.5), 0.5), 4
        ),
        "total_trials": total_recalls,
        "total_corrections": _coerce_learning_int(
            feedback.get("total_corrections", 0), 0
        ),
        "parameters": params,
        "history": [
            {
                "timestamp": str(item.get("timestamp", "")),
                "action": item.get("reason", str(item.get("param", ""))),
                "detail": (
                    f"{item.get('param', '')}: {item.get('old', '')} → {item.get('new', '')}"
                    if "param" in item
                    else str(item.get("reason", ""))
                ),
            }
            for item in (entry if isinstance(entry, dict) else {} for entry in history)
        ],
        "enabled": bool(raw.get("enabled", True)),
    }


class LearningApiMixin:
    async def get_learning_status(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "auto_learning", None):
            return error_response("自主学习功能未启用")
        raw = engine.auto_learning.get_stats()
        return ok_response(_flatten_learning_stats(raw))

    async def get_learning_history(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "auto_learning", None):
            return error_response("自主学习功能未启用")
        args = request.args
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return error_response("limit 必须为整数")
        history = engine.auto_learning._optimizer.get_history(limit)
        history = _safe_learning_history_list(history)
        return ok_response({"history": history})

    async def reset_learning(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "auto_learning", None):
            return error_response("自主学习功能未启用")
        await engine.auto_learning.reset()
        return ok_response({"status": "reset"})


__all__ = ["LearningApiMixin"]
