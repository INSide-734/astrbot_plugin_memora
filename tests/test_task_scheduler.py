"""测试 core/utils/task_scheduler.py — TaskScheduler + _NoOpScheduler."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.platform.task_scheduler import (
    TaskScheduler,
    _NoOpScheduler,
    get_task_scheduler,
)

# ---------------------------------------------------------------------------
# _NoOpScheduler
# ---------------------------------------------------------------------------


class TestNoOpScheduler:
    def test_add_interval_job(self) -> None:
        s = _NoOpScheduler()
        job_id = s.add_interval_job(lambda: None, seconds=30, job_id="my_job")
        assert job_id == "my_job"
        assert "my_job" in s._jobs
        assert s._jobs["my_job"]["type"] == "interval"
        assert s._jobs["my_job"]["interval_seconds"] == 30

    def test_add_interval_job_auto_id(self) -> None:
        s = _NoOpScheduler()

        def my_func() -> None:
            pass

        job_id = s.add_interval_job(my_func, minutes=5)
        assert "my_func" in job_id
        assert s._jobs[job_id]["interval_seconds"] == 300

    def test_add_cron_job(self) -> None:
        s = _NoOpScheduler()
        job_id = s.add_cron_job(lambda: None, hour=3, minute=30, job_id="cron_1")
        assert job_id == "cron_1"
        assert s._jobs["cron_1"]["type"] == "cron"
        assert s._jobs["cron_1"]["hour"] == 3
        assert s._jobs["cron_1"]["minute"] == 30

    def test_add_date_job(self) -> None:
        s = _NoOpScheduler()
        run_date = datetime(2026, 7, 1, 12, 0, 0)
        job_id = s.add_date_job(lambda: None, run_date=run_date, job_id="once")
        assert job_id == "once"
        assert s._jobs["once"]["type"] == "date"
        assert "2026-07-01" in s._jobs["once"]["run_date"]

    def test_remove_job(self) -> None:
        s = _NoOpScheduler()
        s.add_interval_job(lambda: None, seconds=10, job_id="to_remove")
        assert "to_remove" in s._jobs
        s.remove_job("to_remove")
        assert "to_remove" not in s._jobs

    def test_remove_nonexistent_job(self) -> None:
        s = _NoOpScheduler()
        s.remove_job("nonexistent")  # should not raise

    def test_pause_resume_noop(self) -> None:
        s = _NoOpScheduler()
        s.add_interval_job(lambda: None, seconds=10, job_id="j1")
        s.pause_job("j1")  # no-op, should not raise
        s.resume_job("j1")  # no-op, should not raise

    def test_get_job_stats(self) -> None:
        s = _NoOpScheduler()
        s.add_interval_job(lambda: None, seconds=10, job_id="stats_int")
        s.add_cron_job(lambda: None, hour=3, minute=0, job_id="stats_cron")
        s.add_date_job(lambda: None, run_date=datetime.now(), job_id="stats_date")

        stats = s.get_job_stats()
        assert stats["interval"] == 1
        assert stats["cron"] == 1
        assert stats["date"] == 1


# ---------------------------------------------------------------------------
# TaskScheduler — APScheduler path
# ---------------------------------------------------------------------------


class TestTaskSchedulerWithAPScheduler:
    @pytest.fixture
    def ts_with_mock(self) -> TaskScheduler:
        """创建 TaskScheduler with mocked APScheduler internals."""
        mock_sched = MagicMock()
        ts = TaskScheduler.__new__(TaskScheduler)
        ts._scheduler = mock_sched
        ts._noop = _NoOpScheduler()
        ts._available = True
        return ts

    def test_add_interval_job(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock

        def foo() -> None:
            pass

        ts.add_interval_job(foo, seconds=60, job_id="int_job")
        ts._scheduler.add_job.assert_called_once()
        kwargs = ts._scheduler.add_job.call_args[1]
        assert kwargs["trigger"] == "interval"
        assert kwargs["seconds"] == 60
        assert kwargs["id"] == "int_job"

    def test_add_cron_job(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock

        def bar() -> None:
            pass

        ts.add_cron_job(bar, hour=4, minute=15, job_id="cron_job")
        ts._scheduler.add_job.assert_called_once()
        kwargs = ts._scheduler.add_job.call_args[1]
        assert kwargs["trigger"] == "cron"
        assert kwargs["hour"] == 4
        assert kwargs["minute"] == 15

    def test_add_date_job(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock
        run_date = datetime(2026, 6, 24, 18, 0, 0)

        def once() -> None:
            pass

        ts.add_date_job(once, run_date=run_date, job_id="date_job")
        ts._scheduler.add_job.assert_called_once()
        kwargs = ts._scheduler.add_job.call_args[1]
        assert kwargs["trigger"] == "date"
        assert kwargs["run_date"] == run_date

    def test_remove_job(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock
        ts.remove_job("some_job")
        ts._scheduler.remove_job.assert_called_once_with("some_job")

    def test_pause_job(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock
        ts.pause_job("pause_me")
        ts._scheduler.pause_job.assert_called_once_with("pause_me")

    def test_resume_job(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock
        ts.resume_job("resume_me")
        ts._scheduler.resume_job.assert_called_once_with("resume_me")

    def test_get_job_stats(self, ts_with_mock: TaskScheduler) -> None:
        ts = ts_with_mock
        mock_job = MagicMock()
        mock_job.trigger = MagicMock()
        type(mock_job.trigger).__name__ = "IntervalTrigger"
        ts._scheduler.get_jobs.return_value = [mock_job]

        stats = ts.get_job_stats()
        assert "IntervalTrigger" in stats


# ---------------------------------------------------------------------------
# TaskScheduler — no-op fallback
# ---------------------------------------------------------------------------


class TestTaskSchedulerFallback:
    def test_fallback_when_import_fails(self) -> None:
        with patch(
            "core.platform.task_scheduler.TaskScheduler._init_scheduler",
            return_value=None,
        ):
            ts = TaskScheduler.__new__(TaskScheduler)
            ts._scheduler = None
            ts._noop = _NoOpScheduler()
            ts._available = False

            job_id = ts.add_interval_job(lambda: None, seconds=30, job_id="fb")
            assert job_id == "fb"
            assert ts._noop._jobs["fb"]["type"] == "interval"

    def test_remove_job_in_fallback(self) -> None:
        ts = TaskScheduler.__new__(TaskScheduler)
        ts._scheduler = None
        ts._noop = _NoOpScheduler()
        ts._available = False
        ts.add_interval_job(lambda: None, seconds=10, job_id="to_remove")
        ts.remove_job("to_remove")
        assert "to_remove" not in ts._noop._jobs

    def test_get_job_stats_in_fallback(self) -> None:
        ts = TaskScheduler.__new__(TaskScheduler)
        ts._scheduler = None
        ts._noop = _NoOpScheduler()
        ts._available = False
        ts.add_interval_job(lambda: None, seconds=10, job_id="fb_int")
        ts.add_cron_job(lambda: None, hour=3, minute=0, job_id="fb_cron")
        stats = ts.get_job_stats()
        assert stats["interval"] == 1
        assert stats["cron"] == 1


# ---------------------------------------------------------------------------
# get_task_scheduler singleton
# ---------------------------------------------------------------------------


class TestGetTaskScheduler:
    def test_returns_singleton(self) -> None:
        with patch(
            "core.platform.task_scheduler.TaskScheduler._init_scheduler",
            return_value=None,
        ):
            ts1 = get_task_scheduler()
            ts2 = get_task_scheduler()
            assert ts1 is ts2
