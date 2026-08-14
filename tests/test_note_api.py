"""测试 core/api/note_api.py — NoteApiMixin (CRUD, search, versions, batch)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(*, plugin_ready: bool = True, has_store: bool = True):
    from core.platform.transport.page_api.note_api import NoteApiMixin

    class Stub:
        list_notes = NoteApiMixin.list_notes
        search_notes = NoteApiMixin.search_notes
        get_note_detail = NoteApiMixin.get_note_detail
        create_note = NoteApiMixin.create_note
        update_note = NoteApiMixin.update_note
        delete_note = NoteApiMixin.delete_note
        batch_delete_notes = NoteApiMixin.batch_delete_notes
        batch_update_notes = NoteApiMixin.batch_update_notes

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "not ready"}
            if has_store:
                engine = MagicMock()
                engine.note_store = MagicMock()
            else:
                engine = MagicMock(spec=[])
            return {"memory_engine": engine}, None

    return Stub()


class TestNoteValidation:
    @pytest.mark.asyncio
    async def test_list_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin(plugin_ready=False).list_notes()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_list_no_store(self) -> None:
        req = _mock_request()
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin(has_store=False).list_notes()
        assert r["status"] == "ok"
        assert r["data"]["notes"] == []

    @pytest.mark.asyncio
    async def test_search_no_query(self) -> None:
        req = _mock_request(query="")
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().search_notes()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_missing_id(self) -> None:
        req = _mock_request(note_id="0")
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().get_note_detail()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_not_found(self) -> None:
        req = _mock_request(note_id="999")
        mixin = _make_mixin()
        mixin._ensure_plugin_ready_orig = mixin._ensure_plugin_ready

        async def _ready():
            engine = MagicMock()
            engine.note_store = MagicMock()
            engine.note_store.get = AsyncMock(return_value=None)
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.get_note_detail()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_no_store(self) -> None:
        req = _mock_request(note_id="1")
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin(has_store=False).get_note_detail()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_missing_content(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"title": "", "content": ""})
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().create_note()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_no_store(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"title": "T", "content": "C"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin(has_store=False).create_note()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_missing_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 0})
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().update_note()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_no_store(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 1, "title": "T"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin(has_store=False).update_note()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_missing_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 0})
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().delete_note()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_no_ids(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_ids": []})
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().batch_delete_notes()
        assert r["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_update_no_ids(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_ids": [], "field": "status", "value": "archived"}
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await _make_mixin().batch_update_notes()
        assert r["status"] == "error"


class TestNoteHappyPath:
    @pytest.mark.asyncio
    async def test_list_notes(self) -> None:
        req = _mock_request()
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.list_notes = AsyncMock(return_value=([], 0))
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.list_notes()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_search_notes(self) -> None:
        req = _mock_request(query="test")
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.search = AsyncMock(return_value=([], 0))
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.search_notes()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_create_note(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"title": "新笔记", "content": "笔记内容"}
        )
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.create = AsyncMock(return_value=1)
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.create_note()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_update_note(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 1, "title": "更新标题"})
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.get = AsyncMock(return_value=MagicMock())
            store.update = AsyncMock(return_value=None)
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.update_note()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_delete_note(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 1})
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.get = AsyncMock(return_value=MagicMock())
            store.delete = AsyncMock(return_value=True)
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.delete_note()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_batch_delete(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_ids": [1, 2, 3]})
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.delete = AsyncMock(return_value=True)
            store.get = AsyncMock(return_value=MagicMock())
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.batch_delete_notes()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_batch_update(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_ids": [1, 2], "field": "status", "value": "archived"}
        )
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            store.get = AsyncMock(return_value=MagicMock())
            store.update = AsyncMock(return_value=None)
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.batch_update_notes()
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_get_detail_with_versions(self) -> None:
        req = _mock_request(note_id="1")
        mixin = _make_mixin()

        async def _ready():
            engine = MagicMock()
            store = MagicMock()
            note = MagicMock()
            note.to_dict.return_value = {"note_id": 1, "title": "T"}
            store.get = AsyncMock(return_value=note)
            store.get_versions = AsyncMock(return_value=[])
            engine.note_store = store
            return {"memory_engine": engine}, None

        mixin._ensure_plugin_ready = _ready
        with patch("core.platform.transport.page_api.note_api.request", req):
            r = await mixin.get_note_detail()
        assert r["status"] == "ok"
        assert "versions" in r["data"]
