"""core/api/knowledge_api.py 测试 — KnowledgeApiMixin（CRUD、搜索、批量）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.base.list_sorting import SortQuery


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(*, plugin_ready: bool = True, has_store: bool = True):
    from core.api.knowledge_api import KnowledgeApiMixin

    class Stub:
        list_knowledge = KnowledgeApiMixin.list_knowledge
        search_knowledge = KnowledgeApiMixin.search_knowledge
        get_knowledge_detail = KnowledgeApiMixin.get_knowledge_detail
        create_knowledge_entry = KnowledgeApiMixin.create_knowledge_entry
        update_knowledge_entry = KnowledgeApiMixin.update_knowledge_entry
        delete_knowledge_entry = KnowledgeApiMixin.delete_knowledge_entry
        batch_delete_knowledge = KnowledgeApiMixin.batch_delete_knowledge

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "not ready"}
            if has_store:
                engine = MagicMock()
                engine.knowledge_manager = MagicMock()
                engine.knowledge_manager.list_entries = AsyncMock(return_value=([], 0))
                engine.knowledge_manager.search = AsyncMock(return_value=([], 0))
            else:
                engine = MagicMock(spec=[])
            return {"memory_engine": engine}, None

    return Stub()


class TestKnowledgeValidation:
    @pytest.mark.asyncio
    async def test_list_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin(plugin_ready=False).list_knowledge()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_list_no_store(self) -> None:
        req = _mock_request()
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin(has_store=False).list_knowledge()
        assert r["status"] == "ok"
        assert r["data"]["entries"] == []

    @pytest.mark.asyncio
    async def test_search_no_query(self) -> None:
        req = _mock_request(query="")
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().search_knowledge()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_search_no_store(self) -> None:
        req = _mock_request(query="test")
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin(has_store=False).search_knowledge()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_list_rejects_invalid_sort_key(self) -> None:
        req = _mock_request(sort_by="title;drop", sort_order="asc")
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().list_knowledge()
        assert r["status"] == "error"
        assert r["code"] == "invalid_query"
        assert r["field_errors"]["sort_by"] == "sort_by is not supported"

    @pytest.mark.asyncio
    async def test_search_rejects_invalid_sort_order(self) -> None:
        req = _mock_request(query="test", sort_by="title", sort_order="DESC")
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().search_knowledge()
        assert r["status"] == "error"
        assert r["code"] == "invalid_query"
        assert r["field_errors"]["sort_by"] == "sort_order must be asc or desc"

    @pytest.mark.asyncio
    async def test_get_detail_missing_id(self) -> None:
        req = _mock_request(entry_id="0")
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().get_knowledge_detail()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_not_found(self) -> None:
        req = _mock_request(entry_id="999")
        mixin = _make_mixin()
        async def _ready():
            engine = MagicMock()
            manager = MagicMock()
            manager.get_entry = AsyncMock(return_value=None)
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.get_knowledge_detail()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_no_store(self) -> None:
        req = _mock_request(entry_id="1")
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin(has_store=False).get_knowledge_detail()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_missing_title(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"title": "", "content": ""})
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().create_knowledge_entry()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_no_store(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"title": "T", "content": "C", "category": "fact"})
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin(has_store=False).create_knowledge_entry()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_missing_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"entry_id": 0})
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().update_knowledge_entry()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_missing_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"entry_id": 0})
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().delete_knowledge_entry()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_no_ids(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"entry_ids": []})
        with patch("core.api.knowledge_api.request", req):
            r = await _make_mixin().batch_delete_knowledge()
        assert r["status"] == "error"


class TestKnowledgeHappyPath:
    @pytest.mark.asyncio
    async def test_list_knowledge(self) -> None:
        req = _mock_request()
        mixin = _make_mixin()
        manager = MagicMock()
        manager.list_entries = AsyncMock(return_value=([], 0))
        async def _ready():
            engine = MagicMock()
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.list_knowledge()
        assert r["status"] == "ok"
        manager.list_entries.assert_awaited_once_with(
            limit=50,
            offset=0,
            category="",
            sort=SortQuery("updated_at", "desc"),
        )

    @pytest.mark.asyncio
    async def test_search_knowledge(self) -> None:
        req = _mock_request(query="test")
        mixin = _make_mixin()
        manager = MagicMock()
        manager.search = AsyncMock(return_value=([], 0))
        async def _ready():
            engine = MagicMock()
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.search_knowledge()
        assert r["status"] == "ok"
        manager.search.assert_awaited_once_with(
            query="test",
            limit=20,
            category="",
            sort=SortQuery("updated_at", "desc"),
        )

    @pytest.mark.asyncio
    async def test_create_entry(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "title": "知识条目", "content": "内容", "category": "fact"})
        mixin = _make_mixin()
        async def _ready():
            engine = MagicMock()
            manager = MagicMock()
            manager.add_entry = AsyncMock(return_value=1)
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.create_knowledge_entry()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_update_entry(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "entry_id": 1, "title": "更新标题"})
        mixin = _make_mixin()
        async def _ready():
            engine = MagicMock()
            manager = MagicMock()
            manager.get_entry = AsyncMock(return_value=MagicMock())
            manager.update_entry = AsyncMock(return_value=True)
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.update_knowledge_entry()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_delete_entry(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"entry_id": 1})
        mixin = _make_mixin()
        async def _ready():
            engine = MagicMock()
            manager = MagicMock()
            manager.get_entry = AsyncMock(return_value=MagicMock())
            manager.delete_entry = AsyncMock(return_value=True)
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.delete_knowledge_entry()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_batch_delete(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"entry_ids": [1, 2, 3]})
        mixin = _make_mixin()
        async def _ready():
            engine = MagicMock()
            manager = MagicMock()
            manager.delete_entry = AsyncMock(return_value=True)
            manager.get_entry = AsyncMock(return_value=MagicMock())
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.batch_delete_knowledge()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_get_detail(self) -> None:
        req = _mock_request(entry_id="1")
        mixin = _make_mixin()
        async def _ready():
            engine = MagicMock()
            manager = MagicMock()
            entry = MagicMock()
            entry.to_dict.return_value = {"entry_id": 1, "title": "T"}
            manager.get_entry = AsyncMock(return_value=entry)
            engine.knowledge_manager = manager
            return {"memory_engine": engine}, None
        mixin._ensure_plugin_ready = _ready
        with patch("core.api.knowledge_api.request", req):
            r = await mixin.get_knowledge_detail()
        assert r["status"] == "ok"
