"""decay_operations 测试 — 类型衰减乘数和元数据归一化。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from sqlalchemy import Column, Text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel, select

from core.features.decay.application.operations import (
    DecayOperationsMixin,
    _normalize_batch_metadata,
)
from core.features.memory.application.lifecycle_operations import (
    LifecycleOperationsMixin,
)
from core.features.memory.infrastructure.base import apply_perf_pragmas
from core.features.memory.infrastructure.canonical_memory_reader import (
    load_canonical_memories,
    load_canonical_memory,
)
from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)


class TestTypeDecayMultiplier:
    """测试不同记忆类型的衰减倍率。"""

    @pytest.mark.parametrize(
        "memory_type,expected",
        [
            (None, 1.0),
            ("", 1.0),
            ("EPISODIC", 1.5),
            ("episodic", 1.5),
            ("FACTUAL", 0.5),
            ("factual", 0.5),
            ("PREFERENCE", 0.7),
            ("preference", 0.7),
            ("RELATIONAL", 0.6),
            ("relational", 0.6),
            ("unknown_type", 1.0),
        ],
    )
    def test_multiplier_values(self, memory_type: str | None, expected: float) -> None:
        """每种记忆类型都应返回对应的衰减倍率。"""
        result = DecayOperationsMixin._type_decay_multiplier(memory_type)
        assert result == expected


class TestNormalizeBatchMetadata:
    """测试批量 metadata 规范化函数。"""

    def test_empty_list(self) -> None:
        """空列表应原样返回。"""
        assert _normalize_batch_metadata([]) == []

    def test_dict_metadata_passthrough(self) -> None:
        """字典 metadata 应保持不变。"""
        docs = [{"metadata": {"key": "value"}}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {"key": "value"}

    def test_string_metadata_parsed(self) -> None:
        """字符串 metadata 应按 JSON 解析。"""
        docs = [{"metadata": '{"key": "parsed_value"}'}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {"key": "parsed_value"}

    def test_bad_json_metadata_defaults_to_empty(self) -> None:
        """非法 JSON 字符串应转换为空字典。"""
        docs = [{"metadata": "{not valid json}"}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {}

    def test_none_metadata_defaults_to_empty(self) -> None:
        """空值 metadata 应转换为空字典。"""
        docs = [{"metadata": None}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {}

    def test_missing_metadata_defaults_to_empty(self) -> None:
        """缺少 metadata 键时应补充空字典。"""
        docs = [{"other_field": "value"}]
        result = _normalize_batch_metadata(docs)
        # 缺少键时函数会补充 metadata={}。
        assert result[0].get("metadata") == {}
        assert result[0]["other_field"] == "value"

    def test_list_metadata_defaults_to_empty(self) -> None:
        """列表等不支持的 metadata 类型应转换为空字典。"""
        docs = [{"metadata": [1, 2, 3]}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {}

    def test_mixed_batch(self) -> None:
        """同一批次应分别处理合法和非法 metadata。"""
        docs = [
            {"metadata": {"valid": True}},
            {"metadata": '{"parsed": "ok"}'},
            {"metadata": "bad json"},
            {"metadata": None},
        ]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {"valid": True}
        assert result[1]["metadata"] == {"parsed": "ok"}
        assert result[2]["metadata"] == {}
        assert result[3]["metadata"] == {}


class _DecayHost(DecayOperationsMixin):
    """用于验证衰减写边界的最小宿主。"""

    def __init__(self) -> None:
        """构造带模拟数据库和标准衰减配置的宿主。"""

        self._config = {
            "access_decay_window_days": 30.0,
            "access_decay_max_count": 10.0,
            "access_count_decay_multiplier": 0.5,
            "human_like_memory.type_aware_decay_enabled": True,
            "flashbulb.enabled": True,
            "flashbulb.intensity_threshold": 0.90,
        }
        self._db = MagicMock()
        self._db._conn = MagicMock()
        self._invalidate_cache = MagicMock()


class _RealDecayHost(DecayOperationsMixin):
    """用于验证真实 SQLite 衰减幂等性的最小宿主。"""

    def __init__(self, db: aiosqlite.Connection) -> None:
        """绑定真实数据库连接并关闭类型感知衰减。

        参数:
            db: 测试专用的 SQLite 异步连接。
        """

        self._config = {
            "access_decay_window_days": 30.0,
            "access_decay_max_count": 10.0,
            "access_count_decay_multiplier": 0.5,
            "human_like_memory.type_aware_decay_enabled": False,
            "flashbulb.enabled": True,
            "flashbulb.intensity_threshold": 0.90,
        }
        self._db = db
        self._invalidate_cache = MagicMock()


class _TxnContext:
    """把模拟数据库适配为异步事务上下文。"""

    def __init__(self, db: MagicMock) -> None:
        """保存需要由上下文返回的模拟数据库。

        参数:
            db: 协调事务中使用的模拟数据库。
        """

        self.db = db

    async def __aenter__(self) -> MagicMock:
        """进入事务上下文并返回模拟数据库。"""

        return self.db

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """退出事务上下文且不抑制异常。

        参数:
            exc_type: 上下文内异常类型；正常退出时为空。
            exc: 上下文内异常实例；正常退出时为空。
            tb: 上下文内异常回溯；正常退出时为空。
        """

        return None


class TestDecayWriteBoundaries:
    """衰减写入应使用单一协调事务。"""

    @pytest.mark.asyncio
    async def test_single_access_update_uses_one_coordinated_transaction(self) -> None:
        """单条访问更新应只打开一次协调事务。"""

        host = _DecayHost()
        select_cursor = AsyncMock()
        select_cursor.fetchone.return_value = (json.dumps({"importance": 0.5}),)
        host._db.execute = AsyncMock(return_value=select_cursor)

        with (
            patch(
                "core.features.decay.application.operations.coordinated_transaction",
                return_value=_TxnContext(host._db),
            ) as txn_mock,
        ):
            updated = await host.update_access_time(1)

        assert updated is True
        txn_mock.assert_called_once_with(host._db)
        assert host._db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_access_update_uses_one_coordinated_transaction(self) -> None:
        """批量访问更新应在一次协调事务内完成。"""

        host = _DecayHost()
        select_cursor = AsyncMock()
        select_cursor.fetchall.return_value = [
            {"id": 1, "metadata": json.dumps({"importance": 0.5})},
            {"id": 2, "metadata": json.dumps({"importance": 0.7})},
        ]
        host._db.execute = AsyncMock(return_value=select_cursor)
        host._db.executemany = AsyncMock()

        with (
            patch(
                "core.features.decay.application.operations.coordinated_transaction",
                return_value=_TxnContext(host._db),
            ) as txn_mock,
        ):
            affected = await host.update_access_times_batch([1, 2, 1])

        assert affected == 2
        txn_mock.assert_called_once_with(host._db)
        host._db.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_batch_access_update_tolerates_dirty_importance(self) -> None:
        """importance 为 null 或非数字时整批仍应强化，并使用默认基准值。"""

        host = _DecayHost()
        select_cursor = AsyncMock()
        select_cursor.fetchall.return_value = [
            {"id": 1, "metadata": json.dumps({"importance": None})},
            {"id": 2, "metadata": json.dumps({"importance": "未知"})},
        ]
        host._db.execute = AsyncMock(return_value=select_cursor)
        host._db.executemany = AsyncMock()

        with patch(
            "core.features.decay.application.operations.coordinated_transaction",
            return_value=_TxnContext(host._db),
        ):
            affected = await host.update_access_times_batch([1, 2])

        assert affected == 2
        updates = host._db.executemany.await_args.args[1]
        written = [json.loads(payload) for payload, _ in updates]
        assert [item["importance"] for item in written] == [0.51, 0.51]
        assert [item["access_count"] for item in written] == [1, 1]

    @pytest.mark.asyncio
    async def test_single_access_update_tolerates_dirty_importance(self) -> None:
        """单条强化的 importance 非数字时按默认基准值强化并返回成功。"""

        host = _DecayHost()
        select_cursor = AsyncMock()
        select_cursor.fetchone.return_value = (json.dumps({"importance": "0.9?"}),)
        host._db.execute = AsyncMock(return_value=select_cursor)

        with patch(
            "core.features.decay.application.operations.coordinated_transaction",
            return_value=_TxnContext(host._db),
        ):
            updated = await host.update_access_time(1, recall_type="active")

        assert updated is True
        update_args = host._db.execute.await_args_list[1].args
        assert update_args[1][1] == 1
        assert json.loads(update_args[1][0])["importance"] == 0.55

    @pytest.mark.asyncio
    async def test_daily_decay_uses_one_coordinated_transaction(self) -> None:
        """每日衰减应在一次协调事务内完成。"""

        host = _DecayHost()
        select_cursor = AsyncMock()
        select_cursor.fetchall.return_value = [
            {
                "id": 1,
                "metadata": json.dumps(
                    {
                        "importance": 0.8,
                        "access_count": 4,
                        "last_access_time": 0,
                        "memory_type": "FACTUAL",
                    }
                ),
            }
        ]
        host._db.execute = AsyncMock(return_value=select_cursor)
        host._db.executemany = AsyncMock()

        with (
            patch(
                "core.features.decay.application.operations.coordinated_transaction",
                return_value=_TxnContext(host._db),
            ) as txn_mock,
        ):
            affected = await host.apply_daily_decay(decay_rate=0.1, days=1)

        assert affected == 1
        txn_mock.assert_called_once_with(host._db)
        host._db.executemany.assert_awaited_once()


class TestDecayIdempotency:
    """验证同一自然日内的衰减幂等性。"""

    @pytest.mark.asyncio
    async def test_daily_decay_is_idempotent_for_same_calendar_day(
        self,
        tmp_db_path: str,
    ) -> None:
        """同一自然日第二次执行不应重复衰减。

        参数:
            tmp_db_path: pytest 提供的隔离 SQLite 文件路径。
        """

        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        await db.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE,
                content TEXT,
                metadata TEXT
            )
            """
        )
        await db.execute(
            "INSERT INTO documents (doc_id, content, metadata) VALUES (?, ?, ?)",
            (
                "doc-1",
                "stable memory",
                json.dumps({"importance": 0.8, "access_count": 4}),
            ),
        )
        await db.commit()

        host = _RealDecayHost(db)
        first = await host.apply_daily_decay(decay_rate=0.1, days=1)
        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 1")
        first_metadata = json.loads((await cursor.fetchone())["metadata"])
        second = await host.apply_daily_decay(decay_rate=0.1, days=1)
        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 1")
        second_metadata = json.loads((await cursor.fetchone())["metadata"])
        await db.close()

        assert first == 1
        assert second == 0
        assert first_metadata == second_metadata
        assert first_metadata["last_decay_date"]


