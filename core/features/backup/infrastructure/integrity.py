"""备份恢复事务使用的跨数据库、sidecar 完整性与回滚编排。"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
from contextlib import closing
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

from ..domain import (
    BackupOperationError,
    FileRole,
    RestorePlan,
    RestoreStatus,
)

FEEDBACK_SIGNAL_DB_NAME = "feedback_signals.db"
FEEDBACK_HMAC_KEY_NAME = f"{FEEDBACK_SIGNAL_DB_NAME}.hmac.key"

_FEEDBACK_HMAC_KEY_BYTES = 32
_FEEDBACK_HMAC_KEY_MODE = 0o600
_FEEDBACK_FINGERPRINT_METADATA_NAME = "feedback_hmac_key_fingerprint_v1"

OPERATIONAL_FILE_SPECS: dict[str, tuple[FileRole, str, bool]] = {
    "memory_quarantine.sqlite3": (FileRole.OPERATIONAL, "sqlite", False),
    FEEDBACK_SIGNAL_DB_NAME: (FileRole.OPERATIONAL, "sqlite", False),
    FEEDBACK_HMAC_KEY_NAME: (FileRole.OPERATIONAL, "secret", False),
}
OPERATIONAL_BACKUP_PATTERNS = tuple(OPERATIONAL_FILE_SPECS)


class _FeedbackPairState(Enum):
    """反馈文件对的可识别状态。"""

    ABSENT = "absent"
    LEGACY_DB = "legacy_db"
    COMPLETE = "complete"


_FEEDBACK_STATE_RANK = {
    _FeedbackPairState.ABSENT: 0,
    _FeedbackPairState.LEGACY_DB: 1,
    _FeedbackPairState.COMPLETE: 2,
}


def prepare_feedback_backup(data_dir: Path) -> _FeedbackPairState:
    """校验 live 反馈文件对，并返回快照发布校验所需的初始状态。"""

    return _inspect_feedback_pair(data_dir, error_scope="backup")


def finalize_snapshot_file(
    target: Path,
    kind: str,
    metadata: dict[str, object],
) -> None:
    """对 secret 快照收紧权限，并把实际权限写入 manifest metadata。"""

    if kind != "secret":
        return
    target.chmod(_FEEDBACK_HMAC_KEY_MODE)
    metadata["mode"] = stat.S_IMODE(target.stat().st_mode)


def validate_feedback_snapshot(
    root: Path,
    *,
    expected_state: _FeedbackPairState,
) -> None:
    """发布快照前再次校验 DB/key，拒绝复制期间退化的状态。

    初始存在的数据（旧版单库或完整对）必须在快照中保留等价或更强的
    完整性；只有初始为 ABSENT 时才允许最终缺失。
    """

    final_state = _inspect_feedback_pair(root, error_scope="backup")
    if _FEEDBACK_STATE_RANK[final_state] < _FEEDBACK_STATE_RANK[expected_state]:
        raise _feedback_error("backup", "pair_missing")


def validate_feedback_hmac_pair(
    data_dir: Path,
    *,
    error_scope: str = "restore",
    require_pair: bool = False,
) -> None:
    """在不暴露 key 的前提下校验反馈 SQLite/key 对。

    反馈 Store 在 ``feedback_store_metadata`` 中持久化 32-byte HMAC key 的
    原始 SHA-256 digest。未启用反馈时允许整对缺失，但部分缺失或无法验证的
    fingerprint 必须拒绝。
    """

    if error_scope not in {"backup", "restore"}:
        raise ValueError("invalid feedback HMAC error scope")
    db_path = data_dir / FEEDBACK_SIGNAL_DB_NAME
    key_path = data_dir / FEEDBACK_HMAC_KEY_NAME
    db_present = _path_present(db_path)
    key_present = _path_present(key_path)
    if not db_present and not key_present:
        if require_pair:
            raise _feedback_error(error_scope, "pair_missing")
        return
    if db_present != key_present:
        raise _feedback_error(error_scope, "pair_missing")
    if db_path.is_symlink() or not db_path.is_file():
        raise _feedback_error(error_scope, "database_invalid")

    key = _read_feedback_hmac_key(key_path, error_scope)
    try:
        with closing(sqlite3.connect(str(db_path))) as connection:
            row = connection.execute(
                """
                SELECT metadata_value FROM feedback_store_metadata
                WHERE metadata_key = ?
                """,
                (_FEEDBACK_FINGERPRINT_METADATA_NAME,),
            ).fetchone()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise _feedback_error(error_scope, "database_invalid") from exc
    if row is None:
        raise _feedback_error(error_scope, "fingerprint_missing")

    stored_fingerprint = row[0]
    expected_fingerprint = hashlib.sha256(key).digest()
    if (
        not isinstance(stored_fingerprint, bytes)
        or len(stored_fingerprint) != hashlib.sha256().digest_size
        or not secrets.compare_digest(stored_fingerprint, expected_fingerprint)
    ):
        raise _feedback_error(error_scope, "fingerprint_mismatch")


def _feedback_db_is_legacy(db_path: Path) -> bool:
    """判断反馈库是否为 HMAC 方案引入前的旧版单库。

    以 ``feedback_store_metadata`` 表是否存在为判别依据：该表与 HMAC
    key/fingerprint 同时引入，旧版数据库没有该表，初始化时会由
    FeedbackSignalStore 补建 key。无法打开或非普通文件按非旧版处理，
    以便后续校验 fail closed。
    """

    if db_path.is_symlink() or not db_path.is_file():
        return False
    try:
        with closing(sqlite3.connect(str(db_path))) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'feedback_store_metadata'
                """
            ).fetchone()
    except (OSError, sqlite3.DatabaseError):
        return False
    return row is None


