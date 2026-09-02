"""记忆总结任务的不可变领域模型与安全投影。"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from ....shared.contracts.conversation import Message
from ....shared.summary_source import source_window_digest

_MAX_TEXT_LENGTH = 256
_MAX_GATE_JSON_BYTES = 65_536
_MAX_SNAPSHOT_VALUE = 2**63 - 1


class SummaryJobStatus(str, Enum):
    """总结任务允许持久化的闭集状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class CandidateDisposition(str, Enum):
    """候选写入的允许终态动作。"""

    QUARANTINED = "quarantined"
    DISCARD = "discard"
    MARK_WRITE = "mark_write"
    CANONICAL = "canonical"
    SKIPPED_IDEMPOTENT = "skipped_idempotent"
    FAILED = "failed"


class CandidateLedgerStatus(str, Enum):
    """候选 slot ledger 的持久化状态。"""

    PLANNED = "planned"
    WRITING = "writing"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class SummaryReasonCode(str, Enum):
    """跨层共享的固定总结 reason code。"""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    DUPLICATE = "duplicate"
    NO_WINDOW = "no_window"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    STORE_UNAVAILABLE = "store_unavailable"
    MIGRATION_FAILED = "migration_failed"
    SOURCE_INCOMPLETE = "source_incomplete"
    SOURCE_DIGEST_MISMATCH = "source_digest_mismatch"
    CLAIM_LOST = "claim_lost"
    EPOCH_FENCED = "epoch_fenced"
    GENERATION_FENCED = "generation_fenced"
    LEASE_EXPIRED = "lease_expired"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_EXHAUSTED = "retry_exhausted"
    INVALID_ACTION = "invalid_action"
    INVALID_SLOT = "invalid_slot"
    LEDGER_UNRESOLVED = "ledger_unresolved"
    TRIM_BLOCKED = "trim_blocked"
    LEGACY_PENDING = "legacy_pending"
    LEGACY_PENDING_INVALID = "legacy_pending_invalid"
    ABANDONED_CONFIRMED = "abandoned_confirmed"
    NO_FACTS = "no_facts"
    SUMMARY_INVALID = "summary_invalid"
    COMPLETED = "completed"


_FAILURE_REASON_CODES = frozenset(
    {
        SummaryReasonCode.BLOCKED,
        SummaryReasonCode.CANCELLED,
        SummaryReasonCode.UNKNOWN,
        SummaryReasonCode.STORE_UNAVAILABLE,
        SummaryReasonCode.MIGRATION_FAILED,
        SummaryReasonCode.SOURCE_INCOMPLETE,
        SummaryReasonCode.SOURCE_DIGEST_MISMATCH,
        SummaryReasonCode.CLAIM_LOST,
        SummaryReasonCode.EPOCH_FENCED,
        SummaryReasonCode.GENERATION_FENCED,
        SummaryReasonCode.LEASE_EXPIRED,
        SummaryReasonCode.RETRY_SCHEDULED,
        SummaryReasonCode.RETRY_EXHAUSTED,
        SummaryReasonCode.INVALID_ACTION,
        SummaryReasonCode.INVALID_SLOT,
        SummaryReasonCode.LEDGER_UNRESOLVED,
        SummaryReasonCode.SUMMARY_INVALID,
    }
)


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


def _reason(value: SummaryReasonCode | str | None) -> SummaryReasonCode:
    """把外部 reason code 收敛到固定枚举，未知值降级为 unknown。"""
    if value is None:
        return SummaryReasonCode.UNKNOWN
    if isinstance(value, SummaryReasonCode):
        return value
    try:
        return SummaryReasonCode(str(value))
    except ValueError:
        return SummaryReasonCode.UNKNOWN


