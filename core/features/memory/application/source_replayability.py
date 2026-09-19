"""canonical 来源引用的只读可重放状态评估。

按会话存储当前的消息事实，逐项核对 canonical metadata 已持久化的来源引用
（message_id / message_seq / session / epoch / role / 区间 / 指纹），只输出
``replayable`` / ``partial`` / ``unavailable`` / ``unknown`` 与聚合计数、固定
原因码。

边界：

- 只读、不写入、不删除，也不改变 ``/new``、trim、TTL 或 quarantine 的清理语义。
- 响应投影只含状态、计数与原因码，不含正文、身份、引用映射、scope/privacy、
  revision 或 epoch 数值。
- Store 缺失、读取异常、epoch 无法对齐或引用格式不明一律 ``unknown``，不推断
  消息已被删除；只有全部因子（epoch、窗口边界、message_id、message_seq、
  session、role、区间、指纹）都满足才判 ``replayable``。
 - 写入 epoch 取 ``source_epoch``；旧写入只带 ``source_window.session_epoch`` 时回退读取该字段；若 canonical 记录了 ``source_start_seq/source_end_seq`` 或嵌套窗口边界，则参与边界核对；两组边界冲突按无法核对处理。
- 只对账 ``source_evidence`` / ``fact_source_evidence`` 中服务端解析过的引用；
  ``source_refs`` 是话题分段继承的抽取期引用（无稳定身份），不参与对账。
- 可重放性与写入时可验证、canonical 生命周期、当前可召回性互相独立：本模块只
  回答「现在还能不能在消息存储中按写入时的来源窗口与身份逐项验证这些引用」。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from astrbot.api import logger

from ....shared.contracts.conversation import message_evidence_fingerprint
from ..domain.memory_atom import is_resolved_source_reference

STATUS_REPLAYABLE = "replayable"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN = "unknown"

_REASON_REPLAYABLE = "source_replayable"
_REASON_PARTIAL = "source_partial"
_REASON_UNAVAILABLE = "source_unavailable"
_REASON_NOT_RECORDED = "source_not_recorded"
_REASON_UNKNOWN = "source_unknown"
_REASON_STORE_UNAVAILABLE = "source_store_unavailable"
_REASON_STORE_ERROR = "source_store_error"
_REASON_SESSION_UNAVAILABLE = "source_session_unavailable"
_REASON_EPOCH_UNRECORDED = "source_epoch_unrecorded"
_REASON_EPOCH_UNAVAILABLE = "source_epoch_unavailable"
_REASON_EPOCH_MISMATCH = "source_epoch_mismatch"
_REASON_WINDOW_INVALID = "source_window_invalid"

_EVIDENCE_KEYS = ("source_evidence", "fact_source_evidence")


@dataclass(frozen=True, slots=True)
class SourceReplayability:
    """一次来源对账的聚合结果；只暴露计数与固定原因码。"""

    status: str
    total: int
    verified: int
    absent: int
    unverifiable: int
    reason_codes: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        """转换为管理员详情可嵌入的脱敏投影。"""

        return {
            "status": self.status,
            "references": {
                "total": self.total,
                "verified": self.verified,
                "absent": self.absent,
                "unverifiable": self.unverifiable,
            },
            "reason_codes": list(self.reason_codes),
        }


class SourceReplayabilityAssessor:
    """按当前消息存储核对 canonical 来源引用的只读 owner。"""

    def __init__(self, message_store: Any | None) -> None:
        self._message_store = message_store

    async def assess(self, metadata: Mapping[str, Any] | None) -> SourceReplayability:
        """对账 metadata 中的来源引用；任何无法判定的情况都返回 unknown。"""

        normalized: Mapping[str, Any] = (
            metadata if isinstance(metadata, Mapping) else {}
        )
        references = _collect_references(normalized)
        total = len(references)
        if not total:
            return _result(STATUS_UNAVAILABLE, 0, 0, 0, 0, _REASON_NOT_RECORDED)

        session_id = normalized.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return _result(
                STATUS_UNKNOWN, total, 0, 0, total, _REASON_SESSION_UNAVAILABLE
            )

        read_rows = (
            getattr(self._message_store, "get_message_identity_rows", None)
            if self._message_store is not None
            else None
        )
        if not callable(read_rows):
            return _result(
                STATUS_UNKNOWN, total, 0, 0, total, _REASON_STORE_UNAVAILABLE
            )

        window = _recorded_window(normalized)
        if not isinstance(window, _Window):
            return _result(STATUS_UNKNOWN, total, 0, 0, total, _REASON_WINDOW_INVALID)

        resolved: list[Mapping[str, Any]] = []
        unverifiable = 0
        for item in references:
            if is_resolved_source_reference(item):
                resolved.append(item)
            else:
                unverifiable += 1
        if not resolved:
            return _result(STATUS_UNKNOWN, total, 0, 0, total, _REASON_UNKNOWN)

        rows: dict[int, Mapping[str, Any]] = {}
        try:
            loaded = await cast(Any, read_rows)(
                [int(item["message_id"]) for item in resolved]
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "来源可重放读取失败 operation=source_replayability error_class=%s",
                type(exc).__name__,
            )
            return _result(STATUS_UNKNOWN, total, 0, 0, total, _REASON_STORE_ERROR)
        if not isinstance(loaded, Mapping):
            return _result(STATUS_UNKNOWN, total, 0, 0, total, _REASON_STORE_ERROR)
        rows = {
            int(key): value
            for key, value in loaded.items()
            if isinstance(key, int) and isinstance(value, Mapping)
        }

        recorded_epoch = window.session_epoch
        if recorded_epoch is None:
            return _result(STATUS_UNKNOWN, total, 0, 0, total, _REASON_EPOCH_UNRECORDED)
        current_epoch = await self._current_epoch(session_id)
        if current_epoch is None:
            return _result(
                STATUS_UNKNOWN, total, 0, 0, total, _REASON_EPOCH_UNAVAILABLE
            )
        if current_epoch != recorded_epoch:
            # 来源窗口已轮换：不得把当前 epoch 的消息当作写入时来源。
            return _result(
                STATUS_UNAVAILABLE, total, 0, total, 0, _REASON_EPOCH_MISMATCH
            )

        matched = 0
        absent = 0
        for item in resolved:
            row = rows.get(int(item["message_id"]))
            if (
                row is None
                or not window.contains(int(item["message_seq"]))
                or not _row_matches(session_id, item, row)
            ):
                absent += 1
            else:
                matched += 1

        if matched and matched == total:
            status = STATUS_REPLAYABLE
            reasons: tuple[str, ...] = (_REASON_REPLAYABLE,)
        elif matched:
            status = STATUS_PARTIAL
            reasons = (_REASON_PARTIAL,)
        elif unverifiable:
            status = STATUS_UNKNOWN
            reasons = (_REASON_UNKNOWN,)
        else:
            status = STATUS_UNAVAILABLE
            reasons = (_REASON_UNAVAILABLE,)
        return _result(status, total, matched, absent, unverifiable, *reasons)

    async def _current_epoch(self, session_id: str) -> int | None:
        """读取会话当前 epoch；读取缺失或异常时不猜测，返回 None。"""

        read_epoch = getattr(self._message_store, "get_summary_epoch", None)
        if not callable(read_epoch):
            return None
        try:
            value = read_epoch(session_id)
            if inspect.isawaitable(value):
                value = await value
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if not isinstance(value, (tuple, list)) or not value:
            return None
        current = value[0]
        if isinstance(current, bool) or not isinstance(current, int):
            return None
        return current


@dataclass(frozen=True, slots=True)
class _Window:
    """写入时来源窗口的 epoch 与 seq 边界（缺失字段保持 None）。"""

    session_epoch: int | None = None
    start_seq: int | None = None
    end_seq: int | None = None

    def contains(self, message_seq: int) -> bool:
        """按 ``(start_seq, end_seq]`` 语义核对窗口边界；无边界则不限制。"""

        if self.start_seq is not None and message_seq <= self.start_seq:
            return False
        if self.end_seq is not None and message_seq > self.end_seq:
            return False
        return True


class _InvalidWindow(Exception):
    """来源窗口字段存在但结构非法。"""


def _recorded_window(metadata: Mapping[str, Any]) -> _Window | None:
    """读取写入时来源窗口；结构非法或边界冲突时按 unknown 处理。"""

    raw_window = metadata.get("source_window")
    if raw_window is not None and not isinstance(raw_window, Mapping):
        return None
    session_epoch: int | None = None
    if metadata.get("source_epoch") is not None:
        session_epoch = _positive_int(metadata.get("source_epoch"))
        if session_epoch is None:
            return None
    try:
        top_start = _seq_bound(metadata, ("source_start_seq",))
        top_end = _seq_bound(metadata, ("source_end_seq",))
        nested_start: int | None = None
        nested_end: int | None = None
        if isinstance(raw_window, Mapping):
            if session_epoch is None and raw_window.get("session_epoch") is not None:
                session_epoch = _positive_int(raw_window.get("session_epoch"))
                if session_epoch is None:
                    return None
            nested_start = _seq_bound(raw_window, ("start_seq", "start_index"))
            nested_end = _seq_bound(raw_window, ("end_seq", "end_index"))
        if (top_start is None) != (top_end is None):
            return None
        if (nested_start is None) != (nested_end is None):
            return None
        if (
            top_start is not None
            and nested_start is not None
            and top_start != nested_start
        ) or (top_end is not None and nested_end is not None and top_end != nested_end):
            return None
        start_seq = nested_start if nested_start is not None else top_start
        end_seq = nested_end if nested_end is not None else top_end
        if start_seq is not None and end_seq is not None and end_seq <= start_seq:
            return None
    except _InvalidWindow:
        return None
    return _Window(session_epoch=session_epoch, start_seq=start_seq, end_seq=end_seq)


def _positive_int(value: Any) -> int | None:
    """只接受 >=1 的整数，布尔值不算整数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _seq_bound(source: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    """读取可选 seq 边界；多个别名冲突时拒绝猜测。"""

    values: list[int] = []
    for key in keys:
        if key not in source or source.get(key) is None:
            continue
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _InvalidWindow(key)
        values.append(value)
    if not values:
        return None
    if len(set(values)) != 1:
        raise _InvalidWindow("conflicting_seq_bounds")
    return values[0]


def _collect_references(metadata: Mapping[str, Any]) -> list[Any]:
    """收集已持久化的来源引用项；非序列值按不可核对项保留。"""

    items: list[Any] = []
    for key in _EVIDENCE_KEYS:
        if key not in metadata:
            continue
        value = metadata.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for entry in value:
                if (
                    key == "fact_source_evidence"
                    and isinstance(entry, Sequence)
                    and not isinstance(entry, (str, bytes))
                ):
                    items.extend(entry)
                else:
                    items.append(entry)
        elif value is not None:
            items.append(value)
    return items


def _row_matches(
    session_id: str, reference: Mapping[str, Any], row: Mapping[str, Any]
) -> bool:
    """逐项比对会话、序号、角色、区间与指纹；任一项不同都不算可重放。"""

    role = row.get("role")
    content = row.get("content")
    if not isinstance(role, str) or not isinstance(content, str):
        return False
    if row.get("session_id") != session_id:
        return False
    if row.get("message_seq") != reference["message_seq"]:
        return False
    if role != reference["role"]:
        return False
    if not (
        reference["start"] >= 0
        and reference["end"] > reference["start"]
        and reference["end"] <= len(content)
    ):
        return False
    return (
        message_evidence_fingerprint(role, content) == reference["message_fingerprint"]
    )


def _result(
    status: str,
    total: int,
    verified: int,
    absent: int,
    unverifiable: int,
    *reason_codes: str,
) -> SourceReplayability:
    return SourceReplayability(
        status=status,
        total=total,
        verified=verified,
        absent=absent,
        unverifiable=unverifiable,
        reason_codes=tuple(reason_codes),
    )


__all__ = [
    "SourceReplayability",
    "SourceReplayabilityAssessor",
    "STATUS_PARTIAL",
    "STATUS_REPLAYABLE",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
]
