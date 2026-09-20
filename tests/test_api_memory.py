"""核心 API 记忆端点测试：
- memory_batch_api.py — MemoryBatchApiMixin
- memory_read_api.py — MemoryReadApiMixin
- memory_write_api.py — MemoryWriteApiMixin
- memory_stats_recall_api.py — MemoryStatsRecallApiMixin

Validates request validation, response format, and error handling.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
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


def _canonical_list_metadata(**overrides) -> str:
    """构造能通过 canonical 读取门的 documents.metadata JSON 文本。"""

    metadata = {
        "create_time": 100,
        "scope_key": "session:test",
        "privacy_level": "public",
        "source_provenance_complete": True,
    }
    metadata.update(overrides)
    return json.dumps(metadata, ensure_ascii=False)


def _recall_engine(
    results,
    *,
    canonical: dict[int, dict | None] | None = None,
) -> MagicMock:
    """构造召回测试引擎：canonical 回读默认返回 active 行，可显式替换或置空。

    回读走真实批量端口（``faiss_db.document_storage.get_documents``）；默认记录
    带上与检索结果一致的 canonical 正文：``/recall/test`` 会比对候选与 canonical
    行的正文，未提供正文的记录按无法校验处理。
    """

    records = canonical or {}
    contents = {
        int(getattr(result, "doc_id")): getattr(result, "content", "")
        for result in results
        if isinstance(getattr(result, "doc_id", None), int)
    }

    async def _get_documents(metadata_filters=None, ids=None, limit=None, offset=None):
        documents = []
        for memory_id in list(ids or []):
            if memory_id in records:
                record = records[memory_id]
                if record is not None:
                    documents.append(dict(record))
                continue
            documents.append(
                {
                    "id": memory_id,
                    "text": contents.get(memory_id, ""),
                    "metadata": {"memory_status": "active"},
                }
            )
        return documents

    engine = MagicMock()
    engine.search_memories = AsyncMock(return_value=results)
    engine.faiss_db = SimpleNamespace(
        document_storage=SimpleNamespace(get_documents=_get_documents)
    )
    return engine


# ---------------------------------------------------------------------------
# MemoryBatchApiMixin tests
# ---------------------------------------------------------------------------


class TestMemoryBatchValidation:
    """Batch API validates request parameters."""

    @pytest.mark.asyncio
    async def test_batch_memories_requires_ids(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_memories = MemoryBatchApiMixin.batch_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [], "action": "delete"})
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_memories_rejects_non_object_json_payload(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_memories = MemoryBatchApiMixin.batch_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-memory"])
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_memories_unsupported_action(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_memories = MemoryBatchApiMixin.batch_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"memory_ids": [1, 2], "action": "invalid"}
        )
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_memories()
        assert result["status"] == "error"
        assert "不支持" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_batch_delete_valid_ids(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories = AsyncMock(return_value=2)
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [1, 2]})
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 2
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories = AsyncMock(return_value=0)
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-memory"])
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_delete_reports_not_found_ids(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories_detailed = AsyncMock(
                    return_value={
                        "deleted_count": 1,
                        "deleted_ids": [1],
                        "not_found_ids": [999],
                        "failed_ids": [],
                        "errors": [],
                    }
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [1, 999, "bad"]})
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
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
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

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

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 0
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == ["bad"]
        assert result["data"]["not_found_ids"] == []
        assert result["data"]["errors"] == []

    @pytest.mark.asyncio
    async def test_batch_update_invalid_field(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"memory_ids": [1], "field": "invalid_field", "value": "x"}
        )
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_update_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_update_rejects_non_object_json_payload(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-memory"])
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await Stub().batch_update_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_boolean_ids(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_delete_memories = MemoryBatchApiMixin.batch_delete_memories
            _delete_valid_memory_ids = MemoryBatchApiMixin._delete_valid_memory_ids
            _normalize_delete_result = staticmethod(
                MemoryBatchApiMixin._normalize_delete_result
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.batch_delete_memories = AsyncMock(return_value=1)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"memory_ids": [True, 2]})
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await stub.batch_delete_memories()
        assert result["status"] == "ok"
        stub.engine.batch_delete_memories.assert_awaited_once_with([2])
        assert result["data"]["failed_ids"] == [True]
        assert result["data"]["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_batch_update_importance_rejects_boolean_ids_and_normalizes_value(
        self,
    ) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.update_memory = AsyncMock(return_value=True)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "memory_ids": [True, "bad", 3],
                "field": "importance",
                "value": 5,
            }
        )
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await stub.batch_update_memories()
        assert result["status"] == "ok"
        stub.engine.update_memory.assert_awaited_once_with(3, {"importance": 0.5})
        assert result["data"]["updated_count"] == 1
        assert result["data"]["failed_ids"] == [True, "bad"]
        assert result["data"]["failed_count"] == 2

    @pytest.mark.asyncio
    async def test_batch_update_importance_rejects_boolean_value(self) -> None:
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            batch_update_memories = MemoryBatchApiMixin.batch_update_memories
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.update_memory = AsyncMock(return_value=True)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "memory_ids": [3],
                "field": "importance",
                "value": True,
            }
        )
        with patch("core.platform.transport.page_api.memory_batch_api.request", req):
            result = await stub.batch_update_memories()
        assert result["status"] == "ok"
        stub.engine.update_memory.assert_not_awaited()
        assert result["data"]["updated_count"] == 0
        assert result["data"]["failed_ids"] == [3]
        assert result["data"]["failed_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "expected_updates"),
        [
            (0.5, {"importance": 0.5}),
            (5, {"importance": 0.5}),
            (float("nan"), None),
            (-1, None),
            (11, None),
        ],
    )
    async def test_batch_update_impl_importance_matches_canonical_normalization(
        self, value, expected_updates
    ) -> None:
        """批量实现路径的重要性归一化必须与单条更新一致（0-1 原值，1-10 除以 10，越界拒绝）。"""
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        class Stub:
            _batch_update_memories_impl = (
                MemoryBatchApiMixin._batch_update_memories_impl
            )
            _coerce_memory_id = staticmethod(MemoryBatchApiMixin._coerce_memory_id)
            _maintenance_write_guard = staticmethod(lambda: None)

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.update_memory = AsyncMock(return_value=True)
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        result = await stub._batch_update_memories_impl([3], "importance", value)
        assert result["status"] == "ok"
        if expected_updates is None:
            stub.engine.update_memory.assert_not_awaited()
            assert result["data"]["failed_ids"] == [3]
            return
        stub.engine.update_memory.assert_awaited_once_with(3, expected_updates)
        assert result["data"]["updated_count"] == 1


# ---------------------------------------------------------------------------
# MemoryReadApiMixin tests
# ---------------------------------------------------------------------------


class TestMemoryReadValidation:
    """Read API validates parameters."""

    @pytest.mark.asyncio
    async def test_list_memories_plugin_not_ready(self) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                return None, self._error("not ready")

        req = _mock_request()
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().list_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_list_memories_invalid_pagination(self) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

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

        req = _mock_request(page="abc")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().list_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_memory_detail_non_integer_id(self) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request(memory_id="not_a_number")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "error"
        assert "整数" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_get_memory_detail_not_found(self) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "error"
        assert "不存在" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_non_mapping_record(self) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "error"
        assert "不存在" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_non_mapping_normalized_metadata(
        self,
    ) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert "metadata" not in result["data"]
        assert result["data"]["source_replayability"]["status"] == "unavailable"
        assert result["data"]["source_replayability"]["reason_codes"] == [
            "source_not_recorded"
        ]
        assert result["data"]["summary"] == "hello"
        assert result["data"]["type"] == "GENERAL"
        assert result["data"]["status"] == "active"
        assert result["data"]["importance"] == 0.5

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_non_mapping_subgraph_payload(
        self,
    ) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": {
                        "scope_key": "session:test",
                        "privacy_level": "public",
                    },
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }

            def _get_graph_store(self, engine):
                store = MagicMock()
                store.get_subgraph_for_memories = AsyncMock(return_value="bad-subgraph")
                return store

            def _normalize_metadata(self, md):
                return md

        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert result["data"]["graph_context"] is None

    @pytest.mark.asyncio
    async def test_get_memory_detail_tolerates_malformed_subgraph_collections(
        self,
    ) -> None:
        from core.features.memory.graph.domain.models import GraphBoundary
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail
            graph_store = MagicMock()
            graph_store.get_subgraph_for_memories = AsyncMock(
                return_value={
                    "nodes": "bad-nodes",
                    "edges": {"bad": "edges"},
                    "entries": None,
                }
            )

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": {
                        "scope_key": "session:test",
                        "privacy_level": "public",
                    },
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }

            def _get_graph_store(self, engine):
                return self.graph_store

            def _normalize_metadata(self, md):
                return md

        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().get_memory_detail()
        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert result["data"]["graph_context"] == {
            "nodes": [],
            "edges": [],
            "entries": [],
        }
        call = Stub()._get_graph_store(None).get_subgraph_for_memories.await_args
        assert call.kwargs["boundary"] == GraphBoundary(
            "session:test", "public", "2024-01-02"
        )

    @pytest.mark.asyncio
    async def test_get_memory_detail_normalizes_list_like_metadata_fields(self) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
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
    async def test_list_memories_tolerates_non_mapping_normalized_metadata(
        self,
    ) -> None:
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

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

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = ":memory:"
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return "bad-metadata"

        req = _mock_request()
        with (
            patch("core.platform.transport.page_api.memory_read_api.request", req),
            patch(
                "core.platform.transport.page_api.memory_read_api.aiosqlite.connect",
                fake_connect,
            ),
            patch(
                "core.platform.transport.page_api.memory_read_api.apply_perf_pragmas",
                AsyncMock(),
            ),
        ):
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
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

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
        with (
            patch("core.platform.transport.page_api.memory_read_api.request", req),
            patch(
                "core.platform.transport.page_api.memory_read_api.aiosqlite.connect",
                fake_connect,
            ),
            patch(
                "core.platform.transport.page_api.memory_read_api.apply_perf_pragmas",
                AsyncMock(),
            ),
        ):
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
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

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
                                # 身份/来源/修订字段必须被列表白名单投影过滤掉，
                                # 只有已提升到顶层的展示标量可以保留。
                                "metadata": {
                                    "memory_type": "FACT",
                                    "session_id": "sess-secret",
                                    "revision": "rev-secret",
                                    "scope_key": "session:test",
                                    "privacy_level": "private",
                                    "gate_disposition": "mark_write",
                                    "source_evidence": {"message_id": "m-1"},
                                },
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
        with (
            patch("core.platform.transport.page_api.memory_read_api.request", req),
            patch(
                "core.platform.transport.page_api.memory_read_api.aiosqlite.connect",
                fake_connect,
            ),
            patch(
                "core.platform.transport.page_api.memory_read_api.apply_perf_pragmas",
                AsyncMock(),
            ),
        ):
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

    @pytest.mark.asyncio
    async def test_list_memories_filters_mark_write_by_default(self, tmp_path) -> None:
        import aiosqlite

        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        db_path = str(tmp_path / "memories.db")

        async def _seed() -> None:
            """写入通过来源门禁的普通行，以及 mark_write 与 quarantine 两行。"""

            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "CREATE TABLE documents ("
                    "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                    " metadata TEXT, created_at TEXT, updated_at TEXT)"
                )
                await db.executemany(
                    "INSERT INTO documents"
                    " (id, doc_id, text, metadata, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            1,
                            "doc-1",
                            "normal",
                            _canonical_list_metadata(create_time=100),
                            "a",
                            "b",
                        ),
                        (
                            2,
                            "doc-2",
                            "low-confidence",
                            _canonical_list_metadata(
                                create_time=200, gate_disposition="mark_write"
                            ),
                            "c",
                            "d",
                        ),
                        (
                            3,
                            "doc-3",
                            "quarantined",
                            _canonical_list_metadata(
                                create_time=300, gate_disposition="quarantine"
                            ),
                            "e",
                            "f",
                        ),
                    ],
                )
                await db.commit()

        await _seed()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = db_path
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request()
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().list_memories()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert [item["id"] for item in result["data"]["items"]] == [3, 1]

        req = _mock_request(include_mark_write="true")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await Stub().list_memories()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["id"] for item in result["data"]["items"]] == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_list_memories_hides_sources_failing_canonical_gate(
        self, tmp_path
    ) -> None:
        """来源失效或 provenance 不完整的行不返回，且 total 与 items 同口径。"""

        import aiosqlite

        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        db_path = str(tmp_path / "memories-gate.db")

        async def _seed() -> None:
            """写入一条合法来源与五种 canonical 读取门不通过的变体。"""

            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "CREATE TABLE documents ("
                    "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                    " metadata TEXT, created_at TEXT, updated_at TEXT)"
                )
                await db.executemany(
                    "INSERT INTO documents"
                    " (id, doc_id, text, metadata, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            1,
                            "doc-1",
                            "valid",
                            _canonical_list_metadata(create_time=100),
                            "a",
                            "b",
                        ),
                        (
                            2,
                            "doc-2",
                            "orphaned",
                            _canonical_list_metadata(
                                create_time=200, summary_source_orphan=True
                            ),
                            "c",
                            "d",
                        ),
                        (
                            3,
                            "doc-3",
                            "provenance-incomplete",
                            _canonical_list_metadata(
                                create_time=300, source_provenance_complete=False
                            ),
                            "e",
                            "f",
                        ),
                        (4, "doc-4", "legacy", '{"create_time": 400}', "g", "h"),
                        (
                            5,
                            "doc-5",
                            "scope-missing",
                            _canonical_list_metadata(create_time=500, scope_key=None),
                            "i",
                            "j",
                        ),
                        (
                            6,
                            "doc-6",
                            "privacy-invalid",
                            _canonical_list_metadata(
                                create_time=600, privacy_level="secret"
                            ),
                            "k",
                            "l",
                        ),
                        (
                            7,
                            "doc-7",
                            "scope-not-text",
                            _canonical_list_metadata(create_time=700, scope_key=12345),
                            "m",
                            "n",
                        ),
                    ],
                )
                await db.commit()

        await _seed()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = db_path
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        with patch(
            "core.platform.transport.page_api.memory_read_api.request", _mock_request()
        ):
            result = await Stub().list_memories()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert [item["id"] for item in result["data"]["items"]] == [1]

    @staticmethod
    async def _list_with_db(db_path: str, **args) -> dict:
        """用独立 DB 调用列表 handler，返回完整 envelope。"""

        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = db_path
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        with patch(
            "core.platform.transport.page_api.memory_read_api.request",
            _mock_request(**args),
        ):
            return await Stub().list_memories()

    @staticmethod
    async def _seed_dropped_source_db(db_path: str) -> None:
        """写入一行合法来源与五种被 canonical 列表门剔除的变体。"""

        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                " metadata TEXT, created_at TEXT, updated_at TEXT)"
            )
            await db.executemany(
                "INSERT INTO documents"
                " (id, doc_id, text, metadata, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        1,
                        "doc-1",
                        "visible",
                        _canonical_list_metadata(
                            create_time=100, session_id="session-a"
                        ),
                        "a",
                        "b",
                    ),
                    # active 且仍可召回，但 provenance 不完整 → 列表门剔除。
                    (
                        2,
                        "doc-2",
                        "recallable but unlisted",
                        _canonical_list_metadata(
                            create_time=200,
                            session_id="session-a",
                            scope_key="scope:dropped",
                            source_provenance_complete=False,
                        ),
                        "c",
                        "d",
                    ),
                    # scope_key 非文本 → 同样被列表门剔除。
                    (
                        3,
                        "doc-3",
                        "scope not text",
                        _canonical_list_metadata(
                            create_time=300, session_id="session-a", scope_key=12345
                        ),
                        "e",
                        "f",
                    ),
                    # 另一会话的被剔除行只在该会话筛选下计数。
                    (
                        4,
                        "doc-4",
                        "other session",
                        _canonical_list_metadata(
                            create_time=400,
                            session_id="session-b",
                            source_provenance_complete=False,
                        ),
                        "g",
                        "h",
                    ),
                    # 另一状态的被剔除行只在对应状态筛选下计数。
                    (
                        5,
                        "doc-5",
                        "archived",
                        _canonical_list_metadata(
                            create_time=500,
                            session_id="session-a",
                            status="archived",
                            source_provenance_complete=False,
                        ),
                        "i",
                        "j",
                    ),
                    # mark_write 行在被默认筛选挡下时不算来源门剔除。
                    (
                        6,
                        "doc-6",
                        "mark write",
                        _canonical_list_metadata(
                            create_time=600,
                            session_id="session-a",
                            gate_disposition="mark_write",
                            source_provenance_complete=False,
                        ),
                        "k",
                        "l",
                    ),
                ],
            )
            await db.commit()

    @pytest.mark.asyncio
    async def test_list_memories_reports_rows_dropped_by_canonical_gate(
        self, tmp_path
    ) -> None:
        """被列表来源门剔除的行数按本次请求的其它筛选单独统计。"""

        db_path = str(tmp_path / "memories-dropped.db")
        await self._seed_dropped_source_db(db_path)

        default = await self._list_with_db(db_path)
        assert default["status"] == "ok"
        assert default["data"]["total"] == 1
        assert [item["id"] for item in default["data"]["items"]] == [1]
        # 顶层计数与 total 同级；mark_write 行由既有筛选挡下，不计入来源门剔除。
        assert default["data"]["dropped_source_count"] == 4
        # 计数只报告标量：被剔除行的正文与 scope 取值都不回显。
        serialized = json.dumps(default, ensure_ascii=False)
        assert "recallable but unlisted" not in serialized
        assert "scope:dropped" not in serialized

        # 会话筛选：只统计该会话内被门剔除的行。
        session_a = await self._list_with_db(db_path, session_id="session-a")
        assert session_a["data"]["total"] == 1
        assert session_a["data"]["dropped_source_count"] == 3

        session_b = await self._list_with_db(db_path, session_id="session-b")
        assert session_b["data"]["items"] == []
        assert session_b["data"]["total"] == 0
        assert session_b["data"]["dropped_source_count"] == 1

        # 状态筛选：archived 的失效行只在显式状态查询下计数。
        archived = await self._list_with_db(db_path, status="archived")
        assert archived["data"]["total"] == 0
        assert archived["data"]["dropped_source_count"] == 1

        # 关键字同样参与计数口径。
        keyword = await self._list_with_db(db_path, keyword="recallable but unlisted")
        assert keyword["data"]["total"] == 0
        assert keyword["data"]["dropped_source_count"] == 1

        # 显式纳入 mark_write 后该行才进入来源门剔除计数。
        marked = await self._list_with_db(
            db_path, session_id="session-a", include_mark_write="true"
        )
        assert marked["data"]["total"] == 1
        assert marked["data"]["dropped_source_count"] == 4

    @pytest.mark.asyncio
    async def test_list_memories_reports_zero_dropped_source_count(
        self, tmp_path
    ) -> None:
        """全部行通过来源门时计数为 0，且响应 envelope 形状不变。"""

        import aiosqlite

        db_path = str(tmp_path / "memories-no-drop.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                " metadata TEXT, created_at TEXT, updated_at TEXT)"
            )
            await db.executemany(
                "INSERT INTO documents"
                " (id, doc_id, text, metadata, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        1,
                        "doc-1",
                        "first",
                        _canonical_list_metadata(create_time=100),
                        "a",
                        "b",
                    ),
                    (
                        2,
                        "doc-2",
                        "second",
                        _canonical_list_metadata(create_time=200),
                        "c",
                        "d",
                    ),
                ],
            )
            await db.commit()

        result = await self._list_with_db(db_path)
        assert result["status"] == "ok"
        data = result["data"]
        assert [item["id"] for item in data["items"]] == [2, 1]
        assert set(data) == {
            "items",
            "total",
            "dropped_source_count",
            "page",
            "page_size",
            "has_more",
        }
        assert data["total"] == 2
        assert data["dropped_source_count"] == 0

    @pytest.mark.asyncio
    async def test_list_memories_keeps_explicit_status_filter_for_valid_sources(
        self, tmp_path
    ) -> None:
        """状态筛选仍是管理员显式能力：读取门只按来源/provenance 收敛，不按状态隐藏。"""

        import aiosqlite

        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin
        from core.platform.transport.page_api.shared_helpers import (
            SharedPageApiHelpersMixin,
        )

        db_path = str(tmp_path / "memories-status.db")

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                " metadata TEXT, created_at TEXT, updated_at TEXT)"
            )
            await db.executemany(
                "INSERT INTO documents"
                " (id, doc_id, text, metadata, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        1,
                        "doc-1",
                        "archived",
                        _canonical_list_metadata(create_time=100, status="archived"),
                        "a",
                        "b",
                    ),
                    (
                        2,
                        "doc-2",
                        "active",
                        _canonical_list_metadata(create_time=200),
                        "c",
                        "d",
                    ),
                ],
            )
            await db.commit()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories
            _normalize_metadata = staticmethod(
                SharedPageApiHelpersMixin._normalize_metadata
            )

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = db_path
                return {"memory_engine": engine}, None

        with patch(
            "core.platform.transport.page_api.memory_read_api.request",
            _mock_request(status="archived"),
        ):
            archived = await Stub().list_memories()
        assert archived["status"] == "ok"
        assert [item["id"] for item in archived["data"]["items"]] == [1]
        assert archived["data"]["items"][0]["status"] == "archived"

        with patch(
            "core.platform.transport.page_api.memory_read_api.request", _mock_request()
        ):
            all_statuses = await Stub().list_memories()
        assert all_statuses["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_list_memories_applies_requested_scope_and_privacy(
        self, tmp_path
    ) -> None:
        """请求给出的 scope/privacy 必须与 canonical 行当前门禁值一致。"""

        import aiosqlite

        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        db_path = str(tmp_path / "memories-scope.db")

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                " metadata TEXT, created_at TEXT, updated_at TEXT)"
            )
            await db.executemany(
                "INSERT INTO documents"
                " (id, doc_id, text, metadata, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        1,
                        "doc-1",
                        "scope-a public",
                        _canonical_list_metadata(
                            create_time=100,
                            scope_key="scope-a",
                            privacy_level="public",
                        ),
                        "a",
                        "b",
                    ),
                    (
                        2,
                        "doc-2",
                        "scope-b confidential",
                        _canonical_list_metadata(
                            create_time=200,
                            scope_key="scope-b",
                            privacy_level="confidential",
                        ),
                        "c",
                        "d",
                    ),
                    (
                        3,
                        "doc-3",
                        "scope-a confidential",
                        _canonical_list_metadata(
                            create_time=300,
                            scope_key="scope-a",
                            privacy_level="confidential",
                        ),
                        "e",
                        "f",
                    ),
                ],
            )
            await db.commit()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = db_path
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        async def _list(**args) -> dict:
            with patch(
                "core.platform.transport.page_api.memory_read_api.request",
                _mock_request(**args),
            ):
                return await Stub().list_memories()

        scoped = await _list(scope_key="scope-a", privacy_level="public")
        assert [item["id"] for item in scoped["data"]["items"]] == [1]
        assert scoped["data"]["total"] == 1

        scope_only = await _list(scope_key="scope-a")
        assert [item["id"] for item in scope_only["data"]["items"]] == [3, 1]

        privacy_only = await _list(privacy_level="confidential")
        assert [item["id"] for item in privacy_only["data"]["items"]] == [3, 2]

        # 不匹配任何来源的 scope 返回空页，而不是回退到跨来源列表。
        unmatched = await _list(scope_key="scope-other")
        assert unmatched["data"]["items"] == []
        assert unmatched["data"]["total"] == 0

        invalid = await _list(privacy_level="secret")
        assert invalid["status"] == "error"
        assert "privacy_level" in invalid["message"]

    @pytest.mark.asyncio
    async def test_list_memories_treats_missing_privacy_as_shared(
        self, tmp_path
    ) -> None:
        """缺失/null privacy_level 的合法历史行按 shared 口径可见，非法值仍排除。"""

        import aiosqlite

        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        db_path = str(tmp_path / "memories-legacy-privacy.db")
        legacy_without_key = json.dumps(
            {
                "create_time": 200,
                "scope_key": "session:legacy",
                "source_provenance_complete": True,
            },
            ensure_ascii=False,
        )

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT,"
                " metadata TEXT, created_at TEXT, updated_at TEXT)"
            )
            await db.executemany(
                "INSERT INTO documents"
                " (id, doc_id, text, metadata, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        1,
                        "doc-1",
                        "null privacy",
                        _canonical_list_metadata(create_time=100, privacy_level=None),
                        "a",
                        "b",
                    ),
                    (2, "doc-2", "missing privacy", legacy_without_key, "c", "d"),
                    (
                        3,
                        "doc-3",
                        "invalid privacy",
                        _canonical_list_metadata(
                            create_time=300, privacy_level="secret"
                        ),
                        "e",
                        "f",
                    ),
                ],
            )
            await db.commit()

        class Stub:
            list_memories = MemoryReadApiMixin.list_memories

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.db_path = db_path
                return {"memory_engine": engine}, None

            def _normalize_metadata(self, md):
                return md or {}

        async def _list(**args) -> dict:
            with patch(
                "core.platform.transport.page_api.memory_read_api.request",
                _mock_request(**args),
            ):
                return await Stub().list_memories()

        listed = await _list()
        assert listed["status"] == "ok"
        assert [item["id"] for item in listed["data"]["items"]] == [2, 1]
        assert listed["data"]["total"] == 2

        # 请求按 shared 收窄时，回退为 shared 的历史行同样命中；非法值永不命中。
        shared = await _list(privacy_level="shared")
        assert [item["id"] for item in shared["data"]["items"]] == [2, 1]

        confidential = await _list(privacy_level="confidential")
        assert confidential["data"]["items"] == []


class TestMemoryDetailSourceReplayability:
    """详情只暴露聚合可重放状态，Store 缺失或异常不影响详情读取。"""

    @staticmethod
    def _stub(store):
        from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin

        class Stub:
            get_memory_detail = MemoryReadApiMixin.get_memory_detail

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                manager = SimpleNamespace(store=store)
                return {
                    "memory_engine": MagicMock(),
                    "conversation_manager": manager,
                }, None

            async def _get_memory_record(self, mid):
                return {
                    "id": 123,
                    "doc_id": "doc-123",
                    "text": "hello",
                    "metadata": dict(self.metadata),
                    "created_at": "2024-01-01",
                    "updated_at": "2024-01-02",
                }

            def _get_graph_store(self, engine):
                return None

            def _normalize_metadata(self, md):
                return md

        stub = Stub()
        stub.metadata = {"k": "v"}
        return stub

    @pytest.mark.asyncio
    async def test_detail_projects_only_aggregate_replayability(self) -> None:
        from core.shared.contracts.conversation import (
            message_evidence_fingerprint,
        )

        content = "用户事实来源"
        reference = {
            "message_index": 0,
            "message_id": 7,
            "message_seq": 1,
            "role": "user",
            "start": 0,
            "end": len(content),
            "message_fingerprint": message_evidence_fingerprint("user", content),
            "inferred": False,
        }

        class Store:
            async def get_message_identity_rows(self, message_ids):
                return {
                    7: {
                        "session_id": "sess-1",
                        "message_seq": 1,
                        "role": "user",
                        "content": content,
                    }
                }

            async def get_summary_epoch(self, session_id):
                return (1, 0)

        stub = self._stub(Store())
        stub.metadata = {
            "session_id": "sess-1",
            "source_epoch": 1,
            "source_evidence": [reference],
        }
        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await stub.get_memory_detail()

        assert result["status"] == "ok"
        assert result["data"]["memory_id"] == 123
        assert not {"metadata", "session_id", "persona_id"} & set(result["data"])
        payload = result["data"]["source_replayability"]
        assert set(payload) == {"status", "references", "reason_codes"}
        assert payload["status"] == "replayable"
        assert payload["references"] == {
            "total": 1,
            "verified": 1,
            "absent": 0,
            "unverifiable": 0,
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            content,
            reference["message_fingerprint"],
            "sess-1",
            "message_seq",
            "scope_key",
            "privacy_level",
            "revision",
        ):
            assert secret not in serialized

    @pytest.mark.asyncio
    async def test_detail_projects_graph_context_without_sensitive_store_fields(
        self,
    ) -> None:
        graph_store = MagicMock()
        graph_store.get_subgraph_for_memories = AsyncMock(
            return_value={
                "nodes": [
                    {
                        "id": 7,
                        "type": "person",
                        "metadata": {"canary": "node-metadata"},
                        "label": "node-identity",
                        "entry_count": 2,
                        "weight": 1.5,
                    }
                ],
                "edges": [
                    {
                        "source": 7,
                        "target": 8,
                        "relation_type": "related",
                        "metadata": {"canary": "edge-metadata"},
                        "memory_id": 123,
                        "confidence": 0.8,
                    }
                ],
                "entries": [
                    {
                        "entry_type": "fact",
                        "relation_type": "related",
                        "content": "entry-content-canary",
                        "metadata": {"canary": "entry-metadata"},
                        "session_id": "entry-session-canary",
                        "persona_id": "entry-persona-canary",
                    }
                ],
            }
        )
        stub = self._stub(None)
        stub._get_graph_store = lambda _engine: graph_store
        stub.metadata = {
            "scope_key": "session:test",
            "privacy_level": "public",
        }
        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await stub.get_memory_detail()

        assert result["status"] == "ok"
        context = result["data"]["graph_context"]
        assert context == {
            "nodes": [{"type": "person", "entry_count": 2, "weight": 1.5}],
            "edges": [
                {"relation_type": "related", "confidence": 0.8},
            ],
            "entries": [{"entry_type": "fact", "relation_type": "related"}],
        }
        serialized = json.dumps(result, ensure_ascii=False)
        for canary in (
            "node-metadata",
            "node-identity",
            "edge-metadata",
            "entry-content-canary",
            "entry-metadata",
            "entry-session-canary",
            "entry-persona-canary",
        ):
            assert canary not in serialized

    @pytest.mark.asyncio
    async def test_detail_reports_unknown_without_message_store(self) -> None:
        stub = self._stub(None)
        stub.metadata = {
            "session_id": "sess-1",
            "source_epoch": 1,
            "source_evidence": [
                {
                    "message_index": 0,
                    "message_id": 7,
                    "message_seq": 1,
                    "role": "user",
                    "start": 0,
                    "end": 1,
                    "message_fingerprint": "a" * 64,
                    "inferred": False,
                }
            ],
        }
        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await stub.get_memory_detail()

        assert result["status"] == "ok"
        payload = result["data"]["source_replayability"]
        assert payload["status"] == "unknown"
        assert payload["reason_codes"] == ["source_store_unavailable"]

    @pytest.mark.asyncio
    async def test_detail_survives_message_store_error(self) -> None:
        class BrokenStore:
            async def get_message_identity_rows(self, message_ids):
                raise RuntimeError("db locked")

        stub = self._stub(BrokenStore())
        stub.metadata = {
            "session_id": "sess-1",
            "source_evidence": [
                {
                    "message_index": 0,
                    "message_id": 7,
                    "message_seq": 1,
                    "role": "user",
                    "start": 0,
                    "end": 1,
                    "message_fingerprint": "a" * 64,
                    "inferred": False,
                }
            ],
        }
        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await stub.get_memory_detail()

        assert result["status"] == "ok"
        payload = result["data"]["source_replayability"]
        assert payload["status"] == "unknown"
        assert payload["reason_codes"] == ["source_store_error"]

    @pytest.mark.asyncio
    async def test_detail_omits_raw_metadata_and_identity(self) -> None:
        stub = self._stub(None)
        stub.metadata = {
            "session_id": "sess-canary-identity",
            "persona_id": "persona-canary-identity",
            "scope_key": "scope-canary",
            "privacy_level": "confidential-canary",
            "revision": "revision-canary",
            "source_fence": "fence-canary",
            "_quarantine_approval_token_hash": "token-canary",
            "source_digest": "digest-canary",
            "memory_type": "FACT",
            "importance": 0.7,
        }
        req = _mock_request(memory_id="123")
        with patch("core.platform.transport.page_api.memory_read_api.request", req):
            result = await stub.get_memory_detail()

        assert result["status"] == "ok"
        assert not {"metadata", "session_id", "persona_id"} & set(result["data"])
        serialized = json.dumps(result, ensure_ascii=False)
        for canary in (
            "sess-canary-identity",
            "persona-canary-identity",
            "scope-canary",
            "confidential-canary",
            "revision-canary",
            "fence-canary",
            "token-canary",
            "digest-canary",
        ):
            assert canary not in serialized


# ---------------------------------------------------------------------------
# MemoryWriteApiMixin tests
# ---------------------------------------------------------------------------


class TestMemoryWriteValidation:
    """Write API validates update fields."""

    @pytest.mark.asyncio
    async def test_update_memory_rejected_during_pending_restore(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )
        from core.platform.transport.page_api.page_api import PluginPageApi

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

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                raise AssertionError("pending restore should short-circuit writes")

        result = await Stub().update_memory()
        assert result["status"] == "error"
        assert "重启" in result["message"]

    @pytest.mark.asyncio
    async def test_update_memory_invalid_id(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

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
        req.get_json = AsyncMock(
            return_value={"memory_id": "not_int", "field": "importance", "value": 0.5}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_rejects_non_object_json_payload(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

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
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_update_memory_rejects_boolean_id(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

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
        req.get_json = AsyncMock(
            return_value={"memory_id": True, "field": "importance", "value": 0.5}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_missing_field_or_value(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

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
        req.get_json = AsyncMock(
            return_value={"memory_id": 1, "field": "", "value": None}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

            async def _get_memory_record(self, mid):
                return None

            def _normalize_metadata(self, md):
                return md or {}

        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"memory_id": 999, "field": "status", "value": "archived"}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"
        assert "不存在" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_update_memory_invalid_status(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        req.get_json = AsyncMock(
            return_value={"memory_id": 1, "field": "status", "value": "invalid_status"}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_memory_rejects_boolean_importance_value(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        req.get_json = AsyncMock(
            return_value={"memory_id": 1, "field": "importance", "value": True}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await stub.update_memory()
        assert result["status"] == "error"
        assert "重要性必须是数字" in result["message"]
        stub.engine.update_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_memory_unsupported_field(self) -> None:
        from core.platform.transport.page_api.memory_write_api import (
            MemoryWriteApiMixin,
        )

        class Stub:
            update_memory = MemoryWriteApiMixin.update_memory

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
        req.get_json = AsyncMock(
            return_value={"memory_id": 1, "field": "unsupported", "value": "x"}
        )
        with patch("core.platform.transport.page_api.memory_write_api.request", req):
            result = await Stub().update_memory()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# MemoryStatsRecallApiMixin tests
# ---------------------------------------------------------------------------


class TestMemoryStatsRecallValidation:
    """Stats and recall API validates parameters."""

    @pytest.mark.asyncio
    async def test_stats_plugin_not_ready(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                return None, self._error("not ready")

        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            _mock_request(),
        ):
            result = await Stub().get_stats()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_stats_returns_data(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            def _get_graph_store(self, engine):
                return None

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.get_statistics = AsyncMock(
                    return_value={
                        "total": 10,
                        "status_breakdown": {"active": 8, "archived": 2, "deleted": 0},
                        "daily_memory_counts": [
                            {"date": "2026-07-12", "count": 3},
                        ],
                    }
                )
                return {"memory_engine": engine}, None

        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            _mock_request(),
        ):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["daily_memory_counts"] == [
            {"date": "2026-07-12", "count": 3},
        ]

    @pytest.mark.asyncio
    async def test_stats_tolerates_malformed_aggregate_payloads(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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
                engine.atom_store = SimpleNamespace(
                    count_atoms=AsyncMock(return_value="7"),
                    count_by_type=AsyncMock(return_value="bad-breakdown"),
                )
                return {"memory_engine": engine}, None

        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            _mock_request(),
        ):
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
    async def test_stats_prefers_canonical_filtered_atom_count(self) -> None:
        """Atom 计数使用按父 canonical 过滤的端口，不把失效来源计入对外统计。"""

        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            def _get_graph_store(self, engine):
                return None

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.get_statistics = AsyncMock(return_value={})
                engine.atom_store = SimpleNamespace(
                    # raw 口径 9 条里 4 条的父来源已失效，只看当前有效计数 5。
                    count_atoms=AsyncMock(return_value=9),
                    count_current_atoms=AsyncMock(return_value=5),
                    count_by_type=AsyncMock(return_value={"FACT": 3}),
                )
                return {"memory_engine": engine}, None

        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            _mock_request(),
        ):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["atom_count"] == 5
        assert result["data"]["atom_breakdown"] == {"FACT": 3}

    @pytest.mark.asyncio
    async def test_stats_merges_partial_importance_distribution(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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

        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            _mock_request(),
        ):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["importance_distribution"]["0-1"] == 2
        assert result["data"]["importance_distribution"]["4-5"] == 3
        assert result["data"]["importance_distribution"]["1-2"] == 0
        assert result["data"]["importance_distribution"]["bad"] == 0

    @pytest.mark.asyncio
    async def test_stats_tolerates_malformed_recent_session_counts(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            get_stats = MemoryStatsRecallApiMixin.get_stats

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

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

        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            _mock_request(),
        ):
            result = await Stub().get_stats()
        assert result["status"] == "ok"
        assert result["data"]["recent_sessions"] == [
            {"session_id": "good-session", "message_count": 3},
            {"session_id": "bad-session", "message_count": 0},
        ]

    @pytest.mark.asyncio
    async def test_recall_requires_query(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert "查询内容" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_recall_rejects_non_object_json_payload(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-query"])
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_recall_invalid_k(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "test query", "k": "invalid"})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_recall_rejects_boolean_k(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "test query", "k": True})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert "k 必须是整数" in result["message"]

    @pytest.mark.asyncio
    async def test_recall_with_valid_params(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class MockResult:
            def __init__(self, doc_id, content, score):
                self.doc_id = doc_id
                self.content = content
                self.final_score = score
                self.rrf_score = score
                self.bm25_score = score
                self.vector_score = score
                self.metadata = {
                    "memory_type": "GENERAL",
                    "status": "active",
                    "importance": 0.5,
                    "session_id": None,
                    "persona_id": None,
                    "create_time": 1234,
                    "canonical_summary": content,
                }
                self.score_breakdown = {}

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = _recall_engine(
                    [
                        MockResult(1, "result 1", 0.9),
                        MockResult(2, "result 2", 0.7),
                    ]
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "test query", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert len(result["data"]["results"]) == 2
        assert result["data"]["dropped_stale_count"] == 0
        assert result["data"]["query"] == "test query"
        assert result["data"]["k"] == 5
        assert "elapsed_time_ms" in result["data"]

    @pytest.mark.asyncio
    async def test_recall_clamps_k_and_preserves_session_filter(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = _recall_engine([])
                self.engine = engine
                return {"memory_engine": engine}, None

        stub = Stub()
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "query": "test query",
                "k": 999,
                "session_id": "sess-1",
            }
        )
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
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
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

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

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = _recall_engine([MockResult()])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 1})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
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
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class MockResult:
            def __init__(self, doc_id, final_score, metadata, content="content"):
                self.doc_id = doc_id
                self.content = content
                self.final_score = final_score
                self.metadata = metadata
                self.score_breakdown = {"doc_kw": 0.1111111, "bad": "skip"}

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = _recall_engine(
                    [
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
                    ]
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 10})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
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
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class BrokenResults:
            def __iter__(self):
                raise RuntimeError("broken result container")

            def __bool__(self):
                return True

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=BrokenResults())
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert result["data"]["results"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_recall_tolerates_malformed_score_breakdown_container(self) -> None:
        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

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

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = _recall_engine([MockResult()])
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
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

    @pytest.mark.asyncio
    async def test_recall_drops_candidates_invalidated_in_canonical(self) -> None:
        """归档/删除/来源失效后，召回测试不得返回缓存中的旧正文。"""

        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class MockResult:
            def __init__(self, doc_id, content):
                self.doc_id = doc_id
                self.content = content
                self.final_score = 0.9
                self.metadata = {
                    "memory_type": "GENERAL",
                    "status": "active",
                    "importance": 0.5,
                    "session_id": "sess-1",
                    "persona_id": None,
                    "create_time": 111,
                    "canonical_summary": content,
                }
                self.score_breakdown = {}

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = _recall_engine(
                    [
                        MockResult(1, "当前正文"),
                        MockResult(2, "已归档正文"),
                        MockResult(3, "已删除正文"),
                        MockResult(4, "来源失效正文"),
                    ],
                    canonical={
                        # 1 保持可召回；2 归档；3 已无 canonical 行；4 来源被标记失效。
                        1: {
                            "id": 1,
                            "text": "当前正文",
                            "metadata": {"memory_status": "active"},
                        },
                        2: {
                            "id": 2,
                            "text": "已归档正文",
                            "metadata": {"memory_status": "archived"},
                        },
                        3: None,
                        4: {
                            "id": 4,
                            "text": "来源失效正文",
                            "metadata": {
                                "memory_status": "active",
                                "summary_source_orphan": True,
                            },
                        },
                    },
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 10})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "ok"
        assert [item["memory_id"] for item in result["data"]["results"]] == [1]
        assert [item["content"] for item in result["data"]["results"]] == ["当前正文"]
        assert result["data"]["total"] == 1
        assert result["data"]["dropped_stale_count"] == 3
        serialized = json.dumps(result, ensure_ascii=False)
        for stale in ("已归档正文", "已删除正文", "来源失效正文"):
            assert stale not in serialized

    @pytest.mark.asyncio
    async def test_recall_fails_closed_without_canonical_reader(self) -> None:
        """缺少 canonical 回读端口时返回稳定错误，不返回未校验的缓存候选。"""

        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class MockResult:
            doc_id = 1
            content = "未校验正文"
            final_score = 0.9
            metadata = {"memory_type": "GENERAL", "status": "active"}
            score_breakdown = {}

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[MockResult()])
                engine.faiss_db = SimpleNamespace(document_storage=None)
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()
        assert result["status"] == "error"
        assert result["message"] == "召回结果校验失败"
        assert "未校验正文" not in json.dumps(result, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_recall_reads_canonical_records_in_one_batch(self) -> None:
        """canonical 回读走单次批量查询（全部候选 ID 一次读完），不逐条读取。"""

        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class MockResult:
            def __init__(self, doc_id: int) -> None:
                self.doc_id = doc_id
                self.content = "当前正文"
                self.final_score = 0.9
                self.metadata = {"memory_type": "GENERAL", "status": "active"}
                self.score_breakdown = {}

        calls: list[list[int]] = []

        async def _get_documents(
            metadata_filters=None, ids=None, limit=None, offset=None
        ):
            calls.append(list(ids or []))
            return [
                {
                    "id": memory_id,
                    "text": "当前正文",
                    "metadata": {"memory_status": "active"},
                }
                for memory_id in list(ids or [])
            ]

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(
                    return_value=[MockResult(2), MockResult(1), MockResult(2)]
                )
                # 真实引擎的单行读取会把读取异常吞成 None：本用例证明回读确实走
                # 批量端口，而不是逐条单行兜底。
                engine.get_memory = AsyncMock(return_value=None)
                engine.faiss_db = SimpleNamespace(
                    document_storage=SimpleNamespace(get_documents=_get_documents)
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()

        assert result["status"] == "ok"
        # 全部候选 ID 去重后在一次批量查询里读完（顺序按读取端口规范化）。
        assert calls == [[1, 2]]
        assert result["data"]["total"] == 3

    @pytest.mark.asyncio
    async def test_recall_fails_closed_when_canonical_batch_read_fails(self) -> None:
        """批量回读异常时返回显式失败，不把读取故障伪装成「无行」。"""

        from core.platform.transport.page_api.memory_stats_recall_api import (
            MemoryStatsRecallApiMixin,
        )

        class MockResult:
            doc_id = 1
            content = "未校验正文"
            final_score = 0.9
            metadata = {"memory_type": "GENERAL", "status": "active"}
            score_breakdown = {}

        async def _get_documents(
            metadata_filters=None, ids=None, limit=None, offset=None
        ):
            raise RuntimeError("canonical read unavailable")

        class Stub:
            test_recall = MemoryStatsRecallApiMixin.test_recall

            def _ok(self, d):
                return {"status": "ok", "data": d}

            def _error(self, m):
                return {"status": "error", "message": m}

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.search_memories = AsyncMock(return_value=[MockResult()])
                # 真实引擎的单行读取会把读取异常吞成 None；本用例证明批量端口的
                # 读取故障不会被伪装成「无行」，也不会回流到单行兜底。
                engine.get_memory = AsyncMock(return_value=None)
                engine.faiss_db = SimpleNamespace(
                    document_storage=SimpleNamespace(get_documents=_get_documents)
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"query": "edge", "k": 5})
        with patch(
            "core.platform.transport.page_api.memory_stats_recall_api.request", req
        ):
            result = await Stub().test_recall()

        assert result["status"] == "error"
        assert result["message"] == "召回结果校验失败"
        assert "未校验正文" not in json.dumps(result, ensure_ascii=False)


class TestRealtimeSSE:
    """core/api/realtime_api.py — RealtimeSSE 测试（纯逻辑测试）。"""

    def test_register_returns_client_id_and_queue(self) -> None:
        import asyncio

        from core.platform.transport.page_api.realtime_api import RealtimeSSE

        engine = MagicMock()
        sse = RealtimeSSE(engine)
        cid, q = sse.register()
        assert cid.startswith("sse_")
        assert isinstance(q, asyncio.Queue)
        assert sse.connected == 1

    def test_unregister_removes_client(self) -> None:
        from core.platform.transport.page_api.realtime_api import RealtimeSSE

        engine = MagicMock()
        sse = RealtimeSSE(engine)
        cid, q = sse.register()
        assert sse.connected == 1
        sse.unregister(cid)
        assert sse.connected == 0

    def test_connected_reflects_registrations(self) -> None:
        from core.platform.transport.page_api.realtime_api import RealtimeSSE

        engine = MagicMock()
        sse = RealtimeSSE(engine)
        sse.register()
        sse.register()
        sse.register()
        assert sse.connected == 3

    def test_try_put_returns_false_on_success(self) -> None:
        import asyncio

        from core.platform.transport.page_api.realtime_api import RealtimeSSE

        q = asyncio.Queue(maxsize=256)
        assert RealtimeSSE._try_put(q, "test") is False

    def test_try_put_returns_true_on_full(self) -> None:
        import asyncio

        from core.platform.transport.page_api.realtime_api import RealtimeSSE

        q = asyncio.Queue(maxsize=1)
        q.put_nowait("blocking")
        assert RealtimeSSE._try_put(q, "overflow") is True


class TestLearningApi:
    """Tests for core/api/learning_api.py — shadow candidate views."""

    def test_candidate_view_allowlist(self) -> None:
        from core.platform.transport.page_api.learning_api import _candidate_view

        result = _candidate_view(
            {
                "scope_domain": "private:u",
                "proposed_document_weight": 0.72,
                "proposed_graph_weight": 0.28,
                "delta_from_baseline": 0.02,
                "accepted_count": 4,
                "independent_window_count": 2,
                "decayed_support": 0.8,
                "status": "ready_for_review",
                "reason_code": "candidate",
                "secret": "must-not-leak",
            }
        )

        assert result["status"] == "ready_for_review"
        assert result["proposed_document_weight"] == 0.72
        assert "secret" not in result

    def test_candidate_view_tolerates_partial_payload(self) -> None:
        from core.platform.transport.page_api.learning_api import _candidate_view

        result = _candidate_view(
            {"status": "rejected", "reason_code": "insufficient_evidence"}
        )
        assert result["reason_code"] == "insufficient_evidence"
        assert result["proposed_document_weight"] is None