class _FakeEvolutionStore:
    """记录 revision 失效调用并返回安全计数。"""

    def __init__(self, revision_tokens: dict[int, str]) -> None:
        self.revision_tokens = revision_tokens
        self.loaded_ids: list[tuple[int, ...]] = []
        self.invalidated: list[tuple[int, str]] = []

    async def load_sources(self, memory_ids, *, active_only: bool = False):
        """按当前 canonical 快照返回带 revision 的来源行。"""

        self.loaded_ids.append(tuple(int(item) for item in memory_ids))
        return [
            SimpleNamespace(revision_token=self.revision_tokens[int(memory_id)])
            for memory_id in memory_ids
            if int(memory_id) in self.revision_tokens
        ]

    async def invalidate_for_source_revision(
        self, memory_id: int, revision_token: str
    ) -> int:
        """记录失效调用并返回被失效的派生行数。"""

        self.invalidated.append((int(memory_id), str(revision_token)))
        return 3


class _FakeGraphManager:
    """记录源级残留回收调用，可注入失败。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cleaned: list[list[int]] = []

    async def batch_delete_memories(self, memory_ids) -> None:
        """按源级删除回收图产物。"""

        if self.fail:
            raise RuntimeError("graph_cleanup_failed")
        self.cleaned.append([int(item) for item in memory_ids])


class _FakeAtomManager:
    """按固定签名记录 Atom 重派生调用，覆盖同步、失败与报告分支。"""

    def __init__(
        self,
        *,
        sync: bool = False,
        fail: bool = False,
        report: dict | None = None,
    ) -> None:
        self.sync = sync
        self.fail = fail
        self.report = report
        self.calls: list[tuple[list[int], str]] = []

    def rederive_for_sources(self, memory_ids, reason):
        """返回同步结果或 awaitable，模拟 C3 实现前的两种接入形态。"""

        if self.fail:
            raise RuntimeError("atom_rederive_failed")
        self.calls.append(([int(item) for item in memory_ids], str(reason)))
        if self.sync:
            return self.report

        async def _completed() -> dict | None:
            """返回已完成的重派生结果。"""

            return self.report

        return _completed()


class _FakeEngine:
    """派生失效端口与绑定回调的最小引擎替身。"""

    def __init__(
        self, *, graph_failure: bool = False, atom_sync: bool = False, **kwargs
    ) -> None:
        self.memory_evolution_store = _FakeEvolutionStore({1: "r1", 2: "r2", 3: "r3"})
        self.graph_memory_manager = _FakeGraphManager(fail=graph_failure)
        self.atom_lifecycle_manager: Any = _FakeAtomManager(**kwargs, sync=atom_sync)

    async def update_memory(self, memory_id, metadata=None, **kwargs) -> bool:
        """绑定回调：让宿主能解析派生失效端口。"""

        return True

    async def batch_delete_memories(self, memory_ids):
        """绑定回调：让宿主能解析派生失效端口。"""

        return {"deleted": len(memory_ids)}


class _LifecycleHost(LifecycleOperationsMixin):
    """分层 decay 状态更新与派生失效的最小宿主。"""

    def __init__(self, db, engine) -> None:
        self._db = db
        self._update_memory = engine.update_memory
        self._batch_delete_memories = engine.batch_delete_memories
        self._invalidate_cache = MagicMock()


async def _seed_lifecycle_documents(db: aiosqlite.Connection, count: int = 2) -> None:
    """创建 documents 表并写入可用于状态批更新的行。"""

    await db.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT UNIQUE,
            text TEXT,
            content TEXT,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    for memory_id in range(1, count + 1):
        await db.execute(
            "INSERT INTO documents (doc_id, text, content, metadata, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"doc-{memory_id}",
                "stable memory",
                "stable memory",
                "{}",
                "2026-09-20T00:00:00+00:00",
                0.0,
            ),
        )
    await db.commit()


class TestDerivedInvalidationAfterStatusChange:
    """状态批更新提交后必须统一失效派生面，失败只降级。"""

    @pytest.mark.asyncio
    async def test_status_batch_invalidates_evolution_graph_and_atoms(
        self, tmp_db_path: str
    ) -> None:
        """休眠批更新提交后按新 revision 失效，并回收图残留、重派生 Atom。"""

        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        await _seed_lifecycle_documents(db, count=2)
        await create_topic_catalog_schema(db)
        await db.commit()
        engine = _FakeEngine()
        host = _LifecycleHost(db, engine)

        updated = await host._batch_update_status([1, 2], "dormant", 1000.0)

        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 1")
        metadata = json.loads((await cursor.fetchone())["metadata"])
        cursor = await db.execute(
            "SELECT memory_id, operation FROM topic_catalog_dirty ORDER BY memory_id"
        )
        dirty_rows = [tuple(row) for row in await cursor.fetchall()]
        await db.close()

        assert updated == 2
        assert metadata["status"] == "dormant"
        # catalog dirty 由 canonical UPDATE 触发器在同一事务登记，无需重复写入。
        assert dirty_rows == [(1, "status_update"), (2, "status_update")]
        store = engine.memory_evolution_store
        assert store.loaded_ids == [(1,), (2,)]
        assert store.invalidated == [(1, "r1"), (2, "r2")]
        assert engine.graph_memory_manager.cleaned == [[1, 2]]
        assert engine.atom_lifecycle_manager.calls == [([1, 2], "decay_dormant")]
        host._invalidate_cache.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_invalidation_failure_only_degrades_and_keeps_status_write(
        self, tmp_db_path: str
    ) -> None:
        """派生失效失败只降级计数，不回滚已提交的状态写、不抛出异常。"""

        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        await _seed_lifecycle_documents(db, count=2)
        engine = _FakeEngine(graph_failure=True, fail=True)
        host = _LifecycleHost(db, engine)

        updated = await host._batch_update_status([1, 2], "archived", 2000.0)

        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 1")
        metadata = json.loads((await cursor.fetchone())["metadata"])
        summary = await host._invalidate_derived_after_status_change([1, 2], "archived")
        await db.close()

        assert updated == 2
        assert metadata["status"] == "archived"
        assert engine.graph_memory_manager.cleaned == []
        assert engine.atom_lifecycle_manager.calls == []
        assert engine.memory_evolution_store.invalidated == [
            (1, "r1"),
            (2, "r2"),
            (1, "r1"),
            (2, "r2"),
        ]
        assert summary["sources"] == 2
        assert summary["graph_failed"] == 2
        assert summary["atoms_failed"] == 2
        assert summary["atoms_reason_code"] == "atom_rederive_failed"
        assert summary["steps_failed"] == 2

    @pytest.mark.asyncio
    async def test_missing_atom_port_is_skipped_without_failure(self) -> None:
        """Atom 实现缺失时按跳过处理，且同步实现也按 None 安全调用。"""

        engine = _FakeEngine(atom_sync=True)
        host = _LifecycleHost(MagicMock(), engine)

        summary = await host._invalidate_derived_after_status_change([1], "dormant")

        assert summary["atoms_rederived"] == 1
        assert summary["atoms_failed"] == 0
        assert summary["atoms_reason_code"] is None
        assert summary["steps_failed"] == 0
        assert engine.atom_lifecycle_manager.calls == [([1], "decay_dormant")]

        engine_no_atom = _FakeEngine()
        engine_no_atom.atom_lifecycle_manager = None
        host_no_atom = _LifecycleHost(MagicMock(), engine_no_atom)

        skipped = await host_no_atom._invalidate_derived_after_status_change(
            [1], "dormant"
        )

        assert skipped["atoms_rederived"] == 0
        assert skipped["atoms_failed"] == 0
        assert skipped["steps_failed"] == 0

    @pytest.mark.asyncio
    async def test_atoms_report_failure_is_not_reported_as_success(self) -> None:
        """manager 报告 failed/needs_repair 时必须降级为失败计数与稳定原因码。"""

        engine = _FakeEngine(
            report={
                "sources": 2,
                "rederived": 1,
                "purged": 0,
                "skipped": 0,
                "failed": 1,
                "needs_repair": 1,
            }
        )
        host = _LifecycleHost(MagicMock(), engine)

        summary = await host._invalidate_derived_after_status_change([1, 2], "dormant")

        # 成功来源数为真实收敛数（rederived），失败来源单独计数，不得伪报全长。
        assert summary["atoms_rederived"] == 1
        assert summary["atoms_failed"] == 1
        assert summary["atoms_reason_code"] == "atom_rederive_failed"
        assert summary["steps_failed"] == 1
        assert engine.atom_lifecycle_manager.calls == [([1, 2], "decay_dormant")]

    @pytest.mark.asyncio
    async def test_atoms_report_success_keeps_converged_counts(self) -> None:
        """manager 全部成功时按 rederived+purged 计收敛数，不报失败。"""

        engine = _FakeEngine(
            report={
                "sources": 2,
                "rederived": 0,
                "purged": 2,
                "skipped": 0,
                "failed": 0,
                "needs_repair": 0,
            }
        )
        host = _LifecycleHost(MagicMock(), engine)

        summary = await host._invalidate_derived_after_status_change([1, 2], "archived")

        assert summary["atoms_rederived"] == 2
        assert summary["atoms_failed"] == 0
        assert summary["atoms_reason_code"] is None
        assert summary["steps_failed"] == 0


class _OrmDocumentRow(SQLModel, table=True):
    """与 astrbot ``DocumentStorage`` 同形的 canonical 行映射探针。

    ``DocumentStorage`` 把 ``created_at``/``updated_at`` 声明为 ``datetime``，
    读取时按 ISO 文本解析整批行：同一批里只要有一行非 ISO 值，整批读取就会
    抛错。这里只映射 canonical 读取用到的列，解析口径与生产读取端口一致
    （测试环境不可导入真实存储模块，故按同一列类型复现读取语义）。
    """

    __tablename__ = "documents"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    text: str | None = Field(default=None)
    metadata_: str | None = Field(default=None, sa_column=Column("metadata", Text))
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)


class _OrmDocumentStorage:
    """以同形 ORM 读取实现 ``faiss_db.document_storage`` 的最小读取端口。"""

    def __init__(self, db_path: str) -> None:
        """创建指向临时 SQLite 的异步引擎。"""

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async def get_documents(
        self,
        metadata_filters: dict[str, Any],
        ids: list[int] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """按 ID 批量读取 canonical 行，时间列按 ``datetime`` 解析整批。"""

        maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with maker() as session:
            rows = (await session.execute(select(_OrmDocumentRow))).scalars().all()
        documents = [
            {
                "id": row.id,
                "text": row.text,
                "metadata": row.metadata_,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]
        if ids:
            wanted = {int(item) for item in ids}
            documents = [doc for doc in documents if doc["id"] in wanted]
        if limit is not None:
            documents = documents[: int(limit)]
        return documents

    async def close(self) -> None:
        """释放测试引擎。"""

        await self.engine.dispose()


class TestStatusBatchUpdateTimestampWrite:
    """状态批更新写入的 ``updated_at`` 必须是存储后端可解析的 ISO 文本。"""

    @pytest.mark.asyncio
    async def test_updated_at_round_trips_and_keeps_the_given_instant(
        self, tmp_db_path: str
    ) -> None:
        """批更新后整批行可经 datetime 列解析，落库文本等于注入时刻且可重复。"""

        timestamp = 1758345678.5
        expected = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        # 种子里保留历史写点留下的 float 时间戳：更新必须把它们改成可解析文本。
        await _seed_lifecycle_documents(db, count=2)
        host = _LifecycleHost(db, _FakeEngine())

        updated = await host._batch_update_status([1, 2], "dormant", timestamp)
        storage = _OrmDocumentStorage(tmp_db_path)
        try:
            docs = await storage.get_documents(metadata_filters={}, ids=[1, 2], limit=2)
        finally:
            await storage.close()
        cursor = await db.execute("SELECT id, updated_at FROM documents ORDER BY id")
        raw_before = {
            int(row["id"]): row["updated_at"] for row in await cursor.fetchall()
        }
        again = await host._batch_update_status([1, 2], "dormant", timestamp)
        cursor = await db.execute("SELECT id, updated_at FROM documents ORDER BY id")
        raw_after = {
            int(row["id"]): row["updated_at"] for row in await cursor.fetchall()
        }
        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 1")
        metadata = json.loads((await cursor.fetchone())["metadata"])
        await db.close()

        assert updated == 2
        # 整批经 datetime 列解析成功：写 float 时这里会抛 ValueError。
        assert {doc["id"] for doc in docs} == {1, 2}
        assert [datetime.fromisoformat(doc["updated_at"]) for doc in docs] == [
            expected,
            expected,
        ]
        # 落库文本是 ISO 8601 且等于注入时刻（不是读取时刻的 now()）。
        assert [
            datetime.fromisoformat(raw_before[1]),
            datetime.fromisoformat(raw_before[2]),
        ] == [
            expected,
            expected,
        ]
        # 同一时刻重复批更新不得伪造新 revision：缓存/派生按该字符串比对。
        assert again == 2
        assert raw_after == raw_before
        # status_changed_at 语义不变：仍是 Unix 秒。
        assert metadata["status_changed_at"] == timestamp
        assert metadata["memory_status"] == "dormant"


class TestCanonicalReadLegacyTimestampTolerance:
    """单行历史非 ISO 时间值不得让整批 canonical 读取失败。"""

    @pytest.mark.asyncio
    async def test_batch_read_falls_back_to_raw_sql_for_legacy_row(
        self, tmp_db_path: str
    ) -> None:
        """整批含一行历史 float 时间值时，批量与单行读取仍返回全部 canonical 行。"""

        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        await _seed_lifecycle_documents(db, count=3)
        # 第 1 行保留历史写点的 float 时间戳，第 2、3 行已是 ISO 文本。
        await db.execute(
            "UPDATE documents SET updated_at = ? WHERE id IN (2, 3)",
            ("2026-07-24T02:21:07.123456+00:00",),
        )
        await db.commit()
        storage = _OrmDocumentStorage(tmp_db_path)
        faiss_db = SimpleNamespace(document_storage=storage)
        try:
            # 前提：读取端口按 datetime 解析整批，一行脏值就让整批抛错。
            with pytest.raises(ValueError):
                await storage.get_documents(metadata_filters={}, ids=[1, 2, 3], limit=3)
            records = await load_canonical_memories(faiss_db, [1, 2, 3], db)
            single = await load_canonical_memory(faiss_db, db, 1)
            # 两条读取路径都不可用时必须抛出，不得把读取故障伪装成「无行」。
            with pytest.raises(ValueError):
                await load_canonical_memories(faiss_db, [1, 2, 3])
        finally:
            await storage.close()
        await db.close()

        assert set(records) == {1, 2, 3}
        assert records[1]["text"] == "stable memory"
        assert records[1]["metadata"] == {}
        # 原始 revision 表示保留：历史 float 行不得被静默替换成别的值。
        assert records[1]["updated_at"] == "0.0"
        assert records[2]["updated_at"] == "2026-07-24T02:21:07.123456+00:00"
        assert single is not None
        assert single["text"] == "stable memory"
        assert single["updated_at"] == "0.0"


class TestStatusBatchUpdateDerivationReport:
    """状态批更新的派生失效摘要必须以稳定原因码与计数可见。"""

    @pytest.mark.asyncio
    async def test_derivation_failure_is_logged_with_stable_reason_code(
        self, tmp_db_path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """派生失效失败时记录稳定原因码与计数，且不回滚已提交的状态写。"""

        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        await _seed_lifecycle_documents(db, count=2)
        host = _LifecycleHost(db, _FakeEngine(graph_failure=True, fail=True))

        with caplog.at_level(logging.WARNING):
            updated = await host._batch_update_status([1, 2], "archived", 2000.0)

        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 1")
        metadata = json.loads((await cursor.fetchone())["metadata"])
        await db.close()

        assert updated == 2
        assert metadata["status"] == "archived"
        assert "reason_code=derived_invalidation_failed" in caplog.text
        assert "new_status=archived" in caplog.text
        assert "sources=2" in caplog.text
        assert "steps_failed=2" in caplog.text
        assert "graph_failed=2" in caplog.text
        assert "atoms_failed=2" in caplog.text
        assert "atoms_reason_code=atom_rederive_failed" in caplog.text

    @pytest.mark.asyncio
    async def test_converged_derivation_logs_no_failure_code(
        self, tmp_db_path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """派生全部收敛时不写失败原因码，避免把正常维护记成降级。"""

        db = await aiosqlite.connect(tmp_db_path)
        db.row_factory = aiosqlite.Row
        await apply_perf_pragmas(db)
        await _seed_lifecycle_documents(db, count=2)
        host = _LifecycleHost(db, _FakeEngine())

        with caplog.at_level(logging.WARNING):
            updated = await host._batch_update_status([1, 2], "dormant", 1000.0)

        await db.close()

        assert updated == 2
        assert "derived_invalidation_failed" not in caplog.text
