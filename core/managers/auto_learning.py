"""自主学习全局候选、生产发布链与显式恢复编排入口。"""

from __future__ import annotations

import asyncio
import copy
import inspect
import os
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

from ..evaluation.feedback_learning_evidence import (
    LearningEvidenceArtifact,
    artifact_from_record,
    artifact_to_record,
    validate_learning_evidence,
)
from ..features.learning.application import FeedbackSignalManager
from ..features.learning.domain.models import FeedbackSignalAggregate
from .auto_learning_actions import (
    CandidateBinding,
    aggregation_revision_for,
    reduce_global_candidate,
)
from .auto_learning_operations import AutoLearningOperationsMixin
from .auto_learning_persistence import AutoLearningPersistenceMixin
from .auto_learning_records import (
    candidate_status_view,
    claim_view,
    parse_datetime,
    publication_view,
    safe_reason,
    safe_status,
)
from .auto_learning_reload import AutoLearningReloadMixin
from .auto_learning_retention import AutoLearningRetentionMixin
from .auto_learning_state import (
    AutoLearningStatePersistenceError,
    AutoLearningStateStore,
)

_STATE_FILE = "auto_learning.json"
_BINDING_FIELDS = (
    "aggregation_revision",
    "source_config_revision",
    "evidence_revision",
    "quality_gate_version",
)