def _nonnegative_number(value: object, name: str) -> float:
    """校验非负有限时间或租约数值，拒绝布尔值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} 必须是数值")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} 必须是非负有限数")
    return normalized


def _bounded_json_object(value: object, name: str) -> str:
    """只验证有界 JSON 对象外壳，不解释质量门内部配置。"""
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是 JSON 字符串")
    if len(value.encode("utf-8")) > _MAX_GATE_JSON_BYTES:
        raise ValueError(f"{name} 超出长度限制")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} 必须是有效 JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} 顶层必须是对象")
    return value


@dataclass(frozen=True, slots=True)
class SummaryWindowContext:
    """描述待规划总结窗口的稳定会话上下文，不持有消息正文。"""

    session_id: str
    session_epoch: int
    start_seq: int
    end_seq: int = 0
    expected_count: int = 0
    source_digest: str = ""
    persona_id: str | None = None
    chat_type: str | None = None
    group_id: str | None = None
    scope_id: str | None = None
    triggered_by: str = "automatic"
    gate_revision: str = ""
    gate_snapshot_json: str = "{}"
    window_size: int = 2

    def __post_init__(self) -> None:
        """检查范围、身份标签和固定窗口大小。"""
        session_id = _text(self.session_id, "session_id", optional=False)
        assert session_id is not None
        epoch = _nonnegative_int(self.session_epoch, "session_epoch", positive=True)
        start = _nonnegative_int(self.start_seq, "start_seq")
        end = _nonnegative_int(self.end_seq, "end_seq")
        expected = _nonnegative_int(self.expected_count, "expected_count")
        if end == 0 and start > 0 and expected == 0:
            end = start
        if end < start:
            raise ValueError("end_seq 不能小于 start_seq")
        if expected == 0 and end > start:
            expected = end - start
        if expected != end - start:
            raise ValueError("expected_count 必须等于 seq 范围长度")

        digest = _text(self.source_digest, "source_digest") or ""
        triggered = _text(self.triggered_by, "triggered_by", optional=False)
        assert triggered is not None
        revision = _text(self.gate_revision, "gate_revision") or ""
        window_size = _nonnegative_int(self.window_size, "window_size", positive=True)
        if window_size < 2:
            raise ValueError("window_size 至少为 2")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "session_epoch", epoch)
        object.__setattr__(self, "start_seq", start)
        object.__setattr__(self, "end_seq", end)
        object.__setattr__(self, "expected_count", expected)
        object.__setattr__(self, "source_digest", digest)
        object.__setattr__(self, "persona_id", _text(self.persona_id, "persona_id"))
        chat_type = _text(self.chat_type, "chat_type")
        group_id = _text(self.group_id, "group_id")
        scope_id = _text(self.scope_id, "scope_id")
        if chat_type not in {None, "private", "group"}:
            raise ValueError("chat_type 必须是 private 或 group")
        if chat_type == "group" and not group_id:
            raise ValueError("group chat 必须绑定 group_id")
        if chat_type == "private" and group_id is not None:
            raise ValueError("private chat 不得绑定 group_id")
        object.__setattr__(self, "chat_type", chat_type)
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "triggered_by", triggered)
        object.__setattr__(self, "gate_revision", revision)
        object.__setattr__(
            self,
            "gate_snapshot_json",
            _bounded_json_object(self.gate_snapshot_json, "gate_snapshot_json"),
        )
        object.__setattr__(self, "window_size", window_size)


@dataclass(frozen=True, slots=True)
class SummaryJob:
    """任务状态的安全 DTO，不包含消息正文。"""

    job_id: str
    session_id: str
    session_epoch: int
    start_seq: int
    end_seq: int
    expected_count: int
    source_digest: str
    status: SummaryJobStatus = SummaryJobStatus.QUEUED
    persona_id: str | None = None
    chat_type: str | None = None
    group_id: str | None = None
    scope_id: str | None = None
    gate_revision: str = ""
    gate_snapshot_json: str = "{}"
    triggered_by: str = "automatic"
    attempt_count: int = 0
    next_attempt_at: float = 0.0
    lease_until: float | None = None
    worker_generation: int = 0
    failed_stage: str | None = None
    reason_code: SummaryReasonCode = SummaryReasonCode.UNKNOWN
    exception_type: str | None = None
    operator_action: str | None = None
    canonical_count: int = 0
    quarantine_count: int = 0
    discard_count: int = 0
    mark_write_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        """验证持久任务 DTO 的有限标量和范围约束。"""
        for name in ("job_id", "session_id"):
            value = _text(getattr(self, name), name, optional=False)
            assert value is not None
            object.__setattr__(self, name, value)
        epoch = _nonnegative_int(self.session_epoch, "session_epoch", positive=True)
        start = _nonnegative_int(self.start_seq, "start_seq")
        end = _nonnegative_int(self.end_seq, "end_seq")
        expected = _nonnegative_int(self.expected_count, "expected_count")
        if end < start or expected != end - start:
            raise ValueError("任务 seq 范围与 expected_count 不一致")
        object.__setattr__(self, "session_epoch", epoch)
        object.__setattr__(self, "start_seq", start)
        object.__setattr__(self, "end_seq", end)
        object.__setattr__(self, "expected_count", expected)
        object.__setattr__(
            self, "source_digest", _text(self.source_digest, "source_digest") or ""
        )
        status = (
            self.status
            if isinstance(self.status, SummaryJobStatus)
            else SummaryJobStatus(str(self.status))
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_code", _reason(self.reason_code))
        object.__setattr__(self, "persona_id", _text(self.persona_id, "persona_id"))
        object.__setattr__(self, "chat_type", _text(self.chat_type, "chat_type"))
        object.__setattr__(self, "group_id", _text(self.group_id, "group_id"))
        object.__setattr__(self, "scope_id", _text(self.scope_id, "scope_id"))
        object.__setattr__(
            self, "gate_revision", _text(self.gate_revision, "gate_revision") or ""
        )
        object.__setattr__(
            self,
            "gate_snapshot_json",
            _bounded_json_object(self.gate_snapshot_json, "gate_snapshot_json"),
        )
        triggered_by = _text(self.triggered_by, "triggered_by", optional=False)
        assert triggered_by is not None
        object.__setattr__(self, "triggered_by", triggered_by)
        object.__setattr__(
            self, "attempt_count", _nonnegative_int(self.attempt_count, "attempt_count")
        )
        object.__setattr__(
            self,
            "worker_generation",
            _nonnegative_int(self.worker_generation, "worker_generation"),
        )
        object.__setattr__(
            self,
            "next_attempt_at",
            _nonnegative_number(self.next_attempt_at, "next_attempt_at"),
        )
        if self.lease_until is not None:
            object.__setattr__(
                self,
                "lease_until",
                _nonnegative_number(self.lease_until, "lease_until"),
            )
        object.__setattr__(
            self, "operator_action", _text(self.operator_action, "operator_action")
        )
        for name in ("created_at", "updated_at"):
            object.__setattr__(
                self, name, _nonnegative_number(getattr(self, name), name)
            )
        for name in (
            "canonical_count",
            "quarantine_count",
            "discard_count",
            "mark_write_count",
            "failed_count",
            "skipped_count",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """带 worker fencing 条件的任务领取 DTO。"""

    job: SummaryJob
    claim_token: str
    scheduler_id: str
    lease_until: float
    worker_generation: int

    def __post_init__(self) -> None:
        """验证随机 claim token、租约和 generation。"""
        if not isinstance(self.job, SummaryJob):
            raise TypeError("job 必须是 SummaryJob")
        token = _text(self.claim_token, "claim_token", optional=False)
        scheduler = _text(self.scheduler_id, "scheduler_id", optional=False)
        assert token is not None and scheduler is not None
        if not math.isfinite(float(self.lease_until)):
            raise ValueError("lease_until 必须有限")
        generation = _nonnegative_int(
            self.worker_generation, "worker_generation", positive=True
        )
        object.__setattr__(self, "claim_token", token)
        object.__setattr__(self, "scheduler_id", scheduler)
        object.__setattr__(self, "worker_generation", generation)

    def __getattr__(self, name: str) -> Any:
        """兼容 scheduler 直接读取任务字段，同时不复制可变状态。"""
        return getattr(self.job, name)


@dataclass(frozen=True, slots=True)
class SourceWindow:
    """仅供 worker 使用的正文窗口；规划和公开投影不返回此 DTO。"""

    session_id: str
    start_seq: int
    end_seq: int
    expected_count: int
    source_digest: str
    messages: tuple[Message, ...]
    message_seqs: tuple[int, ...]

    def __post_init__(self) -> None:
        """检查 worker 来源窗口的数量、连续序号和内容摘要。"""
        session_id = _text(self.session_id, "session_id", optional=False)
        assert session_id is not None
        start = _nonnegative_int(self.start_seq, "start_seq")
        end = _nonnegative_int(self.end_seq, "end_seq")
        expected = _nonnegative_int(self.expected_count, "expected_count")
        messages = tuple(self.messages)
        seqs = tuple(self.message_seqs)
        expected_seqs = tuple(range(start + 1, end + 1))
        if end <= start or expected != end - start:
            raise ValueError("source window 范围不一致")
        if len(messages) != expected or seqs != expected_seqs:
            raise ValueError("source window 消息不完整")
        if any(message.session_id != session_id for message in messages):
            raise ValueError("source window 包含其他会话消息")
        source_digest = _text(self.source_digest, "source_digest", optional=False)
        assert source_digest is not None
        if source_digest != source_window_digest(messages, seqs):
            raise ValueError("source window 摘要不一致")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "start_seq", start)
        object.__setattr__(self, "end_seq", end)
        object.__setattr__(self, "expected_count", expected)
        object.__setattr__(self, "source_digest", source_digest)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "message_seqs", seqs)


@dataclass(frozen=True, slots=True)
class CandidateIntent:
    """不含候选正文的 slot intent/ledger 摘要。"""

    slot: int
    content_digest: str
    idempotency_key: str = ""
    slot_key: str = ""
    disposition: CandidateDisposition | None = None
    status: CandidateLedgerStatus = CandidateLedgerStatus.PLANNED
    canonical_id: int | None = None

    def __post_init__(self) -> None:
        """验证 slot、摘要和闭集状态。"""
        object.__setattr__(self, "slot", _nonnegative_int(self.slot, "slot"))
        digest = _text(self.content_digest, "content_digest", optional=False)
        assert digest is not None
        object.__setattr__(self, "content_digest", digest)
        if self.disposition is not None and not isinstance(
            self.disposition, CandidateDisposition
        ):
            object.__setattr__(
                self, "disposition", CandidateDisposition(str(self.disposition))
            )
        if not isinstance(self.status, CandidateLedgerStatus):
            object.__setattr__(self, "status", CandidateLedgerStatus(str(self.status)))
        idempotency_key = _text(self.idempotency_key, "idempotency_key") or ""
        object.__setattr__(self, "idempotency_key", idempotency_key)
        slot_key = _text(self.slot_key, "slot_key") or ""
        object.__setattr__(self, "slot_key", slot_key)
        if self.canonical_id is not None:
            object.__setattr__(
                self,
                "canonical_id",
                _nonnegative_int(self.canonical_id, "canonical_id", positive=True),
            )


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """窗口候选终态计数和是否允许推进的结果。"""

    can_advance: bool = False
    canonical_count: int = 0
    quarantine_count: int = 0
    discard_count: int = 0
    mark_write_count: int = 0
    failed_count: int = 0
    skipped_idempotent_count: int = 0
    unknown_count: int = 0
    candidate_slots: tuple[CandidateIntent, ...] = ()
    failed_stage: str | None = None
    reason_code: SummaryReasonCode = SummaryReasonCode.COMPLETED

    def __post_init__(self) -> None:
        """校验结果计数、唯一 slot 与合法无事实结果形状。"""
        count_names = (
            "canonical_count",
            "quarantine_count",
            "discard_count",
            "mark_write_count",
            "failed_count",
            "skipped_idempotent_count",
            "unknown_count",
        )
        for name in count_names:
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        slots = tuple(self.candidate_slots)
        if len({item.slot for item in slots}) != len(slots):
            raise ValueError("candidate slot 不能重复")
        object.__setattr__(self, "candidate_slots", slots)
        object.__setattr__(
            self, "failed_stage", _text(self.failed_stage, "failed_stage")
        )
        reason_code = _reason(self.reason_code)
        if reason_code is SummaryReasonCode.NO_FACTS and (
            not self.can_advance
            or slots
            or any(getattr(self, name) for name in count_names)
        ):
            raise ValueError("no_facts 结果必须推进且不含候选或写入计数")
        object.__setattr__(self, "reason_code", reason_code)

    @property
    def slots(self) -> tuple[CandidateIntent, ...]:
        """返回 candidate slot ledger 摘要。"""
        return self.candidate_slots


@dataclass(frozen=True, slots=True)
class SummaryFailure:
    """窗口失败的固定阶段、reason 和异常类型。"""

    failed_stage: str
    reason_code: SummaryReasonCode
    exception_type: str = ""
    retryable: bool = True
    cancelled: bool = False

    def __post_init__(self) -> None:
        """不允许异常正文进入失败 DTO。"""
        stage = _text(self.failed_stage, "failed_stage", optional=False)
        assert stage is not None
        exception_type = _text(self.exception_type, "exception_type") or ""
        object.__setattr__(self, "failed_stage", stage)
        object.__setattr__(self, "exception_type", exception_type)
        reason_code = _reason(self.reason_code)
        if reason_code not in _FAILURE_REASON_CODES:
            reason_code = SummaryReasonCode.UNKNOWN
        object.__setattr__(self, "reason_code", reason_code)


WindowFailure = SummaryFailure


@dataclass(frozen=True, slots=True)
class SummaryEnqueueResult:
    """只包含安全入队计数，不暴露 session/job 标识。"""

    accepted: bool
    queued: int = 0
    duplicates: int = 0
    active_parallelism: int = 0
    target_parallelism: int = 0
    reason_code: SummaryReasonCode = SummaryReasonCode.UNKNOWN

    def __post_init__(self) -> None:
        """规范化非负入队与并发计数。"""
        for name in (
            "queued",
            "duplicates",
            "active_parallelism",
            "target_parallelism",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        object.__setattr__(self, "accepted", bool(self.accepted))
        object.__setattr__(self, "reason_code", _reason(self.reason_code))


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """窗口完成收口及连续 cursor 投影结果。"""

    accepted: bool
    status: SummaryJobStatus
    cursor: int = 0
    reason_code: SummaryReasonCode = SummaryReasonCode.UNKNOWN

    def __post_init__(self) -> None:
        """限制 cursor 为非负值并规范化状态。"""
        object.__setattr__(
            self,
            "status",
            self.status
            if isinstance(self.status, SummaryJobStatus)
            else SummaryJobStatus(str(self.status)),
        )
        object.__setattr__(self, "cursor", _nonnegative_int(self.cursor, "cursor"))
        object.__setattr__(self, "reason_code", _reason(self.reason_code))


@dataclass(frozen=True, slots=True)
class RetryResult:
    """失败、重试或阻塞收口结果。"""

    accepted: bool
    status: SummaryJobStatus
    attempt_count: int = 0
    next_attempt_at: float = 0.0
    reason_code: SummaryReasonCode = SummaryReasonCode.UNKNOWN

    def __post_init__(self) -> None:
        """限制重试计数和时间为有限标量。"""
        object.__setattr__(
            self,
            "status",
            self.status
            if isinstance(self.status, SummaryJobStatus)
            else SummaryJobStatus(str(self.status)),
        )
        object.__setattr__(
            self, "attempt_count", _nonnegative_int(self.attempt_count, "attempt_count")
        )
        if not math.isfinite(float(self.next_attempt_at)) or self.next_attempt_at < 0:
            raise ValueError("next_attempt_at 必须是非负有限数")
        object.__setattr__(self, "reason_code", _reason(self.reason_code))


@dataclass(frozen=True, slots=True)
class EpochResult:
    """会话 epoch fence 结果。"""

    accepted: bool
    epoch: int
    cancelled_count: int = 0
    reason_code: SummaryReasonCode = SummaryReasonCode.UNKNOWN

    def __post_init__(self) -> None:
        """限制 epoch 与取消计数。"""
        object.__setattr__(
            self, "epoch", _nonnegative_int(self.epoch, "epoch", positive=True)
        )
        object.__setattr__(
            self,
            "cancelled_count",
            _nonnegative_int(self.cancelled_count, "cancelled_count"),
        )
        object.__setattr__(self, "reason_code", _reason(self.reason_code))


@dataclass(frozen=True, slots=True)
class TrimResult:
    """安全修剪来源结果。"""

    accepted: bool
    deleted_count: int = 0
    reason_code: SummaryReasonCode = SummaryReasonCode.UNKNOWN

    def __post_init__(self) -> None:
        """限制删除计数。"""
        object.__setattr__(
            self, "deleted_count", _nonnegative_int(self.deleted_count, "deleted_count")
        )
        object.__setattr__(self, "reason_code", _reason(self.reason_code))


@dataclass(frozen=True, slots=True)
class SummaryTaskSnapshot:
    """诊断、Page 和命令共用的有限非负标量投影。"""

    queued: int = 0
    running: int = 0
    failed: int = 0
    blocked: int = 0
    unknown: int = 0
    cancelled: int = 0
    abandoned: int = 0
    active_parallelism: int = 0
    target_parallelism: int = 0
    canonical_total: int = 0
    quarantine_total: int = 0
    discard_total: int = 0
    mark_write_total: int = 0
    failed_candidate_total: int = 0
    skipped_idempotent_total: int = 0

    def __post_init__(self) -> None:
        """把所有快照字段限制为有限、非负整数。"""
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool):
                value = int(value)
            elif isinstance(value, float):
                value = int(value) if math.isfinite(value) and value >= 0 else 0
            elif not isinstance(value, int) or value < 0:
                value = 0
            object.__setattr__(self, item.name, min(value, _MAX_SNAPSHOT_VALUE))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> "SummaryTaskSnapshot":
        """只从 allowlist 字段构造安全快照，非法值降级为零。"""
        if not isinstance(value, Mapping):
            return cls()
        values: dict[str, int] = {}
        for item in fields(cls):
            try:
                raw = value.get(item.name, 0)
                if isinstance(raw, bool):
                    values[item.name] = int(raw)
                elif isinstance(raw, (int, float, str)):
                    values[item.name] = int(raw)
                else:
                    values[item.name] = 0
            except (TypeError, ValueError, OverflowError):
                values[item.name] = 0
        return cls(**values)

    def to_dict(self) -> dict[str, int]:
        """返回可公开的有限标量字典。"""
        return {item.name: int(getattr(self, item.name)) for item in fields(self)}

    def safe_summary(self) -> dict[str, int]:
        """返回与 to_dict 相同的安全诊断投影。"""
        return self.to_dict()


def sanitize_summary_task_snapshot(
    value: SummaryTaskSnapshot | Mapping[str, object] | None,
) -> SummaryTaskSnapshot:
    """统一诊断入口；异常 Mapping 只降级为空快照。"""
    if isinstance(value, SummaryTaskSnapshot):
        return value
    try:
        return SummaryTaskSnapshot.from_mapping(value)
    except Exception:
        return SummaryTaskSnapshot()


def retry_delay_seconds(
    attempt_count: int, *, base_seconds: int = 5, max_seconds: int = 300
) -> int:
    """计算有界指数退避，不读取 wall clock，也不执行 I/O。"""
    attempt = _nonnegative_int(attempt_count, "attempt_count", positive=True)
    base = _nonnegative_int(base_seconds, "base_seconds", positive=True)
    ceiling = _nonnegative_int(max_seconds, "max_seconds", positive=True)
    if base > ceiling:
        raise ValueError("base_seconds 不能大于 max_seconds")
    return min(ceiling, base * (2 ** min(attempt - 1, 30)))


__all__ = [
    "CandidateDisposition",
    "CandidateIntent",
    "CandidateLedgerStatus",
    "ClaimedJob",
    "CompletionResult",
    "EpochResult",
    "SourceWindow",
    "SummaryEnqueueResult",
    "SummaryFailure",
    "SummaryJob",
    "SummaryJobStatus",
    "SummaryReasonCode",
    "SummaryTaskSnapshot",
    "SummaryWindowContext",
    "TrimResult",
    "WindowFailure",
    "WindowOutcome",
    "retry_delay_seconds",
    "source_window_digest",
    "sanitize_summary_task_snapshot",
]
