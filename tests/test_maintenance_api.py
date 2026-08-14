"""core/api/maintenance_api.py 测试 — MaintenanceApiMixin。

Covers rebuild, purge, compact, backup CRUD, restore, and export endpoints.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.platform.transport.page_api.response_utils import error_response

# ── helpers ───────────────────────────────────────────────────────────


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(
    *,
    plugin_ready: bool = True,
    has_backup: bool = True,
    has_maint: bool = False,
    has_exporter: bool = False,
    backup_path: str = "/tmp/backup.zip",
):
    from core.platform.transport.page_api.maintenance_api import MaintenanceApiMixin

    class Stub:
        rebuild_index = MaintenanceApiMixin.rebuild_index
        _record_index_rebuild_observability = (
            MaintenanceApiMixin._record_index_rebuild_observability
        )
        _coerce_result_int = MaintenanceApiMixin._coerce_result_int
        rebuild_graph_index = MaintenanceApiMixin.rebuild_graph_index
        get_persistence_health = MaintenanceApiMixin.get_persistence_health
        repair_persistence_health = MaintenanceApiMixin.repair_persistence_health
        purge_deleted_memories = MaintenanceApiMixin.purge_deleted_memories
        compact_database = MaintenanceApiMixin.compact_database
        create_backup = MaintenanceApiMixin.create_backup
        list_backups = MaintenanceApiMixin.list_backups
        delete_backup = MaintenanceApiMixin.delete_backup
        batch_delete_backups = MaintenanceApiMixin.batch_delete_backups
        restore_backup = MaintenanceApiMixin.restore_backup
        export_memories = MaintenanceApiMixin.export_memories
        install_dashboard_deps = MaintenanceApiMixin.install_dashboard_deps
        build_dashboard = MaintenanceApiMixin.build_dashboard
        _run_npm_command = MaintenanceApiMixin._run_npm_command
        _dashboard_runtime_config = MaintenanceApiMixin._dashboard_runtime_config
        _truncate_command_output = MaintenanceApiMixin._truncate_command_output
        _dashboard_runtime_build_disabled_response = (
            MaintenanceApiMixin._dashboard_runtime_build_disabled_response
        )
        _get_dashboard_runtime_lock = MaintenanceApiMixin._get_dashboard_runtime_lock
        _resolve_command_executable = MaintenanceApiMixin._resolve_command_executable

        def __init__(self):
            self.plugin = MagicMock()
            if has_backup:
                self.plugin._backup_manager = MagicMock()
                self.plugin._backup_manager.create_backup = AsyncMock(
                    return_value=backup_path
                )
                self.plugin._backup_manager.delete_backup = MagicMock(return_value=True)
                self.plugin._backup_manager.stage_restore = MagicMock(
                    return_value={
                        "staged": 1,
                        "skipped": 0,
                        "pending": True,
                        "staged_files": ["memora.db.restore"],
                        "skipped_files": [],
                    }
                )
                self.plugin._backup_manager.data_dir = "/fake/data"
            else:
                self.plugin._backup_manager = None
            self.plugin.config_manager = MagicMock()
            self.plugin.config_manager.get.side_effect = lambda key, default=None: {
                "dashboard.allow_runtime_build": False,
                "dashboard.build_timeout_seconds": 120,
                "dashboard.max_output_chars": 20000,
            }.get(key, default)
            self.plugin.initializer = MagicMock()
            self.plugin.initializer.data_dir = "/fake/data"
            self.plugin.initializer.index_validator = MagicMock()
            self.plugin.initializer.index_validator.rebuild_indexes = AsyncMock(
                return_value={"success": True, "processed": 3, "errors": 0, "total": 3}
            )

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, error_response("not ready")
            engine = MagicMock(spec=["rebuild_graph_index"])
            engine.rebuild_graph_index = AsyncMock()
            if has_maint:
                engine.maintenance = MagicMock()
                engine.maintenance.purge_deleted = AsyncMock(return_value=5)
            if has_exporter:
                engine.memory_exporter = MagicMock()
                engine.memory_exporter.export_jsonl = AsyncMock(return_value=10)
                engine.memory_exporter.export_markdown = AsyncMock(return_value=10)
            return {"memory_engine": engine}, None

    return Stub()


# ── tests ─────────────────────────────────────────────────────────────


class TestMaintenanceValidation:
    """Plugin-not-ready and error path tests."""

    @pytest.mark.asyncio
    async def test_rebuild_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.rebuild_index()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_purge_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.purge_deleted_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_compact_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.compact_database()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_backup_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.create_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_export_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("quart.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.export_memories()
        assert result["status"] == "error"


class TestMaintenanceHappyPath:
    """Happy path tests with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_rebuild_index_ok(self) -> None:
        mixin = _make_mixin()
        result = await mixin.rebuild_index()
        assert result["status"] == "ok"
        mixin.plugin.initializer.index_validator.rebuild_indexes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rebuild_index_records_observability_snapshot_on_success(
        self,
    ) -> None:
        mixin = _make_mixin()
        mixin.plugin.initializer.index_validator.rebuild_indexes.return_value = {
            "success": True,
            "processed": 8,
            "errors": 1,
            "total": 9,
            "message": "索引已按失败率阈值完成可接受切换",
        }

        result = await mixin.rebuild_index()

        assert result["status"] == "ok"
        observability = mixin.plugin._index_observability
        assert observability["last_rebuild_success"] is True
        assert observability["last_rebuild_errors"] == 1
        assert observability["last_rebuild_total"] == 9
        assert (
            observability["last_rebuild_message"] == "索引已按失败率阈值完成可接受切换"
        )
        assert observability["last_rebuild_duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_rebuild_index_records_observability_snapshot_on_exception(
        self,
    ) -> None:
        mixin = _make_mixin()
        mixin.plugin.initializer.index_validator.rebuild_indexes.side_effect = (
            RuntimeError("boom")
        )

        result = await mixin.rebuild_index()

        assert result["status"] == "error"
        observability = mixin.plugin._index_observability
        assert observability["last_rebuild_success"] is False
        assert observability["last_rebuild_errors"] == 1
        assert observability["last_rebuild_total"] == 0
        assert observability["last_rebuild_message"] == "boom"
        assert observability["last_rebuild_duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_rebuild_index_does_not_call_graph_rebuild(self) -> None:
        mixin = _make_mixin()
        engines, _ = await mixin._ensure_plugin_ready()
        engine = engines["memory_engine"]
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": engine}, None)
        )

        result = await mixin.rebuild_index()

        assert result["status"] == "ok"
        engine.rebuild_graph_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rebuild_graph_index_ok(self) -> None:
        mixin = _make_mixin()
        engines, _ = await mixin._ensure_plugin_ready()
        engine = engines["memory_engine"]
        engine.rebuild_graph_index.return_value = {"rebuilt": 2, "skipped": 1}
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": engine}, None)
        )

        result = await mixin.rebuild_graph_index()

        assert result["status"] == "ok"
        assert result["data"]["result"] == {"rebuilt": 2, "skipped": 1}
        engine.rebuild_graph_index.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_persistence_health_returns_validator_report(self) -> None:
        mixin = _make_mixin()
        validator = MagicMock()
        validator.check = AsyncMock(return_value={"ok": True, "issues": {}})
        with patch(
            "core.platform.transport.page_api.maintenance_api.PersistenceHealthValidator",
            return_value=validator,
        ):
            result = await mixin.get_persistence_health()

        assert result["status"] == "ok"
        assert result["data"]["ok"] is True
        validator.check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_repair_persistence_health_requires_explicit_targets(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={})
        with patch("quart.request", req):
            mixin = _make_mixin()
            result = await mixin.repair_persistence_health()

        assert result["status"] == "error"
        assert "targets" in result["message"]

    @pytest.mark.asyncio
    async def test_purge_no_maint_returns_zero(self) -> None:
        mixin = _make_mixin()
        result = await mixin.purge_deleted_memories()
        assert result["status"] == "ok"
        assert result["data"]["purged"] == 0

    @pytest.mark.asyncio
    async def test_purge_with_maint(self) -> None:
        mixin = _make_mixin(has_maint=True)
        result = await mixin.purge_deleted_memories()
        assert result["status"] == "ok"
        assert result["data"]["purged"] == 5

    @pytest.mark.asyncio
    async def test_compact_ok(self) -> None:
        mixin = _make_mixin()
        result = await mixin.compact_database()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_create_backup_ok(self) -> None:
        mixin = _make_mixin(has_backup=True)
        result = await mixin.create_backup()
        assert result["status"] == "ok"
        assert "path" in result["data"]

    @pytest.mark.asyncio
    async def test_create_backup_no_manager(self) -> None:
        mixin = _make_mixin(has_backup=False)
        result = await mixin.create_backup()
        assert result["status"] == "error"


