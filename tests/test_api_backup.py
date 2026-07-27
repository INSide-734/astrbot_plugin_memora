"""core/api/backup_api.py — BackupApiMixin 测试。

测试辅助函数和响应格式逻辑。实际的备份 I/O 在
test_backup_manager.py 中测试；此处专注于 API 层行为。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.api.backup_api import BackupApiMixin

# ---------------------------------------------------------------------------
# 最小化 Mixin 设置辅助函数
# ---------------------------------------------------------------------------


def _make_mixin(plugin) -> BackupApiMixin:
    """创建 BackupApiMixin 实例。"""

    class C(BackupApiMixin):
        def __init__(self):
            self.plugin = plugin  # type: ignore

        def _ok(self, data):
            from core.api.response_utils import ok_response

            return ok_response(data)

        def _error(self, msg):
            from core.api.response_utils import error_response

            return error_response(msg)

    return C()


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestBackupApiListBackups:
    """list_backups 返回备份文件列表。"""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_initializer_missing(self) -> None:
        plugin = type("P", (), {})()
        mixin = _make_mixin(plugin)
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_data_dir(self) -> None:
        plugin = type("P", (), {"initializer": None})()
        mixin = _make_mixin(plugin)
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_data_dir_empty(self) -> None:
        plugin = type("P", (), {"initializer": type("I", (), {"data_dir": ""})()})()
        mixin = _make_mixin(plugin)
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_backups_from_manager(self) -> None:
        plugin = type(
            "P", (), {"initializer": type("I", (), {"data_dir": "C:/tmp/memora"})()}
        )()
        mixin = _make_mixin(plugin)
        backups = [
            {"name": "backup-a", "path": "a.zip"},
            {"name": "backup-b", "path": "b.zip"},
        ]
        with patch(
            "core.api.backup_api.BackupManager.list_backups",
            return_value=backups,
        ) as mock_list:
            result = await mixin.list_backups()
        mock_list.assert_called_once_with("C:/tmp/memora")
        assert result["status"] == "ok"
        assert result["data"]["backups"] == backups
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_tolerates_non_list_backup_payload(self) -> None:
        plugin = type(
            "P", (), {"initializer": type("I", (), {"data_dir": "C:/tmp/memora"})()}
        )()
        mixin = _make_mixin(plugin)
        with patch(
            "core.api.backup_api.BackupManager.list_backups",
            return_value="bad-backups",
        ):
            result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_accepts_iterable_backup_payload(self) -> None:
        plugin = type(
            "P", (), {"initializer": type("I", (), {"data_dir": "C:/tmp/memora"})()}
        )()
        mixin = _make_mixin(plugin)
        backups = (
            {"name": "backup-a", "path": "a.zip"},
            {"name": "backup-b", "path": "b.zip"},
        )
        with patch(
            "core.api.backup_api.BackupManager.list_backups",
            return_value=backups,
        ):
            result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == list(backups)
        assert result["data"]["total"] == 2


class TestBackupApiResponseFormat:
    """list_backups 响应结构正确。"""

    @pytest.mark.asyncio
    async def test_response_has_status_and_data_keys(self) -> None:
        plugin = type("P", (), {"initializer": None})()
        mixin = _make_mixin(plugin)
        result = await mixin.list_backups()
        assert "status" in result
        assert "data" in result
        assert "backups" in result["data"]
        assert "total" in result["data"]

    @pytest.mark.asyncio
    async def test_total_matches_backups_length(self) -> None:
        plugin = type("P", (), {"initializer": None})()
        mixin = _make_mixin(plugin)
        result = await mixin.list_backups()
        assert result["data"]["total"] == len(result["data"]["backups"])


class TestBackupApiRestoreLifecycle:
    """恢复状态、热重载安排和取消接口。"""

    @pytest.mark.asyncio
    async def test_restore_reload_schedules_plugin_reload(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        request = MagicMock()
        request.get_json = AsyncMock(
            return_value={"name": "backup-a", "apply_mode": "reload"}
        )
        manager = MagicMock()
        manager.stage_restore.return_value = {
            "operation_id": "op-1",
            "restore_status": "staged",
            "staged": 1,
            "warning_codes": [],
        }
        manager.get_restore_status.return_value = {
            "operation_id": "op-1",
            "restore_status": "reload_scheduled",
        }
        plugin = MagicMock(_backup_manager=manager)
        plugin.schedule_backup_restore_reload.return_value = True
        mixin = _make_mixin(plugin)

        with patch("quart.request", request):
            result = await mixin.restore_backup()

        assert result["status"] == "ok"
        assert result["data"]["operation_id"] == "op-1"
        plugin.schedule_backup_restore_reload.assert_called_once_with("op-1")
        manager.mark_reload_scheduled.assert_called_once_with("op-1", True)

    @pytest.mark.asyncio
    async def test_restore_rejects_unknown_apply_mode(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        request = MagicMock()
        request.get_json = AsyncMock(
            return_value={"name": "backup-a", "apply_mode": "live"}
        )
        plugin = MagicMock(_backup_manager=MagicMock())
        mixin = _make_mixin(plugin)

        with patch("quart.request", request):
            result = await mixin.restore_backup()

        assert result["status"] == "error"
        assert result["code"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_status_and_cancel_are_stable_envelopes(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        request = MagicMock()
        request.args = {"operation_id": "op-1"}
        manager = MagicMock()
        manager.get_restore_status.return_value = {
            "operation_id": "op-1",
            "restore_status": "succeeded",
        }
        manager.cancel_restore.return_value = {
            "operation_id": "op-1",
            "restore_status": "cancelled",
        }
        plugin = MagicMock(_backup_manager=manager)
        mixin = _make_mixin(plugin)

        with patch("quart.request", request):
            status = await mixin.get_backup_status()
        assert status == {
            "status": "ok",
            "data": {
                "operation_id": "op-1",
                "restore_status": "succeeded",
            },
        }

        request.get_json = AsyncMock(return_value={"operation_id": "op-1"})
        with patch("quart.request", request):
            cancelled = await mixin.cancel_restore()
        assert cancelled["status"] == "ok"
        assert cancelled["data"]["restore_status"] == "cancelled"
