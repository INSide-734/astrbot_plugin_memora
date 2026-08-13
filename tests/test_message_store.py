"""测试消息存储与消息查询 — message storage + advanced queries."""

import time

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.models.conversation_models import Message


class TestMessageStoreRemaining:
    """Remaining MessageStore tests not covered by test_conversation_store.py."""

    @staticmethod
    def _make_msg(**overrides):
        defaults = dict(
            id=0,
            session_id="sess-ms",
            role="user",
            content="test",
            sender_id="u1",
            sender_name="Tester",
            timestamp=time.time(),
        )
        defaults.update(overrides)
        return Message(**defaults)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_add_message_with_group_id(self, tmp_db_path):
        """add_message persists group_id for group chat messages."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            msg = self._make_msg(
                session_id="group-sess",
                sender_id="u1",
                group_id="group-123",
                role="user",
                content="群聊消息",
            )
            msg_id = await store.add_message(msg)
            assert msg_id > 0

            messages = await store.get_messages("group-sess", limit=10)
            assert len(messages) == 1
            assert messages[0].group_id == "group-123"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_add_message_increments_session_count(self, tmp_db_path):
        """Adding a message increments the session's message_count."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await store.add_message(self._make_msg(session_id="sess-incr"))
            await store.add_message(self._make_msg(session_id="sess-incr"))

            sess = await store.get_session("sess-incr")
            assert sess is not None
            assert sess.message_count == 2
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_messages_not_initialized_returns_empty(self, tmp_db_path):
        """get_messages before initialization returns empty list."""
        store = ConversationStore(tmp_db_path)
        # Do NOT initialize
        assert await store.get_messages("any") == []

    @pytest.mark.asyncio
    async def test_add_message_not_initialized_raises(self, tmp_db_path):
        """add_message before initialization raises RuntimeError."""
        store = ConversationStore(tmp_db_path)
        msg = self._make_msg()
        with pytest.raises(RuntimeError):
            await store.add_message(msg)

    @pytest.mark.asyncio
    async def test_create_session_auto_via_add_message(self, tmp_db_path):
        """add_message implicitly creates a session; duplicate session_id is handled."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            msg1 = self._make_msg(session_id="auto-sess")
            await store.add_message(msg1)
            # Adding another message to the same session should work (ON CONFLICT DO NOTHING)
            msg2 = self._make_msg(session_id="auto-sess", content="second")
            msg_id = await store.add_message(msg2)
            assert msg_id > 0

            # Verify only one session row exists
            async with store.connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
                ("auto-sess",),
            ) as cursor:
                row = await cursor.fetchone()
                assert row[0] == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_trim_with_summarized_index_exceeding_actual(self, tmp_db_path):
        """trim_session_messages handles last_summarized_index > actual count gracefully."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await store.add_message(self._make_msg(session_id="sess-overflow"))
            # Set last_summarized_index to exceed actual message count
            import json

            async with store._write_lock:
                await store.connection.execute(
                    "UPDATE sessions SET metadata = ? WHERE session_id = ?",
                    (json.dumps({"last_summarized_index": 99}), "sess-overflow"),
                )
                await store.connection.commit()

            deleted = await store.trim_session_messages("sess-overflow", delete_count=5)
            assert deleted == 0  # Should refuse to trim unsummarized messages
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_delete_old_sessions_with_ttl_seconds(self, tmp_db_path):
        """delete_old_sessions accepts ttl_seconds parameter."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await store.create_session("ttl-sess", "qq")
            async with store._write_lock:
                await store.connection.execute(
                    "UPDATE sessions SET last_active_at = ? WHERE session_id = ?",
                    (time.time() - 3600, "ttl-sess"),
                )
                await store.connection.commit()

            deleted = await store.delete_old_sessions(ttl_seconds=1800)
            assert deleted >= 1
            assert await store.get_session("ttl-sess") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_delete_old_sessions_empty(self, tmp_db_path):
        """delete_old_sessions with no stale sessions returns 0."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            await store.create_session("fresh-sess", "qq")
            deleted = await store.delete_old_sessions(days=30)
            assert deleted == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_user_message_stats_empty(self, tmp_db_path):
        """get_user_message_stats for empty session returns empty dict."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            stats = await store.get_user_message_stats("no-such-session")
            assert stats == {}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_message_metadata_on_missing_message(self, tmp_db_path):
        """update_message_metadata on missing message id still succeeds (no-op)."""
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            # The method returns True for successful execution (no exception).
            # UPDATE with no matching row is not an error in SQLite.
            result = await store.update_message_metadata(99999, {"key": "val"})
            assert result is True
        finally:
            await store.close()
