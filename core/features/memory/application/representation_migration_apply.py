"""显式 apply 计划的逐条执行：revision CAS 写回、owner 强化与派生重建。

apply 是唯一会改写 canonical 的入口，因此这里只做四件事：

1. 复读计划条目对应的 canonical 行，重新计算目标表示；行已达标则跳过
   （``already_applied``），不可迁移则按闭集原因码计数，都不写库。
2. 比较计划中的 ``expected_revision`` 与当前 revision，不一致只记
   ``revision_conflict``，绝不覆盖并发写入。
3. 通过既有 ``MemoryEngine.update_memory(..., expected_revision=...)`` 提交
   CANONICAL 正文/metadata 或 narrow owner merge 记账；正文改写保持整数 ID，
   duplicate 行不物理删除。
4. 写回返回失败时回读判定：确认已达标按 ``already_applied`` 记账，其余按
   ``revision_conflict`` 计数 —— 既不伪造成功，也不重复写入。

``owner_reinforce`` 只更新 owner 的 ``merge_count`` / ``last_merged_at`` /
``merged_idempotency_keys``（限 16），不搬移正文、不创建第二套 canonical 存储。

批次之后由既有 ``DerivedRebuildCoordinator`` 契约重建派生面；派生失败只报告
``degraded``/``needs_repair``，既不回滚 canonical，也不伪造成功。
``asyncio.CancelledError`` 一律继续传播。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

from ....shared.memory_status import is_memory_recallable
from .representation_migration_contracts import (
    ACTION_OWNER_REINFORCE,
    MAX_MERGED_IDEMPOTENCY_KEYS,
    OUTCOME_APPLIED,
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UNAVAILABLE,
    REASON_ALREADY_APPLIED,
    REASON_APPLY_FAILED,
    REASON_CHECKPOINT_UNAVAILABLE,
    REASON_CONFIRMATION_MISMATCH,
    REASON_CONFIRMATION_REQUIRED,
    REASON_DERIVED_REBUILD_FAILED,
    REASON_DERIVED_REBUILD_UNAVAILABLE,
    REASON_ENGINE_UNAVAILABLE,
    REASON_METADATA_INVALID,
    REASON_NOT_APPLICABLE,
    REASON_OWNER_NOT_FOUND,
    REASON_OWNER_NOT_RECALLABLE,
    REASON_REVISION_CONFLICT,
    REASON_SOURCE_NOT_FOUND,
    REPORT_SCHEMA,
    WRITE_KIND_CONTENT,
    PlanValidationError,
    RepresentationMigrationError,
    UpdateMemory,
    _bump,
    _derived_section,
    _failed_stage,
    _log_summary,
    _sorted_counts,
    assert_report_is_privacy_safe,
)
from .representation_migration_plan import MigrationPlan, MigrationPlanItem
from .representation_migration_scanner import (
    CanonicalRepresentationScanner,
    _checkpoint_last_id,
)
from .representation_migration_target import (
    compute_representation_target,
    parse_metadata,
)


class CanonicalRepresentationApplier(CanonicalRepresentationScanner):
    """在只读扫描能力之上执行显式 apply 计划。"""

    async def apply(
        self,
        plan: MigrationPlan,
        *,
        confirmation: str,
        resume: bool = True,
    ) -> dict[str, Any]:
        """按显式计划逐条 CAS 写回；CAS 冲突只计数，绝不覆盖。

        - 每条写回都携带计划中的 ``expected_revision``，由既有
          ``MemoryEngine.update_memory`` 执行 CAS。
        - 每 ``batch_size`` 条持久化一次 ``migration_status`` checkpoint；取消会
          继续传播，恢复时只重放最后一个批次边界之后的条目（重放幂等）。
        - 计划指纹决定 checkpoint 作用域，避免不同计划互相覆盖进度。
        """

        confirmation_token = confirmation.strip() if confirmation else ""
        if not confirmation_token:
            raise PlanValidationError(REASON_CONFIRMATION_REQUIRED)
        if confirmation_token != plan.operator_confirmation:
            raise PlanValidationError(REASON_CONFIRMATION_MISMATCH)
        self._require_update_memory()
        counts = {
            OUTCOME_APPLIED: 0,
            OUTCOME_SKIPPED: 0,
            OUTCOME_CONFLICT: 0,
            OUTCOME_FAILED: 0,
            OUTCOME_UNAVAILABLE: 0,
        }
        reason_counts: dict[str, int] = {}
        checkpoint_status = "not_required"
        start_after = 0
        if resume:
            checkpoint = await self._read_checkpoint(plan.fingerprint)
            if checkpoint is not None:
                start_after = _checkpoint_last_id(checkpoint)
        resumed = start_after > 0
        pending = [item for item in plan.items if item.memory_id > start_after]
        for index, item in enumerate(pending, start=1):
            outcome, reason_code = await self._apply_item(item)
            counts[outcome] += 1
            if reason_code is not None:
                _bump(reason_counts, reason_code)
            if index % self._batch_size == 0 or index == len(pending):
                recorded = await self._write_checkpoint(
                    plan.fingerprint,
                    last_processed_id=item.memory_id,
                    counts=counts,
                    resumed=resumed,
                    final=index == len(pending),
                )
                checkpoint_status = "recorded" if recorded else "unavailable"
        degraded = bool(
            counts[OUTCOME_CONFLICT]
            or counts[OUTCOME_FAILED]
            or counts[OUTCOME_UNAVAILABLE]
        )
        if counts[OUTCOME_APPLIED] > 0:
            derived_section, derived_degraded = await self._rebuild_derived()
            degraded = degraded or derived_degraded
        else:
            derived_section = _derived_section(
                "not_applicable", None, REASON_NOT_APPLICABLE
            )
        report = {
            "report_schema": REPORT_SCHEMA,
            "mode": "apply",
            "target_representation_version": plan.target_representation_version,
            "status": "degraded" if degraded else "completed",
            "plan_items_count": len(plan.items),
            "resumed": resumed,
            "applied_count": counts[OUTCOME_APPLIED],
            "skipped_count": counts[OUTCOME_SKIPPED],
            "unavailable_count": counts[OUTCOME_UNAVAILABLE],
            "conflict_count": counts[OUTCOME_CONFLICT],
            "failed_count": counts[OUTCOME_FAILED],
            "reason_counts": _sorted_counts(reason_counts),
            "derived": derived_section,
            "checkpoint": {
                "status": checkpoint_status,
                "reason_code": (
                    None
                    if checkpoint_status == "recorded"
                    else REASON_CHECKPOINT_UNAVAILABLE
                    if checkpoint_status == "unavailable"
                    else REASON_NOT_APPLICABLE
                ),
            },
        }
        assert_report_is_privacy_safe(report)
        _log_summary("apply", report)
        return report

    def _require_update_memory(self) -> UpdateMemory:
        """返回 canonical 写回端口；未装配时 fail-closed。"""

        update_memory = self._update_memory
        if update_memory is None:
            raise RepresentationMigrationError(REASON_ENGINE_UNAVAILABLE)
        return update_memory

    async def _apply_item(self, item: MigrationPlanItem) -> tuple[str, str | None]:
        """执行单条计划；返回闭集终态与可选原因码。"""

        if item.action == ACTION_OWNER_REINFORCE:
            return await self._apply_owner_reinforce(item)
        return await self._apply_representation_rewrite(item)

    async def _apply_representation_rewrite(
        self, item: MigrationPlanItem
    ) -> tuple[str, str | None]:
        """原地改写 canonical 正文/metadata，保持整数 ID 与 CAS 语义。"""

        update_memory = self._require_update_memory()
        row = await self._load_row(item.memory_id)
        if row is None:
            return OUTCOME_SKIPPED, REASON_SOURCE_NOT_FOUND
        target = compute_representation_target(row.content, row.metadata_raw)
        if target.reason_code is not None:
            return OUTCOME_UNAVAILABLE, target.reason_code
        if not target.changed:
            return OUTCOME_SKIPPED, REASON_ALREADY_APPLIED
        if row.revision != item.expected_revision:
            return OUTCOME_CONFLICT, REASON_REVISION_CONFLICT
        if target.write_kind == WRITE_KIND_CONTENT:
            updates: dict[str, Any] = {
                "content": target.content,
                "metadata": target.metadata_updates,
            }
        else:
            updates = {"metadata": target.metadata_updates}
        try:
            applied = await update_memory(
                item.memory_id, updates, item.expected_revision
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log_apply_failure("apply", error)
            return OUTCOME_FAILED, REASON_APPLY_FAILED
        if applied:
            return OUTCOME_APPLIED, None
        # 写回可能已提交（例如图派生刷新失败），必须回读判定，不能伪造结果。
        fresh = await self._load_row(item.memory_id)
        if fresh is not None:
            landed = compute_representation_target(fresh.content, fresh.metadata_raw)
            if landed.reason_code is None and not landed.changed:
                return OUTCOME_SKIPPED, REASON_ALREADY_APPLIED
        return OUTCOME_CONFLICT, REASON_REVISION_CONFLICT

    async def _apply_owner_reinforce(
        self, item: MigrationPlanItem
    ) -> tuple[str, str | None]:
        """按既有 narrow owner 语义更新 merge 记账，不删除 duplicate 行。"""

        update_memory = self._require_update_memory()
        owner_id = item.owner_memory_id
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
            return OUTCOME_UNAVAILABLE, REASON_METADATA_INVALID
        owner = await self._load_row(owner_id)
        if owner is None:
            return OUTCOME_SKIPPED, REASON_OWNER_NOT_FOUND
        duplicate = await self._load_row(item.memory_id)
        if duplicate is None:
            return OUTCOME_SKIPPED, REASON_SOURCE_NOT_FOUND
        owner_metadata = parse_metadata(owner.metadata_raw)
        if owner_metadata is None:
            return OUTCOME_UNAVAILABLE, REASON_METADATA_INVALID
        if not is_memory_recallable(owner_metadata):
            return OUTCOME_UNAVAILABLE, REASON_OWNER_NOT_RECALLABLE
        expected_revision = item.owner_expected_revision or ""
        if not expected_revision or owner.revision != expected_revision:
            return OUTCOME_CONFLICT, REASON_REVISION_CONFLICT
        baseline_merge_count = _merge_count(owner_metadata)
        idempotency_key = f"representation-migration:{item.memory_id}"
        updates = {
            "metadata": {
                "merge_count": baseline_merge_count + 1,
                "last_merged_at": self._clock(),
                "merged_idempotency_keys": _merge_keys(owner_metadata, idempotency_key),
            }
        }
        try:
            applied = await update_memory(owner_id, updates, expected_revision)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log_apply_failure("owner_reinforce", error)
            return OUTCOME_FAILED, REASON_APPLY_FAILED
        if applied:
            return OUTCOME_APPLIED, None
        fresh = await self._load_row(owner_id)
        if fresh is not None:
            landed = parse_metadata(fresh.metadata_raw)
            if landed is not None and (
                idempotency_key in _merged_keys(landed)
                or _merge_count(landed) > baseline_merge_count
            ):
                return OUTCOME_SKIPPED, REASON_ALREADY_APPLIED
        return OUTCOME_CONFLICT, REASON_REVISION_CONFLICT

    async def _rebuild_derived(self) -> tuple[dict[str, Any], bool]:
        """调用既有派生重建契约；失败只报告降级，不回滚 canonical。"""

        rebuild = self._derived_rebuild
        if rebuild is None:
            return (
                _derived_section(
                    "unavailable", None, REASON_DERIVED_REBUILD_UNAVAILABLE
                ),
                True,
            )
        try:
            result = await rebuild()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log_derived_failure(error)
            return (
                _derived_section("degraded", None, REASON_DERIVED_REBUILD_FAILED),
                True,
            )
        payload = result if isinstance(result, Mapping) else {}
        if payload.get("success") is True:
            return _derived_section("available", None, None), False
        return (
            _derived_section(
                "degraded", _failed_stage(payload), REASON_DERIVED_REBUILD_FAILED
            ),
            True,
        )


def _merge_count(metadata: Mapping[str, Any]) -> int:
    """读取既有 merge 计数；非法值按 0 处理（与 canonical merge 一致）。"""

    value = metadata.get("merge_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _merged_keys(metadata: Mapping[str, Any]) -> list[str]:
    """读取既有 merged 幂等键；忽略非法项。"""

    value = metadata.get("merged_idempotency_keys")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _merge_keys(metadata: Mapping[str, Any], key: str) -> list[str]:
    """追加幂等键并保留最近 ``MAX_MERGED_IDEMPOTENCY_KEYS`` 项。"""

    keys = [item for item in _merged_keys(metadata) if item != key]
    keys.append(key)
    return keys[-MAX_MERGED_IDEMPOTENCY_KEYS:]


def _log_apply_failure(stage: str, error: Exception) -> None:
    """输出脱敏写回失败日志；只含阶段、原因码与异常类型。"""

    logger.warning(
        "canonical 表示迁移 component=representation_migration "
        "stage=%s status=failed reason_code=%s exception_type=%s",
        stage,
        REASON_APPLY_FAILED,
        error.__class__.__name__,
    )


def _log_derived_failure(error: Exception) -> None:
    """输出脱敏派生重建失败日志；只含原因码与异常类型。"""

    logger.error(
        "canonical 表示迁移 component=representation_migration "
        "stage=derived_rebuild status=failed reason_code=%s exception_type=%s",
        REASON_DERIVED_REBUILD_FAILED,
        error.__class__.__name__,
    )


__all__ = [
    "CanonicalRepresentationApplier",
]
