"""存储层共享写事务注入回归测试。

覆盖 M1 切片后 storage 与共享写事务实现的解耦语义：

- 隔离负例：storage import + 实际写入前后均不加载 ``core.managers*``
- 实例构造依赖：accessor 逐实例注入，双实例互不改线、反序运行与
  reload/terminate 后均无陈旧全局状态
- 旧 manager metric / transaction monkeypatch 路径经注入 accessor 仍生效
- ``BaseException``（含 ``CancelledError``）时 rollback 后原样抛出：屏障确认
  SQL 已执行、事务活跃后再取消，断言连接复用与未提交数据不可见
- benchmark 标准命令在注入后正常退出
- 未注入时给出明确错误
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import core.managers.write_coordinator as write_coordinator
from core.injection.models import InjectionDecisionRecord
from core.managers.write_coordinator import coordinated_transaction, write_transaction
from core.storage.injection_decision_store import InjectionDecisionStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _record(decision_id: str, created_at_ms: int) -> InjectionDecisionRecord:
    """构造一条合法的最小注入决策记录，避免与 store 校验纠缠。"""
    return InjectionDecisionRecord(
        decision_id=decision_id,
        created_at_ms=created_at_ms,
        routing_mode="manual",
        configured_preset="balanced",
        recommended_preset="balanced",
        resolved_preset="balanced",
        preferred_delivery="extra_user_content",
        resolved_delivery="extra_user_content",
        fallback_applied=False,
        outcome="injected",
        primary_reason="MANUAL_SELECTED",
        actual_payload_chars=600,
    )


def _injected_store(db_path: str | Path) -> InjectionDecisionStore:
    """构造注入真实共享写事务 accessor 的存储实例（逐实例注入）。"""
    return InjectionDecisionStore(
        db_path, write_transaction_accessor=lambda: write_coordinator.write_transaction
    )


def _run_subprocess(script: str) -> subprocess.CompletedProcess[str]:
    """在仓库根目录以隔离进程执行脚本，避免污染当前导入状态。"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


class TestIsolationNegative:
    """storage 与共享写事务实现彻底解耦的隔离负例。"""

    def test_storage_import_and_writes_do_not_load_managers(self) -> None:
        """storage import 与实际写入前后均不得出现任何 ``core.managers*`` 模块。"""
        script = """
import asyncio
import sys
import tempfile
from pathlib import Path

from core.injection.models import InjectionDecisionRecord
from core.storage.injection_decision_store import InjectionDecisionStore


def _record(decision_id: str) -> InjectionDecisionRecord:
    return InjectionDecisionRecord(
        decision_id=decision_id,
        created_at_ms=1,
        routing_mode="manual",
        configured_preset="balanced",
        recommended_preset="balanced",
        resolved_preset="balanced",
        preferred_delivery="extra_user_content",
        resolved_delivery="extra_user_content",
        fallback_applied=False,
        outcome="injected",
        primary_reason="MANUAL_SELECTED",
        actual_payload_chars=600,
    )


async def _neutral_write_transaction(operation):
    return await operation()


async def main() -> None:
    assert not any(
        name == "core.managers" or name.startswith("core.managers.")
        for name in sys.modules
    ), f"storage import 不应加载 managers: {sorted(sys.modules)}"

    with tempfile.TemporaryDirectory() as tmp:
        store = InjectionDecisionStore(
            Path(tmp) / "memora.db",
            write_transaction_accessor=lambda: _neutral_write_transaction,
        )
        await store.initialize()
        await store.insert_many([_record("one")])
        await store.cleanup(retention_days=0, max_rows=100)
        await store.insert_many([_record("two")])
        await store.close()

    assert not any(
        name == "core.managers" or name.startswith("core.managers.")
        for name in sys.modules
    ), f"实际写入不应加载 managers: {sorted(sys.modules)}"


asyncio.run(main())
"""
        result = _run_subprocess(script)
        assert result.returncode == 0, result.stdout + result.stderr


