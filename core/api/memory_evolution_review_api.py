"""Memory Evolution 高影响派生候选复核 Page API。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from quart import request

from ..models.memory_evolution import DerivedState
from ..storage.memory_evolution_review import (
    DerivedReviewConflictError,
    DerivedReviewNotAllowedError,
    DerivedReviewNotFoundError,
    DerivedReviewSourceError,
)
from .response_utils import error_response

_ACTIONS = frozenset({"approve", "reject", "replay"})
_ACTION_FIELDS = frozenset({"candidate_id", "action", "expected_revision"})


class MemoryEvolutionReviewApiMixin:
    """Mixin：派生 relation 候选列表、详情和复核动作。"""

    async def list_memory_evolution_review_candidates(self):
        """列出指定状态的高影响 relation 复核候选。"""

        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        del ready
        try:
            store = self._get_memory_evolution_review_store()
            state = str(request.args.get("status") or DerivedState.CANDIDATE.value)
            limit = _positive_int(request.args.get("limit", 50), name="limit")
            items = await store.list_relation_review_candidates(
                state=state,
                limit=min(200, limit),
            )
            return self._ok({"items": [_candidate_payload(item) for item in items]})
        except ValueError:
            return error_response("派生复核查询参数无效", code="invalid_request")
        except Exception as exc:
            logger.error("[派生复核 API] 列表失败：%s", type(exc).__name__)
            return error_response("派生复核列表读取失败", code="derived_review_failed")

    async def get_memory_evolution_review_candidate(self):
        """返回单个派生候选及其低敏动作历史。"""

        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        del ready
        candidate_id = str(request.args.get("candidate_id") or "").strip()
        if not candidate_id:
            return error_response("candidate_id required", code="invalid_request")
        try:
            store = self._get_memory_evolution_review_store()
            candidate = await store.get_relation_review_candidate(candidate_id)
            if candidate is None:
                return error_response(
                    "派生复核候选不存在",
                    code="derived_review_not_found",
                )
            actions = await store.list_relation_review_actions(candidate_id)
            return self._ok(
                {
                    "candidate": _candidate_payload(candidate),
                    "actions": [_action_payload(action) for action in actions],
                }
            )
        except Exception as exc:
            logger.error("[派生复核 API] 详情失败：%s", type(exc).__name__)
            return error_response("派生复核详情读取失败", code="derived_review_failed")

    async def apply_memory_evolution_review_action(self):
        """以候选 revision CAS 执行 approve、reject 或 replay。"""

        try:
            payload = await request.get_json(silent=True)
            candidate_id, action, expected_revision = _parse_action_payload(payload)
        except ValueError:
            return error_response("派生复核请求无效", code="invalid_request")

        guard = self._maintenance_write_guard()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        del ready
        try:
            store = self._get_memory_evolution_review_store()
            result = await store.review_relation_candidate(
                candidate_id,
                action=action,
                expected_revision=expected_revision,
            )
            return self._ok(
                {
                    "candidate_id": result["relation_id"],
                    "candidate_revision": result["revision"],
                    "state": result["state"],
                    "action": action,
                }
            )
        except DerivedReviewConflictError:
            return error_response(
                "派生候选已变化，请刷新后重试",
                code="derived_review_conflict",
            )
        except DerivedReviewNotFoundError:
            return error_response(
                "派生复核候选不存在",
                code="derived_review_not_found",
            )
        except DerivedReviewNotAllowedError:
            return error_response(
                "当前派生候选不允许该动作",
                code="derived_review_not_allowed",
            )
        except DerivedReviewSourceError as exc:
            return error_response(
                "派生候选来源已失效",
                code=exc.reason_code,
            )
        except Exception as exc:
            logger.error("[派生复核 API] 动作失败：%s", type(exc).__name__)
            return error_response("派生复核动作失败", code="derived_review_failed")

    def _get_memory_evolution_review_store(self):
        """从已初始化插件获取唯一 Memory Evolution Store。"""

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        store = getattr(initializer, "memory_evolution_store", None)
        if store is None:
            raise RuntimeError("Memory Evolution Store 未初始化")
        return store


def _parse_action_payload(payload: Any) -> tuple[str, str, int]:
    """严格解析派生复核动作请求体。"""

    if not isinstance(payload, dict) or set(payload) - _ACTION_FIELDS:
        raise ValueError("invalid action payload")
    candidate_id = str(payload.get("candidate_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if not candidate_id or action not in _ACTIONS:
        raise ValueError("invalid derived review action")
    expected_revision = _positive_int(
        payload.get("expected_revision"),
        name="expected_revision",
    )
    return candidate_id, action, expected_revision


def _positive_int(value: Any, *, name: str) -> int:
    """解析严格正整数，禁止布尔值冒充 revision 或 limit。"""

    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    """输出不含 source、scope、privacy、正文和 job 的候选 DTO。"""

    return {
        "candidate_id": candidate["relation_id"],
        "candidate_revision": candidate["revision"],
        "relation_type": candidate["relation_type"],
        "state": candidate["state"],
        "confidence": candidate["confidence"],
        "created_at": candidate.get("created_at"),
        "updated_at": candidate.get("updated_at"),
    }


def _action_payload(action: dict[str, Any]) -> dict[str, Any]:
    """输出不含 relation/source 标识和操作者身份的动作 DTO。"""

    return {
        "action": action["action"],
        "expected_revision": action["expected_revision"],
        "previous_state": action["previous_state"],
        "new_state": action["new_state"],
        "candidate_revision": action["result_revision"],
        "reason_code": action["reason_code"],
        "created_at": action["created_at"],
    }


__all__ = ["MemoryEvolutionReviewApiMixin"]
