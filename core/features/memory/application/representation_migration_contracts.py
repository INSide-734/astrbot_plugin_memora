"""canonical 表示迁移的共享契约：版本、闭集原因码、报告白名单与投影校验。

本模块只包含可安全共享的常量、数据类型、错误与纯投影函数，不持有数据库或
引擎状态：

- ``TARGET_REPRESENTATION_VERSION`` / ``REPORT_SCHEMA`` / ``PLAN_SCHEMA`` 是
  版本化入口，报告与计划都必须显式携带。
- 所有原因码都在 ``_REASON_CODES`` 闭集内；报告投影 ``assert_report_is_privacy_safe``
  按字段白名单 + 固定类型校验，任何额外字段、类型漂移或疑似正文值都 fail-closed。
- 报告小节的阶段名也在闭集内，派生重建内部细节不会泄漏到报告。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from astrbot.api import logger

TARGET_REPRESENTATION_VERSION: Final[str] = "v2"
REPORT_SCHEMA: Final[str] = "memora-memory-representation-report-v1"
PLAN_SCHEMA: Final[str] = "memora-memory-representation-plan-v1"
CHECKPOINT_KEY_PREFIX: Final[str] = "representation_migration:apply:v1:"
DEFAULT_BATCH_SIZE: Final[int] = 200
# 与既有 canonical merge 相同的 merged 幂等键上限；迁移复用同一语义。
MAX_MERGED_IDEMPOTENCY_KEYS: Final[int] = 16

ACTION_REPRESENTATION_REWRITE: Final[str] = "representation_rewrite"
ACTION_OWNER_REINFORCE: Final[str] = "owner_reinforce"
PLAN_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_REPRESENTATION_REWRITE,
    ACTION_OWNER_REINFORCE,
)

WRITE_KIND_CONTENT: Final[str] = "content"
WRITE_KIND_METADATA: Final[str] = "metadata"
WRITE_KIND_NONE: Final[str] = "none"

OUTCOME_APPLIED: Final[str] = "applied"
OUTCOME_SKIPPED: Final[str] = "skipped"
OUTCOME_CONFLICT: Final[str] = "conflict"
OUTCOME_UNAVAILABLE: Final[str] = "unavailable"
OUTCOME_FAILED: Final[str] = "failed"

REASON_METADATA_INVALID: Final[str] = "metadata_invalid"
REASON_NOT_RECALLABLE: Final[str] = "not_recallable"
REASON_EVIDENCE_MAPPING_MISMATCH: Final[str] = "evidence_mapping_mismatch"
REASON_CONTENT_UNAVAILABLE: Final[str] = "content_unavailable"
REASON_CONTENT_PRESERVATION_UNPROVEN: Final[str] = "content_preservation_unproven"
REASON_REVISION_CONFLICT: Final[str] = "revision_conflict"
REASON_ALREADY_APPLIED: Final[str] = "already_applied"
REASON_SOURCE_NOT_FOUND: Final[str] = "source_not_found"
REASON_OWNER_NOT_FOUND: Final[str] = "owner_not_found"
REASON_OWNER_NOT_RECALLABLE: Final[str] = "owner_not_recallable"
REASON_APPLY_FAILED: Final[str] = "apply_failed"
REASON_DERIVED_REBUILD_UNAVAILABLE: Final[str] = "derived_rebuild_unavailable"
REASON_DERIVED_REBUILD_FAILED: Final[str] = "derived_rebuild_failed"
REASON_CHECKPOINT_UNAVAILABLE: Final[str] = "checkpoint_unavailable"
REASON_NOT_APPLICABLE: Final[str] = "not_applicable"
REASON_DRY_RUN: Final[str] = "dry_run"

# apply 计划解析与入口的稳定失败原因码（fail-closed，全部可安全展示）。
REASON_PLAN_UNREADABLE: Final[str] = "plan_unreadable"
REASON_PLAN_JSON_INVALID: Final[str] = "plan_json_invalid"
REASON_PLAN_INVALID: Final[str] = "plan_invalid"
REASON_PLAN_TARGET_UNSUPPORTED: Final[str] = "plan_target_unsupported"
REASON_CONFIRMATION_REQUIRED: Final[str] = "confirmation_required"
REASON_CONFIRMATION_MISMATCH: Final[str] = "confirmation_mismatch"
REASON_ENGINE_UNAVAILABLE: Final[str] = "engine_unavailable"
REASON_SCAN_UNAVAILABLE: Final[str] = "scan_unavailable"
REASON_REPORT_CANARY: Final[str] = "report_canary_detected"

_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        REASON_METADATA_INVALID,
        REASON_NOT_RECALLABLE,
        REASON_EVIDENCE_MAPPING_MISMATCH,
        REASON_CONTENT_UNAVAILABLE,
        REASON_CONTENT_PRESERVATION_UNPROVEN,
        REASON_REVISION_CONFLICT,
        REASON_ALREADY_APPLIED,
        REASON_SOURCE_NOT_FOUND,
        REASON_OWNER_NOT_FOUND,
        REASON_OWNER_NOT_RECALLABLE,
        REASON_APPLY_FAILED,
        REASON_DERIVED_REBUILD_UNAVAILABLE,
        REASON_DERIVED_REBUILD_FAILED,
        REASON_CHECKPOINT_UNAVAILABLE,
        REASON_NOT_APPLICABLE,
        REASON_DRY_RUN,
    }
)
_REASON_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
)

_REPORT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "report_schema",
        "mode",
        "target_representation_version",
        "status",
        "scanned_count",
        "eligible_count",
        "changed_count",
        "unchanged_count",
        "unavailable_count",
        "conflict_count",
        "applied_count",
        "skipped_count",
        "failed_count",
        "plan_items_count",
        "plan_written",
        "exhausted",
        "resumed",
        "action_counts",
        "reason_counts",
        "derived",
        "checkpoint",
    }
)
_DRY_RUN_REPORT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "report_schema",
        "mode",
        "target_representation_version",
        "status",
        "scanned_count",
        "eligible_count",
        "changed_count",
        "unchanged_count",
        "unavailable_count",
        "conflict_count",
        "plan_items_count",
        "plan_written",
        "exhausted",
        "action_counts",
        "reason_counts",
        "derived",
        "checkpoint",
    }
)
_APPLY_REPORT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "report_schema",
        "mode",
        "target_representation_version",
        "status",
        "plan_items_count",
        "resumed",
        "applied_count",
        "skipped_count",
        "unavailable_count",
        "conflict_count",
        "failed_count",
        "reason_counts",
        "derived",
        "checkpoint",
    }
)
_REPORT_STATUS_VALUES: Final[frozenset[str]] = frozenset({"completed", "degraded"})
_ACTION_COUNT_KEYS: Final[frozenset[str]] = frozenset(
    {WRITE_KIND_CONTENT, WRITE_KIND_METADATA, WRITE_KIND_NONE, OUTCOME_UNAVAILABLE}
)
_DERIVED_REPORT_KEYS: Final[frozenset[str]] = frozenset(
    {"status", "stage", "reason_code"}
)
_DERIVED_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"available", "degraded", "unavailable", "not_applicable"}
)
# 派生重建阶段的闭集阶段名；只用于报告定位失败阶段，不泄漏阶段内部细节。
_DERIVED_STAGE_ORDER: Final[tuple[str, ...]] = (
    "canonical",
    "indexes",
    "catalog",
    "graph",
    "evolution",
    "semantic_compression",
    "notes",
)
_DERIVED_STAGES: Final[frozenset[str]] = frozenset(_DERIVED_STAGE_ORDER)
_CHECKPOINT_REPORT_KEYS: Final[frozenset[str]] = frozenset({"status", "reason_code"})
_CHECKPOINT_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"recorded", "unavailable", "not_required"}
)
# 报告标量字段的固定类型：任何类型漂移都按 canary 处理，fail-closed。
_REPORT_TEXT_KEYS: Final[frozenset[str]] = frozenset(
    {"report_schema", "mode", "target_representation_version", "status"}
)
_REPORT_BOOL_KEYS: Final[frozenset[str]] = frozenset(
    {"plan_written", "exhausted", "resumed"}
)
_REPORT_COUNT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "scanned_count",
        "eligible_count",
        "changed_count",
        "unchanged_count",
        "unavailable_count",
        "conflict_count",
        "applied_count",
        "skipped_count",
        "failed_count",
        "plan_items_count",
    }
)
_REPORT_SECTION_KEYS: Final[frozenset[str]] = frozenset(
    {"action_counts", "reason_counts", "derived", "checkpoint"}
)

UpdateMemory = Callable[[int, dict[str, Any], str], Awaitable[Any]]
DerivedRebuild = Callable[[], Awaitable[Any]]


class RepresentationMigrationError(RuntimeError):
    """表示迁移的稳定失败；``reason`` 可安全展示给操作者。"""

    def __init__(self, reason: str) -> None:
        """保存稳定原因码，避免输出输入内容或异常正文。"""

        self.reason = reason
        super().__init__(reason)


class PlanValidationError(RepresentationMigrationError):
    """apply 计划缺失、格式非法或确认令牌不匹配。"""


class ReportCanaryError(RepresentationMigrationError):
    """脱敏报告出现非白名单字段或疑似正文值。"""


@dataclass(frozen=True, slots=True)
class RepresentationTarget:
    """单条 canonical 的目标表示；正文与 metadata 增量只在内存中传递。"""

    write_kind: str
    content: str
    metadata_updates: dict[str, Any]
    changed: bool
    reason_code: str | None = None


def assert_report_is_privacy_safe(report: Mapping[str, Any]) -> None:
    """fail-closed 校验报告：只允许白名单字段、计数与稳定标量。"""

    if set(report) - _REPORT_KEYS:
        raise ReportCanaryError(REASON_REPORT_CANARY)
    mode = report.get("mode")
    if mode == "dry_run" and set(report) != _DRY_RUN_REPORT_KEYS:
        raise ReportCanaryError(REASON_REPORT_CANARY)
    if mode == "apply" and set(report) != _APPLY_REPORT_KEYS:
        raise ReportCanaryError(REASON_REPORT_CANARY)
    if mode not in {"dry_run", "apply"}:
        raise ReportCanaryError(REASON_REPORT_CANARY)
    for key, value in report.items():
        if key in _REPORT_TEXT_KEYS:
            if not isinstance(value, str) or not _SAFE_TOKEN_PATTERN.fullmatch(value):
                raise ReportCanaryError(REASON_REPORT_CANARY)
            continue
        if key in _REPORT_BOOL_KEYS:
            if not isinstance(value, bool):
                raise ReportCanaryError(REASON_REPORT_CANARY)
            continue
        if key in _REPORT_COUNT_KEYS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReportCanaryError(REASON_REPORT_CANARY)
            continue
        if key in _REPORT_SECTION_KEYS:
            if not isinstance(value, Mapping):
                raise ReportCanaryError(REASON_REPORT_CANARY)
            _assert_report_section(key, value)
            continue
        raise ReportCanaryError(REASON_REPORT_CANARY)
    if report.get("report_schema") != REPORT_SCHEMA:
        raise ReportCanaryError(REASON_REPORT_CANARY)
    if report.get("target_representation_version") != TARGET_REPRESENTATION_VERSION:
        raise ReportCanaryError(REASON_REPORT_CANARY)
    if report.get("status") not in _REPORT_STATUS_VALUES:
        raise ReportCanaryError(REASON_REPORT_CANARY)


def _assert_report_section(key: str, section: Mapping[str, Any]) -> None:
    """校验报告的嵌套小节：字段、值域与计数形状都在白名单内。"""

    if key == "action_counts":
        if set(section) - _ACTION_COUNT_KEYS:
            raise ReportCanaryError(REASON_REPORT_CANARY)
        for value in section.values():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReportCanaryError(REASON_REPORT_CANARY)
        return
    if key == "reason_counts":
        for reason_code, count in section.items():
            if not isinstance(reason_code, str) or not _REASON_PATTERN.fullmatch(
                reason_code
            ):
                raise ReportCanaryError(REASON_REPORT_CANARY)
            if reason_code not in _REASON_CODES:
                raise ReportCanaryError(REASON_REPORT_CANARY)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ReportCanaryError(REASON_REPORT_CANARY)
        return
    if key == "derived":
        if set(section) - _DERIVED_REPORT_KEYS:
            raise ReportCanaryError(REASON_REPORT_CANARY)
        if section.get("status") not in _DERIVED_STATUS_VALUES:
            raise ReportCanaryError(REASON_REPORT_CANARY)
        stage = section.get("stage")
        if stage is not None and stage not in _DERIVED_STAGES:
            raise ReportCanaryError(REASON_REPORT_CANARY)
        _assert_optional_reason_code(section.get("reason_code"))
        return
    if key == "checkpoint":
        if set(section) - _CHECKPOINT_REPORT_KEYS:
            raise ReportCanaryError(REASON_REPORT_CANARY)
        if section.get("status") not in _CHECKPOINT_STATUS_VALUES:
            raise ReportCanaryError(REASON_REPORT_CANARY)
        _assert_optional_reason_code(section.get("reason_code"))
        return
    raise ReportCanaryError(REASON_REPORT_CANARY)


def _assert_optional_reason_code(value: Any) -> None:
    """原因码缺失或必须是闭集内的稳定码。"""

    if value is None:
        return
    if not isinstance(value, str):
        raise ReportCanaryError(REASON_REPORT_CANARY)
    if not _REASON_PATTERN.fullmatch(value) or value not in _REASON_CODES:
        raise ReportCanaryError(REASON_REPORT_CANARY)


def _bump(counts: dict[str, int], key: str) -> None:
    """按稳定原因码累加计数。"""

    counts[key] = counts.get(key, 0) + 1


def _sorted_counts(counts: Mapping[str, int]) -> dict[str, int]:
    """按原因码排序，保证报告形状稳定。"""

    return {key: int(counts[key]) for key in sorted(counts) if counts[key]}


def _zero_action_counts() -> dict[str, int]:
    """返回固定形状的动作计数。"""

    return {key: 0 for key in sorted(_ACTION_COUNT_KEYS)}


def _derived_section(
    status: str, stage: str | None, reason_code: str | None
) -> dict[str, Any]:
    """构造派生重建报告小节；阶段名与原因码均在闭集内。"""

    return {"status": status, "stage": stage, "reason_code": reason_code}


def _failed_stage(payload: Mapping[str, Any]) -> str | None:
    """从重建结果中定位首个失败阶段；非闭集阶段名不进入报告。"""

    stages = payload.get("stages")
    if not isinstance(stages, Mapping):
        canonical = payload.get("canonical")
        if isinstance(canonical, Mapping) and canonical.get("success") is False:
            return "canonical"
        return None
    for name in _DERIVED_STAGE_ORDER:
        stage = stages.get(name)
        if isinstance(stage, Mapping) and stage.get("status") == "failed":
            return name
    return None


def _log_summary(stage: str, report: Mapping[str, Any]) -> None:
    """输出脱敏运行摘要；只含组件、阶段、状态与聚合计数。"""

    derived = report.get("derived")
    derived_status = derived.get("status") if isinstance(derived, Mapping) else None
    checkpoint = report.get("checkpoint")
    checkpoint_status = (
        checkpoint.get("status") if isinstance(checkpoint, Mapping) else None
    )
    logger.info(
        "canonical 表示迁移 component=representation_migration stage=%s status=%s "
        "scanned_count=%d changed_count=%d unavailable_count=%d conflict_count=%d "
        "applied_count=%d failed_count=%d derived_status=%s checkpoint_status=%s",
        stage,
        report.get("status"),
        int(report.get("scanned_count", 0)),
        int(report.get("changed_count", report.get("applied_count", 0))),
        int(report.get("unavailable_count", 0)),
        int(report.get("conflict_count", 0)),
        int(report.get("applied_count", 0)),
        int(report.get("failed_count", 0)),
        derived_status,
        checkpoint_status,
    )


__all__ = [
    "ACTION_OWNER_REINFORCE",
    "ACTION_REPRESENTATION_REWRITE",
    "CHECKPOINT_KEY_PREFIX",
    "DEFAULT_BATCH_SIZE",
    "DerivedRebuild",
    "MAX_MERGED_IDEMPOTENCY_KEYS",
    "OUTCOME_APPLIED",
    "OUTCOME_CONFLICT",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "OUTCOME_UNAVAILABLE",
    "PLAN_ACTIONS",
    "PLAN_SCHEMA",
    "PlanValidationError",
    "REASON_ALREADY_APPLIED",
    "REASON_APPLY_FAILED",
    "REASON_CHECKPOINT_UNAVAILABLE",
    "REASON_CONFIRMATION_MISMATCH",
    "REASON_CONFIRMATION_REQUIRED",
    "REASON_CONTENT_PRESERVATION_UNPROVEN",
    "REASON_CONTENT_UNAVAILABLE",
    "REASON_DERIVED_REBUILD_FAILED",
    "REASON_DERIVED_REBUILD_UNAVAILABLE",
    "REASON_DRY_RUN",
    "REASON_ENGINE_UNAVAILABLE",
    "REASON_EVIDENCE_MAPPING_MISMATCH",
    "REASON_METADATA_INVALID",
    "REASON_NOT_APPLICABLE",
    "REASON_NOT_RECALLABLE",
    "REASON_OWNER_NOT_FOUND",
    "REASON_OWNER_NOT_RECALLABLE",
    "REASON_PLAN_INVALID",
    "REASON_PLAN_JSON_INVALID",
    "REASON_PLAN_TARGET_UNSUPPORTED",
    "REASON_PLAN_UNREADABLE",
    "REASON_REPORT_CANARY",
    "REASON_REVISION_CONFLICT",
    "REASON_SCAN_UNAVAILABLE",
    "REASON_SOURCE_NOT_FOUND",
    "REPORT_SCHEMA",
    "ReportCanaryError",
    "RepresentationMigrationError",
    "RepresentationTarget",
    "TARGET_REPRESENTATION_VERSION",
    "UpdateMemory",
    "WRITE_KIND_CONTENT",
    "WRITE_KIND_METADATA",
    "WRITE_KIND_NONE",
    "assert_report_is_privacy_safe",
]
