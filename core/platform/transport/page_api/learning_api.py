"""控制台 API：自主学习 shadow 候选、基线参数与发布状态。"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from quart import request

from ....features.learning.infrastructure.learning_config_adapter import (
    LearningConfigAdapter,
)
from .learning_reload_scheduler import schedule_learning_reload_operation
from .learning_reload_view import learning_reload_view
from .response_utils import error_response, ok_response

_ACTION_FIELDS = frozenset({"action", "candidate_id", "expected_revision", "confirm"})
_ACTION_BODY_MAX_BYTES = 16_384
_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22,128}\Z", re.ASCII)
_LEARNING_WEIGHT_PATHS = (
    "graph_memory.document_route_weight",
    "graph_memory.graph_route_weight",
)
_PUBLICATION_STATUSES = frozenset(
    {"active", "superseded", "rolled_back", "recovery_required"}
)
_LOW_SENSITIVITY_CODE_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)
_CANDIDATE_STATUSES = frozenset(
    {
        "ready_for_review",
        "rejected",
        "published",
        "stale",
        "recovery_required",
        "rolled_back",
        "invalid_state",
    }
)
_CANDIDATE_REASONS = frozenset(
    {
        "candidate",
        "insufficient_evidence",
        "conflicting_evidence",
        "quality_gate_failed",
        "published",
        "stale",
        "recovery_required",
        "rolled_back",
        "legacy_migrated",
        "invalid_state",
    }
)
_ACTION_ERRORS: dict[str, tuple[int, str, bool]] = {
    "invalid_request": (400, "自主学习动作请求无效", False),
    "learning_candidate_unavailable": (404, "自主学习候选不可用", False),
    "learning_action_not_allowed": (409, "当前状态不允许该学习动作", False),
    "learning_publish_in_progress": (409, "自主学习发布正在进行", True),
    "learning_rollback_in_progress": (409, "自主学习回滚正在进行", True),
    "config_revision_conflict": (409, "配置修订已变化", True),
    "config_noop": (409, "目标权重与当前配置相同", False),
    "config_diverged": (409, "当前配置已偏离发布链", False),
    "config_validation_failed": (422, "生产权重未通过配置校验", False),
    "learning_unavailable": (503, "自主学习动作暂不可用", False),
    "learning_state_persistence_failed": (503, "自主学习状态保存失败", False),
    "learning_publish_recovery_required": (
        503,
        "自主学习发布需要人工恢复",
        False,
    ),
    "learning_rollback_recovery_required": (
        503,
        "自主学习回滚需要人工恢复",
        False,
    ),
}
_ACTION_REASON_ALIASES = {
    "disabled": "learning_unavailable",
    "config_persistence_failed": "learning_unavailable",
    "learning_state_recovery_required": "learning_unavailable",
    "stale": "learning_candidate_unavailable",
    "rejected": "learning_candidate_unavailable",
    "insufficient_evidence": "learning_candidate_unavailable",
    "conflicting_evidence": "learning_candidate_unavailable",
    "quality_gate_failed": "learning_candidate_unavailable",
    "invalid_state": "learning_candidate_unavailable",
}


class _DuplicateJsonKeyError(ValueError):
    """表示原始 JSON 对象包含重复键。"""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """将 JSON 对象键值对转为字典，并拒绝任意层级的重复键。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError("learning_action_duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """拒绝 JSON 标准之外的 NaN 与 Infinity 常量。"""

    raise ValueError(f"learning_action_json_constant_invalid:{value}")


def _is_safe_revision(value: object) -> bool:
    """判断配置 revision 是否为 1 到 128 字节的非空 ASCII 字符串。"""

    if not isinstance(value, str) or not value.strip():
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return 1 <= len(encoded) <= 128


def _is_opaque_id(value: object) -> bool:
    """判断动作 ID 是否符合受限 ASCII URL-safe 格式。"""

    return isinstance(value, str) and _OPAQUE_ID_PATTERN.fullmatch(value) is not None


def _learning_error(
    code: str,
    *,
    result: Mapping[str, Any] | None = None,
    reload_state: str | None = None,
) -> tuple[dict[str, Any], int]:
    """构造固定 HTTP 状态、消息、重试语义和低敏可选提交证据。"""

    normalized = _ACTION_REASON_ALIASES.get(code, code)
    status_code, message, retryable = _ACTION_ERRORS.get(
        normalized,
        _ACTION_ERRORS["learning_unavailable"],
    )
    if normalized not in _ACTION_ERRORS:
        normalized = "learning_unavailable"
    error: dict[str, Any] = {
        "code": normalized,
        "message": message,
        "retryable": retryable,
    }
    source = result if isinstance(result, Mapping) else {}
    operation_id = source.get("operation_id")
    if _is_opaque_id(operation_id):
        error["operation_id"] = operation_id
    if source.get("config_applied") is True:
        error["config_applied"] = True
        applied_revision = source.get("applied_revision")
        if _is_safe_revision(applied_revision):
            error["applied_revision"] = applied_revision
        if reload_state in {"queued", "restart_required"}:
            error["reload"] = {"state": reload_state}
    return {"status": "error", "error": error}, status_code


def _request_content_length(request_source: object) -> int | None:
    """从 Quart 或 PluginRequest 读取可信的 Content-Length 数值。"""

    try:
        value = getattr(request_source, "content_length", None)
    except Exception:
        value = None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        headers = getattr(request_source, "headers", None)
        getter = getattr(headers, "get", None)
        header_value = getter("Content-Length") if callable(getter) else None
        if isinstance(header_value, str) and header_value.isascii():
            return int(header_value)
    except (TypeError, ValueError, AttributeError):
        return None
    return None


async def _read_raw_request_body(request_source: object) -> object:
    """优先读取 PluginRequest.body，并兼容 Quart get_data 原始字节接口。"""

    body_reader = getattr(request_source, "body", None)
    if callable(body_reader):
        result = body_reader()
    else:
        data_reader = getattr(request_source, "get_data", None)
        if not callable(data_reader):
            raise TypeError("learning_action_raw_body_unavailable")
        result = data_reader(cache=True)
    return await result if inspect.isawaitable(result) else result


async def _read_learning_action_payload(
    request_source: object,
) -> tuple[dict[str, Any] | None, Any]:
    """读取有限原始 body，拒绝重复键，并校验严格四字段动作 schema。"""

    content_length = _request_content_length(request_source)
    if (
        isinstance(content_length, int)
        and not isinstance(content_length, bool)
        and content_length > _ACTION_BODY_MAX_BYTES
    ):
        return None, _learning_error("invalid_request")
    try:
        raw_body = await _read_raw_request_body(request_source)
    except asyncio.CancelledError:
        raise
    except Exception:
        return None, _learning_error("invalid_request")
    if not isinstance(raw_body, (bytes, bytearray)):
        return None, _learning_error("invalid_request")
    raw_bytes = bytes(raw_body)
    if not raw_bytes or len(raw_bytes) > _ACTION_BODY_MAX_BYTES:
        return None, _learning_error("invalid_request")
    try:
        payload = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return None, _learning_error("invalid_request")
    if not isinstance(payload, dict) or set(payload) != _ACTION_FIELDS:
        return None, _learning_error("invalid_request")
    if payload.get("action") not in {"publish", "rollback"}:
        return None, _learning_error("invalid_request")
    if not _is_opaque_id(payload.get("candidate_id")):
        return None, _learning_error("invalid_request")
    if not _is_safe_revision(payload.get("expected_revision")):
        return None, _learning_error("invalid_request")
    if payload.get("confirm") is not True:
        return None, _learning_error("invalid_request")
    return payload, None


async def _schedule_learning_reload(
    api: object,
    manager: object,
    *,
    action: str,
    candidate_id: str,
    result: Mapping[str, Any],
    changed_paths: tuple[str, ...],
) -> str:
    """校验 manager 结果后委托 reload operation 调度器。"""

    operation_id = result.get("operation_id")
    applied_revision = result.get("applied_revision")
    if (
        not changed_paths
        or not _is_opaque_id(operation_id)
        or not _is_safe_revision(applied_revision)
    ):
        return "restart_required"
    return await schedule_learning_reload_operation(
        api,
        manager,
        action=action,
        candidate_id=candidate_id,
        operation_id=str(operation_id),
        applied_revision=str(applied_revision),
        changed_paths=changed_paths,
    )


def _learning_success(
    action: str,
    candidate_id: str,
    result: Mapping[str, Any],
    *,
    reload_state: str,
) -> tuple[dict[str, Any], int]:
    """把 manager 成功结果裁剪为动作 API 的固定 allowlist。"""

    not_applied = action == "rollback" and result.get("reason_code") == "not_applied"
    status = (
        "not_applied"
        if not_applied
        else ("published" if action == "publish" else "restored")
    )
    reason_code = status
    data: dict[str, Any] = {
        "action": action,
        "candidate_id": candidate_id,
        "status": status,
        "reason_code": reason_code,
    }
    for field in (
        "operation_id",
        "publication_revision",
        "active_publication_revision",
    ):
        value = result.get(field)
        if _is_opaque_id(value):
            data[field] = value
    applied_revision = result.get("applied_revision")
    if _is_safe_revision(applied_revision):
        data["applied_revision"] = applied_revision
    data["changed_paths"] = (
        [] if not_applied else _changed_paths_view(result.get("changed_paths"))
    )
    data["reload"] = {"state": reload_state}
    return ok_response(data), 200


def _changed_paths_view(value: object) -> list[str]:
    """按固定 canonical 顺序裁剪 typed adapter 返回的配置变更路径。"""

    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        return []
    selected = set(value)
    if not selected.issubset(_LEARNING_WEIGHT_PATHS):
        return []
    return [path for path in _LEARNING_WEIGHT_PATHS if path in selected]


def _candidate_view(candidate: object) -> dict:
    """把候选裁剪为只读 allowlist 视图。"""

    source = candidate if isinstance(candidate, dict) else {}
    return {
        "candidate_id": (
            source.get("candidate_id")
            if _is_opaque_id(source.get("candidate_id"))
            else None
        ),
        "expected_revision": (
            source.get("source_config_revision")
            if _is_safe_revision(source.get("source_config_revision"))
            else None
        ),
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


def _safe_reason_code(value: object) -> str | None:
    """只接受低基数 ASCII reason code，拒绝自由文本和结构化对象。"""

    if not isinstance(value, str):
        return None
    return value if _LOW_SENSITIVITY_CODE_PATTERN.fullmatch(value) else None


def _active_publication_view(value: object) -> dict[str, Any] | None:
    """把 manager 的 active publication 裁剪为固定低敏字段。"""

    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for field in (
        "publication_revision",
        "parent_publication_revision",
        "candidate_id",
    ):
        item = value.get(field)
        if item is None and field == "parent_publication_revision":
            result[field] = None
        elif _is_opaque_id(item):
            result[field] = item
    for field in ("requested_revision", "applied_revision"):
        item = value.get(field)
        if _is_safe_revision(item):
            result[field] = item
    status = value.get("status")
    result["status"] = _safe_code(status, _PUBLICATION_STATUSES, "recovery_required")
    published_at = value.get("published_at")
    if isinstance(published_at, str) and len(published_at) <= 64:
        result["published_at"] = published_at
    return result


def _operation_view(value: object) -> dict[str, Any] | None:
    """裁剪 status 中正在执行的 operation，不暴露 candidate 绑定。"""

    if not isinstance(value, Mapping):
        return None
    operation_id = value.get("operation_id")
    action = value.get("action")
    if not _is_opaque_id(operation_id) or action not in {"publish", "rollback"}:
        return None
    result: dict[str, Any] = {"operation_id": operation_id, "action": action}
    created_at = value.get("created_at")
    if isinstance(created_at, str) and len(created_at) <= 64:
        result["created_at"] = created_at
    return result


def _recovery_view(value: object) -> dict[str, Any]:
    """把 manager 恢复状态限制为布尔值、计数和稳定 reason。"""

    source = value if isinstance(value, Mapping) else {}
    return {
        "state_corrupt": source.get("state_corrupt") is True,
        "state_recovery_required": source.get("state_recovery_required") is True,
        "reason_code": _safe_reason_code(source.get("reason_code")),
        "intent_count": _safe_count(source.get("intent_count")) or 0,
        "record_count": _safe_count(source.get("record_count")) or 0,
        "operation": _operation_view(source.get("operation")),
    }


async def _read_learning_status_layers(
    auto_learning: object,
    adapter: LearningConfigAdapter,
    engine: object,
    *,
    baseline: dict[str, float],
) -> tuple[Mapping[str, Any], Any, dict[str, float], bool]:
    """最多重试一次前后快照绑定，返回 manager、持久化和运行时分层。"""

    latest: tuple[Mapping[str, Any], Any, dict[str, float], bool] | None = None
    for _attempt in range(2):
        before = await adapter.get_weight_snapshot()
        manager_status = await auto_learning.get_status_snapshot()
        if not isinstance(manager_status, Mapping):
            raise ValueError("learning_status_snapshot_invalid")
        effective = _weight_pair(getattr(engine, "config", {}), fallback=baseline)
        after = await adapter.get_weight_snapshot()
        consistent = (
            before.revision == after.revision
            and before.config_hash == after.config_hash
        )
        latest = (manager_status, after, effective, consistent)
        if consistent:
            return latest
    assert latest is not None
    return latest


class LearningApiMixin:
    """提供自主学习 shadow 候选与基线参数的只读状态。"""

    async def get_learning_status(self):
        """返回绑定 ConfigManager 前后快照的低敏生产学习状态。"""

        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        auto_learning = getattr(engine, "auto_learning", None)
        if auto_learning is None:
            return error_response("自主学习功能未启用")
        config_manager = getattr(getattr(self, "plugin", None), "config_manager", None)
        if config_manager is None:
            return error_response("自主学习配置状态不可用")
        feedback = getattr(engine, "feedback_signal_manager", None)
        policy = getattr(feedback, "policy", None) if feedback is not None else None
        baseline_document = _safe_number(
            getattr(policy, "baseline_document_weight", None), 0.0, 1.0
        )
        baseline_graph = _safe_number(
            getattr(policy, "baseline_graph_weight", None), 0.0, 1.0
        )
        baseline = {
            "document_route_weight": (
                baseline_document if baseline_document is not None else 0.65
            ),
            "graph_route_weight": baseline_graph
            if baseline_graph is not None
            else 0.35,
        }
        baseline = _weight_pair(
            baseline,
            fallback={"document_route_weight": 0.65, "graph_route_weight": 0.35},
        )
        try:
            (
                manager_status,
                persisted,
                current,
                snapshot_consistent,
            ) = await _read_learning_status_layers(
                auto_learning,
                LearningConfigAdapter(config_manager),
                engine,
                baseline=baseline,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[LearningApi] 读取自主学习原子状态失败 type=%s",
                type(exc).__name__,
            )
            return error_response("自主学习状态暂不可用")

        summary = _summary_view(manager_status)
        raw_candidates = manager_status.get("candidates")
        candidates = raw_candidates if isinstance(raw_candidates, list) else []
        persisted_weights = persisted.as_weights()
        runtime_matches_persisted = all(
            math.isclose(current[key], persisted_weights[key], abs_tol=1e-9)
            for key in ("document_route_weight", "graph_route_weight")
        )
        reload_view = learning_reload_view(
            manager_status.get("reload"),
            runtime_matches_persisted=runtime_matches_persisted,
        )
        state_revision = manager_status.get("state_revision")
        return ok_response(
            {
                "enabled": manager_status.get("enabled") is True,
                **summary,
                "state_revision": (
                    state_revision if _is_safe_revision(state_revision) else None
                ),
                "evidence_count": _safe_count(manager_status.get("evidence_count"))
                or 0,
                "publication_count": _safe_count(
                    manager_status.get("publication_count")
                )
                or 0,
                "snapshot_consistent": snapshot_consistent,
                "persisted_config": {
                    "revision": persisted.revision,
                    **persisted_weights,
                },
                "effective_runtime_config": {
                    "revision": None,
                    **current,
                    "matches_persisted": runtime_matches_persisted,
                },
                "active_publication": _active_publication_view(
                    manager_status.get("active_publication")
                ),
                "recovery": _recovery_view(manager_status.get("recovery")),
                "reload": reload_view,
                "candidates": [_candidate_view(item) for item in candidates],
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

    async def learning_action(self):
        """严格校验 publish/rollback 请求并委托 manager 执行生产配置动作。"""

        guard = self._maintenance_write_guard()
        if guard is not None:
            return guard
        request_source: object = request
        request_getter = getattr(self, "_get_web_request", None)
        if callable(request_getter):
            resolved_request = request_getter()
            if resolved_request is not None:
                request_source = resolved_request
        payload, request_error = await _read_learning_action_payload(request_source)
        if request_error is not None:
            return request_error
        assert payload is not None

        engines, ready_error = await self._ensure_plugin_ready()
        if ready_error is not None or not isinstance(engines, Mapping):
            return _learning_error("learning_unavailable")
        engine = engines.get("memory_engine")
        auto_learning = getattr(engine, "auto_learning", None)
        config_manager = getattr(getattr(self, "plugin", None), "config_manager", None)
        if auto_learning is None or config_manager is None:
            return _learning_error("learning_unavailable")

        action = str(payload["action"])
        candidate_id = str(payload["candidate_id"])
        expected_revision = str(payload["expected_revision"])
        adapter = LearningConfigAdapter(config_manager)
        try:
            if action == "publish":
                result = await auto_learning.publish_candidate(
                    candidate_id,
                    config_adapter=adapter,
                    expected_revision=expected_revision,
                )
            else:
                result = await auto_learning.rollback_last_publish(
                    candidate_id,
                    config_adapter=adapter,
                    expected_revision=expected_revision,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[LearningApi] 自主学习动作调用失败 type=%s",
                type(exc).__name__,
            )
            return _learning_error("learning_unavailable")

        if not isinstance(result, Mapping):
            return _learning_error("learning_unavailable")
        changed_paths = tuple(_changed_paths_view(result.get("changed_paths")))
        succeeded = (
            result.get("published") is True
            if action == "publish"
            else result.get("restored") is True
        )
        if succeeded:
            reload_state = (
                "not_required"
                if action == "rollback" and result.get("reason_code") == "not_applied"
                else await _schedule_learning_reload(
                    self,
                    auto_learning,
                    action=action,
                    candidate_id=candidate_id,
                    result=result,
                    changed_paths=changed_paths,
                )
            )
            return _learning_success(
                action,
                candidate_id,
                result,
                reload_state=reload_state,
            )

        reload_state = (
            await _schedule_learning_reload(
                self,
                auto_learning,
                action=action,
                candidate_id=candidate_id,
                result=result,
                changed_paths=changed_paths,
            )
            if result.get("config_applied") is True
            else None
        )
        return _learning_error(
            str(result.get("reason_code", "learning_unavailable")),
            result=result,
            reload_state=reload_state,
        )

    async def reset_learning(self):
        """清空 shadow 候选与发布快照，不触碰生产配置。"""

        guard_method = getattr(self, "_maintenance_write_guard", None)
        if callable(guard_method):
            guard = guard_method()
            if guard is not None:
                return guard
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
