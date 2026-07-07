"""
标准化任务调度器，提供 APScheduler 包装与空实现降级。

设计原则：
- 优先使用 APScheduler (BackgroundScheduler)
- APScheduler 不可用时降级为 _NoOpScheduler（任务会被记录但不执行）
- 全局单例模式
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 空实现降级
# ---------------------------------------------------------------------------


class _NoOpScheduler:
    """APScheduler 不可用时的降级方案。

    任务信息会记录到日志，但不会实际执行。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}

    def add_interval_job(
        self,
        func: Callable,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        job_id: str | None = None,
    ) -> str:
        jid = job_id or _make_job_id(func)
        interval_s = seconds + minutes * 60 + hours * 3600
        self._jobs[jid] = {
            "type": "interval",
            "func": func.__qualname__,
            "interval_seconds": interval_s,
        }
        logger.warning(
            f"[任务调度器-空实现] 已注册周期任务 '{jid}' "
            f"（间隔={interval_s} 秒），但 APScheduler 不可用，因此不会执行。"
        )
        return jid

    def add_cron_job(
        self,
        func: Callable,
        hour: int | None = None,
        minute: int | None = None,
        job_id: str | None = None,
    ) -> str:
        jid = job_id or _make_job_id(func)
        self._jobs[jid] = {
            "type": "cron",
            "func": func.__qualname__,
            "hour": hour,
            "minute": minute,
        }
        logger.warning(
            f"[任务调度器-空实现] 已注册 Cron 任务 '{jid}'，"
            "但 APScheduler 不可用，因此不会执行。"
        )
        return jid

    def add_date_job(
        self,
        func: Callable,
        run_date: datetime,
        job_id: str | None = None,
    ) -> str:
        jid = job_id or _make_job_id(func)
        self._jobs[jid] = {
            "type": "date",
            "func": func.__qualname__,
            "run_date": run_date.isoformat(),
        }
        logger.warning(
            f"[任务调度器-空实现] 已注册一次性任务 '{jid}' "
            f"（执行时间={run_date.isoformat()}），但 APScheduler 不可用，因此不会执行。"
        )
        return jid

    def remove_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def pause_job(self, job_id: str) -> None:
        logger.debug(f"[任务调度器-空实现] 忽略暂停任务 '{job_id}' 的请求。")

    def resume_job(self, job_id: str) -> None:
        logger.debug(f"[任务调度器-空实现] 忽略恢复任务 '{job_id}' 的请求。")

    def get_job_stats(self) -> dict[str, int]:
        types: dict[str, int] = {}
        for info in self._jobs.values():
            t = info["type"]
            types[t] = types.get(t, 0) + 1
        return types


# ---------------------------------------------------------------------------
# TaskScheduler
# ---------------------------------------------------------------------------


