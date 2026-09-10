"""测试写协调器的连接健康、重试与锁行为。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

import core.features.observability.infrastructure.metrics as monitoring_metrics
from core.features.memory.application.write_coordinator import (
    ConnectionRegistry,
    check_db_alive,
    coordinated_transaction,
    get_write_metrics_snapshot,
    is_connection_fatal,
    reset_write_metrics_snapshot,
    write_transaction,
    write_with_retry,
)


def _metric_sample_value(
    sample_name: str, labels: dict[str, str] | None = None
) -> float:
    labels = labels or {}
    for metric in monitoring_metrics.REGISTRY.collect():
        for sample in metric.samples:
            if sample.name != sample_name:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


class TestIsConnectionFatal:
    """测试 is_connection_fatal."""

    def test_detects_no_active_connection(self) -> None:
        """应识别没有活动连接的致命错误。"""
        assert is_connection_fatal(Exception("no active connection")) is True

    def test_detects_database_not_initialized(self) -> None:
        """应识别数据库未初始化的致命错误。"""
        assert is_connection_fatal(Exception("database is not initialized")) is True

    def test_detects_closed_database(self) -> None:
        """应识别数据库已关闭的致命错误。"""
        assert (
            is_connection_fatal(Exception("cannot operate on a closed database"))
            is True
        )

    def test_case_insensitive(self) -> None:
        """致命错误匹配应忽略大小写。"""
        assert is_connection_fatal(Exception("No Active Connection")) is True

    def test_normal_error_not_fatal(self) -> None:
        """普通锁冲突和未知错误不应视为连接致命错误。"""
        assert is_connection_fatal(Exception("database is locked")) is False
        assert is_connection_fatal(Exception("some other error")) is False

    def test_empty_message(self) -> None:
        """空异常消息不应视为连接致命错误。"""
        assert is_connection_fatal(Exception("")) is False


class TestCheckDbAlive:
    """测试 check_db_alive。"""

    def test_none_is_dead(self) -> None:
        """空数据库对象应判定为不可用。"""
        assert check_db_alive(None) is False

    def test_live_connection(self) -> None:
        """存在底层连接时应判定数据库可用。"""
        db = MagicMock()
        db._conn = MagicMock()  # 提供非空底层连接。
        assert check_db_alive(db) is True

    def test_closed_connection(self) -> None:
        """底层连接为空时应判定数据库不可用。"""
        db = MagicMock()
        db._conn = None
        assert check_db_alive(db) is False

    def test_missing_conn_attr(self) -> None:
        """缺少底层连接属性时应判定数据库不可用。"""
        db = MagicMock(spec=[])  # 不提供 _conn 属性。
        assert check_db_alive(db) is False

    def test_value_error_treated_as_dead(self) -> None:
        """读取连接属性触发 ValueError 时应判定数据库不可用。"""
        db = MagicMock()
        type(db)._conn = property(
            lambda self: (_ for _ in ()).throw(ValueError("boom"))
        )
        assert check_db_alive(db) is False


class TestConnectionRegistry:
    """测试 ConnectionRegistry 类方法。"""

    def test_register_sets_state(self) -> None:
        """注册连接时应保存路径、连接和关联模块。"""
        mock_conn = MagicMock()
        mod_a = MagicMock()
        mod_b = MagicMock()
        ConnectionRegistry.register("test.db", mock_conn, [mod_a, mod_b])
        assert ConnectionRegistry._db_path == "test.db"
        assert ConnectionRegistry._connection is mock_conn
        assert len(ConnectionRegistry._modules) == 2

    def test_is_alive_delegates_to_check_db_alive(self) -> None:
        """存活检查应委托给数据库连接检查函数。"""
        db = MagicMock()
        db._conn = MagicMock()
        ConnectionRegistry.register("test.db", db, [])
        assert ConnectionRegistry.is_alive() is True

    def test_is_alive_detects_none(self) -> None:
        """注册空连接后应报告不可用。"""
        ConnectionRegistry.register("test.db", None, [])
        assert ConnectionRegistry.is_alive() is False

    @pytest.mark.asyncio
    async def test_try_repair_when_alive_returns_true(self) -> None:
        """连接仍可用时修复应直接成功。"""
        db = MagicMock()
        db._conn = MagicMock()
        ConnectionRegistry.register("test.db", db, [])
        assert await ConnectionRegistry.try_repair() is True

    @pytest.mark.asyncio
    async def test_try_repair_reconnects(self, tmp_db_path: str) -> None:
        """连接失效时修复应重新连接并更新关联模块。"""
        # 为重连测试创建真实数据库文件。
        async with aiosqlite.connect(tmp_db_path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
            await db.commit()

        # 注册一个已经失效的模拟连接。
        dead_db = MagicMock()
        dead_db._conn = None
        mod = MagicMock()
        ConnectionRegistry.register(tmp_db_path, dead_db, [mod])

        result = await ConnectionRegistry.try_repair()
        assert result is True
        assert mod._db is not None
        assert mod._db is ConnectionRegistry._connection

        # 释放重连测试创建的连接。
        if ConnectionRegistry._connection is not None:
            await ConnectionRegistry._connection.close()

    @pytest.mark.asyncio
    async def test_try_repair_handles_reconnect_failure(self) -> None:
        """重连失败时修复应关闭旧连接并返回失败。"""
        old_mock = MagicMock()
        old_mock._conn = None  # 确保存活检查返回 False。
        ConnectionRegistry.register("nonexistent.db", old_mock, [])

        # 连接会失败，但仍需确认旧连接已尝试关闭。
        with patch("aiosqlite.connect", side_effect=Exception("cannot connect")):
            result = await ConnectionRegistry.try_repair()
            assert result is False


class TestWriteWithRetry:
    """测试 write_with_retry。"""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self) -> None:
        """操作首次成功时不应重试。"""

        async def op() -> str:
            return "ok"

        result = await write_with_retry(op, max_retries=3)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retry_on_lock_then_succeed(self) -> None:
        """锁冲突后应重试并返回后续成功结果。"""
        reset_write_metrics_snapshot()
        call_count = [0]

        async def op() -> str:
            call_count[0] += 1
            if call_count[0] < 2:
                raise Exception("database is locked")
            return "ok"

        result = await write_with_retry(op, max_retries=3, base_delay=0.001)
        assert result == "ok"
        assert call_count[0] == 2
        snapshot = get_write_metrics_snapshot()
        assert snapshot["failures_total"] == 0
        assert snapshot["last_error"] is None

    @pytest.mark.asyncio
    async def test_raises_on_connection_fatal(self) -> None:
        """连接致命错误应直接向上传播。"""

        async def op() -> str:
            raise Exception("cannot operate on a closed database")

        with pytest.raises(Exception, match="cannot operate on a closed database"):
            await write_with_retry(op, max_retries=3, base_delay=0.001)

    @pytest.mark.asyncio
    async def test_raises_non_retryable(self) -> None:
        """不可重试错误应直接向上传播。"""

        async def op() -> str:
            raise ValueError("unexpected error")

        with pytest.raises(ValueError, match="unexpected"):
            await write_with_retry(op, max_retries=3, base_delay=0.001)

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_lock(self) -> None:
        """持续锁冲突耗尽重试后应抛出错误。"""

        async def op() -> str:
            raise Exception("database is locked")

        with pytest.raises(Exception, match="database is locked"):
            await write_with_retry(op, max_retries=2, base_delay=0.001)

    @pytest.mark.asyncio
    async def test_records_lock_retry_and_final_failure_metrics(self) -> None:
        """锁重试及最终失败应写入对应指标。"""
        reset_write_metrics_snapshot()
        before_retry = _metric_sample_value("memora_write_lock_retries_total")
        before_failure = _metric_sample_value(
            "memora_write_failures_total",
            {"reason": "retry_exhausted"},
        )
        call_count = [0]

        async def eventually_ok() -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("database is locked")
            return "ok"

        assert (
            await write_with_retry(eventually_ok, max_retries=3, base_delay=0.001)
            == "ok"
        )

        async def always_locked() -> str:
            raise Exception("database is locked")

        with pytest.raises(Exception, match="database is locked"):
            await write_with_retry(always_locked, max_retries=2, base_delay=0.001)

        snapshot = get_write_metrics_snapshot()
        assert snapshot["operations_total"] == 2
        assert snapshot["lock_retries_total"] == 2
        assert snapshot["failures_total"] == 1
        assert snapshot["retry_exhausted_total"] == 1
        assert snapshot["fatal_failures_total"] == 0
        assert snapshot["last_error"] == "database is locked"
        if monitoring_metrics.is_prometheus_available():
            assert (
                _metric_sample_value("memora_write_lock_retries_total")
                == before_retry + 2
            )
            assert (
                _metric_sample_value(
                    "memora_write_failures_total",
                    {"reason": "retry_exhausted"},
                )
                == before_failure + 1
            )


class TestWriteTransaction:
    """测试 write_transaction。"""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self) -> None:
        """事务操作首次成功时应返回结果。"""

        async def op() -> str:
            return "txn_ok"

        result = await write_transaction(op, max_retries=3)
        assert result == "txn_ok"

    @pytest.mark.asyncio
    async def test_retry_on_lock_then_succeed(self) -> None:
        """事务锁冲突后应重试并返回成功结果。"""
        call_count = [0]

        async def op() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("database is locked")
            return "txn_ok"

        result = await write_transaction(op, max_retries=5, base_delay=0.001)
        assert result == "txn_ok"
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_raises_on_connection_fatal(self) -> None:
        """事务连接致命错误应直接向上传播。"""

        async def op() -> str:
            raise Exception("no active connection")

        with pytest.raises(Exception, match="no active"):
            await write_transaction(op, max_retries=3, base_delay=0.001)

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_lock(self) -> None:
        """事务持续锁冲突耗尽重试后应抛出错误。"""

        async def op() -> str:
            raise Exception("database is locked")

        with pytest.raises(Exception, match="database is locked"):
            await write_transaction(op, max_retries=2, base_delay=0.001)

    @pytest.mark.asyncio
    async def test_raises_non_retryable(self) -> None:
        """事务不可重试错误应直接向上传播。"""

        async def op() -> str:
            raise TypeError("type error")

        with pytest.raises(TypeError, match="type error"):
            await write_transaction(op, max_retries=3, base_delay=0.001)


class TestCoordinatedTransaction:
    """测试 coordinated_transaction 上下文管理器。"""

    @pytest.mark.asyncio
    async def test_commits_inside_single_context(self) -> None:
        """上下文正常退出时应提交同一连接上的事务。"""
        db = MagicMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        async with coordinated_transaction(db) as conn:
            assert conn is db
            await conn.execute("INSERT INTO t VALUES (1)")

        assert db.execute.await_args_list[0].args == ("BEGIN IMMEDIATE",)
        assert db.execute.await_args_list[1].args == ("INSERT INTO t VALUES (1)",)
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rolls_back_on_body_error(self) -> None:
        """上下文主体报错时应回滚并传播错误。"""
        db = MagicMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        with pytest.raises(RuntimeError, match="boom"):
            async with coordinated_transaction(db):
                raise RuntimeError("boom")

        db.commit.assert_not_awaited()
        db.rollback.assert_awaited_once()
