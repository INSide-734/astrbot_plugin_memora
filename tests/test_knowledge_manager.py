"""KnowledgeManager 测试 — CRUD、去重、过期清理。"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_knowledge_store():
    store = AsyncMock()
    store.list_entries.return_value = ([], 0)
    store.search.return_value = ([], 0)
    store.get.return_value = None
    store.delete.return_value = True
    return store


@pytest.fixture
async def knowledge_mgr(mock_knowledge_store):
    from core.managers.knowledge_manager import KnowledgeManager
    return KnowledgeManager(mock_knowledge_store)


class TestCleanupExpired:
    """cleanup_expired() must paginate through all entries."""

    @staticmethod
    def _make_entry(eid: int, expires_at: float) -> MagicMock:
        entry = MagicMock()
        entry.id = eid
        entry.entry_id = entry.id
        entry.expires_at = expires_at
        return entry

    @pytest.mark.asyncio
    async def test_cleanup_expired_paginates(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        # Page 1: 3 entries (2 expired), Page 2: 2 entries (1 expired), Page 3: empty
        now = time.time()
        page1 = [self._make_entry(1, now - 100), self._make_entry(2, now - 200),
                 self._make_entry(3, now + 9999)]
        page2 = [self._make_entry(4, now - 300), self._make_entry(5, now + 9999)]
        mock_knowledge_store.list_entries.side_effect = [
            (page1, 3), (page2, 2), ([], 0),
        ]
        mgr = KnowledgeManager(mock_knowledge_store)
        removed = await mgr.cleanup_expired()
        assert removed == 3
        # 3 calls: page1 (limit=100, offset=0) → page2 (100, 100) → page3 (100, 200)
        assert mock_knowledge_store.list_entries.call_count == 3

    @pytest.mark.asyncio
    async def test_no_expired_entries(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        now = time.time()
        entry = self._make_entry(1, now + 9999)
        mock_knowledge_store.list_entries.side_effect = [
            ([entry], 1), ([], 0),
        ]
        mgr = KnowledgeManager(mock_knowledge_store)
        removed = await mgr.cleanup_expired()
        assert removed == 0

    @pytest.mark.asyncio
    async def test_empty_store(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        mock_knowledge_store.list_entries.return_value = ([], 0)
        mgr = KnowledgeManager(mock_knowledge_store)
        removed = await mgr.cleanup_expired()
        assert removed == 0

    @pytest.mark.asyncio
    async def test_all_expired(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        now = time.time()
        entries = [self._make_entry(i, now - i * 100) for i in range(50)]
        mock_knowledge_store.list_entries.side_effect = [
            (entries, 50), ([], 0),
        ]
        mgr = KnowledgeManager(mock_knowledge_store)
        removed = await mgr.cleanup_expired()
        assert removed == 50


class TestKnowledgeManagerCrud:
    @pytest.mark.asyncio
    async def test_update_entry_checks_existence(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        from core.models.knowledge_models import KnowledgeEntry

        entry = KnowledgeEntry(title="T", content="C", entry_id=7)
        mock_knowledge_store.get.return_value = entry
        mock_knowledge_store.update.return_value = None

        mgr = KnowledgeManager(mock_knowledge_store)
        assert await mgr.update_entry(entry) is True
        mock_knowledge_store.get.assert_called_once_with(7)
        mock_knowledge_store.update.assert_called_once_with(entry)

    @pytest.mark.asyncio
    async def test_update_entry_missing_returns_false(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        from core.models.knowledge_models import KnowledgeEntry

        mock_knowledge_store.get.return_value = None
        mgr = KnowledgeManager(mock_knowledge_store)

        assert await mgr.update_entry(KnowledgeEntry(entry_id=404)) is False
        mock_knowledge_store.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_entry_merges_similar_existing_entry(self, mock_knowledge_store):
        from core.managers.knowledge_manager import KnowledgeManager
        from core.models.knowledge_models import KnowledgeEntry

        existing = KnowledgeEntry(
            title="Python",
            content="Python is a programming language",
            confidence=0.4,
            tags=["old"],
            entry_id=3,
        )
        incoming = KnowledgeEntry(
            title="Python",
            content="Python is a programming language",
            confidence=0.9,
            tags=["new"],
        )
        mock_knowledge_store.search.return_value = ([existing], 1)

        mgr = KnowledgeManager(mock_knowledge_store)
        entry_id = await mgr.add_entry(incoming)

        assert entry_id == 3
        assert existing.confidence == 0.9
        assert existing.access_count == 1
        assert set(existing.tags) == {"old", "new"}
        mock_knowledge_store.insert.assert_not_called()
        mock_knowledge_store.update.assert_called_once_with(existing)
