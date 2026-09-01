"""总结 worker 的失败类型与固定质量门适配器。"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from ..domain.summary_models import SummaryFailure, SummaryReasonCode


def fixed_quality_key(candidate: Mapping[str, Any]) -> tuple[str, str]:
    """生成固定质量门使用的幂等键与正文摘要快照。"""
    metadata = candidate.get("metadata")
    idempotency_key = (
        str(metadata.get("idempotency_key") or "")
        if isinstance(metadata, Mapping)
        else ""
    )
    content = candidate.get("content")
    if not idempotency_key or not isinstance(content, str):
        raise ValueError("fixed_gate_candidate_invalid")
    return idempotency_key, hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


class SummaryWorkerFailure(RuntimeError):
    """携带固定失败分类，不保存异常正文。"""

    def __init__(
        self,
        failed_stage: str,
        reason_code: SummaryReasonCode,
        *,
        retryable: bool,
        exception_type: str = "",
    ) -> None:
        """保存 worker 可提交的固定失败字段。"""
        super().__init__(reason_code.value)
        self.failed_stage = failed_stage
        self.reason_code = reason_code
        self.retryable = retryable
        self.exception_type = exception_type

    def to_failure(self) -> SummaryFailure:
        """转换为不含异常正文的持久化失败 DTO。"""
        return SummaryFailure(
            failed_stage=self.failed_stage,
            reason_code=self.reason_code,
            exception_type=self.exception_type,
            retryable=self.retryable,
        )


def supports_keyword(call: Callable[..., object], keyword: str) -> bool:
    """判断协作 API 是否显式接受固定快照参数。"""
    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


async def canonical_hook_already_owned(_memory_id: int) -> None:
    """保持候选写入器调用形状；演化由 MemoryEngine 写后钩子唯一调度。"""


class FixedQualityGate:
    """向候选写入器回放同一固定快照下的门禁结果。"""

    def __init__(self, results: Mapping[tuple[str, str], object]) -> None:
        """按幂等键和正文摘要保存已验证的闭集门禁结果。"""
        self._results = dict(results)

    async def route_candidate(
        self,
        candidate: dict[str, Any],
        **_context: object,
    ) -> object:
        """返回预先求值的门禁结果；候选重写或重排时立即失败。"""
        try:
            key = fixed_quality_key(candidate)
        except (TypeError, ValueError) as error:
            raise RuntimeError("fixed_gate_candidate_invalid") from error
        result = self._results.get(key)
        if result is None:
            raise RuntimeError("fixed_gate_result_missing")
        return result


__all__ = [
    "FixedQualityGate",
    "SummaryWorkerFailure",
    "canonical_hook_already_owned",
    "fixed_quality_key",
    "supports_keyword",
]
