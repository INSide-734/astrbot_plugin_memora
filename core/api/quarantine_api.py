"""pre-canonical 记忆隔离队列 Page API。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from quart import request

from ..review.memory_quality_gate import QuarantineApprovalPendingError
from .response_utils import error_response

_VALID_STATUSES = {"pending", "approving", "approved", "rejected", "blocked"}
_MISSING = object()


def _parse_positive_memory_id(value: Any) -> int:
    """Parse a JSON memory ID without allowing bool or coercive values."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("quarantine_canonical_id_required")
    return value


def _parse_candidate_correlation(
    value: Any,
    *,
    candidate_id: str,
    canonical_memory_id: int | None,
) -> int | None:
    """Validate optional client correlation and reconcile its canonical ID."""

    if value is None:
        return canonical_memory_id
    if isinstance(value, str):
        if value.strip() != candidate_id:
            raise ValueError("quarantine_candidate_correlation_invalid")
        return canonical_memory_id
    if not isinstance(value, Mapping):
        raise ValueError("quarantine_candidate_correlation_invalid")
    if set(value) - {"candidate_id", "canonical_memory_id"}:
        raise ValueError("quarantine_candidate_correlation_invalid")
    correlated_candidate_id = value.get("candidate_id")
    if (
        not isinstance(correlated_candidate_id, str)
        or correlated_candidate_id.strip() != candidate_id
    ):
        raise ValueError("quarantine_candidate_correlation_invalid")
    if "canonical_memory_id" not in value:
        return canonical_memory_id
    try:
        correlated_memory_id = _parse_positive_memory_id(
            value.get("canonical_memory_id")
        )
    except ValueError as exc:
        raise ValueError("quarantine_candidate_correlation_invalid") from exc
    if canonical_memory_id is not None and correlated_memory_id != canonical_memory_id:
        raise ValueError("quarantine_candidate_correlation_invalid")
    return correlated_memory_id


