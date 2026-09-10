"""话题候选领域 DTO、来源快照和安全 Prompt 投影。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .topic_label_renderer import (
    normalize_topic_key,
    normalize_topic_label,
    render_topic_labels,
)

_MAX_TEXT_LENGTH = 256


def _text(value: object, name: str, *, optional: bool = True) -> str | None:
    """校验不含正文的有限字符串字段。"""
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    value = value.strip()
    if len(value) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{name} 超出长度限制")
    if not value and optional:
        return None
    if not value:
        raise ValueError(f"{name} 不能为空")
    return value


def _nonnegative_int(value: object, name: str, *, positive: bool = False) -> int:
    """校验非负或正整数，拒绝 bool。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必须是整数")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} 必须为{'正' if positive else '非负'}整数")
    return value


def _nonnegative_number(value: object, name: str) -> float:
    """校验非负有限时间或租约数值，拒绝布尔值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} 必须是数值")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} 必须是非负有限数")
    return normalized


class TopicCandidateMode(str, Enum):
    """话题候选复用允许使用的闭集模式。"""

    OFF = "off"
    OBSERVE = "observe"
    FULL = "full"
    TOP_K = "top_k"


class SourceProvenanceState(str, Enum):
    """话题候选来源证据的三态结果。"""

    ABSENT = "absent"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class TopicCandidateLabel:
    """只含模型可见安全标签和来源证据状态的不可变候选。"""

    safe_label: str
    source_provenance_complete: bool | None = None

    def __post_init__(self) -> None:
        """规范化安全标签，并保留缺失与不完整来源证据的区别。"""

        label = normalize_topic_label(self.safe_label)
        if label is None:
            raise ValueError("topic_label_invalid")
        if self.source_provenance_complete is not None and not isinstance(
            self.source_provenance_complete, bool
        ):
            raise TypeError("source_provenance_complete 必须是布尔值或缺失")
        object.__setattr__(self, "safe_label", label)

    @property
    def provenance_state(self) -> SourceProvenanceState:
        """返回不把缺失证据误当成不完整或完整的三态值。"""

        if self.source_provenance_complete is True:
            return SourceProvenanceState.COMPLETE
        if self.source_provenance_complete is False:
            return SourceProvenanceState.INCOMPLETE
        return SourceProvenanceState.ABSENT

    @property
    def label(self) -> str:
        """返回唯一允许进入模型的安全标签字段。"""

        return self.safe_label


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    """候选指标（安全标量，不含敏感信息）"""

    mode: TopicCandidateMode
    effective_mode: TopicCandidateMode
    n_candidates: int
    n_with_provenance: int
    n_tokens: int | None
    selector_latency_ms: float
    reason: str | None

    def __post_init__(self) -> None:
        """验证不变量"""
        if self.n_candidates < 0:
            raise ValueError("n_candidates must be non-negative")
        if not (0 <= self.n_with_provenance <= self.n_candidates):
            raise ValueError("n_with_provenance must be in [0, n_candidates]")
        if self.selector_latency_ms < 0:
            raise ValueError("selector_latency_ms must be non-negative")
        if self.n_tokens is not None and self.n_tokens < 0:
            raise ValueError("n_tokens must be non-negative or None")


@dataclass(frozen=True, slots=True)
class TopicCandidateContext:
    """固定一次候选读取所需的 scope/privacy/chat type/revision 快照。"""

    scope_key: str = ""
    chat_type: str | None = None
    privacy_level: str | None = None
    resolver_revision: str = ""
    source_digest: str = ""
    session_epoch: int = 0
    scope_reason_code: str = "scope_unavailable"
    source_provenance_complete: bool | None = None

    def __post_init__(self) -> None:
        """校验完整快照；缺失快照只能保持不可用，不执行字段猜测。"""

        scope_key = _text(self.scope_key, "scope_key") or ""
        revision = _text(self.resolver_revision, "resolver_revision") or ""
        digest = _text(self.source_digest, "source_digest") or ""
        chat_type = _text(self.chat_type, "chat_type")
        privacy = _text(self.privacy_level, "privacy_level")
        scope_values = (scope_key, privacy, revision)
        if any(scope_values) and (not all(scope_values) or chat_type is None):
            raise ValueError("scope_snapshot_incomplete")
        if chat_type is not None and chat_type not in {"private", "group"}:
            raise ValueError("chat_type_invalid")
        if privacy is not None and privacy not in {
            "public",
            "shared",
            "confidential",
        }:
            raise ValueError("privacy_level_invalid")
        epoch = _nonnegative_int(self.session_epoch, "session_epoch")
        reason = _text(self.scope_reason_code, "scope_reason_code", optional=False)
        assert reason is not None
        if reason not in {"scope_resolved", "scope_unavailable"}:
            reason = "scope_unavailable"
        marker = self.source_provenance_complete
        if marker is not None and not isinstance(marker, bool):
            raise TypeError("source_provenance_complete 必须是布尔值或缺失")
        if marker is not True:
            reason = "scope_unavailable"
        object.__setattr__(self, "scope_key", scope_key)
        object.__setattr__(self, "chat_type", chat_type)
        object.__setattr__(self, "privacy_level", privacy)
        object.__setattr__(self, "resolver_revision", revision)
        object.__setattr__(self, "source_digest", digest)
        object.__setattr__(self, "session_epoch", epoch)
        object.__setattr__(self, "scope_reason_code", reason)
        object.__setattr__(self, "source_provenance_complete", marker)

    @property
    def available(self) -> bool:
        """返回是否具有可供候选隔离使用的完整快照。"""

        return bool(
            self.source_provenance_complete is True
            and self.scope_key
            and self.chat_type
            and self.privacy_level
            and self.resolver_revision
            and self.scope_reason_code == "scope_resolved"
        )

    @property
    def scope_available(self) -> bool:
        """返回与 ``available`` 相同的兼容属性。"""

        return self.available

    def to_persisted_dict(self) -> dict[str, object]:
        """返回内部 summary job 可持久化的固定快照字段。"""

        return {
            "scope_key": self.scope_key,
            "chat_type": self.chat_type,
            "privacy_level": self.privacy_level,
            "resolver_revision": self.resolver_revision,
            "scope_reason_code": self.scope_reason_code,
            "source_provenance_complete": self.source_provenance_complete,
        }

    def safe_projection(self) -> dict[str, object]:
        """返回不含 scope key/revision 的低敏投影。"""

        return {
            "chat_type": self.chat_type,
            "privacy_level": self.privacy_level,
            "available": self.available,
            "reason_code": self.scope_reason_code,
        }


@dataclass(frozen=True, slots=True)
class TopicCandidateMetrics:
    """候选选择的低基数标量；不保存标签、查询或来源字段。"""

    mode: TopicCandidateMode | str = TopicCandidateMode.OBSERVE
    effective_mode: TopicCandidateMode | str = TopicCandidateMode.OFF
    catalog_status: str = "unavailable"
    topic_count_bucket: str = "unknown"
    candidate_count: int = 0
    bm25_hit_count: int = 0
    recent_fill_count: int = 0
    identity_drop_count: int = 0
    source_provenance_complete_count: int = 0
    source_provenance_missing_count: int = 0
    budget_reason: str = ""
    reason_code: str = "unknown"
    selector_duration_ms: float = 0.0

    def __post_init__(self) -> None:
        """限制模式、原因和所有计时/计数字段为安全标量。"""

        for name in (
            "candidate_count",
            "bm25_hit_count",
            "recent_fill_count",
            "identity_drop_count",
            "source_provenance_complete_count",
            "source_provenance_missing_count",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        object.__setattr__(
            self,
            "selector_duration_ms",
            _nonnegative_number(self.selector_duration_ms, "selector_duration_ms"),
        )
        for name in ("mode", "effective_mode"):
            value = getattr(self, name)
            try:
                value = (
                    value
                    if isinstance(value, TopicCandidateMode)
                    else TopicCandidateMode(str(value))
                )
            except ValueError:
                value = TopicCandidateMode.OFF
            object.__setattr__(self, name, value)
        for name in (
            "catalog_status",
            "topic_count_bucket",
            "budget_reason",
            "reason_code",
        ):
            value = _text(getattr(self, name), name) or ""
            object.__setattr__(self, name, value[:64])

    def to_dict(self) -> dict[str, object]:
        """返回不含候选标签和敏感查询的观测投影。"""

        return {
            "mode": getattr(self.mode, "value", str(self.mode)),
            "effective_mode": getattr(
                self.effective_mode, "value", str(self.effective_mode)
            ),
            "catalog_status": self.catalog_status,
            "topic_count_bucket": self.topic_count_bucket,
            "candidate_count": self.candidate_count,
            "bm25_hit_count": self.bm25_hit_count,
            "recent_fill_count": self.recent_fill_count,
            "identity_drop_count": self.identity_drop_count,
            "source_provenance_complete_count": self.source_provenance_complete_count,
            "source_provenance_missing_count": self.source_provenance_missing_count,
            "budget_reason": self.budget_reason,
            "reason_code": self.reason_code,
            "selector_duration_ms": self.selector_duration_ms,
        }

    safe_projection = to_dict


@dataclass(frozen=True, slots=True)
class TopicCandidateSelection:
    """候选选择结果；生产 Prompt 只能读取完整来源证据的标签。"""

    labels: tuple[str, ...] = ()
    source_provenance_complete: bool | None = None
    mode: TopicCandidateMode | str = TopicCandidateMode.OBSERVE
    effective_mode: TopicCandidateMode | str = TopicCandidateMode.OFF
    catalog_status: str = "unavailable"
    topic_count_bucket: str = "unknown"
    candidate_count: int = 0
    bm25_hit_count: int = 0
    recent_fill_count: int = 0
    identity_drop_count: int = 0
    budget_reason: str = ""
    reason_code: str = "unknown"
    selector_duration_ms: float = 0.0
    metrics: TopicCandidateMetrics | None = None

    def __post_init__(self) -> None:
        """规范化标签、去除重复键并冻结低敏选择统计。"""

        normalized: list[str] = []
        seen: set[str] = set()
        label_markers: list[bool | None] = []
        for value in tuple(self.labels):
            if isinstance(value, TopicCandidateLabel):
                marker_value = value.source_provenance_complete
                value = value.safe_label
            else:
                marker_value = None
            label = normalize_topic_label(value)
            if label is None:
                raise ValueError("topic_label_invalid")
            key = normalize_topic_key(label)
            if key is None or key in seen:
                continue
            seen.add(key)
            normalized.append(label)
            label_markers.append(marker_value)
        marker = self.source_provenance_complete
        if marker is None and label_markers:
            # selection 级标记缺失时从 label 证据推导：全部 label 的
            # provenance 一致才继承该值，混合证据一律置 False（fail-closed）
            if all(entry is True for entry in label_markers):
                marker = True
            elif any(entry is False for entry in label_markers):
                marker = False
        if marker is not None and not isinstance(marker, bool):
            raise TypeError("source_provenance_complete 必须是布尔值或缺失")
        object.__setattr__(self, "labels", tuple(normalized))
        object.__setattr__(self, "source_provenance_complete", marker)
        if self.metrics is not None and not isinstance(
            self.metrics, TopicCandidateMetrics
        ):
            raise TypeError("metrics 必须是 TopicCandidateMetrics")
        if self.metrics is None:
            object.__setattr__(
                self,
                "metrics",
                TopicCandidateMetrics(
                    mode=self.mode,
                    effective_mode=self.effective_mode,
                    catalog_status=self.catalog_status,
                    topic_count_bucket=self.topic_count_bucket,
                    candidate_count=self.candidate_count,
                    bm25_hit_count=self.bm25_hit_count,
                    recent_fill_count=self.recent_fill_count,
                    identity_drop_count=self.identity_drop_count,
                    budget_reason=self.budget_reason,
                    reason_code=self.reason_code,
                    selector_duration_ms=self.selector_duration_ms,
                ),
            )

    @property
    def production_labels(self) -> tuple[str, ...]:
        """只返回 source-level provenance 完整的模型可见标签。"""

        return self.labels if self.source_provenance_complete is True else ()

    @property
    def safe_labels(self) -> tuple[str, ...]:
        """返回已通过字符串安全校验的标签，不改变来源证据语义。"""

        return self.labels

    def render_prompt(self) -> str:
        """使用反思专用无状态 renderer 生成安全候选区块。"""

        return render_topic_labels(self.production_labels).rendered_block

    def safe_projection(self) -> dict[str, object]:
        """返回不含标签的低敏观测投影。"""

        assert self.metrics is not None
        return self.metrics.to_dict()

    to_dict = safe_projection


__all__ = [
    "CandidateMetrics",
    "SourceProvenanceState",
    "TopicCandidateContext",
    "TopicCandidateLabel",
    "TopicCandidateMetrics",
    "TopicCandidateMode",
    "TopicCandidateSelection",
    "normalize_topic_key",
    "normalize_topic_label",
    "render_topic_labels",
]