class AutoLearningManager(
    AutoLearningOperationsMixin,
    AutoLearningPersistenceMixin,
    AutoLearningReloadMixin,
    AutoLearningRetentionMixin,
):
    """生成唯一全局候选并编排可恢复的配置 CAS 发布与回滚。

    state lock 只覆盖内存状态和状态文件；外部 ConfigManager adapter 调用始终
    在锁外执行。每次生产动作先持久化 prepared intent，再执行一次 writer，
    最后以 operation ID CAS 收口 publication 或恢复记录。
    """

    def __init__(
        self,
        feedback_manager: FeedbackSignalManager,
        *,
        data_dir: str = "",
        enabled: bool = False,
        min_independent_windows: int = 2,
        min_samples: int = 3,
        evidence_provider: Callable[
            [Sequence[FeedbackSignalAggregate]],
            LearningEvidenceArtifact
            | Awaitable[LearningEvidenceArtifact | None]
            | None,
        ]
        | None = None,
        quality_gate_version: str = "quality-gate-v1",
    ) -> None:
        """初始化反馈来源、证据绑定入口及单一状态权威。"""

        self._feedback_manager = feedback_manager
        self._data_dir = data_dir
        self._enabled = bool(enabled)
        self._min_independent_windows = max(1, int(min_independent_windows))
        self._min_samples = max(1, int(min_samples))
        if (
            not isinstance(quality_gate_version, str)
            or not quality_gate_version.strip()
        ):
            raise ValueError("learning_quality_gate_version_invalid")
        self._evidence_provider = evidence_provider
        self._quality_gate_version = quality_gate_version.strip()
        self._candidates: dict[str, dict[str, Any]] = {}
        self._evidence_artifacts: dict[str, dict[str, Any]] = {}
        self._publications: dict[str, dict[str, Any]] = {}
        self._publish_intents: dict[str, dict[str, Any]] = {}
        self._operation_claims: dict[str, dict[str, Any]] = {}
        self._terminal_operations: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._tombstone_ttl_days = 30
        self._tombstone_max_entries = 10_000
        self._recovery_records: dict[str, dict[str, Any]] = {}
        self._reload_operation: dict[str, Any] | None = None
        self._active_publication_revision: str | None = None
        self._state_revision: str | None = None
        self._state_corrupt = False
        self._state_recovery_required = False
        self._state_reason_code = "learning_state_missing"
        self._state_lock = asyncio.Lock()
        self._state_store = (
            AutoLearningStateStore(os.path.join(data_dir, _STATE_FILE))
            if data_dir
            else None
        )

    @property
    def enabled(self) -> bool:
        """返回是否允许新 rebuild 与 publish；不影响已有显式 rollback。"""

        return self._enabled

    async def rebuild_candidates(
        self,
        *,
        reference_time: datetime | None = None,
        evidence_artifact: LearningEvidenceArtifact | None = None,
    ) -> list[dict[str, Any]]:
        """把 scoped/window 聚合与受信离线 artifact 归并为全局候选。"""

        if not self._enabled:
            return []
        async with self._state_lock:
            if self._writes_blocked_unlocked() or self._operation_claims:
                return self.get_candidates()

        evaluation_time = reference_time or datetime.now().astimezone()
        aggregates = self._feedback_manager.rebuild(reference_time=evaluation_time)
        if not aggregates:
            return await self._replace_with_referenced_candidates()

        aggregation_revision = aggregation_revision_for(aggregates)
        artifact = evidence_artifact or await self._resolve_evidence(aggregates)
        artifact_record: dict[str, Any] | None = None
        if artifact is None:
            resolved_binding = CandidateBinding(
                source_config_revision="config-revision-unavailable",
                evidence_revision="evidence-revision-unavailable",
                quality_gate_version=self._quality_gate_version,
                evidence_passed=False,
            )
        else:
            gate = validate_learning_evidence(
                artifact,
                aggregation_revision=aggregation_revision,
                source_config_revision=artifact.source_config_revision
                or "config-revision-invalid",
                quality_gate_version=self._quality_gate_version,
            )
            resolved_binding = CandidateBinding(
                source_config_revision=artifact.source_config_revision
                or "config-revision-invalid",
                evidence_revision=artifact.evidence_revision
                or "evidence-revision-invalid",
                quality_gate_version=artifact.quality_gate_version
                or "quality-gate-invalid",
                evidence_passed=gate.passed,
            )
            artifact_record = artifact_to_record(artifact)
        baseline_document, baseline_graph = await self._baseline_for_binding(
            resolved_binding
        )
        candidate = reduce_global_candidate(
            aggregates,
            binding=resolved_binding,
            min_samples=self._min_samples,
            min_independent_windows=self._min_independent_windows,
            baseline_document_weight=baseline_document,
            baseline_graph_weight=baseline_graph,
        )

        async with self._state_lock:
            if self._writes_blocked_unlocked() or self._operation_claims:
                return self.get_candidates()
            candidate_record = candidate.to_record()
            existing_artifact = self._evidence_artifacts.get(
                candidate.evidence_revision
            )
            if (
                artifact_record is not None
                and existing_artifact is not None
                and existing_artifact != artifact_record
            ):
                candidate_record["status"] = "rejected"
                candidate_record["reason_code"] = "quality_gate_failed"
            existing = self._find_same_candidate_unlocked(candidate_record)
            if existing is not None:
                candidate = reduce_global_candidate(
                    aggregates,
                    binding=resolved_binding,
                    min_samples=self._min_samples,
                    min_independent_windows=self._min_independent_windows,
                    baseline_document_weight=baseline_document,
                    baseline_graph_weight=baseline_graph,
                    candidate_id=existing["candidate_id"],
                    created_at=parse_datetime(existing.get("created_at")),
                )
                candidate_record = candidate.to_record()
                if (
                    artifact_record is not None
                    and existing_artifact is not None
                    and existing_artifact != artifact_record
                ):
                    candidate_record["status"] = "rejected"
                    candidate_record["reason_code"] = "quality_gate_failed"
            previous = copy.deepcopy(self._candidates)
            previous_evidence = copy.deepcopy(self._evidence_artifacts)
            self._candidates = {
                key: value
                for key, value in self._candidates.items()
                if self._candidate_is_referenced_unlocked(key)
            }
            self._candidates[candidate.candidate_id] = candidate_record
            if artifact_record is not None and existing_artifact is None:
                self._evidence_artifacts[candidate.evidence_revision] = artifact_record
            self._prune_evidence_artifacts_unlocked()
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._candidates = previous
                self._evidence_artifacts = previous_evidence
                raise
            return self.get_candidates()

    def get_candidates(self) -> list[dict[str, Any]]:
        """返回候选记录的深副本；Page API 仍必须应用外部 allowlist。"""

        return [copy.deepcopy(item) for item in self._candidates.values()]

    def last_published_snapshot(self, candidate_id: str) -> dict[str, Any] | None:
        """返回候选最近 publication 或未收口 intent 的隔离副本。"""

        publications = [
            item
            for item in self._publications.values()
            if item.get("candidate_id") == candidate_id
        ]
        if publications:
            publications.sort(key=lambda item: str(item.get("published_at", "")))
            return copy.deepcopy(publications[-1])
        intent = self._recoverable_publish_intent_unlocked(candidate_id)
        return copy.deepcopy(intent) if intent is not None else None

    async def reset(self) -> dict[str, Any]:
        """仅清除未引用 shadow 候选，保留 publication、intent 与恢复证据。"""

        async with self._state_lock:
            if not self._enabled:
                return {"reset": False, "reason_code": "disabled"}
            if self._writes_blocked_unlocked():
                return {
                    "reset": False,
                    "reason_code": "learning_state_recovery_required",
                }
            if self._operation_claims:
                return {"reset": False, "reason_code": "learning_action_in_progress"}
            previous = copy.deepcopy(self._candidates)
            previous_evidence = copy.deepcopy(self._evidence_artifacts)
            previous_tombstones = copy.deepcopy(self._tombstones)
            previous_terminal = copy.deepcopy(self._terminal_operations)
            self._candidates = {
                key: value
                for key, value in self._candidates.items()
                if self._candidate_is_referenced_unlocked(key)
            }
            retention = self._prune_tombstones_unlocked()
            self._prune_evidence_artifacts_unlocked()
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._candidates = previous
                self._evidence_artifacts = previous_evidence
                self._tombstones = previous_tombstones
                self._terminal_operations = previous_terminal
                raise
            return {
                "reset": True,
                "reason_code": "reset",
                "removed_count": len(previous) - len(self._candidates),
                "tombstones_removed": retention.tombstones_removed,
            }

    async def get_status_snapshot(self) -> dict[str, Any]:
        """在状态锁内返回低敏且自洽的 candidate/publication/recovery 快照。"""

        async with self._state_lock:
            active = self._active_publication_unlocked()
            candidates = [
                candidate_status_view(item) for item in self._candidates.values()
            ]
            statuses = [safe_status(item.get("status")) for item in candidates]
            reasons = {safe_reason(item.get("reason_code")) for item in candidates}
            return {
                "enabled": self._enabled,
                "state_revision": self._state_revision,
                "available": not self._state_corrupt,
                "candidate_count": len(self._candidates),
                "evidence_count": len(self._evidence_artifacts),
                "publication_count": len(self._publications),
                "ready_count": statuses.count("ready_for_review"),
                "rejected_count": statuses.count("rejected")
                + statuses.count("invalid_state")
                + statuses.count("stale"),
                "published_count": sum(
                    item.get("status") in {"active", "superseded"}
                    for item in self._publications.values()
                ),
                "reasons": sorted(reasons),
                "candidates": candidates,
                "active_publication": publication_view(active),
                "recovery": {
                    "state_corrupt": self._state_corrupt,
                    "state_recovery_required": self._state_recovery_required,
                    "reason_code": self._state_reason_code,
                    "intent_count": len(self._publish_intents),
                    "record_count": len(self._recovery_records),
                    "operation": claim_view(self._first_claim_unlocked()),
                },
                "reload": copy.deepcopy(self._reload_operation),
            }

    def safe_summary(self) -> dict[str, Any]:
        """返回不含内部 key、作用域或反馈事件的稳定低基数摘要。"""

        statuses = [
            safe_status(item.get("status")) for item in self._candidates.values()
        ]
        reasons = {
            safe_reason(item.get("reason_code")) for item in self._candidates.values()
        }
        return {
            "available": not self._state_corrupt,
            "candidate_count": len(self._candidates),
            "ready_count": statuses.count("ready_for_review"),
            "rejected_count": statuses.count("rejected")
            + statuses.count("invalid_state")
            + statuses.count("stale"),
            "published_count": sum(
                item.get("status") in {"active", "superseded"}
                for item in self._publications.values()
            ),
            "reasons": sorted(reasons),
        }

    async def _replace_with_referenced_candidates(self) -> list[dict[str, Any]]:
        """无聚合时只保留 publication/intent 引用候选并可靠落盘。"""

        async with self._state_lock:
            if self._operation_claims:
                return self.get_candidates()
            previous = copy.deepcopy(self._candidates)
            previous_evidence = copy.deepcopy(self._evidence_artifacts)
            self._candidates = {
                key: value
                for key, value in self._candidates.items()
                if self._candidate_is_referenced_unlocked(key)
            }
            self._prune_evidence_artifacts_unlocked()
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._candidates = previous
                self._evidence_artifacts = previous_evidence
                raise
            return self.get_candidates()

    async def _resolve_evidence(
        self,
        aggregates: Sequence[FeedbackSignalAggregate],
    ) -> LearningEvidenceArtifact | None:
        """调用受信 provider 取得当前不可变 evidence 绑定。"""

        if self._evidence_provider is None:
            return None
        value = self._evidence_provider(tuple(aggregates))
        if inspect.isawaitable(value):
            value = await value
        return value if isinstance(value, LearningEvidenceArtifact) else None

    async def _candidate_evidence_is_current(self, candidate_id: str) -> bool:
        """在状态锁外复核生产 Provider 当前指针仍选择候选 artifact。"""

        async with self._state_lock:
            candidate = copy.deepcopy(self._candidates.get(candidate_id))
            provider = self._evidence_provider
            artifact_record = (
                copy.deepcopy(
                    self._evidence_artifacts.get(candidate.get("evidence_revision"))
                )
                if isinstance(candidate, dict)
                else None
            )
        if candidate is None:
            return False
        validator = getattr(provider, "validate_current", None)
        if not callable(validator):
            return True
        artifact = artifact_from_record(artifact_record)
        if artifact is None:
            return False
        try:
            result = validator(artifact)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return result is True

    async def _baseline_for_binding(
        self,
        binding: CandidateBinding,
    ) -> tuple[float, float]:
        """为 child candidate 选择 active publication 的真实 after 权重。"""

        async with self._state_lock:
            active = self._active_publication_unlocked()
            if (
                active is not None
                and active.get("applied_revision") == binding.source_config_revision
            ):
                return (
                    float(active["after_document_weight"]),
                    float(active["after_graph_weight"]),
                )
        return (
            float(self._feedback_manager.policy.baseline_document_weight),
            float(self._feedback_manager.policy.baseline_graph_weight),
        )

    def _candidate_is_referenced_unlocked(self, candidate_id: str) -> bool:
        """判断 candidate 是否仍属于 publication、intent 或恢复链。"""

        return any(
            item.get("candidate_id") == candidate_id
            for item in self._publications.values()
        ) or any(
            item.get("candidate_id") == candidate_id
            for item in self._publish_intents.values()
        )

    def _candidate_evidence_is_valid_unlocked(
        self,
        candidate: dict[str, Any],
    ) -> bool:
        """复核候选引用 artifact 的结构、内容哈希、Gate 与三项绑定。"""

        evidence_revision = candidate.get("evidence_revision")
        aggregation_revision = candidate.get("aggregation_revision")
        source_config_revision = candidate.get("source_config_revision")
        quality_gate_version = candidate.get("quality_gate_version")
        if not all(
            isinstance(value, str) and value
            for value in (
                evidence_revision,
                aggregation_revision,
                source_config_revision,
                quality_gate_version,
            )
        ):
            return False
        if quality_gate_version != self._quality_gate_version:
            return False
        artifact = artifact_from_record(self._evidence_artifacts.get(evidence_revision))
        if artifact is None or artifact.evidence_revision != evidence_revision:
            return False
        result = validate_learning_evidence(
            artifact,
            aggregation_revision=aggregation_revision,
            source_config_revision=source_config_revision,
            quality_gate_version=quality_gate_version,
        )
        return result.passed

    def _prune_evidence_artifacts_unlocked(self) -> None:
        """只保留 candidate、publication 或 intent 仍引用的不可变 artifact。"""

        referenced = {
            value
            for collection in (
                self._candidates.values(),
                self._publications.values(),
                self._publish_intents.values(),
            )
            for item in collection
            if isinstance((value := item.get("evidence_revision")), str)
        }
        self._evidence_artifacts = {
            key: value
            for key, value in self._evidence_artifacts.items()
            if key in referenced
        }

    def _find_same_candidate_unlocked(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        """按四项不可变绑定查找同一候选，以稳定重建幂等 ID。"""

        for candidate in self._candidates.values():
            if all(
                candidate.get(field) == record.get(field) for field in _BINDING_FIELDS
            ):
                return candidate
        return None

    def _first_claim_unlocked(self) -> dict[str, Any] | None:
        """返回唯一活动 claim；多 claim 损坏态取稳定首项供只读展示。"""

        if not self._operation_claims:
            return None
        key = sorted(self._operation_claims)[0]
        return self._operation_claims[key]


__all__ = [
    "AutoLearningManager",
    "AutoLearningStatePersistenceError",
]
