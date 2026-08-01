"""自主学习 shadow 候选 — 从统一反馈聚合生成参数候选，不直接改生产配置。"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .feedback_signal_manager import FeedbackSignalManager

_STATE_FILE = "auto_learning.json"
_CANDIDATE_STATUSES = frozenset(
    {"ready_for_review", "rejected", "published", "invalid_state"}
)
_CANDIDATE_REASONS = frozenset(
    {"candidate", "insufficient_evidence", "published", "invalid_state"}
)


class AutoLearningManager:
    """基于 FeedbackSignalManager 聚合生成影子参数候选并支持 CAS 发布。

    候选只写入本地状态文件；``publish_candidate()`` 必须通过单一配置写入口
    并携带 revision，失败时保持生产配置不变。
    """

    def __init__(
        self,
        feedback_manager: FeedbackSignalManager,
        *,
        data_dir: str = "",
        enabled: bool = False,
        min_independent_windows: int = 2,
        min_samples: int = 3,
    ) -> None:
        """初始化统一反馈聚合来源与 shadow 候选状态。"""

        self._feedback_manager = feedback_manager
        self._data_dir = data_dir
        self._enabled = enabled
        self._min_independent_windows = max(1, int(min_independent_windows))
        self._min_samples = max(1, int(min_samples))
        self._candidates: dict[str, dict[str, Any]] = {}
        self._published: dict[str, dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        """返回功能开关状态。"""

        return self._enabled

    async def rebuild_candidates(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """从统一反馈聚合重建 shadow 候选；低样本只报告原因，不改生产配置。"""

        if not self._enabled:
            return []
        reference_time = reference_time or datetime.now().astimezone()
        aggregates = self._feedback_manager.rebuild(reference_time=reference_time)
        rebuilt: dict[str, dict[str, Any]] = {}
        for aggregate in aggregates:
            key = _candidate_key(aggregate.scope_domain, aggregate.persona_domain)
            if aggregate.status == "candidate" and (
                aggregate.accepted_count >= self._min_samples
                and aggregate.independent_window_count >= self._min_independent_windows
            ):
                status = "ready_for_review"
                reason_code = "candidate"
            else:
                status = "rejected"
                reason_code = "insufficient_evidence"
            rebuilt[key] = {
                "candidate_key": key,
                "scope_domain": aggregate.scope_domain,
                "persona_domain": aggregate.persona_domain,
                "proposed_document_weight": aggregate.proposed_document_weight,
                "proposed_graph_weight": aggregate.proposed_graph_weight,
                "delta_from_baseline": aggregate.delta_from_baseline,
                "accepted_count": aggregate.accepted_count,
                "independent_window_count": aggregate.independent_window_count,
                "decayed_support": aggregate.decayed_support,
                "status": status,
                "reason_code": reason_code,
                "policy_version": aggregate.policy_version,
            }
        self._candidates = rebuilt
        await self._save_state()
        return list(rebuilt.values())

    async def publish_candidate(
        self,
        candidate_key: str,
        *,
        config_writer: Any,
        expected_revision: str,
        current_values: Mapping[str, object],
    ) -> dict[str, Any]:
        """按 revision CAS 发布候选，并记录调用方提供的真实发布前权重。"""

        candidate = self._candidates.get(candidate_key)
        if candidate is None or candidate["status"] != "ready_for_review":
            return {
                "published": False,
                "reason_code": "insufficient_evidence",
            }
        previous = _normalized_weights(current_values)
        if previous is None:
            return {"published": False, "reason_code": "invalid_current_values"}
        updates = {
            "graph_memory.document_route_weight": candidate["proposed_document_weight"],
            "graph_memory.graph_route_weight": candidate["proposed_graph_weight"],
        }
        applied = await config_writer(updates, expected_revision=expected_revision)
        if not applied:
            reason_code = str(
                getattr(config_writer, "_last_write_reason_code", None)
                or "publish_rejected"
            )
            return {"published": False, "reason_code": reason_code}
        snapshot = {
            "candidate_key": candidate_key,
            "published_at": datetime.now().astimezone().isoformat(),
            "revision": expected_revision,
            "previous_document_weight": previous["document_route_weight"],
            "previous_graph_weight": previous["graph_route_weight"],
            "document_route_weight": candidate["proposed_document_weight"],
            "graph_route_weight": candidate["proposed_graph_weight"],
        }
        self._published[candidate_key] = snapshot
        candidate["status"] = "published"
        candidate["reason_code"] = "published"
        await self._save_state()
        return {"published": True, "snapshot": snapshot}

    async def rollback_last_publish(
        self,
        candidate_key: str,
        *,
        config_writer: Any,
        expected_revision: str,
    ) -> dict[str, Any]:
        """用最后发布的快照恢复生产权重，同样走 revision CAS。"""

        snapshot = self._published.get(candidate_key)
        if snapshot is None:
            return {"restored": False, "reason_code": "no_published_snapshot"}
        updates = {
            "graph_memory.document_route_weight": snapshot["previous_document_weight"],
            "graph_memory.graph_route_weight": snapshot["previous_graph_weight"],
        }
        applied = await config_writer(updates, expected_revision=expected_revision)
        if not applied:
            return {
                "restored": False,
                "reason_code": str(
                    getattr(config_writer, "_last_write_reason_code", None)
                    or "rollback_rejected"
                ),
            }
        self._published.pop(candidate_key, None)
        await self._save_state()
        return {"restored": True, "reason_code": "restored"}

    def get_candidates(self) -> list[dict[str, Any]]:
        """返回当前 shadow 候选副本。"""

        return [dict(item) for item in self._candidates.values()]

    def last_published_snapshot(self, candidate_key: str) -> dict[str, Any] | None:
        """返回指定候选最后发布快照；未发布返回 None。"""

        snapshot = self._published.get(candidate_key)
        return dict(snapshot) if snapshot is not None else None

    async def reset(self) -> None:
        """清空 shadow 候选与发布快照，不触碰生产配置。"""

        self._candidates = {}
        self._published = {}
        await self._save_state()

    def safe_summary(self) -> dict[str, Any]:
        """返回不含事件正文或身份细节的候选摘要。"""

        statuses = [
            _safe_status(item.get("status")) for item in self._candidates.values()
        ]
        ready = statuses.count("ready_for_review")
        rejected = statuses.count("rejected") + statuses.count("invalid_state")
        reasons = {
            _safe_reason(item.get("reason_code")) for item in self._candidates.values()
        }
        return {
            "available": True,
            "candidate_count": len(self._candidates),
            "ready_count": ready,
            "rejected_count": rejected,
            "published_count": len(self._published),
            "reasons": sorted(reasons),
        }

    def _state_path(self) -> str:
        """返回 shadow 候选状态文件路径。"""

        return os.path.join(self._data_dir, _STATE_FILE)

    async def _save_state(self) -> None:
        """把候选与发布快照原子写入状态文件。"""

        if not self._data_dir:
            return
        temp_path = f"{self._state_path()}.tmp"
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            state = {
                "candidates": self._candidates,
                "published": self._published,
            }
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(temp_path, self._state_path())
        except OSError:
            logger.warning("[自主学习] 候选状态保存失败")
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    async def load_state(self) -> None:
        """从磁盘恢复 shadow 候选与发布快照。"""

        if not self._data_dir:
            return
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                raise ValueError("learning_state_invalid")
            candidates = state.get("candidates", {}) or {}
            published = state.get("published", {}) or {}
            self._candidates = self._normalize_candidates(candidates)
            self._published = self._normalize_published(published)
            logger.info(
                "[自主学习] 状态恢复完成: candidates=%s, published=%s",
                len(self._candidates),
                len(self._published),
            )
        except Exception:
            self._candidates = {}
            self._published = {}
            logger.warning("[自主学习] 状态恢复失败，使用空状态")

    def _normalize_candidates(self, value: object) -> dict[str, dict[str, Any]]:
        """把磁盘候选限制到固定字段、类型和状态集合。"""

        if not isinstance(value, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_key, raw_item in value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_item, dict):
                continue
            scope = raw_item.get("scope_domain")
            persona = raw_item.get("persona_domain")
            if not isinstance(scope, str) or not scope.strip():
                continue
            if persona is not None and (
                not isinstance(persona, str) or not persona.strip()
            ):
                persona = None
            candidate_key = raw_item.get("candidate_key")
            if not isinstance(candidate_key, str) or not candidate_key.strip():
                candidate_key = _candidate_key(scope, persona)
            document_weight = _finite_number(
                raw_item.get("proposed_document_weight"), 0.0, 1.0
            )
            graph_weight = _finite_number(
                raw_item.get("proposed_graph_weight"), 0.0, 1.0
            )
            delta = _finite_number(raw_item.get("delta_from_baseline"), -0.4, 0.4)
            accepted = _nonnegative_int(raw_item.get("accepted_count"))
            windows = _nonnegative_int(raw_item.get("independent_window_count"))
            support = _finite_number(raw_item.get("decayed_support"), 0.0, 1.0)
            policy_version = _positive_int(raw_item.get("policy_version"))
            status = _safe_status(raw_item.get("status"))
            reason = _safe_reason(raw_item.get("reason_code"))
            valid_weights = (
                document_weight is not None
                and graph_weight is not None
                and math.isclose(document_weight + graph_weight, 1.0, abs_tol=1e-6)
            )
            if (
                not valid_weights
                or delta is None
                or accepted is None
                or windows is None
                or support is None
                or policy_version is None
                or status == "invalid_state"
                or reason == "invalid_state"
            ):
                status = "invalid_state"
                reason = "invalid_state"
                document_weight = self._feedback_manager.policy.baseline_document_weight
                graph_weight = self._feedback_manager.policy.baseline_graph_weight
                delta = 0.0
                accepted = 0
                windows = 0
                support = 0.0
                policy_version = self._feedback_manager.policy.policy_version
            normalized[candidate_key] = {
                "candidate_key": candidate_key,
                "scope_domain": scope,
                "persona_domain": persona,
                "proposed_document_weight": document_weight,
                "proposed_graph_weight": graph_weight,
                "delta_from_baseline": delta,
                "accepted_count": accepted,
                "independent_window_count": windows,
                "decayed_support": support,
                "status": status,
                "reason_code": reason,
                "policy_version": policy_version,
            }
        return normalized

    def _normalize_published(self, value: object) -> dict[str, dict[str, Any]]:
        """只恢复具备真实前值和有限权重的发布快照。"""

        if not isinstance(value, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_key, raw_item in value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_item, dict):
                continue
            previous = _normalized_weights(
                {
                    "document_route_weight": raw_item.get("previous_document_weight"),
                    "graph_route_weight": raw_item.get("previous_graph_weight"),
                }
            )
            current = _normalized_weights(raw_item)
            revision = raw_item.get("revision")
            published_at = raw_item.get("published_at")
            if (
                previous is None
                or current is None
                or not isinstance(revision, str)
                or not revision.strip()
                or not isinstance(published_at, str)
                or not published_at.strip()
            ):
                continue
            normalized[raw_key] = {
                "candidate_key": raw_key,
                "published_at": published_at,
                "revision": revision,
                "previous_document_weight": previous["document_route_weight"],
                "previous_graph_weight": previous["graph_route_weight"],
                "document_route_weight": current["document_route_weight"],
                "graph_route_weight": current["graph_route_weight"],
            }
        return normalized


def _candidate_key(scope_domain: str, persona_domain: str | None) -> str:
    """以长度前缀组合 scope/persona，避免同作用域候选互相覆盖。"""

    persona = persona_domain or ""
    return f"{len(scope_domain)}:{scope_domain}|{len(persona)}:{persona}"


def _finite_number(value: object, minimum: float, maximum: float) -> float | None:
    """只接受给定闭区间内的有限数字，拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        return None
    return numeric


def _nonnegative_int(value: object) -> int | None:
    """只接受非负整数计数，拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_int(value: object) -> int | None:
    """只接受正整数版本号，拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _safe_status(value: object) -> str:
    """把未知候选状态收敛为固定 invalid_state。"""

    return (
        value
        if isinstance(value, str) and value in _CANDIDATE_STATUSES
        else "invalid_state"
    )


def _safe_reason(value: object) -> str:
    """把未知候选原因收敛为固定 invalid_state。"""

    return (
        value
        if isinstance(value, str) and value in _CANDIDATE_REASONS
        else "invalid_state"
    )


def _normalized_weights(value: Mapping[str, object]) -> dict[str, float] | None:
    """校验真实路由权重快照并返回标准字段。"""

    document = _finite_number(value.get("document_route_weight"), 0.0, 1.0)
    graph = _finite_number(value.get("graph_route_weight"), 0.0, 1.0)
    if document is None or graph is None:
        return None
    if not math.isclose(document + graph, 1.0, abs_tol=1e-6):
        return None
    return {"document_route_weight": document, "graph_route_weight": graph}


__all__ = ["AutoLearningManager"]
