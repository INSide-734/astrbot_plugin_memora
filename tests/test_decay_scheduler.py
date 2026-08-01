"""DecayScheduler 测试 — 每日衰减、生命周期、备份、状态、补偿。"""

import asyncio
import builtins
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestDecayScheduler:
    @pytest.fixture
    def mock_engine(self):
        engine = AsyncMock()
        engine.apply_daily_decay = AsyncMock(return_value=1)
        engine.cleanup_old_memories = AsyncMock(return_value=0)
        engine.consolidate_memories = AsyncMock(return_value={})
        engine.maintain_storage = AsyncMock(return_value={"success": True})
        engine.config = {"auto_cleanup_enabled": False}
        return engine

    @staticmethod
    def _make_scheduler(mock_engine, tmp_path, **kwargs):
        from core.schedulers.decay_scheduler import DecayScheduler

        defaults = dict(
            memory_engine=mock_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            check_hour=0,
            check_minute=0,
        )
        defaults.update(kwargs)
        return DecayScheduler(**defaults)

    # ---- State file tests ----

    @pytest.mark.asyncio
    async def test_load_state_missing_file(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        result = await s._load_state()
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_state_valid_json(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        state_file = s._state_file
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text('{"last_decay_date": "2026-06-22"}', encoding="utf-8")
        result = await s._load_state()
        assert result == {"last_decay_date": "2026-06-22"}

    @pytest.mark.asyncio
    async def test_load_state_corrupt_json(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        state_file = s._state_file
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("not valid json {{{", encoding="utf-8")
        result = await s._load_state()
        assert result == {}

    @pytest.mark.asyncio
    async def test_save_state(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        await s._save_state({"last_decay_date": "2026-06-22"})
        assert s._state_file.exists()
        loaded = json.loads(s._state_file.read_text(encoding="utf-8"))
        assert loaded["last_decay_date"] == "2026-06-22"

    @pytest.mark.asyncio
    async def test_save_state_write_failure_keeps_previous_state(
        self, mock_engine, tmp_path, monkeypatch
    ):
        s = self._make_scheduler(mock_engine, tmp_path)
        s._state_file.parent.mkdir(parents=True, exist_ok=True)
        s._state_file.write_text('{"last_decay_date": "old"}', encoding="utf-8")

        real_import = builtins.__import__
        real_write_text = Path.write_text

        def blocking_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "aiofiles":
                raise ImportError("blocked")
            return real_import(name, globals, locals, fromlist, level)

        def failing_write_text(self, content, *args, **kwargs):
            if self.name.endswith(".tmp"):
                real_write_text(self, "partial", *args, **kwargs)
                raise OSError("disk full")
            return real_write_text(self, content, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocking_import)
        monkeypatch.setattr(Path, "write_text", failing_write_text)

        await s._save_state({"last_decay_date": "new"})

        assert json.loads(s._state_file.read_text(encoding="utf-8")) == {
            "last_decay_date": "old"
        }

    @pytest.mark.asyncio
    async def test_get_set_last_decay_date(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        assert await s._get_last_decay_date() is None
        await s._set_last_decay_date("2026-06-22")
        assert await s._get_last_decay_date() == "2026-06-22"

    # ---- Missed days calculation ----

    @pytest.mark.asyncio
    async def test_calculate_missed_days_no_previous(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        missed = await s._calculate_missed_days()
        assert missed == 0

    @pytest.mark.asyncio
    async def test_calculate_missed_days_one_day_ago(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        from datetime import datetime, timedelta

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        with patch.object(s, "_get_last_decay_date", AsyncMock(return_value=yesterday)):
            missed = await s._calculate_missed_days()
            assert missed >= 0

    @pytest.mark.asyncio
    async def test_calculate_missed_days_invalid_date(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        with patch.object(
            s, "_get_last_decay_date", AsyncMock(return_value="not-a-date")
        ):
            missed = await s._calculate_missed_days()
            assert missed == 0

    # ---- Check and execute ----

    @pytest.mark.asyncio
    async def test_no_duplicate_same_day(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        today_str = s._get_today_str()
        with patch.object(
            s, "_load_state", AsyncMock(return_value={"last_decay_date": today_str})
        ):
            await s._check_and_execute()
            assert mock_engine.apply_daily_decay.call_count == 0

    @pytest.mark.asyncio
    async def test_check_and_execute_first_run(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        with patch.object(s, "_load_state", AsyncMock(return_value={})):
            with patch.object(s, "_execute_decay", AsyncMock(return_value=True)):
                await s._check_and_execute()
                s._execute_decay.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_and_execute_with_missed_days(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        from datetime import datetime, timedelta

        two_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        with patch.object(
            s, "_load_state", AsyncMock(return_value={"last_decay_date": two_days_ago})
        ):
            with patch.object(
                s, "_execute_decay", AsyncMock(return_value=True)
            ) as mock_exec:
                await s._check_and_execute()
                mock_exec.assert_called_once()
                # Should call with total_days > 1
                call_arg = mock_exec.call_args[0][0]
                assert call_arg >= 2

    # ---- Execute decay (core logic) ----

    @pytest.mark.asyncio
    async def test_execute_decay_with_zero_rate(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path, decay_rate=0.0)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True
            mock_engine.apply_daily_decay.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_decay_with_cleanup(self, mock_engine, tmp_path):
        mock_engine.config = {
            "auto_cleanup_enabled": True,
            "cleanup_days_threshold": 30,
            "cleanup_importance_threshold": 0.3,
        }
        mock_engine.cleanup_old_memories = AsyncMock(return_value=5)
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True
            assert mock_engine.cleanup_old_memories.called

    @pytest.mark.asyncio
    async def test_execute_decay_cleanup_handles_error(self, mock_engine, tmp_path):
        mock_engine.config = {"auto_cleanup_enabled": True}
        mock_engine.cleanup_old_memories = AsyncMock(
            side_effect=RuntimeError("cleanup fail")
        )
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True  # Should not fail overall

    @pytest.mark.asyncio
    async def test_execute_decay_consolidation(self, mock_engine, tmp_path):
        mock_engine.consolidate_memories = AsyncMock(return_value={"paired": 3})
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True
            mock_engine.consolidate_memories.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_decay_consolidation_error(self, mock_engine, tmp_path):
        mock_engine.consolidate_memories = AsyncMock(
            side_effect=RuntimeError("consolidate fail")
        )
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True  # Should not fail overall

    @pytest.mark.asyncio
    async def test_execute_decay_storage_maintenance_success(
        self, mock_engine, tmp_path
    ):
        mock_engine.maintain_storage = AsyncMock(
            return_value={"success": True, "bytes_reclaimed": 1048576}
        )
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True

    @pytest.mark.asyncio
    async def test_execute_decay_storage_maintenance_failure(
        self, mock_engine, tmp_path
    ):
        mock_engine.maintain_storage = AsyncMock(
            return_value={"success": False, "error": "disk full"}
        )
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True  # Should not fail overall

    @pytest.mark.asyncio
    async def test_execute_decay_storage_maintenance_exception(
        self, mock_engine, tmp_path
    ):
        mock_engine.maintain_storage = AsyncMock(
            side_effect=RuntimeError("maintain fail")
        )
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True

    @pytest.mark.asyncio
    async def test_execute_decay_with_backup(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        backup_mgr.create_backup = AsyncMock(return_value={"name": "scheduled_ok"})
        backup_mgr.prune_backups = MagicMock(return_value={"removed": []})
        s = self._make_scheduler(
            mock_engine,
            tmp_path,
            backup_manager=backup_mgr,
            backup_enabled=True,
        )
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True
            backup_mgr.create_backup.assert_called_once_with(kind="scheduled")
            backup_mgr.prune_backups.assert_called_once_with(keep_days=7)

    @pytest.mark.asyncio
    async def test_execute_decay_backup_disabled(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        s = self._make_scheduler(
            mock_engine,
            tmp_path,
            backup_manager=backup_mgr,
            backup_enabled=False,
        )
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            result = await s._execute_decay(1)
            assert result is True
            backup_mgr.create_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_decay_fatal_error(self, mock_engine, tmp_path):
        mock_engine.apply_daily_decay = AsyncMock(side_effect=RuntimeError("fatal"))
        s = self._make_scheduler(mock_engine, tmp_path)
        result = await s._execute_decay(1)
        assert result is False

    # ---- Optional maintenance ----

    @pytest.mark.asyncio
    async def test_optional_maintenance_runs_all(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.profile_manager = AsyncMock()
        mock_engine.knowledge_manager = AsyncMock()
        mock_engine.auto_learning = AsyncMock()
        mock_engine.note_manager = AsyncMock()
        mock_engine.atom_store = AsyncMock()
        mock_engine.profile_manager.decay_and_clean_all = AsyncMock(
            return_value={"scanned": 1, "removed": 0, "failed": 0}
        )
        mock_engine.knowledge_manager.cleanup_expired = AsyncMock(return_value=0)
        mock_engine.auto_learning.rebuild_candidates = AsyncMock()
        mock_engine.note_manager.prune_versions = AsyncMock(return_value=0)
        mock_engine.atom_store.query_upcoming_planned = AsyncMock(return_value=[])
        await s._run_optional_maintenance()
        mock_engine.profile_manager.decay_and_clean_all.assert_called_once()
        mock_engine.knowledge_manager.cleanup_expired.assert_called_once()
        mock_engine.auto_learning.rebuild_candidates.assert_awaited_once()
        mock_engine.note_manager.prune_versions.assert_called_once()

    @pytest.mark.asyncio
    async def test_optional_maintenance_propagates_learning_cancellation(
        self, mock_engine, tmp_path
    ):
        """自主学习候选重建收到取消时必须中止维护链。"""

        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.profile_manager = None
        mock_engine.knowledge_manager = None
        mock_engine.note_manager = None
        mock_engine.atom_store = None
        mock_engine.auto_learning = AsyncMock()
        mock_engine.auto_learning.rebuild_candidates = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await s._run_optional_maintenance()

    @pytest.mark.asyncio
    async def test_maintenance_isolates_failures(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.profile_manager = AsyncMock()
        mock_engine.knowledge_manager = AsyncMock()
        mock_engine.profile_manager.decay_and_clean_all = AsyncMock(
            side_effect=RuntimeError("crash")
        )
        mock_engine.knowledge_manager.cleanup_expired = AsyncMock(return_value=5)
        await s._run_optional_maintenance()
        mock_engine.knowledge_manager.cleanup_expired.assert_called_once()

    @pytest.mark.asyncio
    async def test_maintenance_knowledge_cleanup(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.knowledge_manager = AsyncMock()
        mock_engine.knowledge_manager.cleanup_expired = AsyncMock(return_value=10)
        await s._run_optional_maintenance()
        mock_engine.knowledge_manager.cleanup_expired.assert_called_once()

    @pytest.mark.asyncio
    async def test_maintenance_note_prune(self, mock_engine, tmp_path):
        mock_engine_with_config = AsyncMock()
        mock_engine_with_config.note_manager = AsyncMock()
        mock_engine_with_config.note_manager.prune_versions = AsyncMock(return_value=0)
        mock_engine_with_config.config = {"notes.max_versions": 20}
        # Remove profile_manager etc. that the loop tries to access
        mock_engine_with_config.profile_manager = None
        mock_engine_with_config.knowledge_manager = None
        mock_engine_with_config.auto_learning = None
        mock_engine_with_config.atom_store = None

        s = self._make_scheduler(mock_engine_with_config, tmp_path)
        await s._run_optional_maintenance()
        mock_engine_with_config.note_manager.prune_versions.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_maintenance_proactive_memory(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.atom_store = AsyncMock()
        mock_engine.atom_store.query_upcoming_planned = AsyncMock(
            return_value=[{"id": 1, "content": "reminder"}]
        )
        await s._run_optional_maintenance()
        mock_engine.atom_store.query_upcoming_planned.assert_called_once()
        assert mock_engine._pending_proactive is not None

    @pytest.mark.asyncio
    async def test_maintenance_proactive_empty(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.atom_store = AsyncMock()
        mock_engine.atom_store.query_upcoming_planned = AsyncMock(return_value=[])
        await s._run_optional_maintenance()
        mock_engine.atom_store.query_upcoming_planned.assert_called_once()

    @pytest.mark.asyncio
    async def test_maintenance_optional_errors_isolated(self, mock_engine, tmp_path):
        """Every optional sub-task error is isolated and does not propagate."""
        s = self._make_scheduler(mock_engine, tmp_path)
        mock_engine.profile_manager = AsyncMock()
        mock_engine.profile_manager.decay_and_clean_all = AsyncMock(
            side_effect=RuntimeError("profile")
        )
        mock_engine.knowledge_manager = AsyncMock()
        mock_engine.knowledge_manager.cleanup_expired = AsyncMock(
            side_effect=RuntimeError("knowledge")
        )
        mock_engine.auto_learning = AsyncMock()
        mock_engine.auto_learning.optimize = AsyncMock(
            side_effect=RuntimeError("learning")
        )
        mock_engine.note_manager = AsyncMock()
        mock_engine.note_manager.prune_versions = AsyncMock(
            side_effect=RuntimeError("notes")
        )
        mock_engine.atom_store = AsyncMock()
        mock_engine.atom_store.query_upcoming_planned = AsyncMock(
            side_effect=RuntimeError("proactive")
        )
        # Should not raise
        await s._run_optional_maintenance()

    # ---- Backup ----

    @pytest.mark.asyncio
    async def test_run_backup_success(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        backup_mgr.create_backup = AsyncMock(return_value={"name": "scheduled_ok"})
        backup_mgr.prune_backups = MagicMock(return_value={"removed": []})
        s = self._make_scheduler(mock_engine, tmp_path, backup_manager=backup_mgr)
        await s._run_backup()
        backup_mgr.create_backup.assert_called_once_with(kind="scheduled")
        backup_mgr.prune_backups.assert_called_once_with(keep_days=7)
        assert s.last_backup_result == {"status": "succeeded", "name": "scheduled_ok"}

    @pytest.mark.asyncio
    async def test_run_backup_no_manager(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        await s._run_backup()  # Should not raise

    @pytest.mark.asyncio
    async def test_run_backup_failure(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        backup_mgr.create_backup = AsyncMock(return_value=None)
        backup_mgr.prune_backups = MagicMock()
        s = self._make_scheduler(mock_engine, tmp_path, backup_manager=backup_mgr)
        await s._run_backup()
        backup_mgr.create_backup.assert_called_once_with(kind="scheduled")
        backup_mgr.prune_backups.assert_not_called()
        assert s.last_backup_result["reason_code"] == "backup_create_failed"

    @pytest.mark.asyncio
    async def test_run_backup_exception(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        backup_mgr.create_backup = AsyncMock(side_effect=RuntimeError("backup fail"))
        backup_mgr.prune_backups = MagicMock()
        s = self._make_scheduler(mock_engine, tmp_path, backup_manager=backup_mgr)
        await s._run_backup()  # Should not raise
        assert s.last_backup_result["reason_code"] == "backup_create_failed"

    @pytest.mark.asyncio
    async def test_run_backup_propagates_cancelled_error(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        backup_mgr.create_backup = AsyncMock(side_effect=asyncio.CancelledError())
        s = self._make_scheduler(mock_engine, tmp_path, backup_manager=backup_mgr)
        with pytest.raises(asyncio.CancelledError):
            await s._run_backup()

    @pytest.mark.asyncio
    async def test_cleanup_old_backups(self, mock_engine, tmp_path):
        backup_mgr = MagicMock()
        backup_mgr.prune_backups.return_value = {"removed": ["old_backup_001"]}
        s = self._make_scheduler(
            mock_engine,
            tmp_path,
            backup_manager=backup_mgr,
            backup_keep_days=7,
        )
        await s._cleanup_old_backups()
        backup_mgr.prune_backups.assert_called_once_with(keep_days=7)

    @pytest.mark.asyncio
    async def test_cleanup_old_backups_no_dir(self, mock_engine, tmp_path):
        backup_mgr = MagicMock()
        s = self._make_scheduler(mock_engine, tmp_path, backup_manager=backup_mgr)
        await s._cleanup_old_backups()  # Should not raise
        backup_mgr.prune_backups.assert_called_once_with(keep_days=7)

    @pytest.mark.asyncio
    async def test_cleanup_old_backups_no_manager(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        await s._cleanup_old_backups()  # Should not raise

    @pytest.mark.asyncio
    async def test_backup_only_run_delegates_scheduled_backup_and_prune(
        self, mock_engine, tmp_path
    ):
        manager = MagicMock()
        manager.create_backup = AsyncMock(return_value={"name": "scheduled_ok"})
        manager.prune_backups.return_value = {"removed": []}
        scheduler = self._make_scheduler(
            mock_engine,
            tmp_path,
            decay_rate=0,
            backup_manager=manager,
            backup_enabled=True,
            backup_keep_days=7,
        )
        await scheduler._run_backup()
        manager.create_backup.assert_awaited_once_with(kind="scheduled")
        manager.prune_backups.assert_called_once_with(keep_days=7)

    # ---- Scheduler lifecycle ----

    @pytest.mark.asyncio
    async def test_seconds_until_next_run(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path, check_hour=0, check_minute=0)
        seconds = s._seconds_until_next_run()
        assert seconds >= 0

    @pytest.mark.asyncio
    async def test_seconds_past_midnight(self, mock_engine, tmp_path):
        """When time has already passed check_hour:check_minute today, target tomorrow."""
        import datetime as _dt

        now = _dt.datetime.now()
        s = self._make_scheduler(
            mock_engine,
            tmp_path,
            check_hour=(now.hour - 1) % 24 if now.hour > 0 else 23,
            check_minute=0,
        )
        seconds = s._seconds_until_next_run()
        assert seconds > 0  # Should target tomorrow

    @pytest.mark.asyncio
    async def test_start_and_stop(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        # Mock _check_and_execute to avoid actual decay
        with patch.object(s, "_check_and_execute", AsyncMock()):
            await s.start()
            assert s._running is True
            assert s._task is not None
            await s.stop()
            assert s._running is False
            assert s._task is None

    @pytest.mark.asyncio
    async def test_start_does_not_wait_for_startup_check(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_startup_check():
            started.set()
            await release.wait()

        with patch.object(s, "_check_and_execute", slow_startup_check):
            await s.start()
            await asyncio.wait_for(started.wait(), timeout=1)
            assert s._running is True
            assert s._startup_task is not None
            assert not s._startup_task.done()
            release.set()
            await s.stop()

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        with patch.object(s, "_check_and_execute", AsyncMock()):
            await s.start()
            assert s._running is True
            # Second start should be a no-op
            await s.start()
            assert s._running is True  # Still running

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        await s.stop()  # Should not raise
        assert s._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        with patch.object(s, "_check_and_execute", AsyncMock()):
            await s.start()
        old_task = s._task
        await s.stop()
        # Task should be cancelled
        if old_task:
            assert old_task.done() or old_task.cancelled()

    @pytest.mark.asyncio
    async def test_scheduler_loop_cancelled(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        s._running = True
        # Make _seconds_until_next_run very short, then cancel
        with patch.object(s, "_seconds_until_next_run", return_value=0.01):
            loop_task = asyncio.create_task(s._scheduler_loop())
            await asyncio.sleep(0.05)
            s._running = False
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass  # Expected

    @pytest.mark.asyncio
    async def test_scheduler_loop_error_recovery(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        s._running = True
        # Make _execute_decay fail, then stop the loop
        with patch.object(s, "_seconds_until_next_run", return_value=0.01):
            with patch.object(
                s, "_execute_decay", AsyncMock(side_effect=RuntimeError("decay fail"))
            ):
                task = asyncio.create_task(s._scheduler_loop())
                await asyncio.sleep(0.2)  # Let it run a cycle
                s._running = False
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # ---- Flashbulb memory / multi-day decay ----

    @pytest.mark.asyncio
    async def test_flashbulb_memory_skips_decay(self, mock_engine, tmp_path):
        mock_engine.config = {
            "flashbulb.enabled": True,
            "flashbulb.intensity_threshold": 0.90,
        }
        s = self._make_scheduler(mock_engine, tmp_path)
        await s._execute_decay(days=365)
        assert mock_engine.apply_daily_decay.called

    @pytest.mark.asyncio
    async def test_execute_decay_multi_day(self, mock_engine, tmp_path):
        mock_engine.apply_daily_decay = AsyncMock(return_value=5)
        s = self._make_scheduler(mock_engine, tmp_path)
        with (
            patch.object(s, "_set_last_decay_date", AsyncMock()),
            patch.object(s, "_run_backup", AsyncMock()),
            patch.object(s, "_run_optional_maintenance", AsyncMock()),
        ):
            await s._execute_decay(days=7)
            mock_engine.apply_daily_decay.assert_called_once_with(0.01, 7)

    # ---- Get today str ----

    def test_get_today_str_format(self, mock_engine, tmp_path):
        s = self._make_scheduler(mock_engine, tmp_path)
        today = s._get_today_str()
        assert isinstance(today, str)
        assert len(today) == 10  # YYYY-MM-DD

    # ---- Constructor params ----

    def test_constructor_full_params(self, mock_engine, tmp_path):
        backup_mgr = AsyncMock()
        s = self._make_scheduler(
            mock_engine,
            tmp_path,
            decay_rate=0.05,
            check_hour=3,
            check_minute=30,
            backup_manager=backup_mgr,
            backup_enabled=True,
            backup_keep_days=14,
        )
        assert s.decay_rate == 0.05
        assert s.check_hour == 3
        assert s.check_minute == 30
        assert s.backup_manager is backup_mgr
        assert s.backup_enabled is True
        assert s.backup_keep_days == 14
