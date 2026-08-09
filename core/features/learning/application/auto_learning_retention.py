"""自主学习 rolled-back tombstone 的安全保留与确定性裁剪。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..domain.auto_learning_records import parse_datetime

_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22,128}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class TombstoneRetentionResult:
    """记录一次安全裁剪删除的 tombstone 与幂等终态数量。"""

    tombstones_removed: int
    terminal_operations_removed: int


@dataclass(frozen=True, slots=True)
class _PrunableTombstone:
    """保存已证明安全的 tombstone 排序键与关联终态键。"""

    tombstone_id: str
    completed_at: datetime
    terminal_operation_key: str


class AutoLearningRetentionMixin:
    """为 AutoLearningManager 提供 fail-closed tombstone 裁剪。"""

    def _prune_tombstones_unlocked(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> TombstoneRetentionResult:
        """按 UTC TTL 与容量删除无引用、关系完整的 rolled-back tombstone。"""

        now = reference_time or datetime.now(UTC)
        if now.tzinfo is None:
            return TombstoneRetentionResult(0, 0)
        now_utc = now.astimezone(UTC)
        protected = self._protected_tombstone_references_unlocked()
        if protected is None:
            return TombstoneRetentionResult(0, 0)

        safe_records: list[_PrunableTombstone] = []
        for tombstone_id, record in self._tombstones.items():
            safe = self._safe_prunable_tombstone_unlocked(
                tombstone_id,
                record,
                protected=protected,
            )
            if safe is not None:
                safe_records.append(safe)
        safe_records.sort(key=lambda item: (item.completed_at, item.tombstone_id))

        ttl_days = max(1, int(getattr(self, "_tombstone_ttl_days", 30)))
        max_entries = max(1, int(getattr(self, "_tombstone_max_entries", 10_000)))
        cutoff = now_utc - timedelta(days=ttl_days)
        expired = [item for item in safe_records if item.completed_at < cutoff]
        expired_ids = {item.tombstone_id for item in expired}
        remaining_safe = [
            item for item in safe_records if item.tombstone_id not in expired_ids
        ]
        remaining_count = len(self._tombstones) - len(expired)
        overflow = max(0, remaining_count - max_entries)
        selected = [*expired, *remaining_safe[:overflow]]

        terminal_removed = 0
        for item in selected:
            self._tombstones.pop(item.tombstone_id, None)
            if (
                self._terminal_operations.pop(item.terminal_operation_key, None)
                is not None
            ):
                terminal_removed += 1
        return TombstoneRetentionResult(len(selected), terminal_removed)

    def _protected_tombstone_references_unlocked(
        self,
    ) -> dict[str, set[str]] | None:
        """收集 active/parent、intent、claim 与 recovery 引用；链异常时拒绝裁剪。"""

        protected = {
            "operation_id": set(),
            "candidate_id": set(),
            "publication_revision": set(),
        }
        for collection in (
            self._publish_intents,
            self._operation_claims,
            self._recovery_records,
        ):
            for record in collection.values():
                if not isinstance(record, Mapping):
                    return None
                self._collect_protected_record(record, protected)

        current = self._active_publication_revision
        seen: set[str] = set()
        while current is not None:
            if not _opaque_id(current) or current in seen:
                return None
            seen.add(current)
            publication = self._publications.get(current)
            if (
                not isinstance(publication, Mapping)
                or publication.get("publication_revision") != current
            ):
                return None
            protected["publication_revision"].add(current)
            candidate_id = publication.get("candidate_id")
            if not _opaque_id(candidate_id):
                return None
            protected["candidate_id"].add(str(candidate_id))
            parent = publication.get("parent_publication_revision")
            if parent is not None and not _opaque_id(parent):
                return None
            current = str(parent) if parent is not None else None
        return protected

    @staticmethod
    def _collect_protected_record(
        record: Mapping[str, object],
        protected: dict[str, set[str]],
    ) -> None:
        """把恢复相关记录中的三个稳定引用加入保护集合。"""

        for field in ("operation_id", "candidate_id", "publication_revision"):
            value = record.get(field)
            if _opaque_id(value):
                protected[field].add(str(value))

    def _safe_prunable_tombstone_unlocked(
        self,
        tombstone_id: str,
        record: object,
        *,
        protected: dict[str, set[str]],
    ) -> _PrunableTombstone | None:
        """验证单条 tombstone 的状态、UTC 时间、关系与引用均可安全删除。"""

        if not isinstance(record, Mapping) or record.get("status") != "rolled_back":
            return None
        fields = {
            "tombstone_id": record.get("tombstone_id"),
            "operation_id": record.get("operation_id"),
            "candidate_id": record.get("candidate_id"),
            "publication_revision": record.get("publication_revision"),
        }
        if fields["tombstone_id"] != tombstone_id or not all(
            _opaque_id(value) for value in fields.values()
        ):
            return None
        completed_at = parse_datetime(record.get("completed_at"))
        if completed_at is None or completed_at.utcoffset() != timedelta(0):
            return None
        completed_utc = completed_at.astimezone(UTC)
        for field in ("operation_id", "candidate_id", "publication_revision"):
            if str(fields[field]) in protected[field]:
                return None

        publication_revision = str(fields["publication_revision"])
        candidate_id = str(fields["candidate_id"])
        publication = self._publications.get(publication_revision)
        candidate = self._candidates.get(candidate_id)
        if (
            not isinstance(publication, Mapping)
            or publication.get("publication_revision") != publication_revision
            or publication.get("candidate_id") != candidate_id
            or publication.get("status") != "rolled_back"
            or not isinstance(candidate, Mapping)
            or candidate.get("candidate_id") != candidate_id
            or candidate.get("status") != "rolled_back"
        ):
            return None

        operation_id = str(fields["operation_id"])
        terminal_matches = [
            key
            for key, terminal in self._terminal_operations.items()
            if isinstance(terminal, Mapping)
            and terminal.get("operation_id") == operation_id
            and terminal.get("restored") is True
        ]
        if len(terminal_matches) != 1:
            return None
        return _PrunableTombstone(
            tombstone_id=tombstone_id,
            completed_at=completed_utc,
            terminal_operation_key=terminal_matches[0],
        )


def _opaque_id(value: object) -> bool:
    """判断内部引用是否符合既定 URL-safe opaque ID 形状。"""

    return isinstance(value, str) and _OPAQUE_ID_PATTERN.fullmatch(value) is not None


__all__ = ["AutoLearningRetentionMixin", "TombstoneRetentionResult"]