class QuarantineApiMixin:
    """提供隔离候选列表、详情和 revision 保护的处置入口。"""

    def _get_memory_quality_gate(self) -> Any:
        """从已发布初始化器读取唯一质量门实例。"""

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        gate = getattr(initializer, "memory_quality_gate", None)
        if gate is None:
            raise RuntimeError("memory_quality_gate_unavailable")
        return gate

    async def list_quarantine_candidates(self) -> dict[str, Any]:
        """列出低敏候选摘要，不返回内部身份、窗口或证据指纹。"""

        try:
            status = str(request.args.get("status") or "").strip() or None
            if status is not None and status not in _VALID_STATUSES:
                return error_response(
                    "隔离状态无效",
                    code="quarantine_status_invalid",
                )
            raw_limit = request.args.get("limit", 50)
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                return error_response(
                    "分页数量无效",
                    code="quarantine_limit_invalid",
                )
            gate = self._get_memory_quality_gate()
            candidates = await gate.store.list_candidates(
                status=status,
                limit=min(200, max(1, limit)),
            )
            items = [self._public_quarantine_candidate(item) for item in candidates]
            return self._ok({"items": items, "total": len(items)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[QuarantineAPI] 列表读取失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return error_response(
                "隔离候选读取失败",
                code="quarantine_list_failed",
            )

    async def get_quarantine_candidate_detail(self) -> dict[str, Any]:
        """返回单条候选正文、匿名 offset 证据和低敏动作历史。"""

        candidate_id = str(request.args.get("candidate_id") or "").strip()
        if not candidate_id:
            return error_response(
                "candidate_id 不能为空",
                code="quarantine_candidate_id_required",
            )
        try:
            gate = self._get_memory_quality_gate()
            candidate = await gate.store.get_candidate(candidate_id)
            if candidate is None:
                return error_response(
                    "隔离候选不存在",
                    code="quarantine_candidate_not_found",
                )
            actions = await gate.store.list_actions(candidate_id)
            public_candidate = self._public_quarantine_candidate(
                candidate,
                include_content=True,
            )
            return self._ok(
                {
                    "item": public_candidate,
                    "actions": [
                        self._public_quarantine_action(action) for action in actions
                    ],
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[QuarantineAPI] 详情读取失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return error_response(
                "隔离候选详情读取失败",
                code="quarantine_detail_failed",
            )

    async def apply_quarantine_action(self) -> dict[str, Any]:
        """执行批准、修正后批准或拒绝，并强制校验 expected_revision。"""

        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error_response(
                "请求体必须为 JSON 对象",
                code="quarantine_payload_invalid",
            )
        candidate_id = str(payload.get("candidate_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        expected_revision = payload.get("expected_revision")
        action_payload = payload.get("payload") or {}
        if not candidate_id:
            return error_response(
                "candidate_id 不能为空",
                code="quarantine_candidate_id_required",
            )
        if action not in {"approve", "reject"}:
            return error_response(
                "隔离动作不受支持",
                code="quarantine_action_unsupported",
            )
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            return error_response(
                "expected_revision 必须为整数",
                code="quarantine_revision_required",
            )
        if not isinstance(action_payload, dict):
            return error_response(
                "payload 必须为对象",
                code="quarantine_action_payload_invalid",
            )

        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        _, ready_error = await self._ensure_plugin_ready()
        if ready_error:
            return ready_error

        try:
            gate = self._get_memory_quality_gate()
            if action == "approve":
                content = action_payload.get("content")
                if content is not None and not isinstance(content, str):
                    return error_response(
                        "修正正文必须为字符串",
                        code="quarantine_content_invalid",
                    )
                result = await gate.approve(
                    candidate_id,
                    expected_revision=expected_revision,
                    actor_id="dashboard",
                    content=content,
                )
            else:
                result = await gate.reject(
                    candidate_id,
                    expected_revision=expected_revision,
                    actor_id="dashboard",
                )
            return self._ok(
                {
                    "candidate": self._public_quarantine_candidate(
                        result,
                        include_content=True,
                    ),
                    "action": action,
                }
            )
        except asyncio.CancelledError:
            raise
        except KeyError:
            return error_response(
                "隔离候选不存在",
                code="quarantine_candidate_not_found",
            )
        except ValueError as exc:
            code = str(exc)
            allowed_codes = {
                "quarantine_revision_conflict",
                "quarantine_status_conflict",
                "quarantine_content_required",
                "quarantine_content_too_long",
            }
            if code not in allowed_codes:
                code = "quarantine_action_invalid"
            return error_response("隔离候选状态冲突", code=code)
        except QuarantineApprovalPendingError as exc:
            return error_response(
                "canonical 已写入但隔离状态尚未收口，请执行 repair",
                code="quarantine_approval_pending",
                data={
                    "candidate_id": exc.candidate_id,
                    "revision": exc.revision,
                    "approval_token": exc.approval_token,
                },
            )
        except Exception as exc:
            logger.error(
                "[QuarantineAPI] 动作执行失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return error_response(
                "隔离候选动作执行失败",
                code="quarantine_action_failed",
            )

    async def repair_quarantine_approval(self) -> dict[str, Any]:
        """由管理员核对 canonical 事实，收口 approving 或安全退回 blocked。"""

        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error_response(
                "请求体必须为 JSON 对象",
                code="quarantine_repair_payload_invalid",
            )
        raw_candidate_id = payload.get("candidate_id")
        candidate_id = (
            raw_candidate_id.strip() if isinstance(raw_candidate_id, str) else ""
        )
        action = str(payload.get("action") or "").strip()
        expected_revision = payload.get("expected_revision")
        if not candidate_id:
            return error_response(
                "candidate_id 不能为空",
                code="quarantine_candidate_id_required",
            )
        if action not in {"approve", "block"}:
            return error_response(
                "repair 动作不受支持",
                code="quarantine_repair_action_unsupported",
            )
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            return error_response(
                "expected_revision 必须为整数",
                code="quarantine_revision_required",
            )

        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        _, ready_error = await self._ensure_plugin_ready()
        if ready_error:
            return ready_error

        try:
            gate = self._get_memory_quality_gate()
            if action == "approve":
                raw_canonical_memory_id = payload.get("canonical_memory_id", _MISSING)
                canonical_memory_id: int | None = None
                if (
                    raw_canonical_memory_id is not _MISSING
                    and raw_canonical_memory_id is not None
                ):
                    try:
                        canonical_memory_id = _parse_positive_memory_id(
                            raw_canonical_memory_id
                        )
                    except ValueError:
                        return error_response(
                            "canonical_memory_id 必须为正整数",
                            code="quarantine_canonical_id_required",
                        )
                if "candidate_correlation" in payload:
                    try:
                        canonical_memory_id = _parse_candidate_correlation(
                            payload["candidate_correlation"],
                            candidate_id=candidate_id,
                            canonical_memory_id=canonical_memory_id,
                        )
                    except ValueError:
                        return error_response(
                            "隔离候选关联无效",
                            code="quarantine_candidate_correlation_invalid",
                        )
                if canonical_memory_id is None:
                    return error_response(
                        "canonical_memory_id 必须为正整数",
                        code="quarantine_canonical_id_required",
                    )
                approval_token = payload.get("approval_token")
                if approval_token is not None and not isinstance(approval_token, str):
                    return error_response(
                        "approval_token 无效",
                        code="quarantine_approval_token_invalid",
                    )
                if isinstance(approval_token, str):
                    approval_token = approval_token.strip()
                if approval_token == "":
                    return error_response(
                        "approval_token 不能为空",
                        code="quarantine_approval_token_required",
                    )
                result = await gate.repair_approval(
                    candidate_id,
                    expected_revision=expected_revision,
                    canonical_memory_id=canonical_memory_id,
                    approval_token=approval_token,
                    actor_id="dashboard",
                )
            else:
                result = await gate.repair_blocked(
                    candidate_id,
                    expected_revision=expected_revision,
                    actor_id="dashboard",
                    confirm_canonical_absent=(
                        payload.get("confirm_canonical_absent") is True
                    ),
                )
            return self._ok(
                {
                    "candidate": self._public_quarantine_candidate(
                        result,
                        include_content=True,
                    ),
                    "action": action,
                }
            )
        except asyncio.CancelledError:
            raise
        except KeyError:
            return error_response(
                "隔离候选不存在",
                code="quarantine_candidate_not_found",
            )
        except ValueError as exc:
            code = str(exc)
            allowed_codes = {
                "quarantine_revision_conflict",
                "quarantine_status_conflict",
                "quarantine_approval_token_required",
                "quarantine_approval_token_invalid",
                "quarantine_candidate_correlation_invalid",
                "quarantine_canonical_not_found",
                "quarantine_canonical_mismatch",
                "quarantine_canonical_status_invalid",
                "quarantine_canonical_presence_conflict",
                "quarantine_canonical_absence_confirmation_required",
            }
            if code not in allowed_codes:
                code = "quarantine_repair_invalid"
            return error_response("隔离候选 repair 被拒绝", code=code)
        except Exception as exc:
            logger.error(
                "[QuarantineAPI] repair 执行失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return error_response(
                "隔离候选 repair 失败",
                code="quarantine_repair_failed",
            )

    @staticmethod
    def _public_quarantine_candidate(
        candidate: dict[str, Any],
        *,
        include_content: bool = False,
    ) -> dict[str, Any]:
        """将内部候选投影为不含身份和证据指纹的 API allowlist。"""

        content = str(candidate.get("content") or "")
        metadata = candidate.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        evidence = []
        for item in metadata.get("source_evidence") or []:
            if not isinstance(item, dict):
                continue
            evidence.append(
                {
                    "message_index": item.get("message_index"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "inferred": bool(item.get("inferred", False)),
                }
            )
        result = {
            "candidate_id": candidate.get("candidate_id"),
            "revision": candidate.get("revision"),
            "status": candidate.get("status"),
            "reason_codes": list(candidate.get("reason_codes") or []),
            "content_preview": content[:160],
            "importance": candidate.get("importance"),
            "source_evidence": evidence,
            "canonical_memory_id": candidate.get("canonical_memory_id"),
            "failure_reason": candidate.get("failure_reason"),
            "created_at": candidate.get("created_at"),
            "updated_at": candidate.get("updated_at"),
        }
        if include_content:
            result["content"] = content
        return result

    @staticmethod
    def _public_quarantine_action(action: dict[str, Any]) -> dict[str, Any]:
        """仅公开动作类型、稳定原因码和时间，不公开操作者身份。"""

        payload = action.get("payload")
        public_payload: dict[str, Any] = {}
        if isinstance(payload, dict) and isinstance(payload.get("reason_code"), str):
            public_payload["reason_code"] = payload["reason_code"]
        if isinstance(payload, dict) and isinstance(
            payload.get("content_changed"), bool
        ):
            public_payload["content_changed"] = payload["content_changed"]
        return {
            "action_id": action.get("action_id"),
            "candidate_id": action.get("candidate_id"),
            "action": action.get("action"),
            "payload": public_payload,
            "created_at": action.get("created_at"),
        }


__all__ = ["QuarantineApiMixin"]
