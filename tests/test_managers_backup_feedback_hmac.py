"""反馈 HMAC 数据库与 sidecar 的备份恢复契约测试。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from core.features.backup.application.manager import _BACKUP_INFO_FILE, BackupManager
from core.features.backup.infrastructure import integrity as backup_integrity

_WINDOWS_TEXT_SENSITIVE_KEY = b"A" * 8 + b"\r\n" + b"B" * 22


class _WindowsBinaryReadOsProxy:
    """模拟 Windows 文本/二进制文件描述符读取差异。"""

    name = "nt"
    O_BINARY = 0x8000

    def __init__(self) -> None:
        """记录以二进制模式打开的文件描述符。"""

        self._binary_descriptors: set[int] = set()

    def __getattr__(self, name: str):
        """转发通用 os API，并隐藏 Windows 不提供的 POSIX 能力。"""

        if name == "O_NOFOLLOW":
            raise AttributeError(name)
        return getattr(os, name)

    def open(self, path, flags: int) -> int:
        """打开文件并记录调用方是否要求 O_BINARY。"""

        binary = bool(flags & self.O_BINARY)
        native_binary_flag = getattr(os, "O_BINARY", 0)
        file_descriptor = os.open(
            path,
            flags if native_binary_flag else flags & ~self.O_BINARY,
        )
        if binary:
            self._binary_descriptors.add(file_descriptor)
        return file_descriptor

    def read(self, file_descriptor: int, size: int) -> bytes:
        """二进制模式原样读取，否则模拟 Windows CRLF 文本转换。"""

        value = os.read(file_descriptor, size)
        if file_descriptor in self._binary_descriptors:
            return value
        return value.replace(b"\r\n", b"\n")

    def close(self, file_descriptor: int) -> None:
        """关闭文件描述符并清理二进制模式记录。"""

        self._binary_descriptors.discard(file_descriptor)
        os.close(file_descriptor)


def _write_marker_database(path: Path, label: str) -> None:
    """写入用于区分 source 与 live 的最小 SQLite 数据库。"""

    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker(value) VALUES (?)", (label,))
        connection.commit()
    finally:
        connection.close()


def _write_feedback_hmac_pair(
    data_dir: Path,
    *,
    key: bytes = b"a" * 32,
) -> tuple[Path, Path]:
    """按真实 schema 写入反馈数据库及其 0600 HMAC key。"""

    db_path = data_dir / "feedback_signals.db"
    key_path = data_dir / "feedback_signals.db.hmac.key"
    db_path.unlink(missing_ok=True)
    key_path.unlink(missing_ok=True)
    fingerprint = hashlib.sha256(key).digest()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE feedback_store_metadata (
                metadata_key TEXT PRIMARY KEY,
                metadata_value BLOB NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO feedback_store_metadata(metadata_key, metadata_value)
            VALUES (?, ?)
            """,
            ("feedback_hmac_key_fingerprint_v1", fingerprint),
        )
        connection.execute("CREATE TABLE feedback_events (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return db_path, key_path


def _write_legacy_feedback_database(data_dir: Path) -> Path:
    """写入 HMAC 方案引入前的旧版反馈单库（无 metadata 表、无 key）。"""

    db_path = data_dir / "feedback_signals.db"
    key_path = data_dir / "feedback_signals.db.hmac.key"
    db_path.unlink(missing_ok=True)
    key_path.unlink(missing_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE feedback_events (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE feedback_aggregates (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    return db_path


def test_feedback_hmac_validation_uses_binary_mode_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 下包含 CRLF 的合法密钥不得因文本转换被误判损坏。"""

    _write_feedback_hmac_pair(tmp_path, key=_WINDOWS_TEXT_SENSITIVE_KEY)
    monkeypatch.setattr(backup_integrity, "os", _WindowsBinaryReadOsProxy())

    backup_integrity.validate_feedback_hmac_pair(
        tmp_path,
        error_scope="backup",
        require_pair=True,
    )


def test_feedback_validation_releases_database_handle(tmp_path: Path) -> None:
    """完整性校验返回后应立即释放数据库，允许 Windows 原子替换。"""

    db_path, _ = _write_feedback_hmac_pair(tmp_path)
    backup_integrity.validate_feedback_hmac_pair(
        tmp_path,
        error_scope="backup",
        require_pair=True,
    )

    replacement = tmp_path / "feedback-replacement.db"
    db_path.replace(replacement)
    assert replacement.is_file()


def test_legacy_feedback_inspection_releases_database_handle(tmp_path: Path) -> None:
    """旧库判定返回后应立即释放数据库，允许 Windows 删除。"""

    db_path = _write_legacy_feedback_database(tmp_path)
    backup_integrity.prepare_feedback_backup(tmp_path)

    db_path.unlink()
    assert not db_path.exists()


class TestFeedbackHmacBackupRestore:
    """验证反馈数据库与 HMAC key 始终作为单个恢复单元。"""

    @pytest.mark.asyncio
    async def test_backup_includes_feedback_database_and_hmac_key_as_pair(
        self, tmp_path: Path
    ) -> None:
        """反馈数据库和 0600 HMAC key 必须进入同一个 operational snapshot。"""

        _write_marker_database(tmp_path / "memora.db", "feedback-source")
        _, key_path = _write_feedback_hmac_pair(tmp_path)
        manager = BackupManager(str(tmp_path))

        backup = await manager.create_backup()
        backup_dir = Path(str(backup["directory"]))
        manifest = json.loads(
            (backup_dir / _BACKUP_INFO_FILE).read_text(encoding="utf-8")
        )

        assert {
            "feedback_signals.db",
            "feedback_signals.db.hmac.key",
        }.issubset(manifest["files"])
        key_metadata = manifest["files"][key_path.name]
        assert key_metadata["role"] == "operational"
        assert key_metadata["kind"] == "secret"
        if os.name != "nt":
            assert key_metadata["mode"] == 0o600
        snapshot_key = backup_dir / key_path.name
        assert snapshot_key.read_bytes() == key_path.read_bytes()
        if os.name != "nt":
            assert stat.S_IMODE(snapshot_key.stat().st_mode) == 0o600

    @pytest.mark.asyncio
    async def test_restore_feedback_pair_preserves_key_mode_and_fingerprint(
        self, tmp_path: Path
    ) -> None:
        """成对恢复成功后 key 权限和数据库 fingerprint 必须仍然匹配。"""

        _write_marker_database(tmp_path / "memora.db", "source")
        source_db, source_key = _write_feedback_hmac_pair(tmp_path, key=b"s" * 32)
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()

        _write_marker_database(tmp_path / "memora.db", "live")
        _write_feedback_hmac_pair(tmp_path, key=b"l" * 32)
        staged = manager.stage_restore(str(backup["name"]))
        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "validating"
        assert (tmp_path / source_key.name).read_bytes() == b"s" * 32
        if os.name != "nt":
            assert stat.S_IMODE((tmp_path / source_key.name).stat().st_mode) == 0o600
        with sqlite3.connect(tmp_path / source_db.name) as connection:
            assert connection.execute(
                "SELECT metadata_value FROM feedback_store_metadata WHERE metadata_key = ?",
                ("feedback_hmac_key_fingerprint_v1",),
            ).fetchone() == (hashlib.sha256(b"s" * 32).digest(),)
        manager.mark_restore_succeeded(str(staged["operation_id"]))

    @pytest.mark.asyncio
    async def test_backup_rejects_feedback_database_without_hmac_key(
        self, tmp_path: Path
    ) -> None:
        """缺少 sidecar 时不能发布只含 feedback DB 的备份。"""

        _write_marker_database(tmp_path / "memora.db", "feedback-missing-key")
        _write_feedback_hmac_pair(tmp_path)[1].unlink()
        manager = BackupManager(str(tmp_path))

        with pytest.raises(RuntimeError, match="backup_feedback_hmac_pair_missing"):
            await manager.create_backup()

    @pytest.mark.skipif(os.name == "nt", reason="Windows 不提供 POSIX 文件权限位")
    @pytest.mark.asyncio
    async def test_backup_rejects_feedback_hmac_key_with_wrong_mode(
        self, tmp_path: Path
    ) -> None:
        """HMAC key 权限不是 0600 时必须 fail closed。"""

        _write_marker_database(tmp_path / "memora.db", "feedback-bad-mode")
        key_path = _write_feedback_hmac_pair(tmp_path)[1]
        key_path.chmod(0o644)
        manager = BackupManager(str(tmp_path))

        with pytest.raises(RuntimeError, match="backup_feedback_hmac_key_invalid"):
            await manager.create_backup()

    @pytest.mark.asyncio
    async def test_staged_feedback_fingerprint_mismatch_rolls_back_pair(
        self, tmp_path: Path
    ) -> None:
        """fingerprint 不匹配时 canonical、反馈 DB 和 key 必须一起回滚。"""

        _write_marker_database(tmp_path / "memora.db", "source")
        _, source_key = _write_feedback_hmac_pair(tmp_path, key=b"s" * 32)
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()

        _write_marker_database(tmp_path / "memora.db", "live")
        live_db, live_key = _write_feedback_hmac_pair(tmp_path, key=b"l" * 32)
        live_bytes = {
            path.name: path.read_bytes()
            for path in (tmp_path / "memora.db", live_db, live_key)
        }
        staged = manager.stage_restore(str(backup["name"]))
        payload_key = (
            tmp_path
            / ".restore"
            / str(staged["operation_id"])
            / "payload"
            / source_key.name
        )
        payload_key.write_bytes(b"x" * 32)
        payload_key.chmod(0o600)
        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "rolled_back"
        assert {
            path.name: path.read_bytes()
            for path in (tmp_path / "memora.db", live_db, live_key)
        } == live_bytes
        restore_status = manager.get_restore_status(str(staged["operation_id"]))
        assert restore_status is not None
        assert (
            restore_status["reason_code"]
            == "restore_feedback_hmac_fingerprint_mismatch"
        )

    @pytest.mark.asyncio
    async def test_staged_feedback_missing_key_rolls_back_pair(
        self, tmp_path: Path
    ) -> None:
        """暂存 payload 缺少 HMAC sidecar 时不得留下半恢复状态。"""

        _write_marker_database(tmp_path / "memora.db", "source")
        _, source_key = _write_feedback_hmac_pair(tmp_path, key=b"s" * 32)
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()

        _write_marker_database(tmp_path / "memora.db", "live")
        live_db, live_key = _write_feedback_hmac_pair(tmp_path, key=b"l" * 32)
        live_bytes = {
            path.name: path.read_bytes()
            for path in (tmp_path / "memora.db", live_db, live_key)
        }
        staged = manager.stage_restore(str(backup["name"]))
        payload_key = (
            tmp_path
            / ".restore"
            / str(staged["operation_id"])
            / "payload"
            / source_key.name
        )
        payload_key.unlink()
        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "rolled_back"
        assert {
            path.name: path.read_bytes()
            for path in (tmp_path / "memora.db", live_db, live_key)
        } == live_bytes
        restore_status = manager.get_restore_status(str(staged["operation_id"]))
        assert restore_status is not None
        assert restore_status["reason_code"] == "restore_apply_failed"

    @pytest.mark.asyncio
    async def test_feedback_key_install_failure_rolls_back_database_and_key(
        self, tmp_path: Path
    ) -> None:
        """旧 key 已移走后安装失败时必须恢复原 DB 和原 key。"""

        _write_marker_database(tmp_path / "memora.db", "source")
        _, source_key = _write_feedback_hmac_pair(tmp_path, key=b"s" * 32)
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()

        _write_marker_database(tmp_path / "memora.db", "live")
        live_db, live_key = _write_feedback_hmac_pair(tmp_path, key=b"l" * 32)
        live_bytes = {
            path.name: path.read_bytes()
            for path in (tmp_path / "memora.db", live_db, live_key)
        }
        staged = manager.stage_restore(str(backup["name"]))
        original_replace = os.replace

        def fail_on_key_install(
            source: str | os.PathLike[str],
            target: str | os.PathLike[str],
        ) -> None:
            """在旧 key 已进入 previous 后拒绝安装 payload key。"""

            source_path = Path(source)
            target_path = Path(target)
            if (
                source_path.name == source_key.name
                and source_path.parent.name == "payload"
                and target_path == live_key
            ):
                raise OSError("simulated feedback key install failure")
            original_replace(source, target)

        with patch(
            "core.features.backup.application.restore_transaction.os.replace",
            side_effect=fail_on_key_install,
        ):
            applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "rolled_back"
        assert live_key.is_file()
        assert {
            path.name: path.read_bytes()
            for path in (tmp_path / "memora.db", live_db, live_key)
        } == live_bytes
        assert not (
            tmp_path
            / ".restore"
            / str(staged["operation_id"])
            / "previous"
            / source_key.name
        ).exists()

    @pytest.mark.asyncio
    async def test_backup_accepts_legacy_feedback_database_without_key(
        self, tmp_path: Path
    ) -> None:
        """HMAC 方案前的旧版单库应允许单独备份，不因缺少 key 阻断升级。"""

        _write_marker_database(tmp_path / "memora.db", "legacy-feedback")
        legacy_db = _write_legacy_feedback_database(tmp_path)
        manager = BackupManager(str(tmp_path))

        backup = await manager.create_backup()
        backup_dir = Path(str(backup["directory"]))
        manifest = json.loads(
            (backup_dir / _BACKUP_INFO_FILE).read_text(encoding="utf-8")
        )

        assert "feedback_signals.db" in manifest["files"]
        assert "feedback_signals.db.hmac.key" not in manifest["files"]
        snapshot_db = backup_dir / legacy_db.name
        assert snapshot_db.is_file()
        with sqlite3.connect(snapshot_db) as connection:
            assert (
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'feedback_events'
                    """
                ).fetchone()
                is not None
            )

    @pytest.mark.asyncio
    async def test_restore_legacy_feedback_database_backup_round_trip(
        self, tmp_path: Path
    ) -> None:
        """旧版单库备份可恢复；恢复后由反馈 Store 初始化补建 key。"""

        _write_marker_database(tmp_path / "memora.db", "source")
        _write_legacy_feedback_database(tmp_path)
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()

        # live 侧清空反馈文件，模拟需要恢复旧版单库的目标状态
        (tmp_path / "feedback_signals.db").unlink(missing_ok=True)
        staged = manager.stage_restore(str(backup["name"]))
        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "validating"
        assert (tmp_path / "feedback_signals.db").is_file()
        assert not (tmp_path / "feedback_signals.db.hmac.key").exists()
        manager.mark_restore_succeeded(str(staged["operation_id"]))

    @pytest.mark.asyncio
    async def test_snapshot_rejects_legacy_feedback_db_lost_between_checks(
        self, tmp_path: Path
    ) -> None:
        """初始为旧版单库时，快照最终缺失必须被拒绝（防复制窗口遗漏）。"""

        _write_marker_database(tmp_path / "memora.db", "legacy-vanish")
        legacy_db = _write_legacy_feedback_database(tmp_path)
        expected_state = backup_integrity.prepare_feedback_backup(tmp_path)
        legacy_db.unlink()

        with pytest.raises(RuntimeError, match="backup_feedback_hmac_pair_missing"):
            backup_integrity.validate_feedback_snapshot(
                tmp_path,
                expected_state=expected_state,
            )
