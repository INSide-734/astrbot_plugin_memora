"""核心 API 记忆端点测试：
- memory_batch_api.py — MemoryBatchApiMixin
- memory_read_api.py — MemoryReadApiMixin
- memory_write_api.py — MemoryWriteApiMixin
- memory_stats_recall_api.py — MemoryStatsRecallApiMixin

Validates request validation, response format, and error handling.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# MemoryBatchApiMixin tests
# ---------------------------------------------------------------------------

class TestMemoryBatchValidation:
    """Batch API validates request parameters."""

    @pytest.mark.asyncio
    async def test_batch_memories_requires_ids(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_memories = MemoryBatchApiMixin.batch_memories
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [], "action": "delete"})
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_memories_rejects_non_object_json_payload(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_memories = MemoryBatchApiMixin.batch_memories

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-memory"])
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_memories_unsupported_action(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_memories = MemoryBatchApiMixin.batch_memories
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_ids": [1, 2], "action": "invalid"})
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_memories()
        assert result["status"] == "error"
        assert "不支持" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_batch_delete_valid_ids(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(MemoryBatchApiMixin._normalize_delete_result)
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories = AsyncMock(return_value=2)
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [1, 2]})
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 2
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories = AsyncMock(return_value=0)
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-memory"])
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_delete_reports_not_found_ids(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(MemoryBatchApiMixin._normalize_delete_result)
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories_detailed = AsyncMock(return_value={
                    "deleted_count": 1,
                    "deleted_ids": [1],
                    "not_found_ids": [999],
                    "failed_ids": [],
                    "errors": [],
                })
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [1, 999, "bad"]})
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 1
        assert result["data"]["failed_count"] == 2
        assert result["data"]["failed_ids"] == ["bad"]
        assert result["data"]["not_found_ids"] == [999]

    @pytest.mark.asyncio
    async def test_batch_delete_tolerates_malformed_delete_aggregate_payload(
        self,
    ) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class BrokenList:
            def __iter__(self):
                raise RuntimeError("broken aggregate list")

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories_detailed = AsyncMock(
                    return_value={
                        "deleted_count": "bad-count",
                        "failed_ids": BrokenList(),
                        "not_found_ids": BrokenList(),
                        "errors": BrokenList(),
                    }
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [1, "bad"]})
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 0
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == ["bad"]
        assert result["data"]["not_found_ids"] == []
        assert result["data"]["errors"] == []

    @pytest.mark.asyncio
    async def test_batch_update_invalid_field(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_ids": [1], "field": "invalid_field", "value": "x"})
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_update_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_update_rejects_non_object_json_payload(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-memory"])
        with patch("core.api.memory_batch_api.request", req):
            result = await Stub().batch_update_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_boolean_ids(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories = AsyncMock(return_value=1)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [True, 2]})
        with patch("core.api.memory_batch_api.request", req):
            result = await stub.batch_delete_memories()
        assert result["status"] == "ok"
        stub.engine.batch_delete_memories.assert_awaited_once_with([2])
        assert result["data"]["failed_ids"] == [True]
        assert result["data"]["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_batch_update_importance_rejects_boolean_ids_and_normalizes_value(
        self,
    ) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.update_memory = AsyncMock(return_value=True)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_ids": [True, "bad", 3],
            "field": "importance",
            "value": 5,
        })
        with patch("core.api.memory_batch_api.request", req):
            result = await stub.batch_update_memories()
        assert result["status"] == "ok"
        stub.engine.update_memory.assert_awaited_once_with(3, {"importance": 0.5})
        assert result["data"]["updated_count"] == 1
        assert result["data"]["failed_ids"] == [True, "bad"]
        assert result["data"]["failed_count"] == 2

    @pytest.mark.asyncio
    async def test_batch_update_importance_rejects_boolean_value(self) -> None:
        from core.api.memory_batch_api import MemoryBatchApiMixin

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.update_memory = AsyncMock(return_value=True)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_ids": [3],
            "field": "importance",
            "value": True,
        })
        with patch("core.api.memory_batch_api.request", req):
            result = await stub.batch_update_memories()
        assert result["status"] == "ok"
        stub.engine.update_memory.assert_not_awaited()
        assert result["data"]["updated_count"] == 0
        assert result["data"]["failed_ids"] == [3]
        assert result["data"]["failed_count"] == 1


# ---------------------------------------------------------------------------
# MemoryReadApiMixin tests
# ---------------------------------------------------------------------------

class TestMemoryReadValidation:
    """Read API validates parameters."""

    @pytest.mark.asyncio
    async def test_list_memories_plugin_not_ready(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                return None, self._error("not ready")

        req = _mock_request()
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().list_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_list_memories_invalid_pagination(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = ":memory:"
                return {"memory_engine": engine}, None

        req = _mock_request(page="abc")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().list_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_memory_detail_non_integer_id(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request(memory_id="not_a_number")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "error"
        assert "整数" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_get_memory_detail_not_found(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return None
            def _get_graph_store(self, engine):
                return None
            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request(memory_id="999")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "error"
        assert "不存在" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_non_mapping_record(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return "bad-record"
            def _get_graph_store(self, engine):
                return None
            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request(memory_id="123")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "error"
        assert "不存在" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_non_mapping_normalized_metadata(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": {"k": "v"},
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }
            def _get_graph_store(self, engine):
                return None
            def _normalize_metadata(self, md):
                return "bad-metadata"

        req = _mock_request(memory_id="123")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert result["data"]["metadata"] == {}
        assert result["data"]["summary"] == "hello"
        assert result["data"]["type"] == "GENERAL"
        assert result["data"]["status"] == "active"
        assert result["data"]["importance"] == 0.5

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_non_mapping_subgraph_payload(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": {},
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }
            def _get_graph_store(self, engine):
                store = MagicMock()
                store.get_subgraph_for_memories = AsyncMock(return_value="bad-subgraph")
                return store
            def _normalize_metadata(self, md):
                return {}

        req = _mock_request(memory_id="123")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert result["data"]["graph_context"] is None

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_malformed_subgraph_collections(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": {},
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }
            def _get_graph_store(self, engine):
                store = MagicMock()
                store.get_subgraph_for_memories = AsyncMock(
                    return_value={
                        "nodes": "bad-nodes",
                        "edges": {"bad": "edges"},
                        "entries": None,
                    }
                )
                return store
            def _normalize_metadata(self, md):
                return {}

        req = _mock_request(memory_id="123")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert result["data"]["graph_context"] == {
            "nodes": [],
            "edges": [],
            "entries": [],
        }

    @pytest.mark.asyncio
    async def test_get_memory_detail_normalizes_list_like_metadata_fields(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": {"k": "v"},
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }
            def _get_graph_store(self, engine):
                return None
            def _normalize_metadata(self, md):
                return {
                    "memory_type": "FACT",
                    "status": "archived",
                    "importance": 0.8,
                    "key_facts": "bad-key-facts",
                    "topics": {"bad": "topics"},
                    "update_history": "bad-history",
                }

        req = _mock_request(memory_id="123")
        with patch("core.api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert result["data"]["type"] == "FACT"
        assert result["data"]["status"] == "archived"
        assert result["data"]["importance"] == 0.8
        assert result["data"]["key_facts"] == []
        assert result["data"]["topics"] == []
        assert result["data"]["update_history"] == []

    @pytest.mark.asyncio
    async def test_list_memories_tolerates_non_mapping_normalized_metadata(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class FakeCursor:
            def __init__(self, *, one=None, many=None):
                self._one = one
                self._many = many or []

            async def fetchone(self):
                return self._one

            async def fetchall(self):
                return self._many

        class FakeDb:
            def __init__(self):
                self.row_factory = None

            async def execute(self, query, params):
                if "COUNT(*) AS total" in query:
                    return FakeCursor(one={"total": 1})
                return FakeCursor(
                    many=[
                        {
                            "id": 1,
                            "doc_id": "doc-1",
                            "text": "hello",
                            "metadata": '{"ok": true}',
                            "created_at": "2024-01-01",
                            "updated_at": "2024-01-02",
                        }
                    ]
                )

        @asynccontextmanager
        async def fake_connect(_db_path):
            yield FakeDb()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = ":memory:"
                return {"memory_engine": engine}, None
            def _normalize_metadata(self, md):
                return "bad-metadata"

        req = _mock_request()
        with patch("core.api.memory_read_api.request", req), patch(
            "core.api.memory_read_api.aiosqlite.connect", fake_connect
        ), patch("core.api.memory_read_api.apply_perf_pragmas", AsyncMock()):
            result = await Stub().list_memories()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert result["data"]["items"] == [
            {
                "id": 1,
                "doc_id": "doc-1",
                "text": "hello",
                "content": "hello",
                "summary": "hello",
                "type": "GENERAL",
                "status": "active",
                "importance": 0.5,
                "metadata": {},
                "created_at": "2024-01-01",
                "updated_at": "2024-01-02",
            }
        ]

    @pytest.mark.asyncio
    async def test_list_memories_skips_malformed_result_rows(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class FakeCursor:
            def __init__(self, *, one=None, many=None):
                self._one = one
                self._many = many or []

            async def fetchone(self):
                return self._one

            async def fetchall(self):
                return self._many

        class FakeDb:
            def __init__(self):
                self.row_factory = None

            async def execute(self, query, params):
                if "COUNT(*) AS total" in query:
                    return FakeCursor(one={"total": 2})
                return FakeCursor(
                    many=[
                        "bad-row",
                        {
                            "id": 2,
                            "doc_id": "doc-2",
                            "text": "world",
                            "metadata": {},
                            "created_at": "2024-01-03",
                            "updated_at": "2024-01-04",
                        },
                    ]
                )

        @asynccontextmanager
        async def fake_connect(_db_path):
            yield FakeDb()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = ":memory:"
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request()
        with patch("core.api.memory_read_api.request", req), patch(
            "core.api.memory_read_api.aiosqlite.connect", fake_connect
        ), patch("core.api.memory_read_api.apply_perf_pragmas", AsyncMock()):
            result = await Stub().list_memories()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert result["data"]["items"] == [
            {
                "id": 2,
                "doc_id": "doc-2",
                "text": "world",
                "content": "world",
                "summary": "world",
                "type": "GENERAL",
                "status": "active",
                "importance": 0.5,
                "metadata": {},
                "created_at": "2024-01-03",
                "updated_at": "2024-01-04",
            }
        ]

    @pytest.mark.asyncio
    async def test_list_memories_accepts_mapping_like_result_rows(self) -> None:
        from core.api.memory_read_api import MemoryReadApiMixin

        class MappingLikeRow:
            def __init__(self, data):
                self._data = data

            def __getitem__(self, key):
                return self._data[key]

        class FakeCursor:
            def __init__(self, *, one=None, many=None):
                self._one = one
                self._many = many or []

            async def fetchone(self):
                return self._one

            async def fetchall(self):
                return self._many

        class FakeDb:
            def __init__(self):
                self.row_factory = None

            async def execute(self, query, params):
                if "COUNT(*) AS total" in query:
                    return FakeCursor(one={"total": 1})
                return FakeCursor(
                    many=[
                        MappingLikeRow(
                            {
                                "id": 3,
                                "doc_id": "doc-3",
                                "text": "mapping-row",
                                "metadata": {"memory_type": "FACT"},
                                "created_at": "2024-01-05",
                                "updated_at": "2024-01-06",
                            }
                        )
                    ]
                )

        @asynccontextmanager
        async def fake_connect(_db_path):
            yield FakeDb()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = ":memory:"
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request()
        with patch("core.api.memory_read_api.request", req), patch(
            "core.api.memory_read_api.aiosqlite.connect", fake_connect
        ), patch("core.api.memory_read_api.apply_perf_pragmas", AsyncMock()):
            result = await Stub().list_memories()
        assert result["status"] == "ok"
        assert result["data"]["items"] == [
            {
                "id": 3,
                "doc_id": "doc-3",
                "text": "mapping-row",
                "content": "mapping-row",
                "summary": "mapping-row",
                "type": "FACT",
                "status": "active",
                "importance": 0.5,
                "metadata": {"memory_type": "FACT"},
                "created_at": "2024-01-05",
                "updated_at": "2024-01-06",
            }
        ]


# ---------------------------------------------------------------------------
# MemoryWriteApiMixin tests
# ---------------------------------------------------------------------------

class TestMemoryWriteValidation:
    """Write API validates update fields."""

    @pytest.mark.asyncio
    async def test_update_memory_rejected_during_pending_restore(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin
        from core.page_api import PluginPageApi

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            _maintenance_write_guard = PluginPageApi._maintenance_write_guard

            def __init__(self):
                self.plugin = MagicMock()
                self.plugin._backup_manager = MagicMock()
                self.plugin._backup_manager.has_pending_restores.return_value = True
                self.plugin._backup_manager.list_pending_restores.return_value = [
                    "memora.db.restore"
                ]

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                raise AssertionError("pending restore should short-circuit writes")

        result = await Stub().update_memory()
        assert result["status"] == "error"
        assert "重启" in result["message"]

    @pytest.mark.asyncio
    async def test_update_memory_invalid_id(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_id": "not_int", "field": "importance", "value": 0.5})
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_rejects_non_object_json_payload(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["not", "an", "object"])
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_update_memory_rejects_boolean_id(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_id": True, "field": "importance", "value": 0.5})
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_missing_field_or_value(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_id": 1, "field": "", "value": None})
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return None
            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_id": 999, "field": "status", "value": "archived"})
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"
        assert "不存在" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_update_memory_invalid_status(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {"text": "test", "metadata": {}}
            def _normalize_metadata(self, md):
                return md or {}
            def _importance_to_display(self, v):
                return v

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_id": 1, "field": "status", "value": "invalid_status"})
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_rejects_boolean_importance_value(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.update_memory = AsyncMock(return_value=True)
                self.engine = engine
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {"text": "test", "metadata": {}}
            def _normalize_metadata(self, md):
                return md or {}
            def _importance_to_display(self, v):
                return v

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_id": 1, "field": "importance", "value": True})
        with patch("core.api.memory_write_api.request", req):
            result = await stub.update_memory()
        assert result["status"] == "error"
        assert "重要性必须是数字" in result["message"]
        stub.engine.update_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_memory_unsupported_field(self) -> None:
        from core.api.memory_write_api import MemoryWriteApiMixin

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None
            async def _get_memory_record(self, mid):
                return {"text": "test", "metadata": {}}
            def _normalize_metadata(self, md):
                return md or {}
            def _importance_to_display(self, v):
                return v

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "memory_id": 1, "field": "unsupported", "value": "x"})
        with patch("core.api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# MemoryStatsRecallApiMixin tests
# ---------------------------------------------------------------------------

class TestMemoryStatsRecallValidation:
    """Stats and recall API validates parameters."""

    @pytest.mark.asyncio
    async def test_stats_plugin_not_ready(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                return None, self._error("not ready")

        with patch("core.api.memory_stats_recall_api.request", _mock_request()):
            result = await Stub().get_stats()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_stats_returns_data(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            def _get_graph_store(self, engine):
                return None
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.get_statistics = AsyncMock(return_value={
                    "total": 10,
                    "status_breakdown": {"active": 8, "archived": 2, "deleted": 0},
                    "daily_memory_counts": [
                        {"date": "2026-07-12", "count": 3},
                    ],
                })
                return {"memory_engine": engine}, None

        with patch("core.api.memory_stats_recall_api.request", _mock_request()):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["daily_memory_counts"] == [
            {"date": "2026-07-12", "count": 3},
        ]

    @pytest.mark.asyncio
    async def test_stats_tolerates_malformed_aggregate_payloads(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            def _get_graph_store(self, engine):
                store = MagicMock()
                store.get_memory_entry_stats = AsyncMock(return_value="bad-graph-stats")
                return store

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.get_statistics = AsyncMock(
                    return_value={
                        "status_breakdown": "bad-breakdown",
                        "sessions": "bad-sessions",
                        "importance_distribution": "bad-distribution",
                    }
                )
                engine.atom_store = MagicMock()
                engine.atom_store.count_atoms = AsyncMock(return_value="7")
                engine.atom_store.count_by_type = AsyncMock(return_value="bad-breakdown")
                return {"memory_engine": engine}, None

        with patch("core.api.memory_stats_recall_api.request", _mock_request()):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["active_count"] == 0
        assert result["data"]["archived_count"] == 0
        assert result["data"]["deleted_count"] == 0
        assert result["data"]["graph_nodes"] == 0
        assert result["data"]["graph_edges"] == 0
        assert result["data"]["graph_entries"] == 0
        assert result["data"]["atom_count"] == 7
        assert result["data"]["atom_breakdown"] == {}
        assert result["data"]["recent_sessions"] == []
        assert result["data"]["importance_distribution"] == {
            f"{i}-{i + 1}": 0 for i in range(0, 10)
        }

    @pytest.mark.asyncio
    async def test_stats_merges_partial_importance_distribution(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            def _get_graph_store(self, engine):
                return None

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.get_statistics = AsyncMock(
                    return_value={
                        "importance_distribution": {
                            "0-1": "2",
                            "4-5": 3,
                            "bad": "oops",
                        }
                    }
                )
                return {"memory_engine": engine}, None

        with patch("core.api.memory_stats_recall_api.request", _mock_request()):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["importance_distribution"]["0-1"] == 2
        assert result["data"]["importance_distribution"]["4-5"] == 3
        assert result["data"]["importance_distribution"]["1-2"] == 0
        assert result["data"]["importance_distribution"]["bad"] == 0

    @pytest.mark.asyncio
    async def test_stats_tolerates_malformed_recent_session_counts(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            def _get_graph_store(self, engine):
                return None

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.get_statistics = AsyncMock(
                    return_value={
                        "sessions": {
                            "good-session": 3,
                            "bad-session": "oops",
                        }
                    }
                )
                return {"memory_engine": engine}, None

        with patch("core.api.memory_stats_recall_api.request", _mock_request()):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["recent_sessions"] == [
            {"session_id": "good-session", "message_count": 3},
            {"session_id": "bad-session", "message_count": 0},
        ]

    @pytest.mark.asyncio
    async def test_recall_requires_query(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "", "k": 5})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert "查询内容" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_recall_rejects_non_object_json_payload(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-query"])
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_recall_invalid_k(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "query": "test query", "k": "invalid"})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_recall_rejects_boolean_k(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "query": "test query", "k": True})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert "k 必须是整数" in result["message"]

    @pytest.mark.asyncio
    async def test_recall_with_valid_params(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class MockResult:
            def __init__(self, doc_id, content, score):
                self.doc_id = doc_id
                self.content = content
                self.final_score = score
                self.rrf_score = score
                self.bm25_score = score
                self.vector_score = score
                self.metadata = {"memory_type": "GENERAL", "status": "active",
                                 "importance": 0.5, "session_id": None,
                                 "persona_id": None, "create_time": 1234,
                                 "canonical_summary": content}
                self.score_breakdown = {}

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall
            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}
            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[
                    MockResult(1, "result 1", 0.9),
                    MockResult(2, "result 2", 0.7),
                ])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "query": "test query", "k": 5})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert len(result["data"]["results"]) == 2
        assert result["data"]["query"] == "test query"
        assert result["data"]["k"] == 5
        assert "elapsed_time_ms" in result["data"]

    @pytest.mark.asyncio
    async def test_recall_clamps_k_and_preserves_session_filter(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[])
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "query": "test query",
            "k": 999,
            "session_id": "sess-1",
        })
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await stub.test_recall()
        assert result["status"] == "ok"
        stub.engine.search_memories.assert_awaited_once_with(
            query="test query",
            k=50,
            session_id="sess-1",
            persona_id=None,
        )
        assert result["data"]["k"] == 50
        assert result["data"]["session_id_filter"] == "sess-1"

    @pytest.mark.asyncio
    async def test_recall_filters_non_numeric_score_breakdown_values(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class MockResult:
            def __init__(self):
                self.doc_id = 7
                self.content = "fallback content"
                self.final_score = 0.87654
                self.metadata = {
                    "memory_type": "FACT",
                    "status": "archived",
                    "importance": 0.8,
                    "session_id": "sess-9",
                    "persona_id": "persona-x",
                    "create_time": 5678,
                    "canonical_summary": "",
                }
                self.score_breakdown = {
                    "doc_kw": 0.1234567,
                    "doc_vec": 0.2,
                    "graph_kw": "skip-me",
                    "graph_vec": None,
                    "nested": {"bad": 1},
                }

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[MockResult()])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 1})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        item = result["data"]["results"][0]
        assert item["summary"] == "fallback content"
        assert item["score"] == 0.8765
        assert item["score_percentage"] == 87.65
        assert item["doc_kw_score"] == 0.123457
        assert item["doc_vec_score"] == 0.2
        assert item["graph_kw_score"] is None
        assert item["graph_vec_score"] is None
        assert item["metadata"]["doc_kw"] == 0.123457
        assert "graph_kw" not in item["metadata"]

    @pytest.mark.asyncio
    async def test_recall_skips_malformed_result_objects(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class MockResult:
            def __init__(self, doc_id, final_score, metadata, content="content"):
                self.doc_id = doc_id
                self.content = content
                self.final_score = final_score
                self.metadata = metadata
                self.score_breakdown = {"doc_kw": 0.1111111, "bad": "skip"}

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[
                    MockResult(
                        doc_id=5,
                        final_score=0.81234,
                        metadata={
                            "memory_type": "GENERAL",
                            "status": "active",
                            "importance": 0.6,
                            "session_id": "sess-5",
                            "persona_id": None,
                            "create_time": 111,
                            "canonical_summary": "",
                        },
                        content="good result",
                    ),
                    MockResult(
                        doc_id="oops",
                        final_score=0.7,
                        metadata={"memory_type": "GENERAL"},
                        content="bad doc id",
                    ),
                    MockResult(
                        doc_id=6,
                        final_score="nan?",
                        metadata={"memory_type": "GENERAL"},
                        content="bad score",
                    ),
                    MockResult(
                        doc_id=7,
                        final_score=0.5,
                        metadata="bad metadata",
                        content="bad metadata",
                    ),
                ])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 10})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert result["data"]["results"] == [
            {
                "id": 5,
                "score": 0.8123,
                "type": "GENERAL",
                "importance": 0.6,
                "created_at": 111,
                "summary": "good result",
                "doc_kw_score": 0.111111,
                "doc_vec_score": None,
                "graph_kw_score": None,
                "graph_vec_score": None,
                "memory_id": 5,
                "content": "good result",
                "similarity_score": 0.8123,
                "score_percentage": 81.23,
                "metadata": {
                    "session_id": "sess-5",
                    "persona_id": None,
                    "importance": 0.6,
                    "memory_type": "GENERAL",
                    "status": "active",
                    "create_time": 111,
                    "doc_kw": 0.111111,
                },
                "score_breakdown": {"doc_kw": 0.111111},
            }
        ]

    @pytest.mark.asyncio
    async def test_recall_tolerates_non_iterable_result_container(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class BrokenResults:
            def __iter__(self):
                raise RuntimeError("broken result container")

            def __bool__(self):
                return True

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=BrokenResults())
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert result["data"]["results"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_recall_tolerates_malformed_score_breakdown_container(self) -> None:
        from core.api.memory_stats_recall_api import MemoryStatsRecallApiMixin

        class BrokenBreakdown:
            def items(self):
                raise RuntimeError("broken score breakdown")

            def __bool__(self):
                return True

        class MockResult:
            def __init__(self):
                self.doc_id = 5
                self.content = "good result"
                self.final_score = 0.81234
                self.metadata = {
                    "memory_type": "GENERAL",
                    "status": "active",
                    "importance": 0.6,
                    "session_id": "sess-5",
                    "persona_id": None,
                    "create_time": 111,
                    "canonical_summary": "",
                }
                self.score_breakdown = BrokenBreakdown()

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d): return {"status": "ok", "data": d}
            def _error(self, m): return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[MockResult()])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch("core.api.memory_stats_recall_api.request", req):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert result["data"]["results"] == [
            {
                "id": 5,
                "score": 0.8123,
                "type": "GENERAL",
                "importance": 0.6,
                "created_at": 111,
                "summary": "good result",
                "doc_kw_score": None,
                "doc_vec_score": None,
                "graph_kw_score": None,
                "graph_vec_score": None,
                "memory_id": 5,
                "content": "good result",
                "similarity_score": 0.8123,
                "score_percentage": 81.23,
                "metadata": {
                    "session_id": "sess-5",
                    "persona_id": None,
                    "importance": 0.6,
                    "memory_type": "GENERAL",
                    "status": "active",
                    "create_time": 111,
                },
                "score_breakdown": {},
            }
        ]


class TestRealtimeSSE:
    """core/api/realtime_api.py — RealtimeSSE 测试（纯逻辑测试）。"""

    def test_register_returns_client_id_and_queue(self) -> None:
        import asyncio
        from core.api.realtime_api import RealtimeSSE

        engine = MagicMock()
        sse = RealtimeSSE(engine)
        cid, q = sse.register()
        assert cid.startswith("sse_")
        assert isinstance(q, asyncio.Queue)
        assert sse.connected == 1

    def test_unregister_removes_client(self) -> None:
        from core.api.realtime_api import RealtimeSSE

        engine = MagicMock()
        sse = RealtimeSSE(engine)
        cid, q = sse.register()
        assert sse.connected == 1
        sse.unregister(cid)
        assert sse.connected == 0

    def test_connected_reflects_registrations(self) -> None:
        from core.api.realtime_api import RealtimeSSE

        engine = MagicMock()
        sse = RealtimeSSE(engine)
        sse.register()
        sse.register()
        sse.register()
        assert sse.connected == 3

    def test_try_put_returns_false_on_success(self) -> None:
        import asyncio
        from core.api.realtime_api import RealtimeSSE

        q = asyncio.Queue(maxsize=256)
        assert RealtimeSSE._try_put(q, "test") is False

    def test_try_put_returns_true_on_full(self) -> None:
        import asyncio
        from core.api.realtime_api import RealtimeSSE

        q = asyncio.Queue(maxsize=1)
        q.put_nowait("blocking")
        assert RealtimeSSE._try_put(q, "overflow") is True


class TestLearningApi:
    """Tests for core/api/learning_api.py — _flatten_learning_stats."""

    def test_flatten_stats_with_full_data(self) -> None:
        from core.api.learning_api import _flatten_learning_stats

        raw = {
            "feedback": {
                "total_hits": 50,
                "total_recalls": 100,
                "avg_quality": 0.75,
                "total_corrections": 3,
            },
            "params": {"alpha": 0.5, "beta": 0.25},
            "history": [
                {"timestamp": "t1", "reason": "adjust", "param": "alpha",
                 "old": 0.4, "new": 0.5},
            ],
            "enabled": True,
        }
        result = _flatten_learning_stats(raw)
        assert result["hit_rate"] == 0.5  # 50/100
        assert result["avg_quality"] == 0.75
        assert result["total_trials"] == 100
        assert result["total_corrections"] == 3
        assert result["enabled"] is True
        assert "parameters" in result
        assert len(result["history"]) == 1

    def test_flatten_stats_with_zero_recalls(self) -> None:
        from core.api.learning_api import _flatten_learning_stats

        raw = {
            "feedback": {"total_hits": 0, "total_recalls": 0},
            "params": {},
            "history": [],
            "enabled": False,
        }
        result = _flatten_learning_stats(raw)
        # total_recalls of 0 becomes max(0, 1) = 1 to avoid division by zero
        assert result["hit_rate"] == 0.0  # 0 / 1
        assert result["total_trials"] == 1

    def test_flatten_stats_with_empty_raw(self) -> None:
        from core.api.learning_api import _flatten_learning_stats

        result = _flatten_learning_stats({})
        assert result["hit_rate"] == 0.0
        assert result["avg_quality"] == 0.5
        assert result["history"] == []

    def test_flatten_stats_with_missing_feedback(self) -> None:
        from core.api.learning_api import _flatten_learning_stats

        raw = {"params": {"x": 1}, "history": [], "enabled": True}
        result = _flatten_learning_stats(raw)
        assert result["avg_quality"] == 0.5
        assert result["parameters"] == {"x": 1}

    def test_flatten_history_detail_formatting(self) -> None:
        from core.api.learning_api import _flatten_learning_stats

        raw = {
            "feedback": {"total_hits": 10, "total_recalls": 20,
                        "avg_quality": 0.6, "total_corrections": 2},
            "params": {},
            "history": [
                {"timestamp": "t1", "reason": "decay_rate adjusted",
                 "param": "decay_rate", "old": 0.01, "new": 0.02},
            ],
            "enabled": True,
        }
        result = _flatten_learning_stats(raw)
        entry = result["history"][0]
        assert "decay_rate: 0.01" in entry["detail"]
        assert "0.02" in entry["detail"]
