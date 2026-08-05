"""自主学习受限配置提交的类型化适配器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..base.config_manager import (
    ConfigConflictError,
    ConfigPersistenceError,
    ConfigValidationError,
)

_DOCUMENT_WEIGHT_PATH = "graph_memory.document_route_weight"
_GRAPH_WEIGHT_PATH = "graph_memory.graph_route_weight"
_WEIGHT_PATHS = (_DOCUMENT_WEIGHT_PATH, _GRAPH_WEIGHT_PATH)
_WEIGHT_KEYS = ("document_route_weight", "graph_route_weight")


@dataclass(frozen=True, slots=True)
class LearningConfigApplyResult:
    """描述自主学习权重提交的已验证结果及低敏快照证据。"""

    requested_revision: str
    applied_revision: str | None
    changed_paths: tuple[str, ...]
    before_hash: str
    after_hash: str
    applied: bool
    no_op: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class LearningConfigSnapshot:
    """保存 ConfigManager 权威 revision 与两项受限权重的低敏快照。"""

    revision: str
    document_route_weight: float
    graph_route_weight: float
    config_hash: str
    weight_hash: str

    def as_weights(self) -> dict[str, float]:
        """返回供 intent 与 adapter 使用的隔离权重映射。"""

        return {
            "document_route_weight": self.document_route_weight,
            "graph_route_weight": self.graph_route_weight,
        }


class LearningConfigAdapter:
    """将自主学习权重限制为两个权威 ConfigManager 路径。"""

    def __init__(self, config_manager: Any) -> None:
        """保存提供快照与 revision-CAS 的 ConfigManager。"""

        self._config_manager = config_manager

    async def get_weight_snapshot(self) -> LearningConfigSnapshot:
        """读取并验证 ConfigManager 当前 revision 与两项生产权重。"""

        snapshot, revision = await self._config_manager.get_config_snapshot_async()
        weights = _read_weights(snapshot)
        if not isinstance(revision, str) or not revision or weights is None:
            raise ValueError("learning_config_snapshot_invalid")
        return LearningConfigSnapshot(
            revision=revision,
            document_route_weight=weights["document_route_weight"],
            graph_route_weight=weights["graph_route_weight"],
            config_hash=_snapshot_hash(snapshot),
            weight_hash=_snapshot_hash(weights),
        )

    async def apply_weights(
        self,
        target_weights: Mapping[str, object],
        *,
        expected_revision: str,
    ) -> LearningConfigApplyResult:
        """以最终 revision CAS 提交一对互补权重并验证提交后的权威快照。"""

        (
            before_snapshot,
            before_revision,
        ) = await self._config_manager.get_config_snapshot_async()
        before_hash = _snapshot_hash(before_snapshot)
        before_weights = _read_weights(before_snapshot)
        target = _normalize_weights(target_weights)
        if target is None:
            return _result(
                requested_revision=expected_revision,
                applied_revision=before_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="config_validation_failed",
            )
        if expected_revision != before_revision:
            return _result(
                requested_revision=expected_revision,
                applied_revision=before_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="config_revision_conflict",
            )
        if before_weights == target:
            return _result(
                requested_revision=expected_revision,
                applied_revision=before_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="config_noop",
                no_op=True,
            )

        changes = {
            _DOCUMENT_WEIGHT_PATH: target["document_route_weight"],
            _GRAPH_WEIGHT_PATH: target["graph_route_weight"],
        }
        try:
            write_result = await self._config_manager.apply_config_changes(
                changes,
                expected_revision=expected_revision,
                persist=True,
            )
        except asyncio.CancelledError:
            raise
        except ConfigConflictError:
            return _result(
                requested_revision=expected_revision,
                applied_revision=before_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="config_revision_conflict",
            )
        except ConfigValidationError:
            return _result(
                requested_revision=expected_revision,
                applied_revision=before_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="config_validation_failed",
            )
        except ConfigPersistenceError:
            return await self._resolve_persistence_failure(
                expected_revision=expected_revision,
                before_snapshot=before_snapshot,
                before_revision=before_revision,
                before_hash=before_hash,
                target=target,
            )

        try:
            (
                after_snapshot,
                after_revision,
            ) = await self._config_manager.get_config_snapshot_async()
        except asyncio.CancelledError:
            raise
        except Exception:
            return _result(
                requested_revision=expected_revision,
                applied_revision=None,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="learning_publish_recovery_required",
            )
        after_hash = _snapshot_hash(after_snapshot)
        if (
            getattr(write_result, "revision", None) != after_revision
            or tuple(getattr(write_result, "changed_paths", ())) != _WEIGHT_PATHS
            or _read_weights(after_snapshot) != target
        ):
            return _result(
                requested_revision=expected_revision,
                applied_revision=after_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=after_hash,
                reason_code="learning_publish_recovery_required",
            )
        return _result(
            requested_revision=expected_revision,
            applied_revision=after_revision,
            changed_paths=_WEIGHT_PATHS,
            before_hash=before_hash,
            after_hash=after_hash,
            reason_code="config_applied",
            applied=True,
        )

    async def _resolve_persistence_failure(
        self,
        *,
        expected_revision: str,
        before_snapshot: Mapping[str, object],
        before_revision: str,
        before_hash: str,
        target: dict[str, float],
    ) -> LearningConfigApplyResult:
        """在持久化异常后依据权威快照区分未提交、已提交与未知状态。"""

        try:
            (
                after_snapshot,
                after_revision,
            ) = await self._config_manager.get_config_snapshot_async()
        except asyncio.CancelledError:
            raise
        except Exception:
            return _result(
                requested_revision=expected_revision,
                applied_revision=None,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=before_hash,
                reason_code="learning_publish_recovery_required",
            )
        after_hash = _snapshot_hash(after_snapshot)
        if after_snapshot == before_snapshot and after_revision == before_revision:
            return _result(
                requested_revision=expected_revision,
                applied_revision=after_revision,
                changed_paths=(),
                before_hash=before_hash,
                after_hash=after_hash,
                reason_code="config_persistence_failed",
            )
        if _read_weights(after_snapshot) == target:
            return _result(
                requested_revision=expected_revision,
                applied_revision=after_revision,
                changed_paths=_WEIGHT_PATHS,
                before_hash=before_hash,
                after_hash=after_hash,
                reason_code="config_applied",
                applied=True,
            )
        return _result(
            requested_revision=expected_revision,
            applied_revision=after_revision,
            changed_paths=(),
            before_hash=before_hash,
            after_hash=after_hash,
            reason_code="learning_publish_recovery_required",
        )


def _normalize_weights(source: Mapping[str, object]) -> dict[str, float] | None:
    """验证精确的两个权重键、有限范围及互补和。"""

    if set(source) != set(_WEIGHT_KEYS):
        return None
    document = _finite_weight(source.get("document_route_weight"))
    graph = _finite_weight(source.get("graph_route_weight"))
    if document is None or graph is None or not math.isclose(document + graph, 1.0):
        return None
    return {"document_route_weight": document, "graph_route_weight": graph}


def _finite_weight(value: object) -> float | None:
    """将普通数值限制在闭区间零到一内并拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and 0.0 <= numeric <= 1.0 else None


