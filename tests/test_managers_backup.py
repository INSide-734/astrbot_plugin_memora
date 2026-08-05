"""BackupManager 测试 — 版本检测、备份创建、恢复待处理。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.managers.backup_manager import (
    _BACKUP_INFO_FILE,
    _BACKUP_NAME_RE,
    _VERSION_FILE,
    PLUGIN_VERSION,
    BackupManager,
)


def _db_bytes(label: str) -> bytes:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    connection = sqlite3.connect(handle.name)
    connection.execute("CREATE TABLE marker(value TEXT)")
    connection.execute("INSERT INTO marker(value) VALUES (?)", (label,))
    connection.commit()
    connection.close()
    content = Path(handle.name).read_bytes()
    os.unlink(handle.name)
    return content


# ---------------------------------------------------------------------------
# Version tracking tests
# ---------------------------------------------------------------------------


class TestVersionTracking:
    """get_stored_version, write_current_version, needs_backup."""

    def test_get_stored_version_no_file(self, tmp_path: Path) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.get_stored_version() is None

    def test_get_stored_version_with_file(self, tmp_path: Path) -> None:
        version_file = tmp_path / _VERSION_FILE
        version_file.write_text("2.3.0", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.get_stored_version() == "2.3.0"

    def test_write_current_version(self, tmp_path: Path) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        mgr.write_current_version()
        assert (tmp_path / _VERSION_FILE).read_text(encoding="utf-8") == PLUGIN_VERSION

    def test_needs_backup_fresh_install(self, tmp_path: Path) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.needs_backup() is True

    def test_needs_backup_same_version(self, tmp_path: Path) -> None:
        (tmp_path / _VERSION_FILE).write_text(PLUGIN_VERSION, encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.needs_backup() is False

    def test_needs_backup_different_version(self, tmp_path: Path) -> None:
        (tmp_path / _VERSION_FILE).write_text("2.3.0", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.needs_backup() is True

    def test_get_stored_version_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / _VERSION_FILE).write_text("   \n  ", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        # read_text(encoding="utf-8").strip() is used, so whitespace collapses to ""
        assert mgr.get_stored_version() == ""


# ---------------------------------------------------------------------------
# Backup creation tests
# ---------------------------------------------------------------------------


class TestBackupCreation:
    """backup_if_needed — version-change driven backup."""

    def test_backup_if_needed_no_change(self, tmp_path: Path) -> None:
        (tmp_path / _VERSION_FILE).write_text(PLUGIN_VERSION, encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        result = mgr.backup_if_needed()
        assert result is None

    def test_backup_if_needed_fresh_install(self, tmp_path: Path) -> None:
        """Fresh install should create a backup and write the version file."""
        (tmp_path / "memora.db").write_bytes(_db_bytes("fresh"))
        mgr = BackupManager(data_dir=str(tmp_path))
        result = mgr.backup_if_needed()
        assert result is not None
        backup_path = Path(result["directory"])
        assert backup_path.exists()
        assert backup_path.name.startswith("vunknown_")
        # Verify backup_info.json was written
        info_path = backup_path / _BACKUP_INFO_FILE
        assert info_path.exists()
        info = json.loads(info_path.read_text(encoding="utf-8"))
        assert info["plugin_version"] == PLUGIN_VERSION
        assert info["previous_version"] == "unknown"
        assert info["file_count"] >= 1
        # Version file should be updated
        assert (tmp_path / _VERSION_FILE).read_text(encoding="utf-8") == PLUGIN_VERSION

    def test_backup_if_needed_version_change(self, tmp_path: Path) -> None:
        (tmp_path / _VERSION_FILE).write_text("2.3.0", encoding="utf-8")
        (tmp_path / "memora.db").write_bytes(_db_bytes("version"))
        mgr = BackupManager(data_dir=str(tmp_path))
        result = mgr.backup_if_needed()
        assert result is not None
        backup_path = Path(result["directory"])
        assert backup_path.name.startswith("v2.3.0_")
        info = json.loads((backup_path / _BACKUP_INFO_FILE).read_text(encoding="utf-8"))
        assert info["previous_version"] == "2.3.0"

    def test_backup_copies_matching_files(self, tmp_path: Path) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("memora"))
        (tmp_path / "conversations.db").write_bytes(_db_bytes("conv"))
        # Create a file NOT matching backup patterns
        (tmp_path / "some_log.txt").write_text("log", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        result = mgr.backup_if_needed()
        backup_path = Path(result["directory"])
        # Only matching files should be copied
        copied_files = [
            p.name
            for p in backup_path.iterdir()
            if p.is_file() and p.name != _BACKUP_INFO_FILE
        ]
        assert "memora.db" in copied_files
        assert "conversations.db" in copied_files
        assert "some_log.txt" not in copied_files


# ---------------------------------------------------------------------------
# Manual/Create backup tests
# ---------------------------------------------------------------------------


class TestCreateBackup:
    """create_backup — always creates, timestamp-based directory."""

    @pytest.mark.asyncio
    async def test_create_backup_always_creates(self, tmp_path: Path) -> None:
        (tmp_path / _VERSION_FILE).write_text(PLUGIN_VERSION, encoding="utf-8")
        (tmp_path / "memora.db").write_bytes(_db_bytes("manual"))
        mgr = BackupManager(data_dir=str(tmp_path))
        result = await mgr.create_backup()
        assert result is not None
        backup_path = Path(result["directory"])
        assert "manual_" in backup_path.name
        info = json.loads((backup_path / _BACKUP_INFO_FILE).read_text(encoding="utf-8"))
        assert info["backup_type"] == "manual"
        assert info["manifest_version"] == 2
        assert "data_dir" not in info

    @pytest.mark.asyncio
    async def test_create_backup_failure_does_not_publish_partial_directory(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("failure"))
        mgr = BackupManager(data_dir=str(tmp_path))
        with patch(
            "core.managers.backup_manager.snapshot_sqlite",
            side_effect=OSError("disk"),
        ):
            with pytest.raises(RuntimeError, match="backup_create_failed"):
                await mgr.create_backup()
        assert mgr.list_backups(str(tmp_path)) == []


class TestBackupRetention:
    """验证自动保留策略不删除手动和版本保护备份。"""

    @staticmethod
    def _write_backup(
        tmp_path: Path, name: str, backup_type: str, age_days: int
    ) -> None:
        directory = tmp_path / "backups" / name
        directory.mkdir(parents=True)
        (directory / _BACKUP_INFO_FILE).write_text(
            json.dumps(
                {
                    "manifest_version": 2,
                    "status": "ready",
                    "backup_type": backup_type,
                    "files": {},
                }
            ),
            encoding="utf-8",
        )
        timestamp = time.time() - age_days * 86400
        os.utime(directory, (timestamp, timestamp))

    def test_prune_keeps_manual_and_version_change(self, tmp_path: Path) -> None:
        self._write_backup(tmp_path, "manual_keep", "manual", 30)
        self._write_backup(tmp_path, "version_keep", "version_change", 30)
        self._write_backup(tmp_path, "scheduled_remove", "scheduled", 30)
        manager = BackupManager(str(tmp_path))

        result = manager.prune_backups(keep_days=7, now=time.time())

        assert result["removed"] == ["scheduled_remove"]
        assert (tmp_path / "backups" / "manual_keep").exists()
        assert (tmp_path / "backups" / "version_keep").exists()


# ---------------------------------------------------------------------------
# Delete backup tests
# ---------------------------------------------------------------------------


class TestDeleteBackup:
    """delete_backup."""

    def test_delete_existing_backup(self, tmp_path: Path) -> None:
        # Create a backup directory first
        backup_dir = tmp_path / "backups" / "v2.3.0"
        backup_dir.mkdir(parents=True)
        (backup_dir / _BACKUP_INFO_FILE).write_text("{}", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.delete_backup("v2.3.0") is True
        assert not backup_dir.exists()

    def test_delete_nonexistent_backup(self, tmp_path: Path) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.delete_backup("nonexistent") is False

    @pytest.mark.parametrize(
        "name",
        [
            "../outside",
            "..\\outside",
            "/tmp/x",
            "C:\\tmp\\x",
            ".",
            "bad name",
            "backup:1",
        ],
    )
    def test_rejects_path_like_backup_names(self, tmp_path: Path, name: str) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mgr.delete_backup(name)

    def test_delete_only_backend_listed_backup_name(self, tmp_path: Path) -> None:
        backups_root = tmp_path / "backups"
        backups_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / _BACKUP_INFO_FILE).write_text("{}", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mgr.delete_backup("../outside")
        assert outside.exists()

    def test_delete_backup_rejects_symlinked_directory_escaping_backups_root(
        self, tmp_path: Path
    ) -> None:
        backups_root = tmp_path / "backups"
        backups_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / _BACKUP_INFO_FILE).write_text("{}", encoding="utf-8")
        symlink_dir = backups_root / "linked_backup"
        try:
            symlink_dir.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported on this platform")

        mgr = BackupManager(data_dir=str(tmp_path))

        with pytest.raises(ValueError, match="escapes backups directory"):
            mgr.delete_backup("linked_backup")


# ---------------------------------------------------------------------------
# List backups tests
# ---------------------------------------------------------------------------


class TestListBackups:
    """list_backups static method."""

    def test_list_empty(self, tmp_path: Path) -> None:
        result = BackupManager.list_backups(str(tmp_path))
        assert result == []

    def test_list_with_backups(self, tmp_path: Path) -> None:
        # Create backups directory with a few entries
        b1 = tmp_path / "backups" / "v2.3.0"
        b1.mkdir(parents=True)
        (b1 / _BACKUP_INFO_FILE).write_text(
            json.dumps({"plugin_version": "2.3.0"}), encoding="utf-8"
        )
        (b1 / "memora.db").write_text("data", encoding="utf-8")
        b2 = tmp_path / "backups" / "v2.2.0"
        b2.mkdir(parents=True)
        # No info file — should still be listed with defaults
        (b2 / "memora.db").write_text("data", encoding="utf-8")

        result = BackupManager.list_backups(str(tmp_path))
        assert len(result) == 2
        # Sorted reverse (v2.3.0 before v2.2.0)
        assert result[0]["name"] == "v2.3.0"
        assert result[0]["file_count"] >= 1
        assert "directory" not in result[0]
        assert result[0]["integrity"] == "legacy_unverified"
        assert result[1]["name"] == "v2.2.0"

    def test_list_skips_files_not_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "not_a_backup.txt").write_text("nope")
        result = BackupManager.list_backups(str(tmp_path))
        assert result == []


# ---------------------------------------------------------------------------
# Apply pending restores
# ---------------------------------------------------------------------------


class TestApplyPendingRestores:
    """apply_pending_restores."""

    def test_no_restore_files(self, tmp_path: Path) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.apply_pending_restores()["status"] == "none"

    def test_applies_restore_file(self, tmp_path: Path) -> None:
        # Create a .restore file
        restore_file = tmp_path / "decay_state.json.restore"
        restore_file.write_text("restored content", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        applied = mgr.apply_pending_restores()
        assert applied["restore_status"] == "validating"
        # The .restore file should have been moved
        assert not restore_file.exists()
        target = tmp_path / "decay_state.json"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "restored content"

    def test_applies_restore_overwrites_existing(self, tmp_path: Path) -> None:
        (tmp_path / "decay_state.json").write_text("old content", encoding="utf-8")
        (tmp_path / "decay_state.json.restore").write_text(
            "restored content", encoding="utf-8"
        )
        mgr = BackupManager(data_dir=str(tmp_path))
        mgr.apply_pending_restores()
        assert (tmp_path / "decay_state.json").read_text(
            encoding="utf-8"
        ) == "restored content"


class TestStageRestore:
    """stage_restore."""

    def test_stage_restore_creates_restore_files(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups" / "v2.3.0"
        backup_dir.mkdir(parents=True)
        (backup_dir / _BACKUP_INFO_FILE).write_text("{}", encoding="utf-8")
        (backup_dir / "memora.db").write_bytes(_db_bytes("restored"))
        (tmp_path / "memora.db").write_bytes(_db_bytes("live"))
        mgr = BackupManager(data_dir=str(tmp_path))

        result = mgr.stage_restore("v2.3.0")

        assert result["pending"] is True
        assert result["staged"] == 1
        restored = sqlite3.connect(tmp_path / "memora.db")
        assert restored.execute("SELECT value FROM marker").fetchone() == ("live",)
        restored.close()
        operation_id = str(result["operation_id"])
        assert (tmp_path / ".restore" / operation_id / "payload" / "memora.db").exists()
        assert not [path for path in tmp_path.glob("*.restore") if path.is_file()]
        assert mgr.has_pending_restores() is True
        assert mgr.list_pending_restores() == [operation_id]

    def test_stage_restore_rejects_path_traversal(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "memora.db").write_text("outside", encoding="utf-8")
        mgr = BackupManager(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mgr.stage_restore("../outside")
        assert not (tmp_path / "memora.db.restore").exists()

    @pytest.mark.parametrize(
        "name", ["manual_20260628_120000", "v2.3.0", "backup.test-1"]
    )
    def test_validate_backup_name_accepts_expected_names(
        self, tmp_path: Path, name: str
    ) -> None:
        mgr = BackupManager(data_dir=str(tmp_path))
        assert mgr.validate_backup_name(name) == name

    @pytest.mark.asyncio
    async def test_stage_restore_writes_single_plan_and_payload(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("source"))
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()
        (tmp_path / "memora.db").write_bytes(_db_bytes("live"))

        result = manager.stage_restore(str(backup["name"]), apply_mode="restart")

        operation_id = str(result["operation_id"])
        plan_path = tmp_path / ".restore" / operation_id / "restore_plan.json"
        assert result["restore_status"] == "staged"
        assert (
            json.loads(plan_path.read_text(encoding="utf-8"))["apply_mode"] == "restart"
        )
        assert (plan_path.parent / "payload" / "memora.db").exists()
        assert manager.has_pending_restores() is True

    @pytest.mark.asyncio
    async def test_stage_restore_rejects_tampered_manifest(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("source"))
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()
        backup_dir = Path(backup["directory"])
        (backup_dir / "memora.db").write_bytes(_db_bytes("tampered"))

        with pytest.raises(RuntimeError, match="backup_invalid"):
            manager.stage_restore(str(backup["name"]))

    @pytest.mark.asyncio
    async def test_apply_restore_creates_pre_restore_and_validates(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("source"))
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()
        (tmp_path / "memora.db").write_bytes(_db_bytes("live"))
        staged = manager.stage_restore(str(backup["name"]))

        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "validating"
        assert applied["pre_restore_backup_name"]
        restored = sqlite3.connect(tmp_path / "memora.db")
        assert restored.execute("SELECT value FROM marker").fetchone() == ("source",)
        restored.close()
        manager.mark_restore_succeeded(str(staged["operation_id"]))
        assert (
            manager.get_restore_status(str(staged["operation_id"]))["restore_status"]
            == "succeeded"
        )

    @pytest.mark.asyncio
    async def test_cancel_staged_restore_removes_payload_and_unblocks(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("source"))
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()
        staged = manager.stage_restore(str(backup["name"]))

        cancelled = manager.cancel_restore(str(staged["operation_id"]))

        assert cancelled["restore_status"] == "cancelled"
        assert manager.has_pending_restores() is False
        assert not (
            tmp_path / ".restore" / str(staged["operation_id"]) / "payload"
        ).exists()

    @pytest.mark.asyncio
    async def test_apply_restore_rolls_back_when_later_file_fails(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "memora.db").write_bytes(_db_bytes("source"))
        (tmp_path / "decay_state.json").write_text("source-state", encoding="utf-8")
        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()
        (tmp_path / "memora.db").write_bytes(_db_bytes("live"))
        (tmp_path / "decay_state.json").write_text("live-state", encoding="utf-8")
        manager.stage_restore(str(backup["name"]))
        original = manager._install_restore_file
        calls = 0

        def fail_second(plan_dir: Path, progress):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("install")
            return original(plan_dir, progress)

        with patch.object(manager, "_install_restore_file", side_effect=fail_second):
            result = manager.apply_pending_restores()

        assert result["restore_status"] == "rolled_back"
        restored = sqlite3.connect(tmp_path / "memora.db")
        assert restored.execute("SELECT value FROM marker").fetchone() == ("live",)
        restored.close()
        assert (tmp_path / "decay_state.json").read_text(
            encoding="utf-8"
        ) == "live-state"

    @pytest.mark.asyncio
    async def test_backup_and_restore_include_quarantine_database(
        self, tmp_path: Path
    ) -> None:
        """双库备份恢复必须同时还原 canonical 与 quarantine 状态。"""

        from core.review.quarantine_store import MemoryQuarantineStore

        canonical_path = tmp_path / "memora.db"
        with sqlite3.connect(canonical_path) as db:
            db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT)")
            db.execute("INSERT INTO documents(id, text) VALUES (1, 'source')")
            db.commit()
        quarantine = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
        await quarantine.initialize()
        candidate = await quarantine.stage_candidate(
            candidate_key="backup-quarantine-candidate",
            reason_codes=["summary_quality_low"],
            content="用户喜欢咖啡。",
            metadata={},
            importance=0.7,
            session_id="session-1",
            persona_id=None,
            source_window={"start_index": 0, "end_index": 1},
            is_group_chat=False,
        )
        with sqlite3.connect(quarantine.db_path) as db:
            db.execute(
                """
                UPDATE memory_quarantine_candidates
                SET status = 'approved', canonical_memory_id = 1, revision = 2
                WHERE candidate_id = ?
                """,
                (candidate["candidate_id"],),
            )
            db.commit()

        manager = BackupManager(str(tmp_path))
        backup = await manager.create_backup()
        manifest = json.loads(
            (Path(backup["directory"]) / _BACKUP_INFO_FILE).read_text(encoding="utf-8")
        )
        assert "memory_quarantine.sqlite3" in manifest["files"]

        with sqlite3.connect(canonical_path) as db:
            db.execute("DELETE FROM documents")
            db.execute("INSERT INTO documents(id, text) VALUES (2, 'live')")
            db.commit()
        with sqlite3.connect(quarantine.db_path) as db:
            db.execute(
                "UPDATE memory_quarantine_candidates SET canonical_memory_id = 2"
            )
            db.commit()

        staged = manager.stage_restore(str(backup["name"]))
        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "validating"
        with sqlite3.connect(canonical_path) as db:
            assert db.execute("SELECT id FROM documents").fetchall() == [(1,)]
        with sqlite3.connect(quarantine.db_path) as db:
            assert db.execute(
                "SELECT status, canonical_memory_id FROM memory_quarantine_candidates"
            ).fetchone() == ("approved", 1)
        manager.mark_restore_succeeded(str(staged["operation_id"]))

    @pytest.mark.asyncio
    async def test_legacy_restore_rolls_back_on_orphaned_quarantine_reference(
        self, tmp_path: Path
    ) -> None:
        """旧的单库备份也不得留下 approved 指向不存在 canonical 的状态。"""

        from core.review.quarantine_store import MemoryQuarantineStore

        canonical_path = tmp_path / "memora.db"
        with sqlite3.connect(canonical_path) as db:
            db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT)")
            db.execute("INSERT INTO documents(id, text) VALUES (1, 'live')")
            db.commit()
        source_path = tmp_path / "source.db"
        with sqlite3.connect(source_path) as db:
            db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT)")
            db.execute("INSERT INTO documents(id, text) VALUES (2, 'source')")
            db.commit()
        quarantine = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
        await quarantine.initialize()
        candidate = await quarantine.stage_candidate(
            candidate_key="legacy-orphan-candidate",
            reason_codes=["summary_quality_low"],
            content="用户喜欢咖啡。",
            metadata={},
            importance=0.7,
            session_id="session-1",
            persona_id=None,
            source_window={"start_index": 0, "end_index": 1},
            is_group_chat=False,
        )
        with sqlite3.connect(quarantine.db_path) as db:
            db.execute(
                """
                UPDATE memory_quarantine_candidates
                SET status = 'approved', canonical_memory_id = 1, revision = 2
                WHERE candidate_id = ?
                """,
                (candidate["candidate_id"],),
            )
            db.commit()

        backup_dir = tmp_path / "backups" / "legacy"
        backup_dir.mkdir(parents=True)
        (backup_dir / _BACKUP_INFO_FILE).write_text("{}", encoding="utf-8")
        (backup_dir / "memora.db").write_bytes(source_path.read_bytes())
        manager = BackupManager(str(tmp_path))

        staged = manager.stage_restore("legacy")
        applied = manager.apply_pending_restores()

        assert applied["restore_status"] == "rolled_back"
        with sqlite3.connect(canonical_path) as db:
            assert db.execute("SELECT id FROM documents").fetchall() == [(1,)]
        with sqlite3.connect(quarantine.db_path) as db:
            assert db.execute(
                "SELECT status, canonical_memory_id FROM memory_quarantine_candidates"
            ).fetchone() == ("approved", 1)
        assert (
            manager.get_restore_status(str(staged["operation_id"]))["reason_code"]
            == "restore_quarantine_reference_missing"
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify constant values are sensible."""

    def test_version_file_name(self) -> None:
        assert _VERSION_FILE == ".plugin_version"

    def test_backup_info_file_name(self) -> None:
        assert _BACKUP_INFO_FILE == "backup_info.json"

    def test_backup_name_pattern(self) -> None:
        assert _BACKUP_NAME_RE.fullmatch("manual_20260628_120000")
        assert _BACKUP_NAME_RE.fullmatch("v2.3.0")
        assert _BACKUP_NAME_RE.fullmatch("backup.test-1")
        assert not _BACKUP_NAME_RE.fullmatch("bad name")
