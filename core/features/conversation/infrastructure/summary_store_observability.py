"""总结任务常规日志的隐私安全投影。"""

from __future__ import annotations

from typing import Literal

from astrbot.api import logger

from ...reflection.domain.summary_models import (
    SummaryJobStatus,
    SummaryReasonCode,
    normalize_exception_type,
)

_SummaryStage = Literal["commit", "candidate_reconcile", "startup_reconcile"]
_SUMMARY_STAGES = frozenset({"commit", "candidate_reconcile", "startup_reconcile"})
_SUMMARY_STATUSES = frozenset(item.value for item in SummaryJobStatus)
_SUMMARY_REASONS = frozenset(item.value for item in SummaryReasonCode)


def _safe_count(value: object) -> int:
    """把观测计数压缩为非负整数。"""
    if type(value) is not int:
        return 0
    return max(0, value)


def _safe_stage(value: object) -> str | None:
    """只接受总结观测允许的固定阶段。"""
    return value if type(value) is str and value in _SUMMARY_STAGES else None


def _safe_status(value: object) -> str:
    """将任务状态限制在持久化状态闭集内。"""
    status = value.value if isinstance(value, SummaryJobStatus) else value
    return (
        status
        if type(status) is str and status in _SUMMARY_STATUSES
        else SummaryJobStatus.UNKNOWN.value
    )


def _safe_reason(value: object) -> str:
    """将 reason 限制在总结 reason 闭集内。"""
    reason = value.value if isinstance(value, SummaryReasonCode) else value
    return (
        reason
        if type(reason) is str and reason in _SUMMARY_REASONS
        else SummaryReasonCode.UNKNOWN.value
    )


def _safe_exception(value: object) -> str:
    """只投影归一化异常类别，不读取异常正文。"""
    if isinstance(value, BaseException):
        value = value.__class__.__name__
    elif isinstance(value, type):
        value = value.__name__
    elif type(value) is not str:
        value = value.__class__.__name__
    return normalize_exception_type(value) or "unknown"


def _log_terminal(
    stage: _SummaryStage,
    status: object,
    reason_code: object,
    exception_type: object,
    *,
    canonical_count: object = 0,
    quarantine_count: object = 0,
    discard_count: object = 0,
    mark_write_count: object = 0,
    merged_count: object = 0,
    failed_count: object = 0,
    skipped_count: object = 0,
    unknown_count: object = 0,
) -> None:
    """记录一次已成功提交的终态或候选对账结果。"""
    safe_stage = _safe_stage(stage)
    if safe_stage is None:
        return
    try:
        logger.warning(
            "总结观测 component=summary_store stage=%s status=%s "
            "reason_code=%s exception_type=%s canonical_count=%d "
            "quarantine_count=%d discard_count=%d mark_write_count=%d "
            "merged_count=%d failed_count=%d skipped_count=%d unknown_count=%d",
            safe_stage,
            _safe_status(status),
            _safe_reason(reason_code),
            _safe_exception(exception_type),
            _safe_count(canonical_count),
            _safe_count(quarantine_count),
            _safe_count(discard_count),
            _safe_count(mark_write_count),
            _safe_count(merged_count),
            _safe_count(failed_count),
            _safe_count(skipped_count),
            _safe_count(unknown_count),
        )
    except Exception:
        return


def log_summary_commit(
    status: object,
    reason_code: object,
    exception_type: object = None,
    *,
    canonical_count: object = 0,
    quarantine_count: object = 0,
    discard_count: object = 0,
    mark_write_count: object = 0,
    merged_count: object = 0,
    failed_count: object = 0,
    skipped_count: object = 0,
    unknown_count: object = 0,
) -> None:
    """记录 commit 阶段已提交的总结未知结果。"""
    _log_terminal(
        "commit",
        status,
        reason_code,
        exception_type,
        canonical_count=canonical_count,
        quarantine_count=quarantine_count,
        discard_count=discard_count,
        mark_write_count=mark_write_count,
        merged_count=merged_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        unknown_count=unknown_count,
    )


def log_summary_candidate_reconcile(
    status: object,
    reason_code: object,
    exception_type: object = None,
    *,
    canonical_count: object = 0,
    quarantine_count: object = 0,
    discard_count: object = 0,
    mark_write_count: object = 0,
    merged_count: object = 0,
    failed_count: object = 0,
    skipped_count: object = 0,
    unknown_count: object = 0,
) -> None:
    """记录 candidate reconcile 阶段已提交的未知结果。"""
    _log_terminal(
        "candidate_reconcile",
        status,
        reason_code,
        exception_type,
        canonical_count=canonical_count,
        quarantine_count=quarantine_count,
        discard_count=discard_count,
        mark_write_count=mark_write_count,
        merged_count=merged_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        unknown_count=unknown_count,
    )


def log_summary_startup_reconcile(
    *,
    scanned: object,
    recovered: object,
    preserved_unknown: object,
    fenced: object,
    evidence_error: object,
    reason_code: object = None,
) -> None:
    """记录启动对账提交后的固定汇总计数。"""
    try:
        safe_preserved = _safe_count(preserved_unknown)
        safe_fenced = _safe_count(fenced)
        safe_evidence = _safe_count(evidence_error)
        unresolved = safe_preserved > 0 or safe_fenced > 0
        status = "unknown" if unresolved else "completed"
        requested_reason = str(reason_code) if reason_code is not None else ""
        if requested_reason not in {
            "completed",
            "unknown",
            "ledger_unresolved",
            "epoch_fenced",
            "source_digest_mismatch",
        }:
            requested_reason = ""
        reason = requested_reason or (
            "completed"
            if not unresolved
            else "ledger_unresolved"
            if safe_evidence > 0 or safe_preserved > safe_fenced
            else "unknown"
        )
        logger.warning(
            "总结观测 component=summary_store stage=startup_reconcile "
            "status=%s reason_code=%s scanned_count=%d "
            "recovered_count=%d preserved_unknown_count=%d fenced_count=%d "
            "evidence_error_count=%d",
            status,
            reason,
            _safe_count(scanned),
            _safe_count(recovered),
            safe_preserved,
            safe_fenced,
            safe_evidence,
        )
    except Exception:
        return


__all__ = [
    "log_summary_candidate_reconcile",
    "log_summary_commit",
    "log_summary_startup_reconcile",
]
