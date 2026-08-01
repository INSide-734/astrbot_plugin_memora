"""控制台 API：自主学习 shadow 候选、基线参数与发布状态。"""

from __future__ import annotations

import math

from quart import request

from .response_utils import error_response, ok_response

_CANDIDATE_STATUSES = frozenset(
    {"ready_for_review", "rejected", "published", "invalid_state"}
)
_CANDIDATE_REASONS = frozenset(
    {"candidate", "insufficient_evidence", "published", "invalid_state"}
)


def _candidate_view(candidate: object) -> dict:
    """把候选裁剪为只读 allowlist 视图。"""

    source = candidate if isinstance(candidate, dict) else {}
    return {
        "proposed_document_weight": _safe_number(
            source.get("proposed_document_weight"), 0.0, 1.0
        ),
        "proposed_graph_weight": _safe_number(
            source.get("proposed_graph_weight"), 0.0, 1.0
        ),
        "delta_from_baseline": _safe_number(
            source.get("delta_from_baseline"), -0.4, 0.4
        ),
        "accepted_count": _safe_count(source.get("accepted_count")),
        "independent_window_count": _safe_count(source.get("independent_window_count")),
        "decayed_support": _safe_number(source.get("decayed_support"), 0.0, 1.0),
        "status": _safe_code(
            source.get("status"), _CANDIDATE_STATUSES, "invalid_state"
        ),
        "reason_code": _safe_code(
            source.get("reason_code"), _CANDIDATE_REASONS, "invalid_state"
        ),
    }


def _summary_view(summary: object) -> dict:
    """把 Manager 摘要限制为固定计数和原因码。"""

    source = summary if isinstance(summary, dict) else {}
    raw_reasons = source.get("reasons")
    reasons = raw_reasons if isinstance(raw_reasons, list) else []
    return {
        "available": source.get("available") is True,
        "candidate_count": _safe_count(source.get("candidate_count")) or 0,
        "ready_count": _safe_count(source.get("ready_count")) or 0,
        "rejected_count": _safe_count(source.get("rejected_count")) or 0,
        "published_count": _safe_count(source.get("published_count")) or 0,
        "reasons": sorted(
            {_safe_code(item, _CANDIDATE_REASONS, "invalid_state") for item in reasons}
        ),
    }


def _weight_pair(source: object, *, fallback: dict[str, float]) -> dict[str, float]:
    """读取有限且总和为一的文档路/图路权重。"""

    mapping = source if isinstance(source, dict) else {}
    document = _safe_number(mapping.get("document_route_weight"), 0.0, 1.0)
    graph = _safe_number(mapping.get("graph_route_weight"), 0.0, 1.0)
    if (
        document is None
        or graph is None
        or not math.isclose(document + graph, 1.0, abs_tol=1e-6)
    ):
        return dict(fallback)
    return {"document_route_weight": document, "graph_route_weight": graph}


def _safe_number(value: object, minimum: float, maximum: float) -> float | None:
    """只接受指定闭区间内的有限数字，拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        return None
    return numeric


def _safe_count(value: object) -> int | None:
    """只接受非负整数计数，拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_code(value: object, allowed: frozenset[str], fallback: str) -> str:
    """只允许固定低基数状态或原因码。"""

    return value if isinstance(value, str) and value in allowed else fallback


class LearningApiMixin:
    """提供自主学习 shadow 候选与基线参数的只读状态。"""

    async def get_learning_status(self):
        """返回当前基线、候选数量、发布状态与拒绝原因。"""

        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        auto_learning = getattr(engine, "auto_learning", None)
        if auto_learning is None:
            return error_response("自主学习功能未启用")
        feedback = getattr(engine, "feedback_signal_manager", None)
        policy = getattr(feedback, "policy", None) if feedback is not None else None
        summary = _summary_view(auto_learning.safe_summary())
        baseline = {
            "document_route_weight": _safe_number(
                getattr(policy, "baseline_document_weight", None), 0.0, 1.0
            )
            or 0.65,
            "graph_route_weight": _safe_number(
                getattr(policy, "baseline_graph_weight", None), 0.0, 1.0
            )
            or 0.35,
        }
        baseline = _weight_pair(
            baseline,
            fallback={"document_route_weight": 0.65, "graph_route_weight": 0.35},
        )
        engine_config = getattr(engine, "config", {})
        current = _weight_pair(engine_config, fallback=baseline)
        return ok_response(
            {
                "enabled": bool(getattr(auto_learning, "enabled", True)),
                **summary,
                "candidates": [
                    _candidate_view(item) for item in auto_learning.get_candidates()
                ],
                "current": current,
                "baseline": baseline,
            }
        )

    async def get_learning_history(self):
        """返回最近的 shadow 候选（兼容历史端点命名）。"""

        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        auto_learning = getattr(engine, "auto_learning", None)
        if auto_learning is None:
            return error_response("自主学习功能未启用")
        args = request.args
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return error_response("limit 必须为整数")
        if limit <= 0 or limit > 100:
            return error_response("limit 必须在 1 到 100 之间")
        candidates = auto_learning.get_candidates()[-limit:]
        return ok_response({"history": [_candidate_view(item) for item in candidates]})

    async def reset_learning(self):
        """清空 shadow 候选与发布快照，不触碰生产配置。"""

        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        auto_learning = getattr(engine, "auto_learning", None)
        if auto_learning is None:
            return error_response("自主学习功能未启用")
        await auto_learning.reset()
        return ok_response({"status": "reset"})


__all__ = ["LearningApiMixin"]
