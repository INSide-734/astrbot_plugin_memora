"""decay_operations 测试 — 类型衰减乘数和元数据归一化。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.managers.decay_operations import (
    DecayOperationsMixin,
    _normalize_batch_metadata,
)
from core.storage.base import apply_perf_pragmas


class TestTypeDecayMultiplier:
    """Tests for _type_decay_multiplier static method."""

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
        """Each memory type produces the correct decay multiplier."""
        result = DecayOperationsMixin._type_decay_multiplier(memory_type)
        assert result == expected


class TestNormalizeBatchMetadata:
    """Tests for _normalize_batch_metadata function."""

    def test_empty_list(self) -> None:
        """Empty list is returned unchanged."""
        assert _normalize_batch_metadata([]) == []

    def test_dict_metadata_passthrough(self) -> None:
        """Dict metadata is left as-is."""
        docs = [{"metadata": {"key": "value"}}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {"key": "value"}

    def test_string_metadata_parsed(self) -> None:
        """String metadata is parsed from JSON."""
        docs = [{"metadata": '{"key": "parsed_value"}'}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {"key": "parsed_value"}

    def test_bad_json_metadata_defaults_to_empty(self) -> None:
        """Invalid JSON string metadata becomes empty dict."""
        docs = [{"metadata": "{not valid json}"}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {}

    def test_none_metadata_defaults_to_empty(self) -> None:
        """None metadata becomes empty dict."""
        docs = [{"metadata": None}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {}

    def test_missing_metadata_defaults_to_empty(self) -> None:
        """Missing metadata key gets an empty dict added."""
        docs = [{"other_field": "value"}]
        result = _normalize_batch_metadata(docs)
        # The function adds metadata={} when the key is missing
        assert result[0].get("metadata") == {}
        assert result[0]["other_field"] == "value"

    def test_list_metadata_defaults_to_empty(self) -> None:
        """List metadata (non-dict, non-string) becomes empty dict."""
        docs = [{"metadata": [1, 2, 3]}]
        result = _normalize_batch_metadata(docs)
        assert result[0]["metadata"] == {}

    def test_mixed_batch(self) -> None:
        """Mixed valid and invalid metadata in a batch."""
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
    """Minimal host for exercising decay mixin write boundaries."""

    def __init__(self) -> None:
        self._config = {
            "access_decay_window_days": 30.0,
            "access_decay_max_count": 10.0,
            "access_count_decay_multiplier": 0.5,
            "type_aware_decay_enabled": True,
            "flashbulb.enabled": True,
            "flashbulb.intensity_threshold": 0.90,
        }
        self._db = MagicMock()
        self._db._conn = MagicMock()
        self._invalidate_cache = MagicMock()


class _RealDecayHost(DecayOperationsMixin):
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._config = {
            "access_decay_window_days": 30.0,
            "access_decay_max_count": 10.0,
            "access_count_decay_multiplier": 0.5,
            "type_aware_decay_enabled": False,
            "flashbulb.enabled": True,
            "flashbulb.intensity_threshold": 0.90,
        }
        self._db = db
        self._invalidate_cache = MagicMock()


class _TxnContext:
    def __init__(self, db: MagicMock) -> None:
        self.db = db

    async def __aenter__(self) -> MagicMock:
        return self.db

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class TestDecayWriteBoundaries:
    """Decay writes should use a single coordinated transaction."""

    @pytest.mark.asyncio
    async def test_single_access_update_uses_one_coordinated_transaction(self) -> None:
        host = _DecayHost()
        select_cursor = AsyncMock()
        select_cursor.fetchone.return_value = (json.dumps({"importance": 0.5}),)
        host._db.execute = AsyncMock(return_value=select_cursor)

        with (
            patch(
                "core.managers.decay_operations.coordinated_transaction",
                return_value=_TxnContext(host._db),
            ) as txn_mock,
        ):
            updated = await host.update_access_time(1)

        assert updated is True
        txn_mock.assert_called_once_with(host._db)
        assert host._db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_access_update_uses_one_coordinated_transaction(self) -> None:
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
                "core.managers.decay_operations.coordinated_transaction",
                return_value=_TxnContext(host._db),
            ) as txn_mock,
        ):
            affected = await host.update_access_times_batch([1, 2, 1])

        assert affected == 2
        txn_mock.assert_called_once_with(host._db)
        host._db.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_daily_decay_uses_one_coordinated_transaction(self) -> None:
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
                "core.managers.decay_operations.coordinated_transaction",
                return_value=_TxnContext(host._db),
            ) as txn_mock,
        ):
            affected = await host.apply_daily_decay(decay_rate=0.1, days=1)

        assert affected == 1
        txn_mock.assert_called_once_with(host._db)
        host._db.executemany.assert_awaited_once()


class TestDecayIdempotency:
    @pytest.mark.asyncio
    async def test_daily_decay_is_idempotent_for_same_calendar_day(
        self,
        tmp_db_path: str,
    ) -> None:
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
