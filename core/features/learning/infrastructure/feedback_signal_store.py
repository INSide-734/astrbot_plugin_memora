"""反馈排序实验的隔离 SQLite 事件与聚合 Store。"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from ..domain.models import (
    FeedbackAdapterKind,
    FeedbackSignalAggregate,
    TrustedFeedbackEvent,
)

_UNSET = object()
_TOKEN_KEY_FINGERPRINT_METADATA_NAME = "feedback_hmac_key_fingerprint_v1"
_TOKEN_KEY_BYTES = 32
_TOKEN_KEY_FILE_SUFFIX = ".hmac.key"
_TOKEN_NAMESPACES = frozenset({"decision", "scope", "persona"})


class FeedbackSignalStore:
    """只接受评测任务显式路径的事务 Store，不连接生产数据库。"""

    def __init__(self, db_path: str | Path) -> None:
        """创建隔离连接；调用方负责在评测结束后关闭。"""

        self.db_path = str(db_path)
        self._token_key_path = Path(f"{self.db_path}{_TOKEN_KEY_FILE_SUFFIX}")
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._token_key: bytes | None = None
        self._initialized = False

    def initialize(self) -> None:
        """创建带 dedupe 唯一约束的最小事件和聚合表。"""

        try:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("BEGIN IMMEDIATE")
            metadata_table_existed = (
                self._connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'feedback_store_metadata'
                    """
                ).fetchone()
                is not None
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY,
                adapter_kind TEXT NOT NULL,
                decision_key TEXT NOT NULL,
                variant_key TEXT NOT NULL,
                outcome TEXT NOT NULL,
                scope_domain TEXT NOT NULL,
                persona_domain TEXT,
                observed_at TEXT NOT NULL,
                window_key TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feedback_domain_time
                ON feedback_events(scope_domain, persona_domain, observed_at)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_aggregates (
                scope_domain TEXT NOT NULL,
                persona_domain TEXT,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                accepted_count INTEGER NOT NULL,
                independent_window_count INTEGER NOT NULL,
                decayed_support REAL NOT NULL,
                proposed_document_weight REAL NOT NULL,
                proposed_graph_weight REAL NOT NULL,
                delta_from_baseline REAL NOT NULL,
                status TEXT NOT NULL,
                policy_version INTEGER NOT NULL,
                PRIMARY KEY(scope_domain, persona_domain, window_start, policy_version)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_store_metadata (
                    metadata_key TEXT PRIMARY KEY,
                    metadata_value BLOB NOT NULL
                )
                """
            )
            row = self._connection.execute(
                """
                SELECT metadata_value FROM feedback_store_metadata
                WHERE metadata_key = ?
                """,
                (_TOKEN_KEY_FINGERPRINT_METADATA_NAME,),
            ).fetchone()
            if row is None:
                if metadata_table_existed:
                    raise RuntimeError("feedback_token_key_missing")
                token_key = _load_or_create_token_key(
                    self._token_key_path,
                    allow_create=True,
                )
                key_fingerprint = hashlib.sha256(token_key).digest()
                self._connection.execute(
                    """
                    INSERT INTO feedback_store_metadata(metadata_key, metadata_value)
                    VALUES (?, ?)
                    """,
                    (_TOKEN_KEY_FINGERPRINT_METADATA_NAME, key_fingerprint),
                )
            else:
                raw_fingerprint = row["metadata_value"]
                if (
                    not isinstance(raw_fingerprint, bytes)
                    or len(raw_fingerprint) != hashlib.sha256().digest_size
                ):
                    raise RuntimeError("feedback_token_key_invalid")
                token_key = _load_or_create_token_key(
                    self._token_key_path,
                    allow_create=False,
                )
                if not hmac.compare_digest(
                    raw_fingerprint,
                    hashlib.sha256(token_key).digest(),
                ):
                    raise RuntimeError("feedback_token_key_invalid")
            self._connection.commit()
        except RuntimeError:
            self._connection.rollback()
            raise
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise RuntimeError("feedback_store_initialize_failed") from exc
        self._token_key = token_key
        self._initialized = True

    def opaque_token(self, namespace: str, value: str) -> str:
        """使用 Store 持久化安装密钥生成不可跨安装关联的稳定 token。"""

        self._ensure_initialized()
        if namespace not in _TOKEN_NAMESPACES:
            raise ValueError("feedback_token_namespace_invalid")
        if self._token_key is None:
            raise RuntimeError("feedback_token_key_unavailable")
        normalized = str(value).strip() or "unknown"
        namespace_bytes = namespace.encode("utf-8")
        value_bytes = normalized.encode("utf-8")
        payload = b"".join(
            (
                b"memora-feedback-v2",
                len(namespace_bytes).to_bytes(4, "big"),
                namespace_bytes,
                len(value_bytes).to_bytes(8, "big"),
                value_bytes,
            )
        )
        digest = hmac.new(self._token_key, payload, hashlib.sha256).hexdigest()
        return f"{namespace}:{digest}"

    def insert_events(self, events: Iterable[TrustedFeedbackEvent]) -> dict[str, int]:
        """事务写入事件并以稳定计数区分 accepted/duplicate。"""

        self._ensure_initialized()
        accepted = 0
        duplicates = 0
        try:
            with self._connection:
                for event in events:
                    cursor = self._connection.execute(
                        """
                        INSERT OR IGNORE INTO feedback_events
                        (adapter_kind, decision_key, variant_key, outcome,
                         scope_domain, persona_domain, observed_at, window_key,
                         dedupe_key, schema_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.adapter_kind.value,
                            event.decision_key,
                            event.variant_key,
                            event.outcome.value,
                            event.scope_domain,
                            event.persona_domain,
                            _serialize_time(event.observed_at),
                            event.window_key,
                            event.dedupe_key,
                            event.schema_version,
                        ),
                    )
                    if cursor.rowcount == 1:
                        accepted += 1
                    else:
                        duplicates += 1
        except sqlite3.Error as exc:
            raise RuntimeError("feedback_store_write_failed") from exc
        return {"accepted": accepted, "duplicate_event": duplicates}

    def list_events(
        self,
        *,
        scope_domain: str | None = None,
        persona_domain: str | None | object = _UNSET,
    ) -> list[TrustedFeedbackEvent]:
        """读取内部聚合所需事件；不提供报告级原始字段出口。"""

        self._ensure_initialized()
        persona_filter = "unset"
        if persona_domain is None:
            persona_filter = "null"
        elif persona_domain is not _UNSET:
            persona_filter = "value"
        rows = self._connection.execute(
            """
            SELECT * FROM feedback_events
            WHERE (:scope_domain IS NULL OR scope_domain = :scope_domain)
              AND (
                :persona_filter = 'unset'
                OR (:persona_filter = 'null' AND persona_domain IS NULL)
                OR (
                    :persona_filter = 'value'
                    AND persona_domain = :persona_domain
                )
              )
            ORDER BY observed_at, id
            """,
            {
                "scope_domain": scope_domain,
                "persona_filter": persona_filter,
                "persona_domain": (
                    persona_domain if persona_domain is not _UNSET else None
                ),
            },
        ).fetchall()
        from ..domain.models import FeedbackOutcome

        return [
            TrustedFeedbackEvent(
                adapter_kind=FeedbackAdapterKind(row["adapter_kind"]),
                decision_key=row["decision_key"],
                variant_key=row["variant_key"],
                outcome=FeedbackOutcome(row["outcome"]),
                scope_domain=row["scope_domain"],
                persona_domain=row["persona_domain"],
                observed_at=_parse_time(row["observed_at"]),
                window_key=row["window_key"],
                dedupe_key=row["dedupe_key"],
                schema_version=row["schema_version"],
            )
            for row in rows
        ]

    def delete_events_before(self, cutoff: datetime) -> int:
        """删除保留期之前的原始反馈事件并返回删除数量。"""

        self._ensure_initialized()
        try:
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM feedback_events WHERE observed_at < ?",
                    (_serialize_time(cutoff),),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("feedback_store_delete_failed") from exc
        return max(0, int(cursor.rowcount))

    def delete_decision_events(
        self,
        *,
        adapter_kind: FeedbackAdapterKind,
        decision_key: str,
        variant_key: str,
        scope_domain: str,
        persona_domain: str | None,
    ) -> int:
        """按受控适配器与匿名决策域撤销对应事件。"""

        self._ensure_initialized()
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    DELETE FROM feedback_events
                    WHERE adapter_kind = ?
                      AND decision_key = ?
                      AND variant_key = ?
                      AND scope_domain = ?
                      AND (
                        (? IS NULL AND persona_domain IS NULL)
                        OR persona_domain = ?
                      )
                    """,
                    (
                        adapter_kind.value,
                        decision_key,
                        variant_key,
                        scope_domain,
                        persona_domain,
                        persona_domain,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("feedback_store_delete_failed") from exc
        return max(0, int(cursor.rowcount))

    def revoke_and_replace_aggregates(
        self,
        *,
        adapter_kind: FeedbackAdapterKind,
        decision_key: str,
        variant_key: str,
        scope_domain: str,
        persona_domain: str | None,
        retention_cutoff: datetime,
        aggregate_builder: Callable[
            [list[TrustedFeedbackEvent]], Iterable[FeedbackSignalAggregate]
        ],
    ) -> int:
        """在一个写事务内撤销、清理、重建并替换 aggregate 快照。"""

        self._ensure_initialized()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                DELETE FROM feedback_events
                WHERE adapter_kind = ?
                  AND decision_key = ?
                  AND variant_key = ?
                  AND scope_domain = ?
                  AND (
                    (? IS NULL AND persona_domain IS NULL)
                    OR persona_domain = ?
                  )
                """,
                (
                    adapter_kind.value,
                    decision_key,
                    variant_key,
                    scope_domain,
                    persona_domain,
                    persona_domain,
                ),
            )
            deleted = max(0, int(cursor.rowcount))
            if not deleted:
                self._connection.rollback()
                return 0
            self._connection.execute(
                "DELETE FROM feedback_events WHERE observed_at < ?",
                (_serialize_time(retention_cutoff),),
            )
            aggregates = list(aggregate_builder(self.list_events()))
            self._replace_aggregate_rows(aggregates)
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise RuntimeError("feedback_store_revoke_failed") from exc
        except Exception:
            self._connection.rollback()
            raise
        return deleted

    def replace_aggregates(self, aggregates: Iterable[FeedbackSignalAggregate]) -> None:
        """原子替换当前 policy 下的聚合快照。"""

        self._ensure_initialized()
        rows = list(aggregates)
        try:
            with self._connection:
                self._replace_aggregate_rows(rows)
        except sqlite3.Error as exc:
            raise RuntimeError("feedback_store_aggregate_failed") from exc

    def _replace_aggregate_rows(
        self,
        rows: list[FeedbackSignalAggregate],
    ) -> None:
        """在调用方事务中替换 aggregate 行，不自行提交。"""

        if not rows:
            self._connection.execute("DELETE FROM feedback_aggregates")
            return
        policy_versions = {item.policy_version for item in rows}
        for policy_version in policy_versions:
            self._connection.execute(
                "DELETE FROM feedback_aggregates WHERE policy_version = ?",
                (policy_version,),
            )
        for aggregate in rows:
            self._connection.execute(
                """
                INSERT INTO feedback_aggregates
                (scope_domain, persona_domain, window_start, window_end,
                 accepted_count, independent_window_count, decayed_support,
                 proposed_document_weight, proposed_graph_weight,
                 delta_from_baseline, status, policy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregate.scope_domain,
                    aggregate.persona_domain,
                    _serialize_time(aggregate.window_start),
                    _serialize_time(aggregate.window_end),
                    aggregate.accepted_count,
                    aggregate.independent_window_count,
                    aggregate.decayed_support,
                    aggregate.proposed_document_weight,
                    aggregate.proposed_graph_weight,
                    aggregate.delta_from_baseline,
                    aggregate.status,
                    aggregate.policy_version,
                ),
            )

    def list_aggregates(
        self, *, policy_version: int | None = None
    ) -> list[sqlite3.Row]:
        """读取内部聚合行供 Manager 重放，不返回原始事件内容。"""

        self._ensure_initialized()
        if policy_version is None:
            return self._connection.execute(
                "SELECT * FROM feedback_aggregates ORDER BY window_start"
            ).fetchall()
        return self._connection.execute(
            "SELECT * FROM feedback_aggregates WHERE policy_version = ? ORDER BY window_start",
            (policy_version,),
        ).fetchall()

    def clear_aggregates(self) -> None:
        """删除派生聚合而保留事件，供完整重建验证。"""

        self._ensure_initialized()
        with self._connection:
            self._connection.execute("DELETE FROM feedback_aggregates")

    def safe_summary(self) -> dict[str, int]:
        """返回不含 key、domain、事件内容的安全计数。"""

        self._ensure_initialized()
        event_count = self._connection.execute(
            "SELECT COUNT(*) FROM feedback_events"
        ).fetchone()[0]
        aggregate_count = self._connection.execute(
            "SELECT COUNT(*) FROM feedback_aggregates"
        ).fetchone()[0]
        return {
            "event_count": int(event_count),
            "aggregate_count": int(aggregate_count),
        }

    def close(self) -> None:
        """关闭隔离 SQLite 连接。"""

        self._connection.close()
        self._token_key = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """拒绝在 schema 初始化前使用 Store。"""

        if not self._initialized:
            raise RuntimeError("feedback_store_not_initialized")


def _load_or_create_token_key(path: Path, *, allow_create: bool) -> bytes:
    """读取独立密钥文件；仅首次迁移允许原子创建。"""

    existing = _read_token_key(path)
    if existing is not None:
        return existing
    if not allow_create:
        raise RuntimeError("feedback_token_key_missing")

    candidate = os.urandom(_TOKEN_KEY_BYTES)
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
    except OSError as exc:
        raise RuntimeError("feedback_token_key_create_failed") from exc
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path, follow_symlinks=False)
        except FileExistsError:
            pass
        else:
            _fsync_directory(path.parent)
    except OSError as exc:
        raise RuntimeError("feedback_token_key_create_failed") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass

    installed = _read_token_key(path)
    if installed is None:
        raise RuntimeError("feedback_token_key_missing")
    return installed


def _read_token_key(path: Path) -> bytes | None:
    """不跟随链接读取 0600 普通文件，并严格校验密钥长度。"""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("feedback_token_key_invalid") from exc
    try:
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RuntimeError("feedback_token_key_invalid")
        value = os.read(file_descriptor, _TOKEN_KEY_BYTES + 1)
    finally:
        os.close(file_descriptor)
    if len(value) != _TOKEN_KEY_BYTES:
        raise RuntimeError("feedback_token_key_invalid")
    return value


def _fsync_directory(path: Path) -> None:
    """持久化同目录原子链接，确保重启后 key 与数据库 fingerprint 一致。"""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    file_descriptor = os.open(path, flags)
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _serialize_time(value: datetime) -> str:
    """规范化为 UTC ISO 时间。"""

    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat()


def _parse_time(value: str) -> datetime:
    """恢复 UTC ISO 时间。"""

    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc)


__all__ = ["FeedbackSignalStore"]
