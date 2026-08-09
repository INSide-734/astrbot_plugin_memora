"""AutoLearningManager 的安全状态持久化、恢复与 legacy 迁移 mixin。"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ..domain.auto_learning_actions import new_opaque_id
from ..domain.auto_learning_records import (
    legacy_candidate,
    legacy_intent,
    legacy_publication,
    mapping_records,
    normalize_candidate,
    utc_now,
)
from ..infrastructure.auto_learning_state import (
    AutoLearningStatePersistenceError,
)
from .auto_learning_reload import normalize_reload_operation


class AutoLearningPersistenceMixin:
    """为自主学习状态机提供 schema/checksum Store 接入和显式迁移。"""

    async def load_state(self) -> None:
        """加载 envelope、迁移严格 legacy 状态，并把残留 claim 标记为恢复态。"""

        if self._state_store is None:
            return
        async with self._state_lock:
            result = await self._state_store.load()
            self._state_reason_code = result.reason_code
            self._state_revision = result.state_revision
            self._state_corrupt = result.state_corrupt
            self._state_recovery_required = result.recovery_required
            if result.payload is None:
                return
            if result.migration_required:
                payload = self._migrate_legacy_payload(result.payload)
                assert result.migration_revision is not None
                self._restore_payload(payload)
                self._state_revision = await self._state_store.migrate_legacy(
                    payload,
                    expected_legacy_revision=result.migration_revision,
                )
                self._state_corrupt = False
                self._state_recovery_required = False
                self._state_reason_code = "learning_state_migrated"
                return
            try:
                self._restore_payload(result.payload)
            except (TypeError, ValueError):
                self._state_corrupt = True
                self._state_recovery_required = True
                self._state_reason_code = "learning_state_payload_invalid"
                return
            if self._operation_claims:
                for operation_id, claim in list(self._operation_claims.items()):
                    intent = self._publish_intents.get(operation_id)
                    if intent is not None:
                        intent["phase"] = "recovery_required"
                    self._add_recovery_record_unlocked(
                        operation_id=operation_id,
                        action=str(claim.get("action", "unknown")),
                        reason_code="operation_interrupted",
                    )
                self._operation_claims = {}

    async def save_state(self) -> None:
        """公开保存当前状态，供生命周期关闭阶段显式收口。"""

        async with self._state_lock:
            await self._save_state()

    async def _save_state(self) -> None:
        """在调用方持锁时把 manager payload 写入安全 state store。"""

        if self._state_store is None:
            return
        if self._writes_blocked_unlocked():
            raise AutoLearningStatePersistenceError("learning_state_recovery_required")
        self._state_revision = await self._state_store.save(self._state_payload())

    def _writes_blocked_unlocked(self) -> bool:
        """判断状态文件损坏或恢复要求是否阻止所有新写动作。"""

        return self._state_corrupt or self._state_recovery_required

    def _add_recovery_record_unlocked(
        self,
        *,
        operation_id: str,
        action: str,
        reason_code: str,
    ) -> None:
        """追加不含候选、权重或原始异常的低敏恢复记录。"""

        recovery_revision = new_opaque_id()
        self._recovery_records[recovery_revision] = {
            "recovery_revision": recovery_revision,
            "operation_id": operation_id,
            "action": action,
            "reason_code": reason_code,
            "created_at": utc_now(),
        }

    def _state_payload(self) -> dict[str, Any]:
        """返回交给 state store 的 JSON 兼容完整状态载荷。"""

        return {
            "candidates": self._candidates,
            "evidence_artifacts": self._evidence_artifacts,
            "publications": self._publications,
            "publish_intents": self._publish_intents,
            "operation_claims": self._operation_claims,
            "terminal_operations": self._terminal_operations,
            "tombstones": self._tombstones,
            "recovery_records": self._recovery_records,
            "reload_operation": self._reload_operation,
            "active_publication_revision": self._active_publication_revision,
        }

    def _restore_payload(self, payload: Mapping[str, Any]) -> None:
        """严格恢复 manager 集合；非法结构 fail-closed 而不选择性丢记录。"""

        candidates = mapping_records(payload.get("candidates", {}))
        evidence = mapping_records(payload.get("evidence_artifacts", {}))
        publications = mapping_records(payload.get("publications", {}))
        intents = mapping_records(payload.get("publish_intents", {}))
        claims = mapping_records(payload.get("operation_claims", {}))
        terminal = mapping_records(payload.get("terminal_operations", {}))
        tombstones = mapping_records(payload.get("tombstones", {}))
        recovery = mapping_records(payload.get("recovery_records", {}))
        reload_operation = normalize_reload_operation(payload.get("reload_operation"))
        active = payload.get("active_publication_revision")
        if active is not None and (
            not isinstance(active, str) or active not in publications
        ):
            raise ValueError("learning_active_publication_invalid")
        if len(claims) > 1:
            raise ValueError("learning_operation_claim_invalid")
        self._evidence_artifacts = copy.deepcopy(evidence)
        self._candidates = {
            key: normalize_candidate(key, value) for key, value in candidates.items()
        }
        for candidate in self._candidates.values():
            if candidate.get("status") == "ready_for_review" and not (
                self._candidate_evidence_is_valid_unlocked(candidate)
            ):
                candidate["status"] = "rejected"
                candidate["reason_code"] = "quality_gate_failed"
        self._publications = copy.deepcopy(publications)
        self._publish_intents = copy.deepcopy(intents)
        self._operation_claims = copy.deepcopy(claims)
        self._terminal_operations = copy.deepcopy(terminal)
        self._tombstones = copy.deepcopy(tombstones)
        self._recovery_records = copy.deepcopy(recovery)
        self._reload_operation = reload_operation
        self._active_publication_revision = active

    def _migrate_legacy_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """为严格旧候选/快照分配 opaque ID，并保留可回滚证据。"""

        old_candidates = mapping_records(payload.get("candidates", {}))
        old_published = mapping_records(payload.get("published", {}))
        old_intents = mapping_records(payload.get("publish_intents", {}))
        all_old_keys = set(old_candidates) | set(old_published) | set(old_intents)
        key_to_id = {key: new_opaque_id() for key in all_old_keys}
        candidates: dict[str, dict[str, Any]] = {}
        for old_key, raw in old_candidates.items():
            candidate_id = key_to_id[old_key]
            candidates[candidate_id] = legacy_candidate(candidate_id, old_key, raw)
        publications: dict[str, dict[str, Any]] = {}
        for old_key, raw in old_published.items():
            publication_id = new_opaque_id()
            candidate_id = key_to_id[old_key]
            publications[publication_id] = legacy_publication(
                publication_id,
                candidate_id,
                raw,
            )
            candidates.setdefault(
                candidate_id,
                legacy_candidate(candidate_id, old_key, raw),
            )
        intents: dict[str, dict[str, Any]] = {}
        for old_key, raw in old_intents.items():
            operation_id = new_opaque_id()
            candidate_id = key_to_id[old_key]
            intents[operation_id] = legacy_intent(operation_id, candidate_id, raw)
            candidates.setdefault(
                candidate_id,
                legacy_candidate(candidate_id, old_key, raw),
            )
        active = next(iter(publications)) if len(publications) == 1 else None
        recovery: dict[str, dict[str, Any]] = {}
        if len(publications) > 1:
            recovery_id = new_opaque_id()
            recovery[recovery_id] = {
                "recovery_revision": recovery_id,
                "operation_id": new_opaque_id(),
                "action": "migration",
                "reason_code": "legacy_publication_ambiguous",
                "created_at": utc_now(),
            }
        return {
            "candidates": candidates,
            "evidence_artifacts": {},
            "publications": publications,
            "publish_intents": intents,
            "operation_claims": {},
            "terminal_operations": {},
            "tombstones": {},
            "recovery_records": recovery,
            "reload_operation": None,
            "active_publication_revision": active,
        }


__all__ = ["AutoLearningPersistenceMixin"]