def _inspect_feedback_pair(
    data_dir: Path,
    *,
    error_scope: str,
) -> _FeedbackPairState:
    """分类反馈文件对状态；部分缺失或校验失败一律 fail closed。

    旧版单库（无 metadata 表）不参与 HMAC 契约，允许以单库形态备份；
    其余部分对状态（孤立 key、HMAC 库缺 key）视为完整性破坏。
    """

    if error_scope not in {"backup", "restore"}:
        raise ValueError("invalid feedback HMAC error scope")
    db_path = data_dir / FEEDBACK_SIGNAL_DB_NAME
    key_path = data_dir / FEEDBACK_HMAC_KEY_NAME
    db_present = _path_present(db_path)
    key_present = _path_present(key_path)
    if not db_present:
        if not key_present:
            return _FeedbackPairState.ABSENT
        raise _feedback_error(error_scope, "pair_missing")
    if not key_present:
        if _feedback_db_is_legacy(db_path):
            return _FeedbackPairState.LEGACY_DB
        raise _feedback_error(error_scope, "pair_missing")
    validate_feedback_hmac_pair(data_dir, error_scope=error_scope, require_pair=True)
    return _FeedbackPairState.COMPLETE


def _feedback_error(scope: str, suffix: str) -> BackupOperationError:
    """构造稳定且不含敏感信息的备份或恢复错误码。"""

    return BackupOperationError(f"{scope}_feedback_hmac_{suffix}")


def _path_present(path: Path) -> bool:
    """把悬空符号链接视为存在，以便 fail closed。"""

    return path.is_symlink() or path.exists()


def _read_feedback_hmac_key(path: Path, error_scope: str) -> bytes:
    """以二进制模式读取 sidecar key，并校验类型、权限与长度。"""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise _feedback_error(error_scope, "key_invalid") from exc
    try:
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode) or (
            os.name != "nt"
            and stat.S_IMODE(metadata.st_mode) != _FEEDBACK_HMAC_KEY_MODE
        ):
            raise _feedback_error(error_scope, "key_invalid")
        key = os.read(file_descriptor, _FEEDBACK_HMAC_KEY_BYTES + 1)
    finally:
        os.close(file_descriptor)
    if len(key) != _FEEDBACK_HMAC_KEY_BYTES:
        raise _feedback_error(error_scope, "key_invalid")
    return key


def validate_feedback_backup_files(root: Path, names: set[str]) -> None:
    """拒绝缺少任一成员或内部不一致的反馈备份文件对。

    允许旧版单库（无 metadata 表）以单文件形态进入恢复计划：恢复完成后
    由 FeedbackSignalStore 初始化补建 HMAC key。
    """

    pair = {FEEDBACK_SIGNAL_DB_NAME, FEEDBACK_HMAC_KEY_NAME}
    present = pair.intersection(names)
    if not present:
        return
    state = _inspect_feedback_pair(root, error_scope="backup")
    if state is _FeedbackPairState.LEGACY_DB and present == {FEEDBACK_SIGNAL_DB_NAME}:
        return
    if present != pair or state is not _FeedbackPairState.COMPLETE:
        raise BackupOperationError("backup_feedback_hmac_pair_missing")


def validate_feedback_backup_specs(
    root: Path,
    file_specs: Iterable[Mapping[str, object]],
) -> None:
    """从 manager 的恢复规格中提取文件名并校验反馈备份对。"""

    validate_feedback_backup_files(
        root,
        {str(item["name"]) for item in file_specs},
    )


