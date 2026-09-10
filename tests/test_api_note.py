"""core/api/note_api.py — NoteApiMixin 测试。

验证请求校验、响应格式与错误处理。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.notes.domain import Note, NoteStatus
from core.platform.transport.page_api.note_api import NoteApiMixin


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(
    *,
    store_available: bool = True,
    notes_list: list | None = None,
    notes_total: object = 0,
    search_notes: list | None = None,
    search_total: object = 0,
    detail_note: object | None = None,
    versions_list: object | None = None,
    create_id: int = 1,
    delete_result: bool = True,
    manager_available: bool = False,
):
    """创建 NoteApiMixin 测试替身。"""

    class Stub:
        list_notes = NoteApiMixin.list_notes
        search_notes = NoteApiMixin.search_notes
        get_note_detail = NoteApiMixin.get_note_detail
        create_note = NoteApiMixin.create_note
        update_note = NoteApiMixin.update_note
        delete_note = NoteApiMixin.delete_note
        get_note_versions = NoteApiMixin.get_note_versions
        archive_note = NoteApiMixin.archive_note
        batch_notes = NoteApiMixin.batch_notes
        batch_delete_notes = NoteApiMixin.batch_delete_notes
        batch_update_notes = NoteApiMixin.batch_update_notes
        _batch_delete_notes_impl = NoteApiMixin._batch_delete_notes_impl

        async def _ensure_plugin_ready(self):
            engine = MagicMock()
            if store_available:
                engine.note_store = MagicMock()
                engine.note_store.list_notes = AsyncMock(
                    return_value=(notes_list or [], notes_total)
                )
                engine.note_store.search = AsyncMock(
                    return_value=(search_notes or [], search_total)
                )
                engine.note_store.get = AsyncMock(return_value=detail_note)
                engine.note_store.get_versions = AsyncMock(
                    return_value=versions_list or []
                )
                engine.note_store.create = AsyncMock(return_value=create_id)
                engine.note_store.update = AsyncMock(return_value=None)
                engine.note_store.delete = AsyncMock(return_value=delete_result)
                if manager_available:
                    engine.note_manager = MagicMock()
                    engine.note_manager.get_note = AsyncMock(return_value=detail_note)
                    engine.note_manager.update_note = AsyncMock(
                        return_value=detail_note
                    )
            self.engine = engine
            return {"memory_engine": engine}, None

    return Stub()


def _make_note(title="Test", content="Body", note_id_val=1, status=NoteStatus.ACTIVE):
    n = Note(title=title, content=content, tags=[], user_id="u1", note_id=note_id_val)
    n.status = status
    return n


class TestNoteValidation:
    """Notes API validates required fields."""

    @pytest.mark.asyncio
    async def test_create_requires_title_and_content(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"title": "", "content": ""})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.create_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.create_note()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_search_requires_query(self) -> None:
        req = _mock_request(query="")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.search_notes()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_search_rejects_non_numeric_limit(self) -> None:
        req = _mock_request(query="topic", limit="abc")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.search_notes()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_requires_note_id(self) -> None:
        req = _mock_request(note_id="0")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.get_note_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_rejects_non_numeric_note_id(self) -> None:
        req = _mock_request(note_id="abc")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.get_note_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_requires_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 0})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_note()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_rejects_non_numeric_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": "abc"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_rejects_boolean_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": True})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.delete_note()
        assert result["status"] == "error"
        assert "note_id must be an integer" in result["message"]

    @pytest.mark.asyncio
    async def test_update_requires_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 0, "title": "new"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.update_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.update_note()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_update_rejects_non_numeric_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": "abc", "title": "new"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.update_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_rejects_boolean_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_id": True, "field": "title", "value": "new"}
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=_make_note(note_id_val=1))
            result = await mixin.update_note()
        assert result["status"] == "error"
        assert "note_id must be an integer" in result["message"]

    @pytest.mark.asyncio
    async def test_update_note_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_id": 999, "title": "new", "content": "body"}
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=None)
            result = await mixin.update_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_accepts_changes_envelope(self) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "note_id": 2,
                "changes": {
                    "title": "new title",
                    "content": "new body",
                    "tags": ["new"],
                    "status": "archived",
                },
            }
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.update_note()
        assert result["status"] == "ok"
        updated = mixin.engine.note_store.update.await_args.args[0]
        assert updated.title == "new title"
        assert updated.content == "new body"
        assert updated.tags == ["new"]
        assert updated.status == NoteStatus.ARCHIVED

    @pytest.mark.asyncio
    async def test_update_changes_rejects_read_only_field_before_store_write(
        self,
    ) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 2, "changes": {"note_id": 3}})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.update_note()
        assert result["status"] == "error"
        mixin.engine.note_store.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_keeps_field_value_compatibility(self) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_id": 2, "field": "title", "value": "new title"}
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.update_note()
        assert result["status"] == "ok"
        assert mixin.engine.note_store.update.await_args.args[0].title == "new title"

    @pytest.mark.asyncio
    async def test_update_changes_valid_title_and_invalid_status_keeps_note_unchanged(
        self,
    ) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "note_id": 2,
                "changes": {"title": "new title", "status": "invalid"},
            }
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.update_note()
        assert result["status"] == "error"
        assert note.title == "old"
        assert note.status is NoteStatus.ACTIVE
        mixin.engine.note_store.update.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["title", "content"])
    async def test_update_changes_rejects_blank_text_without_mutating_note(
        self, field
    ) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 2, "changes": {field: "   "}})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.update_note()
        assert result["status"] == "error"
        assert note.title == "old"
        assert note.content == "body"
        mixin.engine.note_store.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_changes_rejects_malformed_tags_without_mutating_note(
        self,
    ) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_id": 2, "changes": {"tags": ["ok", 2]}}
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.update_note()
        assert result["status"] == "error"
        assert note.tags == []
        mixin.engine.note_store.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_changes_uses_manager_path_with_detached_candidate(
        self,
    ) -> None:
        note = _make_note(title="old", content="body", note_id_val=2)
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "note_id": 2,
                "changes": {
                    "title": "new title",
                    "content": "new body",
                    "tags": [" one ", "two", "one"],
                    "status": "archived",
                },
            }
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note, manager_available=True)
            result = await mixin.update_note()
        assert result["status"] == "ok"
        mixin.engine.note_store.update.assert_not_awaited()
        kwargs = mixin.engine.note_manager.update_note.await_args.kwargs
        assert kwargs == {
            "title": "new title",
            "content": "new body",
            "tags": ["one", "two"],
            "status": "archived",
        }
        assert note.title == "old"

    @pytest.mark.asyncio
    async def test_archive_requires_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 0})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.archive_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_archive_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.archive_note()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_archive_rejects_non_numeric_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": "abc"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.archive_note()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_archive_rejects_boolean_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": True})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=_make_note(note_id_val=1))
            result = await mixin.archive_note()
        assert result["status"] == "error"
        assert "note_id must be an integer" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_requires_ids(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_ids": [], "action": "delete"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_notes()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_notes_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_notes()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_unsupported_action(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_ids": [1], "action": "invalid"})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_notes()
        assert result["status"] == "error"
        assert "不支持" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_list_rejects_non_numeric_limit_or_offset(self) -> None:
        req = _mock_request(limit="abc", offset="1x")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.list_notes()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_notes()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_update_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-note"])
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_update_notes()
        assert result["status"] == "error"
        assert "JSON" in result["message"]


class TestNoteHappyPath:
    """Notes API with mocked store."""

    @pytest.mark.asyncio
    async def test_list_skips_malformed_note_items(self) -> None:
        req = _mock_request(limit="10", offset="0")
        broken = MagicMock()
        type(broken).to_dict = lambda self: (_ for _ in ()).throw(
            RuntimeError("broken note")
        )
        good_1 = _make_note(title="A", content="body-a", note_id_val=1)
        good_2 = _make_note(title="B", content="body-b", note_id_val=2)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(notes_list=[good_1, broken, good_2], notes_total=3)
            result = await mixin.list_notes()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["title"] for item in result["data"]["notes"]] == ["A", "B"]

    @pytest.mark.asyncio
    async def test_list_coerces_malformed_total_count(self) -> None:
        req = _mock_request(limit="10", offset="0")
        good = _make_note(title="A", content="body-a", note_id_val=1)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(notes_list=[good], notes_total="bad-total")
            result = await mixin.list_notes()
        assert result["status"] == "ok"
        assert result["data"]["notes"] == [good.to_dict()]
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_search_skips_malformed_note_items(self) -> None:
        req = _mock_request(query="topic", limit="10")
        broken = MagicMock()
        type(broken).to_dict = lambda self: (_ for _ in ()).throw(
            RuntimeError("broken note")
        )
        good = _make_note(title="A", content="body-a", note_id_val=1)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(search_notes=[good, broken], search_total=2)
            result = await mixin.search_notes()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert [item["title"] for item in result["data"]["notes"]] == ["A"]

    @pytest.mark.asyncio
    async def test_search_coerces_malformed_total_count(self) -> None:
        req = _mock_request(query="topic", limit="10")
        good = _make_note(title="A", content="body-a", note_id_val=1)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(search_notes=[good], search_total="bad-total")
            result = await mixin.search_notes()
        assert result["status"] == "ok"
        assert result["data"]["notes"] == [good.to_dict()]
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_create_returns_note_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "title": "New Note",
                "content": "Some content",
                "tags": ["tag1"],
                "user_id": "u1",
            }
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(create_id=42)
            result = await mixin.create_note()
        assert result["status"] == "ok"
        assert result["data"]["note_id"] == 42

    @pytest.mark.asyncio
    async def test_delete_returns_result(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 1})
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.delete_note()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio
    async def test_archive_sets_status(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 1})
        note = _make_note(note_id_val=1, status=NoteStatus.ACTIVE)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.archive_note()
        assert result["status"] == "ok"
        assert result["data"]["status"] == "archived"

    @pytest.mark.asyncio
    async def test_archive_returns_error_for_malformed_note_version(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"note_id": 1})

        class BrokenVersionNote:
            title = "Test"
            content = "Body"
            tags = []
            status = NoteStatus.ACTIVE

            @property
            def version(self):
                raise RuntimeError("broken note version")

        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=BrokenVersionNote())
            result = await mixin.archive_note()
        assert result["status"] == "error"
        assert "note version serialization failed" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_delete_notes(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_ids": [1, 2, 3], "action": "delete"}
        )
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(delete_result=True)
            result = await mixin.batch_notes()
        assert result["status"] == "ok"
        assert "deleted_count" in result["data"]
        assert result["data"]["total"] == 3

    @pytest.mark.asyncio
    async def test_batch_archive_rejects_boolean_ids_and_processes_valid_ones(
        self,
    ) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_ids": [True, 2], "action": "archive"}
        )
        note = _make_note(note_id_val=2, status=NoteStatus.ACTIVE)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.batch_notes()
        assert result["status"] == "ok"
        assert result["data"]["archived_count"] == 1
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == [True]

    @pytest.mark.asyncio
    async def test_batch_update_status_rejects_boolean_ids_and_updates_valid_ones(
        self,
    ) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "note_ids": [True, 2],
                "field": "status",
                "value": "archived",
            }
        )
        note = _make_note(note_id_val=2, status=NoteStatus.ACTIVE)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.batch_update_notes()
        assert result["status"] == "ok"
        assert result["data"]["updated_count"] == 1
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == [True]

    @pytest.mark.asyncio
    async def test_batch_update_status_marks_every_id_failed_when_value_invalid(
        self,
    ) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "note_ids": [1, 2],
                "field": "status",
                "value": "not-a-status",
            }
        )
        note = _make_note(note_id_val=1, status=NoteStatus.ACTIVE)
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note)
            result = await mixin.batch_update_notes()
        assert result["status"] == "ok"
        assert result["data"]["updated_count"] == 0
        assert result["data"]["failed_count"] == 2
        assert result["data"]["failed_ids"] == [1, 2]

    @pytest.mark.asyncio
    async def test_update_returns_error_for_malformed_note_version(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"note_id": 1, "field": "title", "value": "Updated"}
        )

        class BrokenVersionNote:
            title = "Test"
            content = "Body"
            tags = []
            status = NoteStatus.ACTIVE

            @property
            def version(self):
                raise RuntimeError("broken note version")

        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=BrokenVersionNote())
            result = await mixin.update_note()
        assert result["status"] == "error"
        assert "note version serialization failed" in result["message"]

    @pytest.mark.asyncio
    async def test_detail_not_found(self) -> None:
        req = _mock_request(note_id="5")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=None)
            result = await mixin.get_note_detail()
        assert result["status"] == "error"
        assert "not found" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_detail_returns_versions(self) -> None:
        req = _mock_request(note_id="1")
        note = _make_note(note_id_val=1)
        versions = [
            MagicMock(version=1, content="v1", created_at="2024-01-01"),
            MagicMock(version=2, content="v2", created_at="2024-01-02"),
        ]
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note, versions_list=versions)
            result = await mixin.get_note_detail()
        assert result["status"] == "ok"
        assert "versions" in result["data"]
        assert len(result["data"]["versions"]) == 2

    @pytest.mark.asyncio
    async def test_detail_returns_error_for_malformed_note_payload(self) -> None:
        req = _mock_request(note_id="1")
        note = MagicMock()
        note.to_dict.side_effect = RuntimeError("broken note")
        versions = [MagicMock(version=1, content="v1", created_at="2024-01-01")]
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note, versions_list=versions)
            result = await mixin.get_note_detail()
        assert result["status"] == "error"
        assert "note serialization failed" in result["message"]

    @pytest.mark.asyncio
    async def test_detail_skips_malformed_versions(self) -> None:
        req = _mock_request(note_id="1")
        note = _make_note(note_id_val=1)

        class BrokenVersion:
            @property
            def version(self):
                raise RuntimeError("boom")

        versions = [
            MagicMock(version=1, content="v1", created_at="2024-01-01"),
            BrokenVersion(),
        ]
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note, versions_list=versions)
            result = await mixin.get_note_detail()
        assert result["status"] == "ok"
        assert result["data"]["versions"] == [
            {"version": 1, "content": "v1", "created_at": "2024-01-01"}
        ]

    @pytest.mark.asyncio
    async def test_detail_tolerates_non_iterable_version_container(self) -> None:
        req = _mock_request(note_id="1")
        note = _make_note(note_id_val=1)

        class BrokenVersions:
            def __iter__(self):
                raise RuntimeError("broken versions container")

            def __bool__(self):
                return True

        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(detail_note=note, versions_list=BrokenVersions())
            result = await mixin.get_note_detail()
        assert result["status"] == "ok"
        assert result["data"]["note"]["note_id"] == 1
        assert result["data"]["versions"] == []

    @pytest.mark.asyncio
    async def test_get_versions_requires_note_id(self) -> None:
        req = _mock_request(note_id="0")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.get_note_versions()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_versions_rejects_non_numeric_note_id(self) -> None:
        req = _mock_request(note_id="abc")
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin()
            result = await mixin.get_note_versions()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_versions_skips_malformed_versions(self) -> None:
        req = _mock_request(note_id="1")

        class BrokenVersion:
            @property
            def content(self):
                raise RuntimeError("boom")

        versions = [
            MagicMock(version=2, content="v2", created_at="2024-01-02"),
            BrokenVersion(),
        ]
        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(versions_list=versions)
            result = await mixin.get_note_versions()
        assert result["status"] == "ok"
        assert result["data"]["versions"] == [
            {"version": 2, "content": "v2", "created_at": "2024-01-02"}
        ]

    @pytest.mark.asyncio
    async def test_get_versions_tolerates_non_iterable_version_container(self) -> None:
        req = _mock_request(note_id="1")

        class BrokenVersions:
            def __iter__(self):
                raise RuntimeError("broken versions container")

            def __bool__(self):
                return True

        with patch("core.platform.transport.page_api.note_api.request", req):
            mixin = _make_mixin(versions_list=BrokenVersions())
            result = await mixin.get_note_versions()
        assert result["status"] == "ok"
        assert result["data"]["versions"] == []


@pytest.mark.asyncio
async def test_versions_backend_failure_is_redacted_and_safely_logged() -> None:
    secret = "note-version-secret"
    mixin = _make_mixin()
    engine = MagicMock()
    engine.note_store = MagicMock()
    engine.note_store.get_versions = AsyncMock(side_effect=RuntimeError(secret))
    mixin._ensure_plugin_ready = AsyncMock(
        return_value=({"memory_engine": engine}, None)
    )
    request_mock = _mock_request(note_id="7")
    with (
        patch("core.platform.transport.page_api.note_api.request", request_mock),
        patch("core.platform.transport.page_api.note_api.logger.error") as logged,
    ):
        result = await mixin.get_note_versions()
    assert result["code"] == "internal_error"
    assert secret not in repr(result)
    assert secret not in repr(logged.call_args_list)


@pytest.mark.asyncio
async def test_full_form_update_backend_failure_is_redacted() -> None:
    secret = "note-update-secret"
    note = _make_note()
    mixin = _make_mixin()
    engine = MagicMock()
    engine.note_store = MagicMock()
    engine.note_store.get = AsyncMock(return_value=note)
    engine.note_store.update = AsyncMock(side_effect=RuntimeError(secret))
    mixin._ensure_plugin_ready = AsyncMock(
        return_value=({"memory_engine": engine}, None)
    )
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(return_value={"note_id": 7, "title": "After"})
    with patch("core.platform.transport.page_api.note_api.request", request_mock):
        result = await mixin.update_note()
    assert result["code"] == "internal_error"
    assert secret not in repr(result)