def _read_weights(snapshot: Mapping[str, object]) -> dict[str, float] | None:
    """从权威配置快照读取并校验两个 graph_memory 权重。"""

    graph_memory = snapshot.get("graph_memory")
    if not isinstance(graph_memory, Mapping):
        return None
    return _normalize_weights(
        {
            "document_route_weight": graph_memory.get("document_route_weight"),
            "graph_route_weight": graph_memory.get("graph_route_weight"),
        }
    )


def _snapshot_hash(snapshot: Mapping[str, object]) -> str:
    """计算配置快照的稳定 SHA-256 哈希，不返回其中的敏感值。"""

    payload = json.dumps(
        snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result(
    *,
    requested_revision: str,
    applied_revision: str | None,
    changed_paths: tuple[str, ...],
    before_hash: str,
    after_hash: str,
    reason_code: str,
    applied: bool = False,
    no_op: bool = False,
) -> LearningConfigApplyResult:
    """以固定字段构造适配器结果，避免调用方猜测失败状态。"""

    return LearningConfigApplyResult(
        requested_revision=requested_revision,
        applied_revision=applied_revision,
        changed_paths=changed_paths,
        before_hash=before_hash,
        after_hash=after_hash,
        applied=applied,
        no_op=no_op,
        reason_code=reason_code,
    )


__all__ = [
    "LearningConfigAdapter",
    "LearningConfigApplyResult",
    "LearningConfigSnapshot",
]