class TestInjectionSemantics:
    """经上层构造注入的动态 accessor 保持旧 monkeypatch / reload 语义。"""

    @pytest.mark.asyncio
    async def test_manager_transaction_patch_path_intercepts_store_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """patch managers 模块属性后，store cleanup 经注入 accessor 使用 patched 实现。"""
        patched = AsyncMock(wraps=write_transaction)
        monkeypatch.setattr(write_coordinator, "write_transaction", patched)
        with tempfile.TemporaryDirectory() as tmp:
            store = _injected_store(Path(tmp) / "memora.db")
            await store.initialize()
            await store.cleanup(retention_days=0, max_rows=100)
            await store.close()
        assert patched.await_count >= 1

    @pytest.mark.asyncio
    async def test_manager_metric_monkeypatch_still_observed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """patch ``_inc_metric`` 后，经注入 accessor 的写路径仍记录指标。"""
        calls: list[tuple[str, float]] = []
        monkeypatch.setattr(
            write_coordinator,
            "_inc_metric",
            lambda name, **kwargs: calls.append((name, kwargs.get("amount", 1.0))),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = _injected_store(Path(tmp) / "memora.db")
            await store.initialize()
            await store.insert_many([_record("one", 1)])
            await store.close()
        assert calls == [("WRITE_OPERATIONS_TOTAL", 1.0)]

    def test_injected_accessor_resolves_current_module_after_reload(self) -> None:
        """reload managers 模块后，实例注入的 accessor 仍解析当前实现。"""
        importlib.reload(write_coordinator)
        store = _injected_store("unused-memora.db")
        try:
            accessor = store._write_transaction_accessor
            assert accessor is not None
            assert accessor() is write_coordinator.write_transaction
        finally:
            del store

    @pytest.mark.asyncio
    async def test_uninjected_store_fails_clearly(self) -> None:
        """未注入 accessor 时，写路径必须给出明确错误而非绕过共享事务。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = InjectionDecisionStore(Path(tmp) / "memora.db")
            await store.initialize()
            with pytest.raises(RuntimeError, match="write_transaction 尚未注入"):
                await store.insert_many([_record("one", 1)])
            with pytest.raises(RuntimeError, match="write_transaction 尚未注入"):
                await store.cleanup(retention_days=0, max_rows=100)
            await store.close()


class TestInstanceInjection:
    """实例级注入：双实例互不改线、反序运行与 terminate 后无陈旧状态。"""

    @pytest.mark.asyncio
    async def test_dual_instances_keep_their_own_accessors(
        self, tmp_path: Path
    ) -> None:
        """后构造的实例不得重定向既有实例；各自写路径使用各自 accessor。"""
        calls_a: list[str] = []
        calls_b: list[str] = []

        async def transaction_a(operation) -> int:
            """记录实例 A 的写事务调用。"""
            calls_a.append("A")
            return await operation()

        async def transaction_b(operation) -> int:
            """记录实例 B 的写事务调用。"""
            calls_b.append("B")
            return await operation()

        store_a = InjectionDecisionStore(
            tmp_path / "a.db", write_transaction_accessor=lambda: transaction_a
        )
        store_b = InjectionDecisionStore(
            tmp_path / "b.db", write_transaction_accessor=lambda: transaction_b
        )
        await store_a.initialize()
        await store_b.initialize()
        try:
            await store_a.insert_many([_record("a1", 1)])
            await store_b.insert_many([_record("b1", 1)])
            await store_a.insert_many([_record("a2", 2)])
            await store_b.insert_many([_record("b2", 2)])
            assert calls_a == ["A", "A"]
            assert calls_b == ["B", "B"]
        finally:
            await store_a.close()
            await store_b.close()

    @pytest.mark.asyncio
    async def test_uninjected_and_injected_instances_coexist_in_any_order(
        self, tmp_path: Path
    ) -> None:
        """未注入实例失败不影响已注入实例；两种先后顺序均成立。"""
        uninjected = InjectionDecisionStore(tmp_path / "u.db")
        await uninjected.initialize()
        with pytest.raises(RuntimeError, match="write_transaction 尚未注入"):
            await uninjected.insert_many([_record("one", 1)])
        await uninjected.close()

        injected = _injected_store(tmp_path / "i.db")
        await injected.initialize()
        assert await injected.insert_many([_record("two", 2)]) == 1
        await injected.close()

        uninjected_again = InjectionDecisionStore(tmp_path / "u2.db")
        await uninjected_again.initialize()
        with pytest.raises(RuntimeError, match="write_transaction 尚未注入"):
            await uninjected_again.cleanup(retention_days=0, max_rows=100)
        await uninjected_again.close()

        injected_again = _injected_store(tmp_path / "i2.db")
        await injected_again.initialize()
        assert await injected_again.insert_many([_record("three", 3)]) == 1
        await injected_again.close()

    @pytest.mark.asyncio
    async def test_terminate_does_not_leak_accessor_to_fresh_instances(
        self, tmp_path: Path
    ) -> None:
        """关闭实例后不残留全局状态：新实例未注入仍明确失败。"""
        injected = _injected_store(tmp_path / "a.db")
        await injected.initialize()
        await injected.insert_many([_record("one", 1)])
        await injected.close()

        fresh = InjectionDecisionStore(tmp_path / "b.db")
        await fresh.initialize()
        with pytest.raises(RuntimeError, match="write_transaction 尚未注入"):
            await fresh.insert_many([_record("two", 2)])
        await fresh.close()


class TestBaseExceptionRollback:
    """BaseException（含 CancelledError）必须 rollback 后原样抛出。"""

    @pytest.mark.asyncio
    async def test_cancel_during_active_store_write_rolls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SQL 已执行、事务活跃时取消写路径：回滚、连接复用且未提交行不可见。"""
        store = _injected_store(tmp_path / "memora.db")
        await store.initialize()
        try:
            assert store.connection is not None
            sql_done = asyncio.Event()
            proceed = asyncio.Event()
            real_commit = store.connection.commit

            async def commit_after_barrier() -> None:
                """屏障：SQL 已执行后等待放行，取消前不真正提交。"""
                sql_done.set()
                await proceed.wait()
                await real_commit()

            monkeypatch.setattr(store.connection, "commit", commit_after_barrier)
            task = asyncio.create_task(store.insert_many([_record("one", 1)]))
            await asyncio.wait_for(sql_done.wait(), timeout=10)
            assert store.connection.in_transaction is True
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.connection.in_transaction is False
            async with aiosqlite.connect(str(tmp_path / "memora.db")) as reader:
                cursor = await reader.execute(
                    "SELECT COUNT(*) FROM injection_decisions"
                )
                assert (await cursor.fetchone())[0] == 0
            monkeypatch.undo()
            assert await store.insert_many([_record("two", 2)]) == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_cancel_during_active_cleanup_rolls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cleanup 在 DELETE 已执行、事务活跃时取消：回滚、行仍可见且连接可复用。"""
        store = _injected_store(tmp_path / "memora.db")
        await store.initialize()
        try:
            now = 1_000
            await store.insert_many([_record("keep", now), _record("drop", now + 1)])
            assert store.connection is not None
            sql_done = asyncio.Event()
            proceed = asyncio.Event()
            real_commit = store.connection.commit

            async def commit_after_barrier() -> None:
                """屏障：DELETE 已执行后等待放行，取消前不真正提交。"""
                sql_done.set()
                await proceed.wait()
                await real_commit()

            monkeypatch.setattr(store.connection, "commit", commit_after_barrier)
            task = asyncio.create_task(
                store.cleanup(retention_days=0, max_rows=1, now_ms=now + 1)
            )
            await asyncio.wait_for(sql_done.wait(), timeout=10)
            assert store.connection.in_transaction is True
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.connection.in_transaction is False
            async with aiosqlite.connect(str(tmp_path / "memora.db")) as reader:
                cursor = await reader.execute(
                    "SELECT COUNT(*) FROM injection_decisions"
                )
                assert (await cursor.fetchone())[0] == 2
            monkeypatch.undo()
            result = await store.cleanup(retention_days=0, max_rows=1, now_ms=now + 1)
            assert result.deleted_expired == 0
            assert result.deleted_overflow == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_cancel_during_active_coordinated_transaction_rolls_back(
        self, tmp_path: Path
    ) -> None:
        """manager 侧 coordinated_transaction 活跃时取消：回滚、表不可见且连接可复用。"""
        db_path = tmp_path / "memora.db"
        async with aiosqlite.connect(str(db_path)) as db:
            sql_done = asyncio.Event()
            proceed = asyncio.Event()

            async def use_transaction() -> None:
                """在事务内建表并停在屏障，等待取消。"""
                async with coordinated_transaction(db) as conn:
                    await conn.execute("CREATE TABLE t (id INTEGER)")
                    sql_done.set()
                    await proceed.wait()

            task = asyncio.create_task(use_transaction())
            await asyncio.wait_for(sql_done.wait(), timeout=10)
            assert db.in_transaction is True
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert db.in_transaction is False
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE name = 't'")
            assert await cursor.fetchone() is None
            async with coordinated_transaction(db) as conn:
                await conn.execute("CREATE TABLE t (id INTEGER)")


class TestBenchmarkStandardCommand:
    """benchmark 标准命令回归：真实构造点注入后不得因缺 accessor 失败。"""

    def test_standard_command_exits_zero(self) -> None:
        """``python scripts/benchmark_injection_decisions.py`` 必须正常完成。"""
        script = str(REPO_ROOT / "scripts" / "benchmark_injection_decisions.py")
        result = subprocess.run(
            [sys.executable, script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