class TestBackupCRUD:
    """Backup listing, deletion, and restore."""

    @pytest.mark.asyncio
    async def test_list_backups_ok(self) -> None:
        mixin = _make_mixin(has_backup=True)
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert "backups" in result["data"]

    @pytest.mark.asyncio
    async def test_list_backups_no_data_dir(self) -> None:
        mixin = _make_mixin(has_backup=False)
        mixin.plugin.initializer = None
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []

    @pytest.mark.asyncio
    async def test_list_backups_fallback_to_initializer(self) -> None:
        mixin = _make_mixin(has_backup=False)
        result = await mixin.list_backups()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_list_backups_tolerates_non_list_manager_payload(self) -> None:
        mixin = _make_mixin(has_backup=True)
        with patch(
            "core.features.backup.application.BackupManager.list_backups",
            return_value="bad-backups",
        ):
            result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_list_backups_accepts_iterable_manager_payload(self) -> None:
        mixin = _make_mixin(has_backup=True)
        backups = (
            {"name": "backup-a", "path": "a.zip"},
            {"name": "backup-b", "path": "b.zip"},
        )
        with patch(
            "core.features.backup.application.BackupManager.list_backups",
            return_value=backups,
        ):
            result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == list(backups)
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_delete_backup_missing_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": ""})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.delete_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_backup_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["backup1"])
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"
        assert "JSON" in result["message"]
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_backup_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "backup1"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=False)
            result = await mixin.delete_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_backup_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "nonexistent"})
        mixin = _make_mixin(has_backup=True)
        mixin.plugin._backup_manager.delete_backup.return_value = False
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_backup_ok(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "backup1"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "ok"
        mixin.plugin._backup_manager.delete_backup.assert_called_once_with("backup1")

    @pytest.mark.asyncio
    async def test_delete_backup_rejects_path_traversal_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "../outside"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_backup_rejects_invalid_characters(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "bad name"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_missing_names(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": []})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_list_names_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": "backup1"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["b1", "b2"])
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"
        assert "JSON" in result["message"]
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": ["b1", "b2"]})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=False)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_ok(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": ["b1", "b2", "b3"]})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.batch_delete_backups()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] == 3
        mixin.plugin._backup_manager.delete_backup.assert_any_call("b1")
        mixin.plugin._backup_manager.delete_backup.assert_any_call("b2")
        mixin.plugin._backup_manager.delete_backup.assert_any_call("b3")

    @pytest.mark.asyncio
    async def test_batch_delete_counts_invalid_names_as_failed(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": ["good", "../bad", "bad name"]})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.batch_delete_backups()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] == 1
        assert result["data"]["failed"] == 2
        mixin.plugin._backup_manager.delete_backup.assert_called_once_with("good")

    @pytest.mark.asyncio
    async def test_restore_missing_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": ""})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.restore_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restore_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["backup1"])
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"
        assert "JSON" in result["message"]
        mixin.plugin._backup_manager.stage_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "b1"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=False)
            result = await mixin.restore_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restore_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "nonexistent"})
        mixin = _make_mixin(has_backup=True)
        mixin.plugin._backup_manager.stage_restore.side_effect = FileNotFoundError(
            "backup not found: nonexistent"
        )
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restore_rejects_path_traversal_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "../outside"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.stage_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_rejects_invalid_characters(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "bad name"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.stage_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_tolerates_malformed_stage_restore_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "backup1"})
        mixin = _make_mixin(has_backup=True)
        mixin.plugin._backup_manager.stage_restore.return_value = {
            "staged": "1",
            "skipped": "bad-count",
            "staged_files": "bad-files",
            "skipped_files": {"bad": "files"},
        }
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "ok"
        assert result["data"]["staged"] == 1
        assert result["data"]["skipped"] == 0
        assert result["data"]["pending"] is True
        assert result["data"]["staged_files"] == []
        assert result["data"]["skipped_files"] == []


