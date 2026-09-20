"""canonical 表示迁移的显式 apply 计划：版本化载荷、解析与指纹。

计划是唯一的危险输入，因此解析是 fail-closed 的：

- 只允许 ``plan_schema``、``target_representation_version``、
  ``operator_confirmation``、``items`` 四个顶层字段，以及每条记录的
  ``memory_id`` / ``action`` / ``expected_revision``（``owner_reinforce`` 额外
  允许 owner 的 ID 与 revision）；任何额外字段——尤其是正文、query 或来源映射
  ——都会让整份计划被拒绝。
- 目标表示版本必须是当前代码支持的版本，不做任何兼容猜测。
- 条目按 canonical 整数 ID 升序排列，指纹（不含确认令牌）决定 checkpoint 作用域，
  使同一计划可以安全地中断/恢复，且不同计划不会互相覆盖进度。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .representation_migration_contracts import (
    ACTION_OWNER_REINFORCE,
    PLAN_ACTIONS,
    PLAN_SCHEMA,
    REASON_CONFIRMATION_REQUIRED,
    REASON_PLAN_INVALID,
    REASON_PLAN_JSON_INVALID,
    REASON_PLAN_TARGET_UNSUPPORTED,
    REASON_PLAN_UNREADABLE,
    TARGET_REPRESENTATION_VERSION,
    PlanValidationError,
)

_PLAN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "plan_schema",
        "target_representation_version",
        "operator_confirmation",
        "items",
    }
)
_PLAN_ITEM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "memory_id",
        "action",
        "expected_revision",
        "owner_memory_id",
        "owner_expected_revision",
    }
)


@dataclass(frozen=True, slots=True)
class MigrationPlanItem:
    """apply 计划中的单条操作；只含 canonical ID 与读取时的 revision。"""

    memory_id: int
    action: str
    expected_revision: str
    owner_memory_id: int | None = None
    owner_expected_revision: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """转换为计划文件条目；不包含正文、scope 或其他敏感字段。"""

        payload: dict[str, Any] = {
            "memory_id": self.memory_id,
            "action": self.action,
            "expected_revision": self.expected_revision,
        }
        if self.action == ACTION_OWNER_REINFORCE:
            payload["owner_memory_id"] = self.owner_memory_id
            payload["owner_expected_revision"] = self.owner_expected_revision
        return payload


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """版本化 apply 计划；指纹决定 checkpoint 作用域。"""

    operator_confirmation: str
    items: tuple[MigrationPlanItem, ...]
    fingerprint: str
    plan_schema: str = PLAN_SCHEMA
    target_representation_version: str = TARGET_REPRESENTATION_VERSION

    def to_payload(self) -> dict[str, Any]:
        """转换为可直接写盘的版本化计划文件。"""

        return {
            "plan_schema": self.plan_schema,
            "target_representation_version": self.target_representation_version,
            "operator_confirmation": self.operator_confirmation,
            "items": [item.to_payload() for item in self.items],
        }

    @classmethod
    def parse(cls, payload: Any) -> "MigrationPlan":
        """解析并 fail-closed 校验计划；任何非白名单字段都拒绝。"""

        document = _load_json_payload(payload)
        if set(document) - _PLAN_KEYS:
            raise PlanValidationError(REASON_PLAN_INVALID)
        if document.get("plan_schema") != PLAN_SCHEMA:
            raise PlanValidationError(REASON_PLAN_INVALID)
        if document.get("target_representation_version") != (
            TARGET_REPRESENTATION_VERSION
        ):
            raise PlanValidationError(REASON_PLAN_TARGET_UNSUPPORTED)
        confirmation = document.get("operator_confirmation")
        if not isinstance(confirmation, str) or not confirmation.strip():
            raise PlanValidationError(REASON_PLAN_INVALID)
        raw_items = document.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise PlanValidationError(REASON_PLAN_INVALID)
        items = tuple(_parse_plan_item(entry) for entry in raw_items)
        if len({item.memory_id for item in items}) != len(items):
            raise PlanValidationError(REASON_PLAN_INVALID)
        items = tuple(sorted(items, key=lambda item: item.memory_id))
        return cls(
            operator_confirmation=confirmation.strip(),
            items=items,
            fingerprint=_plan_fingerprint(items),
        )


def build_migration_plan(
    items: Sequence[MigrationPlanItem],
    *,
    operator_confirmation: str,
) -> MigrationPlan:
    """从已核对的条目构造计划；确认令牌缺失或条目为空时拒绝。"""

    confirmation = operator_confirmation.strip() if operator_confirmation else ""
    if not confirmation:
        raise PlanValidationError(REASON_CONFIRMATION_REQUIRED)
    if not items:
        raise PlanValidationError(REASON_PLAN_INVALID)
    ordered = tuple(sorted(items, key=lambda item: item.memory_id))
    if len({item.memory_id for item in ordered}) != len(ordered):
        raise PlanValidationError(REASON_PLAN_INVALID)
    return MigrationPlan(
        operator_confirmation=confirmation,
        items=ordered,
        fingerprint=_plan_fingerprint(ordered),
    )


def _plan_fingerprint(items: Sequence[MigrationPlanItem]) -> str:
    """对计划条目计算稳定指纹；不包含确认令牌、正文或 scope。"""

    canonical = json.dumps(
        [item.to_payload() for item in items],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(
        f"{PLAN_SCHEMA}\x00{TARGET_REPRESENTATION_VERSION}\x00{canonical}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]


def _load_json_payload(payload: Any) -> dict[str, Any]:
    """读取计划输入；非对象、非 JSON 或非法编码一律 fail-closed。"""

    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PlanValidationError(REASON_PLAN_UNREADABLE) from error
    if not isinstance(payload, str):
        raise PlanValidationError(REASON_PLAN_UNREADABLE)
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as error:
        raise PlanValidationError(REASON_PLAN_JSON_INVALID) from error
    if not isinstance(document, Mapping):
        raise PlanValidationError(REASON_PLAN_INVALID)
    return dict(document)


def _parse_plan_item(entry: Any) -> MigrationPlanItem:
    """解析单条计划；额外字段（可能是正文或隐私 canary）一律拒绝。"""

    if not isinstance(entry, Mapping) or set(entry) - _PLAN_ITEM_KEYS:
        raise PlanValidationError(REASON_PLAN_INVALID)
    memory_id = entry.get("memory_id")
    if isinstance(memory_id, bool) or not isinstance(memory_id, int) or memory_id < 1:
        raise PlanValidationError(REASON_PLAN_INVALID)
    action = entry.get("action")
    if action not in PLAN_ACTIONS:
        raise PlanValidationError(REASON_PLAN_INVALID)
    expected_revision = entry.get("expected_revision")
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise PlanValidationError(REASON_PLAN_INVALID)
    owner_memory_id = entry.get("owner_memory_id")
    owner_expected_revision = entry.get("owner_expected_revision")
    if action == ACTION_OWNER_REINFORCE:
        if (
            isinstance(owner_memory_id, bool)
            or not isinstance(owner_memory_id, int)
            or owner_memory_id < 1
        ):
            raise PlanValidationError(REASON_PLAN_INVALID)
        if (
            not isinstance(owner_expected_revision, str)
            or not owner_expected_revision.strip()
        ):
            raise PlanValidationError(REASON_PLAN_INVALID)
    elif owner_memory_id is not None or owner_expected_revision is not None:
        raise PlanValidationError(REASON_PLAN_INVALID)
    return MigrationPlanItem(
        memory_id=memory_id,
        action=action,
        expected_revision=expected_revision.strip(),
        owner_memory_id=owner_memory_id if action == ACTION_OWNER_REINFORCE else None,
        owner_expected_revision=(
            owner_expected_revision.strip()
            if action == ACTION_OWNER_REINFORCE
            and isinstance(owner_expected_revision, str)
            else None
        ),
    )


__all__ = [
    "MigrationPlan",
    "MigrationPlanItem",
    "build_migration_plan",
]
