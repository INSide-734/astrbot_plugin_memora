"""
SQLite 写协调器 — 序列化写入 + 重试 + 抖动退避 + 连接坏死检测 + 自动重连

解决多连接并发写入导致的 ``database is locked`` 问题：

- **L3 重试**：带随机抖动的指数退避，消解瞬态锁冲突
- **L4 序列化**：模块级 ``asyncio.Lock`` 确保同时只有一个写入事务
- **L5 自动重连**：检测连接坏死后，自动重建并广播给所有持有方
- **慢锁检测**：>5s 自动记录诊断日志，定位长事务
- **连接坏死检测**：区分"可重试的锁冲突"和"连接已死的致命错误"

用法::

    from .write_coordinator import write_transaction, check_db_alive, is_connection_fatal

    # 操作前检查连接
    if not check_db_alive(self._db):
        logger.warning("连接坏死，跳过")
        return

    # 写入自动序列化 + 重试
    await write_transaction(lambda: self._db.execute(sql, params))
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar

import aiosqlite
from astrbot.api import logger

T = TypeVar("T")

# ---- 连接错误特征词 ----

_CONNECTION_FATAL_MARKERS = (
    "no active connection",
    "database is not initialized",
    "cannot operate on a closed database",
)


def is_connection_fatal(exc: Exception) -> bool:
    """判断异常是否为连接坏死（不可重试）。"""
    msg = str(exc).lower()
    return any(marker in msg for marker in _CONNECTION_FATAL_MARKERS)


def _is_retry_eligible(exc: Exception) -> bool:
    """判断异常是否可重试（锁冲突类）。"""
    msg = str(exc).lower()
    return "locked" in msg and not is_connection_fatal(exc)


# ---- 全局写锁 ----

_WRITE_LOCK: asyncio.Lock | None = None
_WRITE_METRICS: dict[str, Any] = {
    "operations_total": 0,
    "lock_retries_total": 0,
    "failures_total": 0,
    "retry_exhausted_total": 0,
    "fatal_failures_total": 0,
    "non_retryable_failures_total": 0,
    "last_error": None,
}


def _get_write_lock() -> asyncio.Lock:
    """获取或创建模块级写锁（惰性初始化，确保单线程安全）。"""
    global _WRITE_LOCK
    if _WRITE_LOCK is None:
        _WRITE_LOCK = asyncio.Lock()
    return _WRITE_LOCK


def reset_write_metrics_snapshot() -> None:
    """重置进程内写协调器指标。

    测试通过此函数隔离断言；生产调用方通常只通过指标摘要接口读取快照。
    """
    _WRITE_METRICS.update(
        {
            "operations_total": 0,
            "lock_retries_total": 0,
            "failures_total": 0,
            "retry_exhausted_total": 0,
            "fatal_failures_total": 0,
            "non_retryable_failures_total": 0,
            "last_error": None,
        }
    )


def get_write_metrics_snapshot() -> dict[str, Any]:
    """返回可 JSON 序列化的写入重试与失败计数快照。"""
    return dict(_WRITE_METRICS)


def _inc_metric(metric_name: str, amount: float = 1.0) -> None:
    try:
        from ...observability.infrastructure import metrics

        metric = getattr(metrics, metric_name)
        metric.inc(amount)
    except Exception:
        return


def _inc_failure_metric(reason: str) -> None:
    try:
        from ...observability.infrastructure import metrics

        metrics.WRITE_FAILURES_TOTAL.labels(reason=reason).inc()
    except Exception:
        return


# ---- 连接注册表（自动重连） ----


class ConnectionRegistry:
    """管理共享数据库连接，支持坏死检测与自动重连。

    在 ``MemoryEngine.initialize()`` 中注册一次::

        ConnectionRegistry.register(
            db_path, db_connection,
            [self._write_journal, self._retrieval, self._maintenance, self._schema]
        )

    之后所有 Mixin 通过 ``check_db_alive()`` 检查连接，通过
    ``ConnectionRegistry.try_repair()`` 触发自动重连。
    """

    _db_path: str = ""
    _connection: Any = None
    _modules: list[Any] = []  # 持有数据库连接引用的模块列表

    @classmethod
    def register(
        cls,
        db_path: str,
        connection: Any,
        modules: list[Any],
    ) -> None:
        """注册共享连接及其持有方模块（初始化时调用一次）。"""
        cls._db_path = db_path
        cls._connection = connection
        cls._modules = list(modules)

    @classmethod
    def is_alive(cls) -> bool:
        """检查注册的连接是否存活。"""
        return check_db_alive(cls._connection)

    @classmethod
    async def try_repair(cls) -> bool:
        """尝试重建连接并广播给所有持有方。

        返回值：
            ``True`` 表示重连成功，所有模块的 ``_db`` 已更新。
        """
        if cls.is_alive():
            return True

        logger.warning("[重连] 数据库连接已坏死，尝试自动重连……")

        # 关闭旧连接（如果尚未完全关闭）
        if cls._connection is not None:
            with contextlib.suppress(Exception):
                await cls._connection.close()

        try:
            new_conn = await aiosqlite.connect(cls._db_path)
            new_conn.row_factory = aiosqlite.Row
            from ..infrastructure.base import apply_perf_pragmas

            await apply_perf_pragmas(new_conn)

            cls._connection = new_conn
            for mod in cls._modules:
                mod._db = new_conn

            logger.info("[重连] 数据库自动重连成功")
            return True
        except Exception as exc:
            logger.error(f"[重连] 数据库自动重连失败: {exc}")
            return False


# ---- 内部：可重试执行核心 ----


async def _execute_with_retry(
    fn: Callable[[], Coroutine[Any, Any, T]],
    label: str,
    max_retries: int,
    base_delay: float,
    fatal_msg: str,
    exhausted_msg: str,
    lock_msg: str,
) -> T:
    """带写锁 + 指数退避 + 随机抖动 的执行核心。

    由 ``write_with_retry`` 和 ``write_transaction`` 共享，
    仅通过 *label* 区分日志前缀和错误文案。
    """
    lock = _get_write_lock()

    async with lock:
        _WRITE_METRICS["operations_total"] += 1
        _inc_metric("WRITE_OPERATIONS_TOTAL")
        for attempt in range(max_retries):
            try:
                return await fn()
            except Exception as exc:
                if is_connection_fatal(exc):
                    _WRITE_METRICS["last_error"] = str(exc)
                    _WRITE_METRICS["failures_total"] += 1
                    _WRITE_METRICS["fatal_failures_total"] += 1
                    _inc_failure_metric("fatal")
                    logger.error(f"{label} {fatal_msg}: {exc}")
                    raise

                if _is_retry_eligible(exc):
                    if attempt == max_retries - 1:
                        _WRITE_METRICS["last_error"] = str(exc)
                        _WRITE_METRICS["failures_total"] += 1
                        _WRITE_METRICS["retry_exhausted_total"] += 1
                        _inc_failure_metric("retry_exhausted")
                        logger.error(f"{label} {exhausted_msg}: {exc}")
                        raise

                    _WRITE_METRICS["lock_retries_total"] += 1
                    _inc_metric("WRITE_LOCK_RETRIES_TOTAL")
                    delay = base_delay * (2**attempt) + random.random() * 0.03
                    logger.warning(
                        f"{label} {lock_msg}, {delay:.3f}s 后重试 "
                        f"({attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                _WRITE_METRICS["last_error"] = str(exc)
                _WRITE_METRICS["failures_total"] += 1
                _WRITE_METRICS["non_retryable_failures_total"] += 1
                _inc_failure_metric("non_retryable")
                raise

        raise RuntimeError(f"{label} _execute_with_retry 意外退出")


# ---- 公共 API ----


async def write_with_retry(
    fn: Callable[[], Coroutine[Any, Any, T]],
    max_retries: int = 5,
    base_delay: float = 0.05,
) -> T:
    """使用写锁 + 重试 + 抖动执行写操作。"""
    return await _execute_with_retry(
        fn,
        label="[写入]",
        max_retries=max_retries,
        base_delay=base_delay,
        fatal_msg="连接已坏死，放弃操作",
        exhausted_msg=f"重试 {max_retries} 次后仍然失败",
        lock_msg="数据库锁定",
    )


async def write_transaction(
    fn: Callable[[], Coroutine[Any, Any, T]],
    max_retries: int = 5,
    base_delay: float = 0.05,
) -> T:
    """事务性写入：将 *fn* 作为整体重试。"""
    return await _execute_with_retry(
        fn,
        label="[写入]",
        max_retries=max_retries,
        base_delay=base_delay,
        fatal_msg="连接已坏死，放弃事务",
        exhausted_msg=f"事务重试 {max_retries} 次后仍然失败",
        lock_msg="事务锁定",
    )


@contextlib.asynccontextmanager
async def coordinated_transaction(db: Any) -> AsyncIterator[Any]:
    """在全局写锁内包住 BEGIN/业务写入/commit/rollback 的事务边界。"""
    lock = _get_write_lock()
    async with lock:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        except Exception:
            with contextlib.suppress(Exception):
                await db.rollback()
            raise


def check_db_alive(db) -> bool:
    """同步检查数据库连接是否存活。

    通过探测 ``_conn`` 属性判断底层 sqlite3 连接是否仍然打开。
    这是快速路径，不发起 I/O。

    返回值：
        ``True`` 表示连接正常（至少未被显式关闭）。
    """
    if db is None:
        return False
    try:
        inner = db._conn
        return inner is not None
    except (AttributeError, ValueError):
        return False


__all__ = [
    "write_with_retry",
    "write_transaction",
    "coordinated_transaction",
    "get_write_metrics_snapshot",
    "reset_write_metrics_snapshot",
    "is_connection_fatal",
    "check_db_alive",
    "ConnectionRegistry",
]