class TestRestoreBackup:
    """Restore backup with real temp directories."""

    @pytest.mark.asyncio
    async def test_restore_rejects_legacy_backup_without_canonical_database(
        self,
    ) -> None:
        import tempfile

        req = _mock_request()
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "data")
            backup_dir = os.path.join(data_dir, "backups", "test_backup")
            os.makedirs(backup_dir, exist_ok=True)
            # Create some files to restore
            test_file = os.path.join(backup_dir, "test.db")
            with open(test_file, "w") as f:
                f.write("test data")
            restore_file = os.path.join(backup_dir, "memora.index")
            with open(restore_file, "w") as f:
                f.write("index data")

            req.get_json = AsyncMock(return_value={"name": "test_backup"})
            mixin = _make_mixin(has_backup=True)
            from core.features.backup.application import BackupManager

            mixin.plugin._backup_manager = BackupManager(data_dir)
            with patch("quart.request", req):
                r = await mixin.restore_backup()
            assert r["status"] == "error"
            assert r["code"] == "backup_invalid"


class TestExportMemories:
    """Memory export endpoint tests."""

    @pytest.mark.asyncio
    async def test_export_no_exporter(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"format": "jsonl"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=False)
            result = await mixin.export_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_export_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["jsonl"])
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=True)
            result = await mixin.export_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_export_tolerates_non_numeric_export_count(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"format": "jsonl"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=True)
            engines, _ = await mixin._ensure_plugin_ready()
            engine = engines["memory_engine"]
            engine.memory_exporter.export_jsonl = AsyncMock(return_value="bad-count")
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=({"memory_engine": engine}, None)
            )
            with (
                patch("builtins.open", create=True) as mock_open,
                patch("tempfile.NamedTemporaryFile") as mock_tmp,
                patch("os.unlink") as mock_unlink,
            ):
                tmp = MagicMock()
                tmp.__enter__.return_value.name = "/tmp/export.jsonl"
                tmp.__exit__.return_value = False
                mock_tmp.return_value = tmp
                mock_open.return_value.__enter__.return_value.read.return_value = (
                    "line-1\n"
                )
                result = await mixin.export_memories()
        assert result["status"] == "ok"
        assert result["data"]["content"] == "line-1\n"
        assert result["data"]["count"] == 0
        assert result["data"]["format"] == "jsonl"
        mock_unlink.assert_called_once_with("/tmp/export.jsonl")


