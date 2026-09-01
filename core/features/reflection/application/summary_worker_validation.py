"""总结 worker 的快照和身份校验辅助。"""

from __future__ import annotations

import inspect
from typing import Any, Mapping

from ..domain.summary_models import ClaimedJob, SummaryReasonCode


class SummaryWorkerValidationMixin:
    """提供快照参数适配与群聊身份校验。"""

    @staticmethod
    def _fixed_snapshot_kwargs(
        call: Any,
        claim: ClaimedJob,
        payload: Mapping[str, object],
        *,
        required: bool,
    ) -> dict[str, Any]:
        """向已完成快照接入的 API 传递同一 job 快照，否则保守阻塞。"""

        def supports(keyword: str) -> bool:
            try:
                parameters = inspect.signature(call).parameters.values()
            except (TypeError, ValueError):
                return False
            return any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                or parameter.name == keyword
                for parameter in parameters
            )

        fixed = bool(claim.gate_revision or payload)
        if supports("gate_snapshot_json"):
            kwargs: dict[str, Any] = {"gate_snapshot_json": claim.gate_snapshot_json}
            if supports("gate_revision"):
                kwargs["gate_revision"] = claim.gate_revision
            return kwargs
        if supports("gate_snapshot"):
            kwargs = {"gate_snapshot": payload}
            if supports("gate_revision"):
                kwargs["gate_revision"] = claim.gate_revision
            return kwargs
        if fixed and required:
            from .summary_worker import SummaryWorkerFailure

            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="SnapshotAdapterUnavailable",
            )
        return {}

    @staticmethod
    def _is_group_chat(claim: ClaimedJob) -> bool:
        """只使用已固化 chat_type/group_id 判定群聊，不猜测 session 标识。"""

        if claim.chat_type not in (None, "", "private", "group"):
            from .summary_worker import SummaryWorkerFailure

            raise SummaryWorkerFailure(
                "identity_scope",
                SummaryReasonCode.BLOCKED,
                retryable=False,
            )
        return claim.chat_type == "group" or bool(claim.group_id)


__all__ = ["SummaryWorkerValidationMixin"]