class TaskScheduler:
    """APScheduler 包装器。

    配置：
    - coalesce=False (不合并错过的任务)
    - max_instances=1 (每个 job 最多 1 个并发实例)
    - misfire_grace_time=60 (错过 60s 内仍触发)
    - timezone='Asia/Shanghai'

    用法::

        scheduler = get_task_scheduler()
        scheduler.add_interval_job(my_func, minutes=30, job_id="cleanup")
        scheduler.add_cron_job(my_func, hour=3, minute=0, job_id="daily")
    """

    def __init__(self) -> None:
        self._scheduler: Any = None
        self._noop = _NoOpScheduler()
        self._available = False
        self._init_scheduler()

    def _init_scheduler(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            self._scheduler = AsyncIOScheduler(
                timezone="Asia/Shanghai",
                job_defaults={
                    "coalesce": False,
                    "max_instances": 1,
                    "misfire_grace_time": 60,
                },
            )
            self._scheduler.start()
            self._available = True
            logger.info("[任务调度器] APScheduler（AsyncIOScheduler）已启动")
        except ImportError:
            self._available = False
            logger.warning(
                "[任务调度器] APScheduler 不可用，已降级为空实现模式"
                "（任务会被记录但不执行）"
            )
        except Exception as exc:
            self._available = False
            logger.warning(
                f"[任务调度器] APScheduler 启动失败（{exc}），已降级为空实现模式。"
            )

    def add_interval_job(
        self,
        func: Callable,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        job_id: str | None = None,
    ) -> str:
        """注册周期性任务。

        参数：
            func: 异步或同步可调用对象。
            seconds: 间隔秒数。
            minutes: 间隔分钟数。
            hours: 间隔小时数。
            job_id: 可选的任务 ID，用于后续管理。

        返回：
            注册后的任务 ID。
        """
        if not self._available:
            return self._noop.add_interval_job(
                func, seconds=seconds, minutes=minutes, hours=hours, job_id=job_id
            )

        jid = job_id or _make_job_id(func)
        self._scheduler.add_job(
            func,
            trigger="interval",
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            id=jid,
            replace_existing=True,
        )
        logger.debug(f"[任务调度器] 已注册周期任务 '{jid}'。")
        return jid

    def add_cron_job(
        self,
        func: Callable,
        hour: int | None = None,
        minute: int | None = None,
        job_id: str | None = None,
    ) -> str:
        """注册 Cron 任务。

        参数：
            func: 异步或同步可调用对象。
            hour: 小时（0-23）。
            minute: 分钟（0-59）。
            job_id: 可选的任务 ID。

        返回：
            注册后的任务 ID。
        """
        if not self._available:
            return self._noop.add_cron_job(func, hour=hour, minute=minute, job_id=job_id)

        jid = job_id or _make_job_id(func)
        self._scheduler.add_job(
            func,
            trigger="cron",
            hour=hour,
            minute=minute,
            id=jid,
            replace_existing=True,
        )
        logger.debug(f"[任务调度器] 已注册 Cron 任务 '{jid}'。")
        return jid

    def add_date_job(
        self,
        func: Callable,
        run_date: datetime,
        job_id: str | None = None,
    ) -> str:
        """注册一次性任务。

        参数：
            func: 异步或同步可调用对象。
            run_date: 执行时间。
            job_id: 可选的任务 ID。

        返回：
            注册后的任务 ID。
        """
        if not self._available:
            return self._noop.add_date_job(func, run_date, job_id=job_id)

        jid = job_id or _make_job_id(func)
        self._scheduler.add_job(
            func,
            trigger="date",
            run_date=run_date,
            id=jid,
            replace_existing=True,
        )
        logger.debug(
            f"[任务调度器] 已注册一次性任务 '{jid}'，执行时间为 {run_date.isoformat()}。"
        )
        return jid

    def remove_job(self, job_id: str) -> None:
        """移除任务。

        参数：
            job_id: 要移除的任务 ID。
        """
        if self._available and self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        else:
            self._noop.remove_job(job_id)

    def pause_job(self, job_id: str) -> None:
        """暂停任务。

        参数：
            job_id: 要暂停的任务 ID。
        """
        if self._available and self._scheduler:
            try:
                self._scheduler.pause_job(job_id)
            except Exception:
                pass
        else:
            self._noop.pause_job(job_id)

    def resume_job(self, job_id: str) -> None:
        """恢复任务。

        参数：
            job_id: 要恢复的任务 ID。
        """
        if self._available and self._scheduler:
            try:
                self._scheduler.resume_job(job_id)
            except Exception:
                pass
        else:
            self._noop.resume_job(job_id)

    def get_job_stats(self) -> dict[str, int]:
        """获取任务统计信息。

        返回：
            形如 `{job_type: count}` 的字典。
        """
        if self._available and self._scheduler:
            jobs = self._scheduler.get_jobs()
            types: dict[str, int] = {}
            for job in jobs:
                name = getattr(job.trigger, "__class__", type(job.trigger)).__name__
                types[name] = types.get(name, 0) + 1
            return types
        return self._noop.get_job_stats()


def _make_job_id(func: Callable) -> str:
    """生成默认任务 ID。"""
    return f"{func.__module__}.{func.__qualname__}"


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_task_scheduler: TaskScheduler | None = None


def get_task_scheduler() -> TaskScheduler:
    """获取 TaskScheduler 全局单例。

    返回：
        TaskScheduler 实例。
    """
    global _task_scheduler
    if _task_scheduler is None:
        _task_scheduler = TaskScheduler()
    return _task_scheduler


__all__ = [
    "TaskScheduler",
    "get_task_scheduler",
    "_NoOpScheduler",
]
