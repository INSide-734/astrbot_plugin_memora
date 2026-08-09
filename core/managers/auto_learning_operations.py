"""AutoLearningManager 的发布、回滚、claim 与三阶段收口 mixin。"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

from ..features.learning.domain.auto_learning_actions import (
    is_opaque_id,
    new_opaque_id,
    weight_snapshot_hash,
)
from ..features.learning.domain.auto_learning_records import (
    operation_key,
    publish_failure,
    rollback_failure,
    utc_now,
)
from .auto_learning_state import AutoLearningStatePersistenceError


class AutoLearningOperationsMixin:
    """编排锁内 intent、锁外 ConfigManager adapter 与锁内 CAS 收口。"""

    async def publish_candidate(
        self,
        candidate_id: str,
        *,
        config_adapter: Any,
        expected_revision: str,
    ) -> dict[str, Any]:
        """以 opaque candidate ID 声明发布 intent，并在锁外执行一次配置 CAS。"""

        action_key = operation_key("publish", candidate_id, expected_revision)
        async with self._state_lock:
            terminal = self._terminal_operations.get(action_key)
            if terminal is not None:
                return copy.deepcopy(terminal)
        evidence_current = await self._candidate_evidence_is_current(candidate_id)
        try:
            before_snapshot = await config_adapter.get_weight_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            return publish_failure("learning_unavailable")

        async with self._state_lock:
            terminal = self._terminal_operations.get(action_key)
            if terminal is not None:
                return copy.deepcopy(terminal)
            unavailable = self._validate_publish_unlocked(
                candidate_id,
                expected_revision=expected_revision,
                before_snapshot=before_snapshot,
                evidence_current=evidence_current,
            )
            if unavailable is not None:
                return unavailable
            in_progress = self._claim_conflict_unlocked("publish", candidate_id)
            if in_progress is not None:
                return in_progress

            candidate = self._candidates[candidate_id]
            operation_id = new_opaque_id()
            publication_revision = new_opaque_id()
            intent = {
                "operation_id": operation_id,
                "action": "publish",
                "phase": "prepared",
                "candidate_id": candidate_id,
                "publication_revision": publication_revision,
                "parent_publication_revision": self._active_publication_revision,
                "requested_revision": expected_revision,
                "before_config_hash": before_snapshot.config_hash,
                "before_weight_hash": before_snapshot.weight_hash,
                "before_document_weight": before_snapshot.document_route_weight,
                "before_graph_weight": before_snapshot.graph_route_weight,
                "target_weight_hash": candidate["target_snapshot_hash"],
                "target_document_weight": candidate["proposed_document_weight"],
                "target_graph_weight": candidate["proposed_graph_weight"],
                "aggregation_revision": candidate["aggregation_revision"],
                "source_config_revision": candidate["source_config_revision"],
                "evidence_revision": candidate["evidence_revision"],
                "quality_gate_version": candidate["quality_gate_version"],
                "created_at": utc_now(),
            }
            self._publish_intents[operation_id] = intent
            self._operation_claims[operation_id] = {
                "operation_id": operation_id,
                "action": "publish",
                "candidate_id": candidate_id,
                "operation_key": action_key,
                "created_at": intent["created_at"],
            }
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._publish_intents.pop(operation_id, None)
                self._operation_claims.pop(operation_id, None)
                return publish_failure("learning_state_persistence_failed")

        try:
            apply_result = await config_adapter.apply_weights(
                {
                    "document_route_weight": candidate["proposed_document_weight"],
                    "graph_route_weight": candidate["proposed_graph_weight"],
                },
                expected_revision=expected_revision,
            )
        except asyncio.CancelledError:
            await self._mark_cancelled_operation(operation_id)
            raise
        except Exception:
            return await self._finish_unknown_publish(operation_id)
        return await self._finish_publish(operation_id, apply_result)

    async def rollback_last_publish(
        self,
        candidate_id: str,
        *,
        config_adapter: Any,
        expected_revision: str,
    ) -> dict[str, Any]:
        """只对当前 active publication 或未收口 intent 执行显式回滚。"""

        action_key = operation_key("rollback", candidate_id, expected_revision)
        try:
            current_snapshot = await config_adapter.get_weight_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            return rollback_failure("learning_unavailable")

        async with self._state_lock:
            terminal = self._terminal_operations.get(action_key)
            if terminal is not None:
                return copy.deepcopy(terminal)
            if self._state_corrupt or self._state_recovery_required:
                return rollback_failure("learning_state_recovery_required")
            in_progress = self._claim_conflict_unlocked("rollback", candidate_id)
            if in_progress is not None:
                return rollback_failure(in_progress["reason_code"])

            publication = self._active_publication_unlocked()
            source_intent: dict[str, Any] | None = None
            if publication is None or publication.get("candidate_id") != candidate_id:
                source_intent = self._recoverable_publish_intent_unlocked(candidate_id)
                if source_intent is None:
                    return rollback_failure("learning_candidate_unavailable")
                comparison = self._compare_intent_snapshot(
                    source_intent, current_snapshot
                )
                if comparison == "not_applied":
                    result = {
                        "restored": True,
                        "reason_code": "not_applied",
                        "candidate_id": candidate_id,
                    }
                    self._clear_recovered_intent_unlocked(source_intent, result)
                    try:
                        await self._save_state()
                    except AutoLearningStatePersistenceError:
                        return rollback_failure(
                            "learning_state_persistence_failed",
                            recovery_required=True,
                        )
                    return result
                if comparison != "applied":
                    return rollback_failure("config_diverged")
            elif current_snapshot.revision != publication.get(
                "applied_revision"
            ) or current_snapshot.config_hash != publication.get("after_config_hash"):
                return rollback_failure("config_diverged")

            operation_id = new_opaque_id()
            source = publication or source_intent
            assert source is not None
            target_weights = {
                "document_route_weight": float(source["before_document_weight"]),
                "graph_route_weight": float(source["before_graph_weight"]),
            }
            intent = {
                "operation_id": operation_id,
                "action": "rollback",
                "phase": "prepared",
                "candidate_id": candidate_id,
                "publication_revision": source.get("publication_revision"),
                "requested_revision": expected_revision,
                "before_config_hash": current_snapshot.config_hash,
                "before_weight_hash": current_snapshot.weight_hash,
                "before_document_weight": current_snapshot.document_route_weight,
                "before_graph_weight": current_snapshot.graph_route_weight,
                "target_weight_hash": weight_snapshot_hash(target_weights),
                "target_document_weight": target_weights["document_route_weight"],
                "target_graph_weight": target_weights["graph_route_weight"],
                "created_at": utc_now(),
            }
            self._publish_intents[operation_id] = intent
            self._operation_claims[operation_id] = {
                "operation_id": operation_id,
                "action": "rollback",
                "candidate_id": candidate_id,
                "operation_key": action_key,
                "created_at": intent["created_at"],
            }
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._publish_intents.pop(operation_id, None)
                self._operation_claims.pop(operation_id, None)
                return rollback_failure("learning_state_persistence_failed")

        try:
            apply_result = await config_adapter.apply_weights(
                target_weights,
                expected_revision=expected_revision,
            )
        except asyncio.CancelledError:
            await self._mark_cancelled_operation(operation_id)
            raise
        except Exception:
            return await self._finish_unknown_rollback(operation_id)
        return await self._finish_rollback(operation_id, apply_result)

    def _validate_publish_unlocked(
        self,
        candidate_id: str,
        *,
        expected_revision: str,
        before_snapshot: Any,
        evidence_current: bool,
    ) -> dict[str, Any] | None:
        """在 writer 前验证开关、状态、candidate binding 和 active parent。"""

        if not self._enabled:
            return publish_failure("disabled")
        if self._writes_blocked_unlocked():
            return publish_failure("learning_state_recovery_required")
        if not is_opaque_id(candidate_id):
            return publish_failure("learning_candidate_unavailable")
        candidate = self._candidates.get(candidate_id)
        if candidate is None or candidate.get("status") != "ready_for_review":
            return publish_failure("learning_candidate_unavailable")
        if not evidence_current or not self._candidate_evidence_is_valid_unlocked(
            candidate
        ):
            return publish_failure("learning_candidate_unavailable")
        if (
            expected_revision != candidate.get("source_config_revision")
            or before_snapshot.revision != expected_revision
            or before_snapshot.weight_hash != candidate.get("baseline_snapshot_hash")
        ):
            return publish_failure("learning_candidate_unavailable")
        active = self._active_publication_unlocked()
        if active is not None and (
            before_snapshot.revision != active.get("applied_revision")
            or before_snapshot.config_hash != active.get("after_config_hash")
        ):
            return publish_failure("config_diverged")
        return None

    def _claim_conflict_unlocked(
        self,
        action: str,
        candidate_id: str,
    ) -> dict[str, Any] | None:
        """把任意活动 claim 映射为立即返回的稳定并发结果。"""

        claim = self._first_claim_unlocked()
        if claim is None:
            return None
        if claim.get("action") == action and claim.get("candidate_id") == candidate_id:
            if action == "publish":
                return publish_failure("learning_publish_in_progress")
            return rollback_failure("learning_rollback_in_progress")
        if action == "publish":
            return publish_failure("learning_action_not_allowed")
        return rollback_failure("learning_action_not_allowed")

    async def _finish_publish(self, operation_id: str, result: Any) -> dict[str, Any]:
        """按 claim CAS 把 typed adapter 结果收口为 publication 或恢复记录。"""

        async with self._state_lock:
            claim, intent = self._claimed_intent_unlocked(operation_id, "publish")
            if claim is None or intent is None:
                return publish_failure(
                    "learning_publish_recovery_required",
                    recovery_required=True,
                )
            action_key = str(claim["operation_key"])
            candidate_id = str(intent["candidate_id"])
            if bool(getattr(result, "no_op", False)):
                response = publish_failure("config_noop")
                self._complete_non_applied_unlocked(operation_id, action_key, response)
                await self._save_terminal_or_recovery(operation_id, "publish")
                return response
            if bool(getattr(result, "applied", False)):
                if getattr(result, "before_hash", None) != intent.get(
                    "before_config_hash"
                ) or not isinstance(getattr(result, "applied_revision", None), str):
                    return await self._set_recovery_unlocked(
                        operation_id,
                        action="publish",
                        reason_code="learning_publish_recovery_required",
                    )
                publication_revision = str(intent["publication_revision"])
                parent_revision = intent.get("parent_publication_revision")
                parent = (
                    self._publications.get(parent_revision)
                    if isinstance(parent_revision, str)
                    else None
                )
                if parent is not None:
                    parent["status"] = "superseded"
                publication = {
                    "publication_id": publication_revision,
                    "publication_revision": publication_revision,
                    "parent_publication_revision": parent_revision,
                    "candidate_id": candidate_id,
                    "before_config_hash": intent["before_config_hash"],
                    "after_config_hash": str(result.after_hash),
                    "before_weight_hash": intent["before_weight_hash"],
                    "after_weight_hash": intent["target_weight_hash"],
                    "before_document_weight": intent["before_document_weight"],
                    "before_graph_weight": intent["before_graph_weight"],
                    "after_document_weight": intent["target_document_weight"],
                    "after_graph_weight": intent["target_graph_weight"],
                    "requested_revision": intent["requested_revision"],
                    "applied_revision": str(result.applied_revision),
                    "evidence_revision": intent["evidence_revision"],
                    "aggregation_revision": intent["aggregation_revision"],
                    "quality_gate_version": intent["quality_gate_version"],
                    "status": "active",
                    "published_at": utc_now(),
                }
                self._publications[publication_revision] = publication
                self._active_publication_revision = publication_revision
                candidate = self._candidates.get(candidate_id)
                if candidate is not None:
                    candidate["status"] = "published"
                    candidate["reason_code"] = "published"
                response = {
                    "published": True,
                    "reason_code": "published",
                    "candidate_id": candidate_id,
                    "operation_id": operation_id,
                    "publication_revision": publication_revision,
                    "applied_revision": str(result.applied_revision),
                    "changed_paths": list(getattr(result, "changed_paths", ())),
                }
                self._publish_intents.pop(operation_id, None)
                self._operation_claims.pop(operation_id, None)
                self._terminal_operations[action_key] = copy.deepcopy(response)
                try:
                    await self._save_state()
                except AutoLearningStatePersistenceError:
                    self._add_recovery_record_unlocked(
                        operation_id=operation_id,
                        action="publish",
                        reason_code="final_state_persistence_failed",
                    )
                    return publish_failure(
                        "learning_publish_recovery_required",
                        recovery_required=True,
                        config_applied=True,
                        applied_revision=str(result.applied_revision),
                    )
                return response

            reason_code = str(
                getattr(result, "reason_code", "config_persistence_failed")
            )
            if reason_code == "learning_publish_recovery_required":
                return await self._set_recovery_unlocked(
                    operation_id,
                    action="publish",
                    reason_code=reason_code,
                )
            response = publish_failure(reason_code)
            self._complete_non_applied_unlocked(operation_id, action_key, response)
            await self._save_terminal_or_recovery(operation_id, "publish")
            return response

    async def _finish_rollback(self, operation_id: str, result: Any) -> dict[str, Any]:
        """按 claim CAS 收口 rollback，并把 active 指针恢复到直接 parent。"""

        async with self._state_lock:
            claim, intent = self._claimed_intent_unlocked(operation_id, "rollback")
            if claim is None or intent is None:
                return rollback_failure(
                    "learning_rollback_recovery_required",
                    recovery_required=True,
                )
            action_key = str(claim["operation_key"])
            if bool(getattr(result, "applied", False)):
                publication_revision = intent.get("publication_revision")
                publication = (
                    self._publications.get(publication_revision)
                    if isinstance(publication_revision, str)
                    else None
                )
                if publication is None:
                    source_intent = self._recoverable_publish_intent_unlocked(
                        str(intent["candidate_id"]),
                        exclude_operation_id=operation_id,
                    )
                    if source_intent is not None:
                        self._publish_intents.pop(
                            str(source_intent["operation_id"]), None
                        )
                else:
                    publication["status"] = "rolled_back"
                    publication["rolled_back_at"] = utc_now()
                    parent_revision = publication.get("parent_publication_revision")
                    self._active_publication_revision = (
                        parent_revision if isinstance(parent_revision, str) else None
                    )
                    parent = self._active_publication_unlocked()
                    if parent is not None:
                        parent["status"] = "active"
                        parent.setdefault(
                            "original_applied_revision",
                            parent.get("applied_revision"),
                        )
                        parent["applied_revision"] = getattr(
                            result,
                            "applied_revision",
                            parent.get("applied_revision"),
                        )
                        parent["after_config_hash"] = str(result.after_hash)
                        parent["reactivated_at"] = utc_now()
                    tombstone_id = new_opaque_id()
                    self._tombstones[tombstone_id] = {
                        "tombstone_id": tombstone_id,
                        "operation_id": operation_id,
                        "candidate_id": intent["candidate_id"],
                        "publication_revision": publication_revision,
                        "status": "rolled_back",
                        "completed_at": publication["rolled_back_at"],
                    }
                candidate = self._candidates.get(str(intent["candidate_id"]))
                if candidate is not None:
                    candidate["status"] = "rolled_back"
                    candidate["reason_code"] = "rolled_back"
                response = {
                    "restored": True,
                    "reason_code": "restored",
                    "candidate_id": intent["candidate_id"],
                    "operation_id": operation_id,
                    "applied_revision": getattr(result, "applied_revision", None),
                    "changed_paths": list(getattr(result, "changed_paths", ())),
                    "active_publication_revision": self._active_publication_revision,
                }
                self._publish_intents.pop(operation_id, None)
                self._operation_claims.pop(operation_id, None)
                self._terminal_operations[action_key] = copy.deepcopy(response)
                try:
                    await self._save_state()
                except AutoLearningStatePersistenceError:
                    self._add_recovery_record_unlocked(
                        operation_id=operation_id,
                        action="rollback",
                        reason_code="final_state_persistence_failed",
                    )
                    return rollback_failure(
                        "learning_rollback_recovery_required",
                        recovery_required=True,
                        config_applied=True,
                    )
                return response

            reason_code = str(
                getattr(result, "reason_code", "config_persistence_failed")
            )
            if reason_code == "learning_publish_recovery_required":
                reason_code = "learning_rollback_recovery_required"
            if reason_code == "learning_rollback_recovery_required":
                return await self._set_recovery_unlocked(
                    operation_id,
                    action="rollback",
                    reason_code=reason_code,
                )
            response = rollback_failure(reason_code)
            self._complete_non_applied_unlocked(operation_id, action_key, response)
            await self._save_terminal_or_recovery(operation_id, "rollback")
            return response

    async def _finish_unknown_publish(self, operation_id: str) -> dict[str, Any]:
        """把未类型化 writer 异常收敛为需显式恢复的发布结果。"""

        async with self._state_lock:
            return await self._set_recovery_unlocked(
                operation_id,
                action="publish",
                reason_code="learning_publish_recovery_required",
            )

    async def _finish_unknown_rollback(self, operation_id: str) -> dict[str, Any]:
        """把未类型化 writer 异常收敛为需显式恢复的回滚结果。"""

        async with self._state_lock:
            return await self._set_recovery_unlocked(
                operation_id,
                action="rollback",
                reason_code="learning_rollback_recovery_required",
            )

    async def _mark_cancelled_operation(self, operation_id: str) -> None:
        """在取消传播前尽力保留未知提交 intent，不执行自动重试或回滚。"""

        async with self._state_lock:
            intent = self._publish_intents.get(operation_id)
            if intent is None:
                return
            intent["phase"] = "recovery_required"
            self._operation_claims.pop(operation_id, None)
            self._add_recovery_record_unlocked(
                operation_id=operation_id,
                action=str(intent.get("action", "unknown")),
                reason_code="operation_cancelled",
            )
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                logger.warning("[自主学习] 取消后的恢复状态持久化失败")

    async def _set_recovery_unlocked(
        self,
        operation_id: str,
        *,
        action: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """保留 intent、释放 claim并写入低敏恢复记录。"""

        intent = self._publish_intents.get(operation_id)
        if intent is not None:
            intent["phase"] = "recovery_required"
            intent["reason_code"] = reason_code
        self._operation_claims.pop(operation_id, None)
        self._add_recovery_record_unlocked(
            operation_id=operation_id,
            action=action,
            reason_code=reason_code,
        )
        try:
            await self._save_state()
        except AutoLearningStatePersistenceError:
            pass
        if action == "publish":
            return publish_failure(reason_code, recovery_required=True)
        return rollback_failure(reason_code, recovery_required=True)

    async def _save_terminal_or_recovery(
        self,
        operation_id: str,
        action: str,
    ) -> None:
        """保存非提交终态；失败时只保留恢复记录，不覆盖配置。"""

        try:
            await self._save_state()
        except AutoLearningStatePersistenceError:
            self._add_recovery_record_unlocked(
                operation_id=operation_id,
                action=action,
                reason_code="terminal_state_persistence_failed",
            )

    def _complete_non_applied_unlocked(
        self,
        operation_id: str,
        action_key: str,
        response: dict[str, Any],
    ) -> None:
        """清除确定未提交的 intent/claim，并缓存幂等终态。"""

        self._publish_intents.pop(operation_id, None)
        self._operation_claims.pop(operation_id, None)
        self._terminal_operations[action_key] = copy.deepcopy(response)

    def _clear_recovered_intent_unlocked(
        self,
        intent: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        """显式确认未提交后清除旧 publish intent 及其恢复记录。"""

        operation_id = str(intent["operation_id"])
        self._publish_intents.pop(operation_id, None)
        self._operation_claims.pop(operation_id, None)
        for key, record in list(self._recovery_records.items()):
            if record.get("operation_id") == operation_id:
                self._recovery_records.pop(key, None)
        self._terminal_operations[
            operation_key(
                "rollback",
                str(intent["candidate_id"]),
                str(intent["requested_revision"]),
            )
        ] = copy.deepcopy(response)

    def _compare_intent_snapshot(self, intent: Mapping[str, Any], snapshot: Any) -> str:
        """把重启后权威权重分为未提交、已提交或配置分叉。"""

        if snapshot.weight_hash == intent.get("before_weight_hash"):
            return "not_applied"
        if snapshot.weight_hash == intent.get("target_weight_hash"):
            return "applied"
        return "config_diverged"

    def _claimed_intent_unlocked(
        self,
        operation_id: str,
        action: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """返回 operation ID 与动作均匹配的 claim/intent。"""

        claim = self._operation_claims.get(operation_id)
        intent = self._publish_intents.get(operation_id)
        if (
            claim is None
            or intent is None
            or claim.get("action") != action
            or intent.get("action") != action
        ):
            return None, None
        return claim, intent

    def _active_publication_unlocked(self) -> dict[str, Any] | None:
        """返回当前唯一 active publication 的内部引用。"""

        if self._active_publication_revision is None:
            return None
        return self._publications.get(self._active_publication_revision)

    def _recoverable_publish_intent_unlocked(
        self,
        candidate_id: str,
        *,
        exclude_operation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """查找候选仍需显式收口的 publish intent。"""

        matches = [
            item
            for operation_id, item in self._publish_intents.items()
            if operation_id != exclude_operation_id
            and item.get("action") == "publish"
            and item.get("candidate_id") == candidate_id
            and item.get("phase") in {"prepared", "recovery_required"}
        ]
        matches.sort(key=lambda item: str(item.get("created_at", "")))
        return matches[-1] if matches else None


__all__ = ["AutoLearningOperationsMixin"]
