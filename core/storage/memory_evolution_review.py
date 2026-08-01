"""高影响 Memory Evolution relation 的人工复核持久化。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    RelationType,
    RelationView,
)
from ..models.temporal import serialize_datetime
from .memory_evolution_derived import _serialized_write

_HIGH_IMPACT_RELATION_TYPES = (
    RelationType.UPDATES.value,
    RelationType.CONTRADICTS.value,
    RelationType.PREFERENCE_CHANGE.value,
    RelationType.SUPERSEDES.value,
)
_REVIEW_ACTIONS = frozenset({"approve", "reject", "replay"})


class DerivedReviewNotFoundError(LookupError):
    """目标派生候选不存在。"""


class DerivedReviewConflictError(RuntimeError):
    """候选 revision 已变化，调用方必须重新读取。"""


class DerivedReviewNotAllowedError(ValueError):
    """relation 类型、状态或动作不允许当前转换。"""


class DerivedReviewSourceError(RuntimeError):
    """候选引用的 canonical source 已失效或边界不一致。"""

    def __init__(self, reason_code: str) -> None:
        """保存可对外映射的稳定来源失败码。"""

        super().__init__(reason_code)
        self.reason_code = reason_code


class MemoryEvolutionReviewMixin:
    """提供高影响 relation 候选的查询、CAS 动作与低敏审计。"""

    async def _create_relation_review_tables(self) -> None:
        """补齐 relation revision，并创建独立动作审计表。"""

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        columns = await self.connection.execute("PRAGMA table_info(memory_relations)")
        column_names = {str(row[1]) for row in await columns.fetchall()}
        if "revision" not in column_names:
            await self.connection.execute(
                "ALTER TABLE memory_relations "
                "ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_derived_review_actions (
              action_id TEXT PRIMARY KEY,
              relation_id TEXT NOT NULL,
              action TEXT NOT NULL,
              expected_revision INTEGER NOT NULL,
              previous_state TEXT NOT NULL,
              new_state TEXT NOT NULL,
              result_revision INTEGER NOT NULL,
              reason_code TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_derived_review_actions_relation
              ON memory_derived_review_actions(relation_id, created_at, action_id);
            """
        )

    async def list_relation_review_candidates(
        self,
        *,
        state: DerivedState | str = DerivedState.CANDIDATE,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按稳定顺序列出指定状态的高影响 relation 候选。"""

        state_value = _derived_state_value(state)
        safe_limit = _review_limit(limit)
        placeholders = ",".join("?" for _ in _HIGH_IMPACT_RELATION_TYPES)
        rows = await self._fetch_all(
            "SELECT relation_id,revision,relation_type,state,confidence,"
            "created_at,updated_at FROM memory_relations "
            f"WHERE relation_type IN ({placeholders}) AND state=? "
            "ORDER BY updated_at DESC, relation_id DESC LIMIT ?",
            (*_HIGH_IMPACT_RELATION_TYPES, state_value, safe_limit),
        )
        return [_candidate_view(row) for row in rows]

    async def get_relation_review_candidate(
        self,
        relation_id: str,
    ) -> dict[str, Any] | None:
        """读取单个高影响 relation 候选的低敏复核视图。"""

        row = await self._get_review_relation_row(relation_id)
        if row is None or row["relation_type"] not in _HIGH_IMPACT_RELATION_TYPES:
            return None
        return _candidate_view(row)

    async def list_relation_review_actions(
        self,
        relation_id: str,
    ) -> list[dict[str, Any]]:
        """按时间顺序读取 relation 的低敏复核动作历史。"""

        rows = await self._fetch_all(
            "SELECT action_id,action,expected_revision,previous_state,new_state,"
            "result_revision,reason_code,created_at "
            "FROM memory_derived_review_actions WHERE relation_id=? "
            "ORDER BY created_at ASC, action_id ASC",
            (str(relation_id),),
        )
        return [dict(row) for row in rows]

    @_serialized_write
    async def review_relation_candidate(
        self,
        relation_id: str,
        *,
        action: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """以 revision CAS 审批、拒绝或重放高影响 relation 候选。"""

        normalized_action = str(action).strip().lower()
        if normalized_action not in _REVIEW_ACTIONS:
            raise DerivedReviewNotAllowedError("unsupported_review_action")
        safe_revision = _expected_revision(expected_revision)
        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")

        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = await self._get_review_relation_row(relation_id)
            if row is None:
                raise DerivedReviewNotFoundError("derived_candidate_not_found")
            if row["relation_type"] not in _HIGH_IMPACT_RELATION_TYPES:
                raise DerivedReviewNotAllowedError("relation_not_reviewable")
            current_revision = int(row["revision"])
            if current_revision != safe_revision:
                raise DerivedReviewConflictError("derived_candidate_revision_conflict")

            previous_state = DerivedState(str(row["state"]))
            new_state = _next_review_state(previous_state, normalized_action)
            if normalized_action in {"approve", "replay"}:
                await self._validate_review_relation_sources(row)

            result_revision = current_revision + 1
            updated_at = serialize_datetime(datetime.now(timezone.utc))
            cursor = await self.connection.execute(
                "UPDATE memory_relations SET state=?,revision=?,updated_at=? "
                "WHERE relation_id=? AND revision=? AND state=?",
                (
                    new_state.value,
                    result_revision,
                    updated_at,
                    str(relation_id),
                    current_revision,
                    previous_state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise DerivedReviewConflictError("derived_candidate_revision_conflict")
            await self.connection.execute(
                "INSERT INTO memory_derived_review_actions "
                "(action_id,relation_id,action,expected_revision,previous_state,"
                "new_state,result_revision,reason_code,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    str(relation_id),
                    normalized_action,
                    current_revision,
                    previous_state.value,
                    new_state.value,
                    result_revision,
                    "derived_review_applied",
                    updated_at,
                ),
            )
            await self.connection.commit()
        except BaseException:
            await self.connection.rollback()
            raise

        stored = await self.get_relation_review_candidate(relation_id)
        if stored is None:
            raise RuntimeError("derived review action did not persist")
        return stored

    async def _get_review_relation_row(
        self,
        relation_id: str,
    ) -> dict[str, Any] | None:
        """读取复核与 source 校验需要的内部 relation 行。"""

        candidate_id = str(relation_id).strip()
        if not candidate_id:
            return None
        return await self._fetch_one(
            "SELECT * FROM memory_relations WHERE relation_id=?",
            (candidate_id,),
        )

    async def _validate_review_relation_sources(self, row: dict[str, Any]) -> None:
        """在 approve/replay 前复用派生写入边界重新校验 canonical source。"""

        relation = RelationView(
            relation_id=str(row["relation_id"]),
            source_memory_id=int(row["source_memory_id"]),
            target_memory_id=int(row["target_memory_id"]),
            relation_type=RelationType(str(row["relation_type"])),
            confidence=float(row["confidence"]),
            scope_key=str(row["scope_key"]),
            privacy_level=str(row["privacy_level"]),
            state=DerivedState(str(row["state"])),
            source_revision=str(row["source_revision"]),
            target_revision=str(row["target_revision"]),
        )
        plan = DerivedApplyPlan(
            relations=(relation,),
            source_revisions={
                relation.source_memory_id: str(row["source_revision"]),
                relation.target_memory_id: str(row["target_revision"]),
            },
        )
        try:
            await self._validate_plan_sources(plan)
        except ValueError as exc:
            raise DerivedReviewSourceError(_source_reason_code(exc)) from None


def _candidate_view(row: dict[str, Any]) -> dict[str, Any]:
    """把内部 relation 行投影为不含 source 与作用域的复核视图。"""

    return {
        "relation_id": str(row["relation_id"]),
        "revision": int(row["revision"]),
        "relation_type": str(row["relation_type"]),
        "state": str(row["state"]),
        "confidence": float(row["confidence"]),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _derived_state_value(state: DerivedState | str) -> str:
    """规范化外部状态筛选值。"""

    try:
        return (
            state.value
            if isinstance(state, DerivedState)
            else DerivedState(state).value
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown derived review state") from exc


def _review_limit(limit: int) -> int:
    """把复核列表上限限制到稳定的 1..200。"""

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("review limit must be an integer")
    return min(200, max(1, limit))


def _expected_revision(value: int) -> int:
    """验证候选 revision CAS 输入。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DerivedReviewConflictError("invalid_expected_revision")
    return value


def _next_review_state(previous: DerivedState, action: str) -> DerivedState:
    """根据显式动作和当前状态返回唯一允许的下一状态。"""

    transitions = {
        (DerivedState.CANDIDATE, "approve"): DerivedState.ACTIVE,
        (DerivedState.CANDIDATE, "reject"): DerivedState.REJECTED,
        (DerivedState.REJECTED, "replay"): DerivedState.CANDIDATE,
        (DerivedState.INVALIDATED, "replay"): DerivedState.CANDIDATE,
    }
    next_state = transitions.get((previous, action))
    if next_state is None:
        raise DerivedReviewNotAllowedError("derived_review_transition_not_allowed")
    return next_state


def _source_reason_code(exc: ValueError) -> str:
    """把内部 source 校验失败收敛为稳定、低敏 reason code。"""

    reason = str(exc)
    if reason in {
        "source_memory_not_found",
        "source_revision_mismatch",
        "source_scope_mismatch",
        "source_privacy_mismatch",
    }:
        return reason
    return "derived_source_invalid"


__all__ = [
    "DerivedReviewConflictError",
    "DerivedReviewNotAllowedError",
    "DerivedReviewNotFoundError",
    "DerivedReviewSourceError",
    "MemoryEvolutionReviewMixin",
]
