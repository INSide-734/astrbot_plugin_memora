"""canonical 记忆表示的离线迁移：只读扫描、脱敏报告与显式 apply plan。

为什么 apply 必须是显式入口：

- 表示迁移会改写 canonical ``documents`` 的正文与 metadata。任何自动执行都可能
  在无人复核的计划上批量改写生产记忆，因此本模块默认只做只读扫描，输出只含
  白名单计数/状态/原因码的版本化报告；apply 必须由操作者显式提供版本化计划文件、
  确认令牌和每条记录的 ``expected_revision``。
- 迁移不物理删除 canonical 行、不生成第二套 ``doc_id``：正文改写复用
  ``MemoryEngine.update_memory(..., expected_revision=...)`` 的既有 CAS 语义，
  因此 ``documents.id`` 保持唯一权威；dedup 强化只按既有 narrow owner 语义更新
  owner 的 merge 记账字段，不搬移正文、不删除 duplicate 行。
- 派生数据（FTS/向量/catalog/graph/evolution/notes）由既有
  ``DerivedRebuildCoordinator`` 在 apply 批次之后重建；派生失败只报告
  ``degraded``/``needs_repair``，既不回滚 canonical，也不伪造成功。

实现按职责拆分（本模块只做装配与公开导出，避免单文件继续膨胀）：

- ``representation_migration_contracts``：版本、闭集原因码、报告白名单与投影校验。
- ``representation_migration_plan``：显式 apply 计划的版本化载荷与 fail-closed 解析。
- ``representation_migration_target``：目标表示的纯函数计算（含文本不丢失证明）。
- ``representation_migration_scanner``：keyset 扫描、只读 dry-run 与 checkpoint 读写。
- ``representation_migration_apply``：逐条 revision CAS 写回、owner 强化与派生重建。

测试与验证边界：自动化测试只使用临时 SQLite 与内存 fixture，不连接真实实例；
apply 入口缺少授权运行时适配器时 fail-closed（``engine_unavailable``）。
"""

from __future__ import annotations

from typing import Any

from .representation_migration_apply import CanonicalRepresentationApplier
from .representation_migration_contracts import (
    ACTION_OWNER_REINFORCE,
    ACTION_REPRESENTATION_REWRITE,
    CHECKPOINT_KEY_PREFIX,
    DEFAULT_BATCH_SIZE,
    MAX_MERGED_IDEMPOTENCY_KEYS,
    OUTCOME_APPLIED,
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UNAVAILABLE,
    PLAN_ACTIONS,
    PLAN_SCHEMA,
    REASON_ALREADY_APPLIED,
    REASON_APPLY_FAILED,
    REASON_CHECKPOINT_UNAVAILABLE,
    REASON_CONFIRMATION_MISMATCH,
    REASON_CONFIRMATION_REQUIRED,
    REASON_CONTENT_PRESERVATION_UNPROVEN,
    REASON_CONTENT_UNAVAILABLE,
    REASON_DERIVED_REBUILD_FAILED,
    REASON_DERIVED_REBUILD_UNAVAILABLE,
    REASON_DRY_RUN,
    REASON_ENGINE_UNAVAILABLE,
    REASON_EVIDENCE_MAPPING_MISMATCH,
    REASON_METADATA_INVALID,
    REASON_NOT_APPLICABLE,
    REASON_NOT_RECALLABLE,
    REASON_OWNER_NOT_FOUND,
    REASON_OWNER_NOT_RECALLABLE,
    REASON_PLAN_INVALID,
    REASON_PLAN_JSON_INVALID,
    REASON_PLAN_TARGET_UNSUPPORTED,
    REASON_PLAN_UNREADABLE,
    REASON_REPORT_CANARY,
    REASON_REVISION_CONFLICT,
    REASON_SCAN_UNAVAILABLE,
    REASON_SOURCE_NOT_FOUND,
    REPORT_SCHEMA,
    TARGET_REPRESENTATION_VERSION,
    WRITE_KIND_CONTENT,
    WRITE_KIND_METADATA,
    WRITE_KIND_NONE,
    DerivedRebuild,
    PlanValidationError,
    ReportCanaryError,
    RepresentationMigrationError,
    RepresentationTarget,
    UpdateMemory,
    assert_report_is_privacy_safe,
)
from .representation_migration_plan import (
    MigrationPlan,
    MigrationPlanItem,
    build_migration_plan,
)
from .representation_migration_scanner import (
    CanonicalRepresentationScanner,
    DryRunOutcome,
)
from .representation_migration_target import (
    compute_representation_target,
    parse_metadata,
)


class CanonicalRepresentationMigrationService(CanonicalRepresentationApplier):
    """keyset 扫描 canonical 表示、输出脱敏报告，并按显式计划执行 CAS 写回。"""


def build_migration_service(
    engine: Any,
    *,
    db_connection: Any | None = None,
    derived_rebuild: DerivedRebuild | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> CanonicalRepresentationMigrationService:
    """按 MemoryEngine 既有能力装配迁移服务（不新增第二套 canonical 入口）。"""

    async def _update_memory(
        memory_id: int, updates: dict[str, Any], expected_revision: str
    ) -> Any:
        """以既有乐观校验语义提交 canonical 更新。"""

        return await engine.update_memory(
            memory_id,
            updates,
            expected_revision=expected_revision,
        )

    resolved_connection = (
        db_connection
        if db_connection is not None
        else getattr(engine, "db_connection", None)
    )
    if resolved_connection is None:
        raise RepresentationMigrationError(REASON_ENGINE_UNAVAILABLE)
    return CanonicalRepresentationMigrationService(
        db_connection=resolved_connection,
        update_memory=_update_memory,
        derived_rebuild=derived_rebuild,
        batch_size=batch_size,
    )


__all__ = [
    "ACTION_OWNER_REINFORCE",
    "ACTION_REPRESENTATION_REWRITE",
    "CHECKPOINT_KEY_PREFIX",
    "CanonicalRepresentationMigrationService",
    "CanonicalRepresentationScanner",
    "DEFAULT_BATCH_SIZE",
    "DerivedRebuild",
    "DryRunOutcome",
    "MAX_MERGED_IDEMPOTENCY_KEYS",
    "MigrationPlan",
    "MigrationPlanItem",
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
    "build_migration_plan",
    "build_migration_service",
    "compute_representation_target",
    "parse_metadata",
]
