"""测试 MessageQueryMixin — query methods, metadata updates, message-count sync."""

import json

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)


class TestMessageQueryConnectionNone:
    """所有 public methods gracefully handle connection=None."""

    @pytest.mark.asyncio
    async def test_get_message_count_no_connection(self, tmp_db_path):
        """get_message_count returns 0 when connection is None."""
        store = ConversationStore(tmp_db_path)
        # Don't initialize — connection stays None
        assert await store.get_message_count("s1") == 0

    @pytest.mark.asyncio
    async def test_get_user_message_stats_no_connection(self, tmp_db_path):
        """get_user_message_stats returns {} when connection is None."""
        store = ConversationStore(tmp_db_path)
        assert await store.get_user_message_stats("s1") == {}

    @pytest.mark.asyncio
    async def test_update_message_metadata_no_connection(self, tmp_db_path):
        """update_message_metadata returns False when connection is None."""
        store = ConversationStore(tmp_db_path)
        assert await store.update_message_metadata(1, {"key": "val"}) is False

    @pytest.mark.asyncio
    async def test_search_messages_no_connection(self, tmp_db_path):
        """search_messages returns [] when connection is None."""
        store = ConversationStore(tmp_db_path)
        assert await store.search_messages("s1", "hello") == []

    @pytest.mark.asyncio
    async def test_get_messages_range_no_connection(self, tmp_db_path):
        """get_messages_range returns [] when connection is None."""
        store = ConversationStore(tmp_db_path)
        assert await store.get_messages_range("s1", offset=0, limit=10) == []

    @pytest.mark.asyncio
    async def test_sync_message_counts_no_connection(self, tmp_db_path):
        """sync_message_counts returns {} when connection is None."""
        store = ConversationStore(tmp_db_path)
        assert await store.sync_message_counts() == {}

    @pytest.mark.asyncio
    async def test_reset_summarized_index_no_connection(self, tmp_db_path):
        """reset_summarized_index_if_needed returns False when connection is None."""
        store = ConversationStore(tmp_db_path)
        assert await store.reset_summarized_index_if_needed("s1") is False


class TestMessageQueryWithData:
    """查询 methods with data populated."""

    async def _setup_session_with_messages(self, store):
        """创建 a session and add messages."""
        await store.create_session("sess-q", "qq")
        # Add a few messages directly via the connection
        conn = store.connection
        for i in range(5):
            await conn.execute(
                """
                INSERT INTO messages (session_id, role, content, sender_id, sender_name, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sess-q",
                    "user" if i % 2 == 0 else "assistant",
                    f"Message content number {i}",
                    f"sender_{i % 3}",
                    f"Sender {i % 3}",
                    i * 1000.0,
                    json.dumps({"idx": i}),
                ),
            )
        await conn.commit()

    @pytest.mark.asyncio
    async def test_get_message_count_with_data(self, tmp_db_path):
        """get_message_count returns correct count."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            assert await store.get_message_count("sess-q") == 5
            assert await store.get_message_count("nonexistent") == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_user_message_stats_with_data(self, tmp_db_path):
        """get_user_message_stats aggregates by sender_id."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            stats = await store.get_user_message_stats("sess-q")
            assert "sender_0" in stats
            assert "sender_1" in stats
            assert "sender_2" in stats
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_message_metadata_success(self, tmp_db_path):
        """update_message_metadata updates and returns True."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            # Get a message ID
            msgs = await store.search_messages("sess-q", "content", limit=1)
            assert len(msgs) > 0
            msg_id = msgs[0].id
            result = await store.update_message_metadata(msg_id, {"updated": True})
            assert result is True
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_message_metadata_invalid_id(self, tmp_db_path):
        """update_message_metadata on nonexistent id returns False (or succeeds silently with 0 rows)."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            result = await store.update_message_metadata(99999, {"key": "val"})
            # SQL UPDATE with no matching row: returns True (no exception), just 0 rows affected
            assert result is True
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_messages_with_data(self, tmp_db_path):
        """search_messages finds messages containing keyword."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            results = await store.search_messages("sess-q", "number", limit=10)
            assert len(results) == 5
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_messages_no_match(self, tmp_db_path):
        """search_messages returns empty on no match."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            results = await store.search_messages(
                "sess-q", "zzz_nonexistent_zzz", limit=10
            )
            assert results == []
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_messages_range_with_data(self, tmp_db_path):
        """get_messages_range returns correct slice."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            results = await store.get_messages_range("sess-q", offset=1, limit=2)
            assert len(results) == 2
            # Should be sorted by timestamp ASC
            assert results[0].timestamp < results[1].timestamp
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_sync_message_counts_all_correct(self, tmp_db_path):
        """sync_message_counts when all counts are already correct (covers logger.info path)."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            # First call fixes the mismatch (direct SQL bypassed message_count update)
            await store.sync_message_counts()
            # Second call: counts are now correct — should return empty dict
            fixed = await store.sync_message_counts()
            assert fixed == {}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_reset_summarized_index_not_needed(self, tmp_db_path):
        """reset_summarized_index_if_needed returns False when index is valid."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            result = await store.reset_summarized_index_if_needed("sess-q")
            assert result is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_reset_summarized_index_session_not_found(self, tmp_db_path):
        """reset_summarized_index_if_needed returns False for unknown session."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            result = await store.reset_summarized_index_if_needed("nonexistent")
            assert result is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_reset_summarized_index_needs_reset(self, tmp_db_path):
        """reset_summarized_index_if_needed resets when index exceeds message_count."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await self._setup_session_with_messages(store)
            # Manually set a bogus last_summarized_index > message_count
            await store.connection.execute(
                "UPDATE sessions SET metadata = ? WHERE session_id = ?",
                (json.dumps({"last_summarized_index": 999}), "sess-q"),
            )
            await store.connection.commit()
            result = await store.reset_summarized_index_if_needed("sess-q")
            assert result is True
        finally:
            await store.close()
