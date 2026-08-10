"""decay_operations 测试 — 类型衰减乘数和元数据归一化。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.features.decay.application.operations import (
    DecayOperationsMixin,
    _normalize_batch_metadata,
)
from core.storage.base import apply_perf_pragmas


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
