"""Jargon 管理员严格编辑服务。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from astrbot.api import logger

from ..api.editing_utils import finite_float, required_text
from ..base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    compute_entity_revision,
)
from .jargon_store import JargonStore, _meaning_revision_payload
from .models import JargonMeaning


_CREATE_FIELDS = frozenset(
    {
        "term",
        "group_id",
        "meaning",
        "confidence",
        "is_jargon",
        "is_confirmed",
        "is_global",
    }
)
_EDITABLE_FIELDS = frozenset(
    {"meaning", "confidence", "is_jargon", "is_confirmed", "is_global"}
)
_BATCH_ACTION_CHANGES: dict[str, dict[str, bool]] = {
    "confirm": {"is_confirmed": True},
    "unconfirm": {"is_confirmed": False},
    "set_global": {"is_global": True},
    "unset_global": {"is_global": False},
}
_BATCH_ACTIONS = frozenset({"delete", *_BATCH_ACTION_CHANGES})
_MutationResult = TypeVar("_MutationResult")


class JargonAdminService:
    """协调管理员发起的 Jargon 写入。"""

    def __init__(
        self,
        store: JargonStore,
        invalidate_group: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._invalidate_group = invalidate_group

    @staticmethod
    def revision_for(meaning: JargonMeaning) -> str:
        """根据完整持久化状态返回规范修订版本。"""

        return compute_entity_revision(_meaning_revision_payload(meaning))

    async def create(self, **fields: Any) -> JargonMeaning:
        """校验并严格创建管理员词条。"""

        meaning = self._validated_new_meaning(fields)
        return await self._run_mutation(
            self._store.create_strict(meaning),
            group_id=meaning.group_id,
            operation="create",
        )

    async def update(
        self,
        *,
        term: Any,
        group_id: Any,
        changes: Any,
        expected_revision: Any,
    ) -> JargonMeaning:
        """按修订版本应用经过完整校验的部分更新。"""

        normalized_term = required_text(term, field="term", maximum=128)
        normalized_group = required_text(group_id, field="group_id", maximum=128)
        revision = required_text(
            expected_revision, field="expected_revision", maximum=256
        )
        normalized_changes = self._validated_changes(changes)
        return await self._run_mutation(
            self._store.update_if_revision(
                normalized_term,
                normalized_group,
                normalized_changes,
                expected_revision=revision,
            ),
            group_id=normalized_group,
            operation="update",
        )

    async def delete(
        self,
        *,
        term: Any,
        group_id: Any,
        expected_revision: Any,
    ) -> bool:
        """按修订版本严格删除一个词条。"""

        normalized_term = required_text(term, field="term", maximum=128)
        normalized_group = required_text(group_id, field="group_id", maximum=128)
        revision = required_text(
            expected_revision, field="expected_revision", maximum=256
        )
        return await self._run_mutation(
            self._store.delete_if_revision(
                normalized_term,
                normalized_group,
                expected_revision=revision,
            ),
            group_id=normalized_group,
            operation="delete",
        )

    async def batch(self, *, action: Any, items: Any) -> dict[str, Any]:
        """逐项分派安全批量动作，并保留独立、稳定的失败结果。"""

        normalized_action = required_text(action, field="action", maximum=64)
        if normalized_action not in _BATCH_ACTIONS:
            raise EntityValidationError({"action": "不支持的批量操作"})
        if not isinstance(items, list):
            raise EntityValidationError({"items": "必须为数组"})
        if not 1 <= len(items) <= 100:
            raise EntityValidationError({"items": "项目数量必须在 1 到 100 之间"})

        succeeded_ids: list[dict[str, str]] = []
        failures: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            identity_ref: dict[str, Any] = {"item_index": index}
            try:
                identity, revision = self._validated_batch_item(item)
                identity_ref = identity
                if normalized_action == "delete":
                    await self.delete(
                        term=identity["term"],
                        group_id=identity["group_id"],
                        expected_revision=revision,
                    )
                else:
                    await self.update(
                        term=identity["term"],
                        group_id=identity["group_id"],
                        changes=_BATCH_ACTION_CHANGES[normalized_action],
                        expected_revision=revision,
                    )
                succeeded_ids.append(identity)
            except Exception as exc:
                failures.append(self._batch_failure(identity_ref, exc))

        return {
            "total": len(items),
            "succeeded_count": len(succeeded_ids),
            "failed_count": len(failures),
            "succeeded_ids": succeeded_ids,
            "failures": failures,
        }

    async def _run_mutation(
        self,
        mutation: Awaitable[_MutationResult],
        *,
        group_id: str,
        operation: str,
    ) -> _MutationResult:
        """得到确定写入结果并在已提交时失效缓存。"""

        task = asyncio.ensure_future(mutation)
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            try:
                result = task.result()
            except BaseException:
                raise cancellation from None
            self._invalidate(group_id, operation=operation)
            raise cancellation
        self._invalidate(group_id, operation=operation)
        return result

    def _invalidate(self, group_id: str, *, operation: str) -> None:
        if self._invalidate_group is None:
            return
        try:
            self._invalidate_group(group_id)
        except Exception as exc:
            group_ref = hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]
            logger.warning(
                "[JargonAdmin] cache_invalidation_failed "
                "operation=%s group_ref=%s error_class=%s",
                operation,
                group_ref,
                type(exc).__name__,
            )

    @staticmethod
    def _validated_changes(changes: Any) -> dict[str, Any]:
        if not isinstance(changes, Mapping):
            raise EntityValidationError({"changes": "必须为对象"})
        unknown = sorted(set(changes) - _EDITABLE_FIELDS)
        if unknown:
            raise EntityValidationError({name: "字段不可写" for name in unknown})
        if not changes:
            raise EntityValidationError({"changes": "不能为空"})

        normalized: dict[str, Any] = {}
        if "meaning" in changes:
            normalized["meaning"] = required_text(
                changes["meaning"], field="meaning", maximum=4096
            )
        if "confidence" in changes:
            confidence = finite_float(changes["confidence"], field="confidence")
            if not 0.0 <= confidence <= 1.0:
                raise EntityValidationError(
                    {"confidence": "必须在 0.0 到 1.0 之间"}
                )
            normalized["confidence"] = confidence
        for field in ("is_jargon", "is_confirmed", "is_global"):
            if field in changes:
                normalized[field] = JargonAdminService._validated_bool(
                    changes[field], field
                )
        return normalized

    @staticmethod
    def _validated_bool(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise EntityValidationError({field: "必须为布尔值"})
        return value

    @staticmethod
    def _validated_batch_item(item: Any) -> tuple[dict[str, str], str]:
        if not isinstance(item, Mapping):
            raise EntityValidationError({"item": "必须为对象"})
        unknown = sorted(set(item) - {"identity", "expected_revision"})
        if unknown:
            raise EntityValidationError({name: "字段不可写" for name in unknown})

        identity = item.get("identity")
        if not isinstance(identity, Mapping):
            raise EntityValidationError({"identity": "必须为对象"})
        identity_unknown = sorted(set(identity) - {"term", "group_id"})
        if identity_unknown:
            raise EntityValidationError(
                {"identity." + name: "字段不可写" for name in identity_unknown}
            )
        normalized_identity = {
            "term": required_text(
                identity.get("term"), field="identity.term", maximum=128
            ),
            "group_id": required_text(
                identity.get("group_id"), field="identity.group_id", maximum=128
            ),
        }
        revision = required_text(
            item.get("expected_revision"),
            field="expected_revision",
            maximum=256,
        )
        return normalized_identity, revision

    @staticmethod
    def _batch_failure(
        identity: dict[str, Any],
        exc: Exception,
    ) -> dict[str, Any]:
        failure: dict[str, Any] = {
            "identity": dict(identity),
            "code": "operation_failed",
            "message": "操作失败",
        }
        if isinstance(exc, EntityValidationError):
            failure.update(
                code="validation_error",
                message=str(exc),
                field_errors=dict(exc.field_errors),
            )
        elif isinstance(exc, EntityAlreadyExistsError):
            failure.update(code="already_exists", message=str(exc))
        elif isinstance(exc, EntityNotFoundError):
            failure.update(code="not_found", message=str(exc))
        elif isinstance(exc, EditConflictError):
            failure.update(
                code="edit_conflict",
                message=str(exc),
                current_entity=dict(exc.current_entity),
                current_revision=exc.current_revision,
            )
        return failure

    @staticmethod
    def _validated_new_meaning(fields: dict[str, Any]) -> JargonMeaning:
        unknown = sorted(set(fields) - _CREATE_FIELDS)
        if unknown:
            raise EntityValidationError({name: "字段不可写" for name in unknown})

        term = required_text(fields.get("term"), field="term", maximum=128)
        group_id = required_text(
            fields.get("group_id"), field="group_id", maximum=128
        )
        meaning = required_text(
            fields.get("meaning"), field="meaning", maximum=4096
        )
        confidence = finite_float(fields.get("confidence"), field="confidence")
        if not 0.0 <= confidence <= 1.0:
            raise EntityValidationError({"confidence": "必须在 0.0 到 1.0 之间"})

        boolean_values: dict[str, bool] = {}
        defaults = {"is_jargon": True, "is_confirmed": True, "is_global": False}
        for field, default in defaults.items():
            boolean_values[field] = JargonAdminService._validated_bool(
                fields.get(field, default), field
            )

        now = time.time()
        return JargonMeaning(
            term=term,
            group_id=group_id,
            meaning=meaning,
            confidence=confidence,
            is_jargon=boolean_values["is_jargon"],
            is_confirmed=boolean_values["is_confirmed"],
            is_global=boolean_values["is_global"],
            is_complete=boolean_values["is_confirmed"],
            count=0,
            last_inference_count=0,
            context_examples=[],
            created_at=now,
            updated_at=now,
        )
