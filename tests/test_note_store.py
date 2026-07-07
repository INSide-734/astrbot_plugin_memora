"""测试笔记存储 — CRUD + prune_versions."""
import pytest
from core.models.note_models import Note, NoteStatus
from core.storage.note_store import NoteStore


class TestNoteStore:

    @staticmethod
    async def _create_note(store, title="test", content="hello",
                           tags=None):
        note = Note(title=title, content=content, tags=tags or [])
        note_id = await store.create(note)
        note.note_id = note_id
        return note

    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "My Note", "Content here")
        fetched = await s.get(note.note_id)
        assert fetched is not None
        assert fetched.title == "My Note"

    @pytest.mark.asyncio
    async def test_version_tracking(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "V1", "original")
        note.content = "updated"
        await s.update(note)
        assert len(await s.get_versions(note.note_id)) == 2

    @pytest.mark.asyncio
    async def test_stale_note_update_is_rejected_without_duplicate_version(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "Concurrent", "v1")

        first = await s.get(note.note_id)
        second = await s.get(note.note_id)
        assert first is not None
        assert second is not None

        first.content = "first writer"
        assert await s.update(first) is True

        second.content = "stale writer"
        assert await s.update(second) is False

        fetched = await s.get(note.note_id)
        versions = await s.get_versions(note.note_id)
        assert fetched is not None
        assert fetched.content == "first writer"
        assert fetched.version == 2
        assert [version.version for version in versions] == [2, 1]

    @pytest.mark.asyncio
    async def test_prune_versions_caps(self, tmp_db_path):
        """P3: Version count must not exceed max_versions."""
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "prune", "v1")
        for i in range(25):
            note.content = f"v{i+2}"
            await s.update(note)
        assert len(await s.get_versions(note.note_id)) == 26
        removed = await s.prune_versions(max_versions=20)
        assert removed == 6
        assert len(await s.get_versions(note.note_id)) == 20

    @pytest.mark.asyncio
    async def test_prune_30_updates_capped_at_20(self, tmp_db_path):
        """P2.4: 30 consecutive updates → max 20 versions retained."""
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "stress", "v1")
        for i in range(30):
            note.content = f"version_{i+2}"
            await s.update(note)
        versions_before = await s.get_versions(note.note_id)
        assert len(versions_before) == 31
        removed = await s.prune_versions(max_versions=20)
        assert removed == 11
        versions_after = await s.get_versions(note.note_id)
        assert len(versions_after) == 20
        kept = [v.content for v in versions_after]
        assert "version_22" in kept
        assert "v1" not in kept

    @pytest.mark.asyncio
    async def test_prune_no_excess(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "few", "versions")
        note.content = "v2"
        await s.update(note)
        assert await s.prune_versions(max_versions=20) == 0

    @pytest.mark.asyncio
    async def test_search_content(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        await self._create_note(s, "Alpha", "lorem ipsum")
        results, _ = await s.search("lorem")
        assert any(r.title == "Alpha" for r in results)

    @pytest.mark.asyncio
    async def test_delete(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "del", "c")
        assert await s.delete(note.note_id)

    @pytest.mark.asyncio
    async def test_soft_delete_preserves_versions_and_hides_from_default_list(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "soft-del", "v1")
        note.content = "v2"
        await s.update(note)

        assert await s.soft_delete(note.note_id) is True
        fetched = await s.get(note.note_id)
        assert fetched is not None
        assert fetched.status == NoteStatus.DELETED
        assert len(await s.get_versions(note.note_id)) == 2

        notes, total = await s.list_notes(limit=50)
        assert total == 0
        assert notes == []

        deleted_notes, deleted_total = await s.list_notes(
            limit=50, status=NoteStatus.DELETED.value
        )
        assert deleted_total == 1
        assert deleted_notes[0].note_id == note.note_id

    @pytest.mark.asyncio
    async def test_hard_delete_removes_versions(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        note = await self._create_note(s, "hard-del", "v1")
        note.content = "v2"
        await s.update(note)
        assert len(await s.get_versions(note.note_id)) == 2

        assert await s.delete(note.note_id) is True
        assert await s.get(note.note_id) is None
        assert await s.get_versions(note.note_id) == []

    @pytest.mark.asyncio
    async def test_list_status(self, tmp_db_path):
        s = NoteStore(tmp_db_path)
        await s.init_table()
        await self._create_note(s, "a1", "a")
        await self._create_note(s, "a2", "b")
        _, total = await s.list_notes(limit=50)
        assert total >= 2
