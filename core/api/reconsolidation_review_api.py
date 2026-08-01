"""记忆再巩固候选复核 Page API。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from quart import request

from ..storage.reconsolidation_store import (
    ReconsolidationCandidateConflictError,
    ReconsolidationCandidateNotFoundError,
)
from .response_utils import error_response

_STATUSES = frozenset({"pending", "approved", "rejected", "failed", "rolled_back"})
_ACTIONS = frozenset({"approve", "reject", "rollback"})
_ACTION_FIELDS = frozenset({"candidate_id", "action"})


class ReconsolidationReviewApiMixin:
    """Mixin：再巩固候选列表、详情和人工动作。"""

    async def list_reconsolidation_review_candidates(self):
        """列出再巩固候选；默认只返回待人工处理的 pending 项。"""

        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        try:
            store = _store_from_engine(ready["memory_engine"])
            status = str(request.args.get("status") or "pending").strip().lower()
            if status != "all" and status not in _STATUSES:
                return error_response("再巩固状态无效", code="invalid_request")
            limit = _positive_int(request.args.get("limit", 50), name="limit")
            offset = _non_negative_int(
                request.args.get("offset", 0),
                name="offset",
            )
            page = await store.list_candidates_page(
                status=None if status == "all" else status,
                offset=offset,
                limit=min(200, limit),
            )
            return self._ok(
                {
                    **page,
                    "items": [_candidate_summary(item) for item in page["items"]],
                }
            )
        except RuntimeError:
            return error_response(
                "再巩固候选 Store 未初始化",
                code="reconsolidation_unavailable",
            )
        except ValueError:
            return error_response("再巩固查询参数无效", code="invalid_request")
        except Exception as exc:
            logger.error("[再巩固复核 API] 列表失败：%s", type(exc).__name__)
            return error_response(
                "再巩固候选列表读取失败",
                code="reconsolidation_review_failed",
            )

    async def get_reconsolidation_review_candidate(self):
        """返回候选正文对比和低敏动作历史。"""

        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        candidate_id = str(request.args.get("candidate_id") or "").strip()
        if not candidate_id:
            return error_response("缺少必填参数 candidate_id", code="invalid_request")
        try:
            store = _store_from_engine(ready["memory_engine"])
            candidate = await store.get_candidate(candidate_id)
            if candidate is None:
                return error_response(
                    "再巩固候选不存在",
                    code="reconsolidation_review_not_found",
                )
            actions = await store.list_actions(candidate_id)
            return self._ok(
                {
                    "candidate": _candidate_detail(candidate),
                    "actions": [_action_summary(action) for action in actions],
                }
            )
        except RuntimeError:
            return error_response(
                "再巩固候选 Store 未初始化",
                code="reconsolidation_unavailable",
            )
        except Exception as exc:
            logger.error("[再巩固复核 API] 详情失败：%s", type(exc).__name__)
            return error_response(
                "再巩固候选详情读取失败",
                code="reconsolidation_review_failed",
            )

    async def apply_reconsolidation_review_action(self):
        """执行 approve、reject 或 rollback，并以候选状态 CAS 保护动作。"""

        try:
            payload = await request.get_json(silent=True)
            candidate_id, action = _parse_action_payload(payload)
        except ValueError:
            return error_response("再巩固复核请求无效", code="invalid_request")

        guard = self._maintenance_write_guard()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        engine = ready["memory_engine"]
        manager = getattr(engine, "reconsolidation", None)
        if manager is None:
            return error_response(
                "再巩固功能未启用",
                code="reconsolidation_unavailable",
            )

        try:
            if action == "approve":
                result = await manager.apply_candidate(
                    candidate_id, engine.update_memory
                )
                if not result.get("applied"):
                    return error_response(
                        "再巩固候选来源已变化，未应用",
                        code=str(
                            result.get("reason_code") or "reconsolidation_apply_failed"
                        ),
                        data={"candidate_id": candidate_id, "status": "rejected"},
                    )
                status = str(result["candidate"]["status"])
            elif action == "reject":
                rejected = await manager.reject_candidate(candidate_id)
                status = str(rejected["status"])
            else:
                result = await manager.rollback_candidate(
                    candidate_id,
                    get_memory_cb=engine.get_memory,
                    update_memory_cb=engine.update_memory,
                )
                if not result.get("restored"):
                    return error_response(
                        "再巩固候选回滚失败",
                        code=str(
                            result.get("reason_code")
                            or "reconsolidation_rollback_failed"
                        ),
                        data={"candidate_id": candidate_id},
                    )
                status = "rolled_back"
            return self._ok(
                {
                    "candidate_id": candidate_id,
                    "action": action,
                    "status": status,
                }
            )
        except ReconsolidationCandidateConflictError:
            return error_response(
                "再巩固候选已变化，请刷新后重试",
                code="reconsolidation_review_conflict",
            )
        except ReconsolidationCandidateNotFoundError:
            return error_response(
                "再巩固候选不存在",
                code="reconsolidation_review_not_found",
            )
        except Exception as exc:
            logger.error("[再巩固复核 API] 动作失败：%s", type(exc).__name__)
            return error_response(
                "再巩固候选动作失败",
                code="reconsolidation_review_failed",
            )


def _store_from_engine(engine: Any):
    """从已就绪 MemoryEngine 获取唯一再巩固 Store。"""

    store = getattr(engine, "reconsolidation_store", None)
    if store is None:
        raise RuntimeError("reconsolidation store unavailable")
    return store


def _parse_action_payload(payload: Any) -> tuple[str, str]:
    """严格解析候选动作请求体，拒绝未知字段和空 ID。"""

    if not isinstance(payload, dict) or set(payload) - _ACTION_FIELDS:
        raise ValueError("invalid reconsolidation action payload")
    candidate_id = str(payload.get("candidate_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if not candidate_id or action not in _ACTIONS:
        raise ValueError("invalid reconsolidation action")
    return candidate_id, action


def _positive_int(value: Any, *, name: str) -> int:
    """解析严格正整数，禁止布尔值冒充 limit。"""

    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _non_negative_int(value: Any, *, name: str) -> int:
    """解析非负整数，禁止布尔值冒充 offset。"""

    if isinstance(value, bool):
        raise ValueError(f"{name} 必须为非负整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须为非负整数") from exc
    if parsed < 0:
        raise ValueError(f"{name} 必须为非负整数")
    return parsed


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    """输出列表 DTO，不暴露 canonical ID、revision、正文或 metadata。"""

    return {
        "candidate_id": candidate["candidate_id"],
        "status": candidate["status"],
        "change_summary": candidate["change_summary"],
        "evidence_type": candidate["evidence_type"],
        "reason_code": candidate["reason_code"],
        "created_at": candidate["created_at"],
        "updated_at": candidate["updated_at"],
    }


def _candidate_detail(candidate: dict[str, Any]) -> dict[str, Any]:
    """输出人工复核所需正文对比，不暴露 source revision 或 metadata。"""

    return {
        **_candidate_summary(candidate),
        "old_content": candidate["old_content"],
        "proposed_content": candidate["proposed_content"],
    }


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    """输出低敏动作历史，不返回候选内部字段或操作者身份。"""

    return {
        "action": action["action"],
        "reason_code": action["reason_code"],
        "created_at": action["created_at"],
    }


__all__ = ["ReconsolidationReviewApiMixin"]
