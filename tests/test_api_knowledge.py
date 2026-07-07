"""core/api/knowledge_api.py — KnowledgeApiMixin 测试。

Validates request validation, response format, and error handling.
Uses unittest.mock.patch to mock quart.request imports.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.knowledge_api import KnowledgeApiMixin
from core.models.knowledge_models import KnowledgeEntry, KnowledgeType


def _make_mock_request(**args):
    """Create a mock quart.request with args dict and async get_json."""
    mock = MagicMock()
    mock.args = args  # plain dict for .get() access
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(*, store_available: bool = True,
                entries_list: list | None = None,
                entries_total: int = 0,
                search_entries: list | None = None,
                search_total: int = 0,
                detail_entry: KnowledgeEntry | None = None,
                insert_id: int = 1,
                delete_result: bool = True,
                plugin_ready: bool = True):
    """Create a KnowledgeApiMixin stub with mocked MemoryEngine."""

    class Stub:
        list_knowledge = KnowledgeApiMixin.list_knowledge
        search_knowledge = KnowledgeApiMixin.search_knowledge
        get_knowledge_detail = KnowledgeApiMixin.get_knowledge_detail
        create_knowledge_entry = KnowledgeApiMixin.create_knowledge_entry
        update_knowledge_entry = KnowledgeApiMixin.update_knowledge_entry
        delete_knowledge_entry = KnowledgeApiMixin.delete_knowledge_entry
        batch_knowledge = KnowledgeApiMixin.batch_knowledge
        batch_delete_knowledge = KnowledgeApiMixin.batch_delete_knowledge
        batch_update_knowledge = KnowledgeApiMixin.batch_update_knowledge
        _batch_delete_knowledge_impl = KnowledgeApiMixin._batch_delete_knowledge_impl

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "plugin not ready"}
            if store_available:
                engine = MagicMock()
                engine.knowledge_manager = MagicMock()
                engine.knowledge_manager.list_entries = AsyncMock(
                    return_value=(entries_list or [], entries_total))
                engine.knowledge_manager.search = AsyncMock(
                    return_value=(search_entries or [], search_total))
                engine.knowledge_manager.get_entry = AsyncMock(
                    return_value=detail_entry)
                engine.knowledge_manager.add_entry = AsyncMock(return_value=insert_id)
                engine.knowledge_manager.update_entry = AsyncMock(return_value=True)
                engine.knowledge_manager.delete_entry = AsyncMock(
                    return_value=delete_result)
            else:
                engine = MagicMock(spec=[])
            self.engine = engine
            return {"memory_engine": engine}, None

    return Stub()


class TestKnowledgeValidation:
    """Knowledge API validates required fields."""

    @pytest.mark.asyncio
    async def test_create_requires_title_and_content(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"title": "", "content": ""})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.create_knowledge_entry()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_rejects_non_object_json_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value=["bad-entry"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.create_knowledge_entry()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_get_detail_requires_entry_id(self) -> None:
        mock_req = _make_mock_request(entry_id="0")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.get_knowledge_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_rejects_non_numeric_entry_id(self) -> None:
        mock_req = _make_mock_request(entry_id="abc")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.get_knowledge_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_search_requires_query(self) -> None:
        mock_req = _make_mock_request(query="")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.search_knowledge()
        assert result["status"] == "error"
        assert "query" in result.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_search_rejects_non_numeric_limit(self) -> None:
        mock_req = _make_mock_request(query="topic", limit="abc")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.search_knowledge()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_requires_entry_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"entry_id": 0})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.delete_knowledge_entry()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_rejects_non_object_json_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value=["bad-entry"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.delete_knowledge_entry()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_rejects_non_numeric_entry_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"entry_id": "abc"})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.delete_knowledge_entry()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_rejects_boolean_entry_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"entry_id": True})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.delete_knowledge_entry()
        assert result["status"] == "error"
        assert "entry_id must be an integer" in result["message"]

    @pytest.mark.asyncio
    async def test_update_requires_entry_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "entry_id": 0, "title": "new"})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_rejects_non_object_json_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value=["bad-entry"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_update_rejects_non_numeric_entry_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "entry_id": "abc", "title": "new"})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_rejects_boolean_entry_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={"entry_id": True, "field": "title", "value": "new"}
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(
                detail_entry=KnowledgeEntry(
                    title="old",
                    content="body",
                    category=KnowledgeType.FACT,
                    confidence=0.8,
                    tags=["a"],
                )
            )
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "error"
        assert "entry_id must be an integer" in result["message"]

    @pytest.mark.asyncio
    async def test_update_entry_not_found(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "entry_id": 999, "title": "new", "content": "body"})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(detail_entry=None)
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_requires_ids(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "entry_ids": [], "action": "delete"})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.batch_knowledge()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_knowledge_rejects_non_object_json_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value=["bad-entry"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.batch_knowledge()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_list_rejects_non_numeric_limit_or_offset(self) -> None:
        mock_req = _make_mock_request(limit="abc", offset="1x")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.list_knowledge()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_unsupported_action(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "entry_ids": [1, 2], "action": "invalid"})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.batch_knowledge()
        assert result["status"] == "error"
        assert "不支持" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value=["bad-entry"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_knowledge()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_update_rejects_non_object_json_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value=["bad-entry"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin()
            result = await mixin.batch_update_knowledge()
        assert result["status"] == "error"
        assert "JSON" in result["message"]


class TestKnowledgeStoreUnavailable:
    """Knowledge API when store is unavailable."""

    @pytest.mark.asyncio
    async def test_search_requires_query_when_store_unavailable(self) -> None:
        """When store is unavailable, empty query still returns error."""
        mock_req = _make_mock_request(query="")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(store_available=False)
            result = await mixin.search_knowledge()
        # Empty query triggers validation before store check
        assert result["status"] == "error"


class TestKnowledgeHappyPath:
    """Knowledge API with mocked store."""

    @pytest.mark.asyncio
    async def test_list_skips_malformed_entry_items(self) -> None:
        mock_req = _make_mock_request(limit="10", offset="0")
        broken = MagicMock()
        type(broken).to_dict = lambda self: (_ for _ in ()).throw(RuntimeError("broken knowledge entry"))
        good_1 = KnowledgeEntry(
            title="a",
            content="body-a",
            category=KnowledgeType.FACT,
            confidence=0.8,
            tags=["x"],
        )
        good_2 = KnowledgeEntry(
            title="b",
            content="body-b",
            category=KnowledgeType.RULE,
            confidence=0.7,
            tags=["y"],
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(entries_list=[good_1, broken, good_2], entries_total=3)
            result = await mixin.list_knowledge()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["title"] for item in result["data"]["entries"]] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_tolerates_malformed_entry_container_and_total(self) -> None:
        class BrokenEntries:
            def __iter__(self):
                raise RuntimeError("broken knowledge entries")

            def __bool__(self):
                return True

        class Stub:
            list_knowledge = KnowledgeApiMixin.list_knowledge

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.knowledge_manager = MagicMock()
                engine.knowledge_manager.list_entries = AsyncMock(
                    return_value=(BrokenEntries(), "bad-total")
                )
                return {"memory_engine": engine}, None

        mock_req = _make_mock_request(limit="10", offset="0")
        with patch("core.api.knowledge_api.request", mock_req):
            result = await Stub().list_knowledge()
        assert result["status"] == "ok"
        assert result["data"]["entries"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_search_skips_malformed_entry_items(self) -> None:
        mock_req = _make_mock_request(query="topic", limit="10")
        broken = MagicMock()
        type(broken).to_dict = lambda self: (_ for _ in ()).throw(RuntimeError("broken knowledge entry"))
        good = KnowledgeEntry(
            title="a",
            content="body-a",
            category=KnowledgeType.FACT,
            confidence=0.8,
            tags=["x"],
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(search_entries=[good, broken], search_total=2)
            result = await mixin.search_knowledge()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert [item["title"] for item in result["data"]["entries"]] == ["a"]

    @pytest.mark.asyncio
    async def test_search_with_query(self) -> None:
        mock_req = _make_mock_request(query="test")
        e = KnowledgeEntry(title="test", content="body", category=KnowledgeType.FACT,
                           confidence=0.8, tags=["a"])
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(search_entries=[e], search_total=1)
            result = await mixin.search_knowledge()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_create_inserts_and_returns_id(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "title": "New Knowledge", "content": "Some content",
            "category": "fact", "confidence": 0.9, "tags": ["tag1"]
        })
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(insert_id=42)
            result = await mixin.create_knowledge_entry()
        assert result["status"] == "ok"
        assert result["data"]["entry_id"] == 42

    @pytest.mark.asyncio
    async def test_create_treats_boolean_confidence_as_default(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "title": "New Knowledge", "content": "Some content",
            "category": "fact", "confidence": True, "tags": ["tag1"]
        })
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(insert_id=42)
            result = await mixin.create_knowledge_entry()
        assert result["status"] == "ok"
        created_entry = mixin.engine.knowledge_manager.add_entry.await_args.args[0]
        assert created_entry.confidence == 0.5

    @pytest.mark.asyncio
    async def test_create_normalizes_string_tags_payload(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={
                "title": "New Knowledge",
                "content": "Some content",
                "category": "fact",
                "confidence": 0.9,
                "tags": "tag1, tag2",
            }
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(insert_id=42)
            result = await mixin.create_knowledge_entry()
        assert result["status"] == "ok"
        created_entry = mixin.engine.knowledge_manager.add_entry.await_args.args[0]
        assert created_entry.tags == ["tag1", "tag2"]

    @pytest.mark.asyncio
    async def test_delete_returns_result(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"entry_id": 1})
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.delete_knowledge_entry()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio
    async def test_batch_delete_knowledge(self) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "entry_ids": [1, 2, 3], "action": "delete"
        })
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.batch_knowledge()
        assert result["status"] == "ok"
        assert "deleted_count" in result["data"]
        assert result["data"]["total"] == 3

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_boolean_entry_ids_and_processes_valid_ones(
        self,
    ) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={"entry_ids": [True, 2], "action": "delete"}
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.batch_knowledge()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 1
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == [True]

    @pytest.mark.asyncio
    async def test_batch_update_category_rejects_boolean_ids_and_updates_valid_ones(
        self,
    ) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={
                "entry_ids": [True, 2],
                "field": "category",
                "value": "rule",
            }
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(
                detail_entry=KnowledgeEntry(
                    title="old",
                    content="body",
                    category=KnowledgeType.FACT,
                    confidence=0.8,
                    tags=["a"],
                )
            )
            result = await mixin.batch_update_knowledge()
        assert result["status"] == "ok"
        assert result["data"]["updated_count"] == 1
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == [True]

    @pytest.mark.asyncio
    async def test_batch_update_category_marks_every_id_failed_when_value_invalid(
        self,
    ) -> None:
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={
                "entry_ids": [1, 2],
                "field": "category",
                "value": "not-a-category",
            }
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(
                detail_entry=KnowledgeEntry(
                    title="old",
                    content="body",
                    category=KnowledgeType.FACT,
                    confidence=0.8,
                    tags=["a"],
                )
            )
            result = await mixin.batch_update_knowledge()
        assert result["status"] == "ok"
        assert result["data"]["updated_count"] == 0
        assert result["data"]["failed_count"] == 2
        assert result["data"]["failed_ids"] == [1, 2]

    @pytest.mark.asyncio
    async def test_update_ignores_boolean_confidence_and_preserves_existing_value(
        self,
    ) -> None:
        entry = KnowledgeEntry(
            title="old",
            content="body",
            category=KnowledgeType.FACT,
            confidence=0.8,
            tags=["a"],
        )
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={
                "entry_id": 2,
                "field": "confidence",
                "value": True,
            }
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(detail_entry=entry)
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "ok"
        updated_entry = mixin.engine.knowledge_manager.update_entry.await_args.args[0]
        assert updated_entry.confidence == 0.8

    @pytest.mark.asyncio
    async def test_update_normalizes_string_tags_payload(self) -> None:
        entry = KnowledgeEntry(
            title="old",
            content="body",
            category=KnowledgeType.FACT,
            confidence=0.8,
            tags=["a"],
        )
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            return_value={
                "entry_id": 2,
                "tags": "tag1, tag2",
            }
        )
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(detail_entry=entry)
            result = await mixin.update_knowledge_entry()
        assert result["status"] == "ok"
        updated_entry = mixin.engine.knowledge_manager.update_entry.await_args.args[0]
        assert updated_entry.tags == ["tag1", "tag2"]

    @pytest.mark.asyncio
    async def test_detail_not_found(self) -> None:
        mock_req = _make_mock_request(entry_id="5")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(detail_entry=None)
            result = await mixin.get_knowledge_detail()
        assert result["status"] == "error"
        assert "not found" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_detail_returns_error_for_malformed_entry_payload(self) -> None:
        mock_req = _make_mock_request(entry_id="1")
        broken = MagicMock()
        broken.to_dict.side_effect = RuntimeError("broken knowledge entry")
        with patch("core.api.knowledge_api.request", mock_req):
            mixin = _make_mixin(detail_entry=broken)
            result = await mixin.get_knowledge_detail()
        assert result["status"] == "error"
        assert "entry serialization failed" in result["message"]
