"""自主学习配置提交后的 reload operation 持久化与生命周期对账。"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .auto_learning_records import utc_now
from .auto_learning_state import AutoLearningStatePersistenceError

_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22,128}\Z", re.ASCII)
_REVISION_PATTERN = re.compile(r"[\x21-\x7e]{1,128}\Z", re.ASCII)
_REASON_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)
_RELOAD_STATES = frozenset(
    {"queued", "running", "succeeded", "failed", "restart_required"}
)
_TERMINAL_RELOAD_STATES = frozenset({"succeeded"})
_LEARNING_WEIGHT_PATHS = frozenset(
    {
        "graph_memory.document_route_weight",
        "graph_memory.graph_route_weight",
    }
)
_RELOAD_RECORD_FIELDS = frozenset(
    {
        "operation_id",
        "action",
        "state",
        "reason_code",
        "applied_revision",
        "changed_paths",
        "target_document_weight",
        "target_graph_weight",
        "created_at",
        "updated_at",
    }
)


class AutoLearningReloadMixin:
    """为 AutoLearningManager 提供 reload operation 状态机。"""

    async def record_reload_operation(
        self,
        *,
        action: str,
        candidate_id: str,
        operation_id: str,
        applied_revision: str,
        changed_paths: Sequence[str],
        state: str,
    ) -> dict[str, Any] | None:
        """在配置提交后记录真实排队结果及内部目标权重。"""

        if (
            action not in {"publish", "rollback"}
            or not _opaque_id(candidate_id)
            or not _opaque_id(operation_id)
            or not _safe_revision(applied_revision)
            or state not in {"queued", "restart_required"}
        ):
            return None
        normalized_paths = _changed_paths(changed_paths)
        if not normalized_paths:
            return None
        async with self._state_lock:
            if not await self._refresh_state_if_changed_unlocked():
                return None
            target = self._reload_target_unlocked(
                action=action,
                candidate_id=candidate_id,
                operation_id=operation_id,
            )
            if target is None:
                return None
            current = self._reload_operation
            if (
                isinstance(current, Mapping)
                and current.get("operation_id") == operation_id
                and current.get("state") in _TERMINAL_RELOAD_STATES
            ):
                return copy.deepcopy(dict(current))
            previous = copy.deepcopy(self._reload_operation)
            timestamp = utc_now()
            reason_code = "reload_queued" if state == "queued" else "reload_not_queued"
            self._reload_operation = {
                "operation_id": operation_id,
                "action": action,
                "state": state,
                "reason_code": reason_code,
                "applied_revision": applied_revision,
                "changed_paths": list(normalized_paths),
                "target_document_weight": target[0],
                "target_graph_weight": target[1],
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._reload_operation = previous
                raise
            return copy.deepcopy(self._reload_operation)

    async def update_reload_operation(
        self,
        operation_id: str,
        *,
        state: str,
        reason_code: str,
        expected_state_revision: str | None = None,
    ) -> dict[str, Any] | None:
        """按 operation ID 更新 reload 状态，拒绝迟到事件覆盖 succeeded 终态。"""

        if (
            not _opaque_id(operation_id)
            or state not in _RELOAD_STATES
            or not _safe_reason(reason_code)
            or (
                expected_state_revision is not None
                and not _safe_revision(expected_state_revision)
            )
        ):
            return None
        async with self._state_lock:
            if not await self._refresh_state_if_changed_unlocked():
                return None
            current = self._reload_operation
            if (
                not isinstance(current, Mapping)
                or current.get("operation_id") != operation_id
            ):
                return None
            if (
                expected_state_revision is not None
                and self._state_revision != expected_state_revision
            ):
                return (
                    copy.deepcopy(dict(current))
                    if current.get("state") == "succeeded"
                    else None
                )
            if current.get("state") in _TERMINAL_RELOAD_STATES:
                return copy.deepcopy(dict(current))
            previous = copy.deepcopy(self._reload_operation)
            updated = dict(current)
            updated["state"] = state
            updated["reason_code"] = reason_code
            updated["updated_at"] = utc_now()
            self._reload_operation = updated
            try:
                # 状态 Store 携带前置刷新读取的 revision，并在写入时执行最终的
                # 跨实例 CAS，避免旧插件实例覆盖新生命周期状态。
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._reload_operation = previous
                raise
            return copy.deepcopy(updated)

    async def reconcile_reload_operation(
        self,
        *,
        effective_document_weight: float,
        effective_graph_weight: float,
    ) -> dict[str, Any] | None:
        """新生命周期按实际运行时权重把未决 reload 收口为成功或失败。"""

        if not _weight_pair(effective_document_weight, effective_graph_weight):
            return None
        async with self._state_lock:
            if not await self._refresh_state_if_changed_unlocked():
                return (
                    copy.deepcopy(self._reload_operation)
                    if isinstance(self._reload_operation, Mapping)
                    else None
                )
            current = self._reload_operation
            if not isinstance(current, Mapping):
                return None
            # 状态主文件损坏或仅从备份恢复时，不能把内存中的推断结果
            # 再写回磁盘；保持未决快照，等待显式恢复后再完成对账。
            if self._writes_blocked_unlocked():
                return copy.deepcopy(dict(current))
            if current.get("state") == "succeeded":
                return copy.deepcopy(dict(current))
            target_document = current.get("target_document_weight")
            target_graph = current.get("target_graph_weight")
            if not _weight_pair(target_document, target_graph):
                return None
            matches = math.isclose(
                float(effective_document_weight),
                float(target_document),
                abs_tol=1e-9,
            ) and math.isclose(
                float(effective_graph_weight),
                float(target_graph),
                abs_tol=1e-9,
            )
            previous = copy.deepcopy(self._reload_operation)
            updated = dict(current)
            updated["state"] = "succeeded" if matches else "failed"
            updated["reason_code"] = (
                "runtime_config_reconciled" if matches else "runtime_config_mismatch"
            )
            updated["updated_at"] = utc_now()
            self._reload_operation = updated
            try:
                await self._save_state()
            except AutoLearningStatePersistenceError:
                self._reload_operation = previous
                raise
            return copy.deepcopy(updated)

    async def _refresh_state_if_changed_unlocked(self) -> bool:
        """执行生命周期回调前从磁盘刷新 Manager 状态。

        调用方已持有 ``_state_lock``。回调可能来自旧插件实例，因此必须先读取
        最新持久 revision；Store 缺失、损坏或需要显式恢复时拒绝继续写入。
        """

        if self._state_store is None:
            return True
        result = await self._state_store.load()
        if (
            result.state_revision == self._state_revision
            and not result.state_corrupt
            and not result.recovery_required
        ):
            return True
        self._state_reason_code = result.reason_code
        self._state_revision = result.state_revision
        self._state_corrupt = result.state_corrupt
        self._state_recovery_required = result.recovery_required
        if result.payload is None or result.migration_required:
            return False
        try:
            self._restore_payload(result.payload)
        except (TypeError, ValueError):
            self._state_corrupt = True
            self._state_recovery_required = True
            self._state_reason_code = "learning_state_payload_invalid"
            return False
        return not self._writes_blocked_unlocked()

    def _reload_target_unlocked(
        self,
        *,
        action: str,
        candidate_id: str,
        operation_id: str,
    ) -> tuple[float, float] | None:
        """从 publication/tombstone 权威链解析本次 reload 应加载的目标权重。"""

        if action == "publish":
            publication = self._active_publication_unlocked()
            if (
                not isinstance(publication, Mapping)
                or publication.get("candidate_id") != candidate_id
            ):
                return None
            return _publication_weights(publication, prefix="after")

        tombstone = next(
            (
                item
                for item in self._tombstones.values()
                if item.get("operation_id") == operation_id
                and item.get("candidate_id") == candidate_id
                and item.get("status") == "rolled_back"
            ),
            None,
        )
        if not isinstance(tombstone, Mapping):
            return None
        active = self._active_publication_unlocked()
        if isinstance(active, Mapping):
            return _publication_weights(active, prefix="after")
        rolled_back = self._publications.get(tombstone.get("publication_revision"))
        if not isinstance(rolled_back, Mapping):
            return None
        return _publication_weights(rolled_back, prefix="before")


def normalize_reload_operation(value: object) -> dict[str, Any] | None:
    """严格恢复持久化 reload operation；结构非法时抛出 ValueError。"""

    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _RELOAD_RECORD_FIELDS:
        raise ValueError("learning_reload_operation_invalid")
    operation_id = value.get("operation_id")
    action = value.get("action")
    state = value.get("state")
    reason_code = value.get("reason_code")
    applied_revision = value.get("applied_revision")
    changed_paths = value.get("changed_paths")
    if (
        not _opaque_id(operation_id)
        or action not in {"publish", "rollback"}
        or state not in _RELOAD_STATES
        or not _safe_reason(reason_code)
        or not _safe_revision(applied_revision)
        or not isinstance(changed_paths, list)
        or not _changed_paths(changed_paths)
        or not _weight_pair(
            value.get("target_document_weight"),
            value.get("target_graph_weight"),
        )
        or not all(
            isinstance(value.get(field), str) and 1 <= len(str(value[field])) <= 64
            for field in ("created_at", "updated_at")
        )
    ):
        raise ValueError("learning_reload_operation_invalid")
    return copy.deepcopy(dict(value))


def _publication_weights(
    publication: Mapping[str, object],
    *,
    prefix: str,
) -> tuple[float, float] | None:
    """读取 publication 的 before/after 权重并验证归一化范围。"""

    document = publication.get(f"{prefix}_document_weight")
    graph = publication.get(f"{prefix}_graph_weight")
    if not _weight_pair(document, graph):
        return None
    return float(document), float(graph)


def _weight_pair(document: object, graph: object) -> bool:
    """验证两个权重为有限非布尔数值、各自在范围内且总和为一。"""

    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (document, graph)
    ):
        return False
    document_value = float(document)
    graph_value = float(graph)
    return (
        math.isfinite(document_value)
        and math.isfinite(graph_value)
        and 0.0 <= document_value <= 1.0
        and 0.0 <= graph_value <= 1.0
        and math.isclose(document_value + graph_value, 1.0, abs_tol=1e-9)
    )


def _changed_paths(value: Sequence[object]) -> tuple[str, ...]:
    """按固定 canonical 顺序裁剪本次 reload 的生产权重路径。"""

    if isinstance(value, (str, bytes)) or not all(
        isinstance(item, str) for item in value
    ):
        return ()
    selected = set(value)
    if not selected or not selected.issubset(_LEARNING_WEIGHT_PATHS):
        return ()
    return tuple(path for path in sorted(_LEARNING_WEIGHT_PATHS) if path in selected)


def _opaque_id(value: object) -> bool:
    """判断内部 operation/candidate ID 是否符合 opaque URL-safe 形状。"""

    return isinstance(value, str) and _OPAQUE_ID_PATTERN.fullmatch(value) is not None


def _safe_revision(value: object) -> bool:
    """判断配置 revision 是否为有限长度可见 ASCII 字符串。"""

    return isinstance(value, str) and _REVISION_PATTERN.fullmatch(value) is not None


def _safe_reason(value: object) -> bool:
    """判断 reload reason 是否属于低敏稳定代码形状。"""

    return isinstance(value, str) and _REASON_PATTERN.fullmatch(value) is not None


__all__ = ["AutoLearningReloadMixin", "normalize_reload_operation"]