def validate_feedback_restore_files(data_dir: Path, names: set[str]) -> None:
    """确保恢复计划不会只替换反馈数据库或 sidecar。

    旧版单库不参与 HMAC 契约：计划只恢复旧版单库、或 live 侧仅存在
    旧版单库时，允许按单文件处理；HMAC 对仍必须整体恢复。
    """

    pair = {FEEDBACK_SIGNAL_DB_NAME, FEEDBACK_HMAC_KEY_NAME}
    planned = pair.intersection(names)
    touches_feedback_contract = bool(planned) or "memora.db" in names
    if not touches_feedback_contract:
        return
    live_state = _inspect_feedback_pair(data_dir, error_scope="restore")
    if planned == pair:
        if live_state is not _FeedbackPairState.COMPLETE:
            raise BackupOperationError("restore_feedback_hmac_pair_missing")
        return
    if planned == {FEEDBACK_SIGNAL_DB_NAME}:
        if live_state is _FeedbackPairState.LEGACY_DB:
            return
        raise BackupOperationError("restore_feedback_hmac_pair_missing")
    if planned:
        raise BackupOperationError("restore_feedback_hmac_pair_missing")
    # 未计划任何反馈文件（仅恢复 canonical）：live 侧存在 HMAC 对时拒绝
    if live_state is _FeedbackPairState.COMPLETE:
        raise BackupOperationError("restore_feedback_hmac_pair_missing")


def rollback_restore_files(
    data_dir: Path,
    restore_root: Path,
    plan: RestorePlan,
    write_plan: Callable[[RestorePlan], None],
) -> None:
    """按逆序回滚 staged restore，包含未落盘 progress 的部分安装。"""

    plan_dir = restore_root / plan.operation_id
    plan.status = RestoreStatus.ROLLING_BACK
    write_plan(plan)
    try:
        for progress in reversed(plan.files):
            target = data_dir / progress.name
            previous = plan_dir / "previous" / progress.name
            if previous.exists():
                if target.exists():
                    target.unlink()
                os.replace(previous, target)
            elif progress.installed and target.exists():
                target.unlink()
        plan.status = RestoreStatus.ROLLED_BACK
        write_plan(plan)
    except OSError as exc:
        plan.status = RestoreStatus.ROLLBACK_PENDING
        plan.reason_code = "restore_rollback_pending"
        write_plan(plan)
        raise BackupOperationError("restore_rollback_pending") from exc


def validate_restored_files(
    data_dir: Path,
    plan: RestorePlan,
    quick_check: Callable[[Path], str],
) -> None:
    """校验恢复文件自身、canonical/quarantine 引用与反馈文件对。"""

    for progress in plan.files:
        path = data_dir / progress.name
        if not path.is_file() or path.is_symlink():
            raise BackupOperationError("restore_apply_failed")
        if path.suffix in {".db", ".sqlite3"}:
            quick_check(path)
    validate_quarantine_references(data_dir)
    validate_feedback_restore_files(
        data_dir,
        {progress.name for progress in plan.files},
    )


def validate_quarantine_references(data_dir: Path) -> None:
    """拒绝 approved 行指向恢复后不存在的 canonical ID。"""

    canonical_path = data_dir / "memora.db"
    quarantine_path = data_dir / "memory_quarantine.sqlite3"
    if not canonical_path.is_file() or not quarantine_path.is_file():
        return
    with closing(sqlite3.connect(str(canonical_path))) as canonical_db:
        canonical_tables = {
            str(row[0])
            for row in canonical_db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "documents" not in canonical_tables:
            return
        try:
            canonical_ids = {
                int(row[0]) for row in canonical_db.execute("SELECT id FROM documents")
            }
        except (TypeError, ValueError) as exc:
            raise BackupOperationError("restore_canonical_reference_invalid") from exc
    with closing(sqlite3.connect(str(quarantine_path))) as quarantine_db:
        quarantine_tables = {
            str(row[0])
            for row in quarantine_db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "memory_quarantine_candidates" not in quarantine_tables:
            return
        rows = quarantine_db.execute(
            """
            SELECT candidate_id, canonical_memory_id
            FROM memory_quarantine_candidates
            WHERE status = 'approved'
            """
        ).fetchall()
    missing: list[str] = []
    for candidate_id, canonical_memory_id in rows:
        if canonical_memory_id is None:
            missing.append(str(candidate_id))
            continue
        try:
            if (
                isinstance(canonical_memory_id, float)
                and not canonical_memory_id.is_integer()
            ):
                raise ValueError("non_integral_reference")
            canonical_id = int(canonical_memory_id)
        except (TypeError, ValueError) as exc:
            raise BackupOperationError("restore_quarantine_reference_invalid") from exc
        if canonical_id not in canonical_ids:
            missing.append(str(candidate_id))
    if missing:
        raise BackupOperationError("restore_quarantine_reference_missing")


__all__ = [
    "FEEDBACK_HMAC_KEY_NAME",
    "FEEDBACK_SIGNAL_DB_NAME",
    "OPERATIONAL_BACKUP_PATTERNS",
    "OPERATIONAL_FILE_SPECS",
    "finalize_snapshot_file",
    "prepare_feedback_backup",
    "rollback_restore_files",
    "validate_feedback_backup_files",
    "validate_feedback_backup_specs",
    "validate_feedback_hmac_pair",
    "validate_feedback_restore_files",
    "validate_feedback_snapshot",
    "validate_quarantine_references",
    "validate_restored_files",
]