class TestDashboardMaintenance:
    """Dashboard runtime install/build safety controls."""

    def test_dashboard_runtime_config_treats_false_string_as_disabled(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": "false",
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)

        allow_runtime_build, timeout_seconds, max_output_chars = (
            mixin._dashboard_runtime_config()
        )

        assert allow_runtime_build is False
        assert timeout_seconds == 120
        assert max_output_chars == 20000

    def test_dashboard_runtime_config_falls_back_and_clamps_numeric_values(
        self,
    ) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": "true",
            "dashboard.build_timeout_seconds": "bad",
            "dashboard.max_output_chars": 200,
        }.get(key, default)

        allow_runtime_build, timeout_seconds, max_output_chars = (
            mixin._dashboard_runtime_config()
        )

        assert allow_runtime_build is True
        assert timeout_seconds == 120
        assert max_output_chars == 1000

    def test_dashboard_runtime_config_treats_unknown_string_as_disabled(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": "definitely-not-a-bool",
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)

        allow_runtime_build, timeout_seconds, max_output_chars = (
            mixin._dashboard_runtime_config()
        )

        assert allow_runtime_build is False
        assert timeout_seconds == 120
        assert max_output_chars == 20000

    @pytest.mark.asyncio
    async def test_install_dashboard_disabled_by_default(self) -> None:
        mixin = _make_mixin()
        result = await mixin.install_dashboard_deps()
        assert result["status"] == "error"
        assert "已禁用" in result["message"]

    @pytest.mark.asyncio
    async def test_build_dashboard_disabled_by_default(self) -> None:
        mixin = _make_mixin()
        result = await mixin.build_dashboard()
        assert result["status"] == "error"
        assert "已禁用" in result["message"]

    @pytest.mark.asyncio
    async def test_install_dashboard_uses_npm_ci_when_enabled(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": True,
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)
        mixin._run_npm_command = AsyncMock(
            return_value={
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "timed_out": False,
            }
        )
        with patch("os.path.isfile", return_value=True):
            result = await mixin.install_dashboard_deps()
        assert result["status"] == "ok"
        assert result["data"]["command"] == "npm ci"
        mixin._run_npm_command.assert_awaited_once()
        assert mixin._run_npm_command.await_args.args[0] == ["npm", "ci"]

    @pytest.mark.asyncio
    async def test_build_dashboard_enabled_calls_build(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": True,
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)
        mixin._run_npm_command = AsyncMock(
            return_value={
                "stdout": "built",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "timed_out": False,
            }
        )
        with patch("os.path.isfile", return_value=True):
            result = await mixin.build_dashboard()
        assert result["status"] == "ok"
        assert result["data"]["command"] == "npm run build"
        assert mixin._run_npm_command.await_args.args[0] == ["npm", "run", "build"]

    def test_truncate_command_output_short(self) -> None:
        mixin = _make_mixin()
        assert type(mixin)._truncate_command_output("abc", 10) == "abc"

    def test_truncate_command_output_long(self) -> None:
        mixin = _make_mixin()
        output = type(mixin)._truncate_command_output("a" * 50, 20)
        assert len(output) <= 20
        assert output != "a" * 50

    def test_resolve_command_executable_uses_direct_match(self) -> None:
        mixin = _make_mixin()
        with patch("shutil.which", side_effect=["C:/nodejs/npm.cmd"]):
            resolved = type(mixin)._resolve_command_executable("npm")
        assert resolved == "C:/nodejs/npm.cmd"

    def test_resolve_command_executable_falls_back_to_windows_suffixes(self) -> None:
        mixin = _make_mixin()
        with (
            patch("sys.platform", "win32"),
            patch(
                "shutil.which",
                side_effect=[None, "C:/nodejs/npm.cmd"],
            ),
        ):
            resolved = type(mixin)._resolve_command_executable("npm")
        assert resolved == "C:/nodejs/npm.cmd"
