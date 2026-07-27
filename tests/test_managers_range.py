"""测试 range_and_metadata — message range queries and session metadata."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.range_and_metadata import RangeAndMetadataMixin

# ---------------------------------------------------------------------------
# Concrete test class
# ---------------------------------------------------------------------------


class _TestRangeManager(RangeAndMetadataMixin):
    """具体 class for testing RangeAndMetadataMixin."""

    def __init__(self, store=None):
        self.store = store or MagicMock()


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


class TestRangeAndMetadataStructure:
    """Smoke tests for RangeAndMetadataMixin."""

    def test_get_messages_range_exists(self) -> None:
        """get_messages_range method is defined."""
        assert hasattr(RangeAndMetadataMixin, "get_messages_range")

    def test_update_session_metadata_exists(self) -> None:
        """update_session_metadata method is defined."""
        assert hasattr(RangeAndMetadataMixin, "update_session_metadata")

    def test_get_session_metadata_exists(self) -> None:
        """get_session_metadata method is defined."""
        assert hasattr(RangeAndMetadataMixin, "get_session_metadata")

    def test_reset_session_metadata_exists(self) -> None:
        """reset_session_metadata method is defined."""
        assert hasattr(RangeAndMetadataMixin, "reset_session_metadata")


# ---------------------------------------------------------------------------
# get_messages_range tests
# ---------------------------------------------------------------------------


class TestGetMessagesRange:
    """测试 get_messages_range 异步方法。"""

    @pytest.mark.asyncio
    async def test_session_not_found(self) -> None:
        """当 session does not exist, returns empty list."""
        mgr = _TestRangeManager()
        mgr.get_session_info = AsyncMock(return_value=None)

        result = await mgr.get_messages_range("missing")
        assert result == []

    @pytest.mark.asyncio
    async def test_start_index_negative_adjusted(self) -> None:
        """负 start_index is adjusted to 0."""
        mgr = _TestRangeManager()
        session_info = MagicMock(message_count=10)
        mgr.get_session_info = AsyncMock(return_value=session_info)
        mgr.store.get_message_count = AsyncMock(return_value=10)
        mgr.store.sync_message_counts = AsyncMock()
        mgr.store.get_messages_range = AsyncMock(
            return_value=[MagicMock(id=1), MagicMock(id=2)]
        )

        result = await mgr.get_messages_range("s1", start_index=-5, end_index=2)
        assert len(result) == 2
        # Should be called with offset=0 (start=0), limit=2
        call_kwargs = mgr.store.get_messages_range.call_args[1]
        assert call_kwargs["offset"] == 0
        assert call_kwargs["limit"] == 2

    @pytest.mark.asyncio
    async def test_start_index_beyond_total(self) -> None:
        """当 start_index >= total_messages, returns empty list."""
        mgr = _TestRangeManager()
        session_info = MagicMock(message_count=5)
        mgr.get_session_info = AsyncMock(return_value=session_info)
        mgr.store.get_message_count = AsyncMock(return_value=5)

        result = await mgr.get_messages_range("s1", start_index=10, end_index=15)
        assert result == []

    @pytest.mark.asyncio
    async def test_end_index_beyond_total_adjusted(self) -> None:
        """End_index beyond total is clamped to total."""
        mgr = _TestRangeManager()
        session_info = MagicMock(message_count=10)
        mgr.get_session_info = AsyncMock(return_value=session_info)
        mgr.store.get_message_count = AsyncMock(return_value=10)
        mgr.store.sync_message_counts = AsyncMock()
        mgr.store.get_messages_range = AsyncMock(return_value=[MagicMock(id=1)])

        result = await mgr.get_messages_range("s1", start_index=5, end_index=100)
        assert len(result) == 1
        call_kwargs = mgr.store.get_messages_range.call_args[1]
        assert call_kwargs["offset"] == 5
        assert call_kwargs["limit"] == 5  # 10 - 5

    @pytest.mark.asyncio
    async def test_start_gte_end_returns_empty(self) -> None:
        """当 start_index >= end_index after clamping, returns empty."""
        mgr = _TestRangeManager()
        session_info = MagicMock(message_count=10)
        mgr.get_session_info = AsyncMock(return_value=session_info)
        mgr.store.get_message_count = AsyncMock(return_value=10)

        # start=10, end=8 → after start clamp, start >= end
        result = await mgr.get_messages_range("s1", start_index=10, end_index=8)
        assert result == []

    @pytest.mark.asyncio
    async def test_normal_range(self) -> None:
        """Normal range returns messages from store."""
        mgr = _TestRangeManager()
        session_info = MagicMock(message_count=20)
        mgr.get_session_info = AsyncMock(return_value=session_info)
        mgr.store.get_message_count = AsyncMock(return_value=20)
        mgr.store.sync_message_counts = AsyncMock()
        expected_msgs = [MagicMock(id=i) for i in range(5)]
        mgr.store.get_messages_range = AsyncMock(return_value=expected_msgs)

        result = await mgr.get_messages_range("s1", start_index=10, end_index=15)
        assert result == expected_msgs
        mgr.store.get_messages_range.assert_called_once_with(
            session_id="s1", offset=10, limit=5
        )

    @pytest.mark.asyncio
    async def test_inconsistent_counts_triggers_sync(self) -> None:
        """当 recorded count differs from actual, sync is triggered."""
        mgr = _TestRangeManager()
        session_info = MagicMock(message_count=10)  # recorded
        mgr.get_session_info = AsyncMock(return_value=session_info)
        mgr.store.get_message_count = AsyncMock(return_value=15)  # actual differs
        mgr.store.sync_message_counts = AsyncMock()
        mgr.store.get_messages_range = AsyncMock(return_value=[MagicMock(id=1)])

        await mgr.get_messages_range("s1", start_index=0, end_index=5)
        mgr.store.sync_message_counts.assert_called_once()


# ---------------------------------------------------------------------------
# update_session_metadata tests
# ---------------------------------------------------------------------------


class TestUpdateSessionMetadata:
    """测试 update_session_metadata 异步方法。"""

    @pytest.mark.asyncio
    async def test_session_not_found(self) -> None:
        """当 session does not exist, no update occurs."""
        mgr = _TestRangeManager()
        mgr.store.get_session = AsyncMock(return_value=None)

        await mgr.update_session_metadata("missing", "key", "value")
        # Should complete without error

    @pytest.mark.asyncio
    async def test_updates_metadata(self) -> None:
        """当 session exists, metadata is updated and saved."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {}
        mgr.store.get_session = AsyncMock(return_value=session)
        mgr.store.connection = AsyncMock()
        mgr.store.connection.execute = AsyncMock()
        mgr.store.connection.commit = AsyncMock()

        await mgr.update_session_metadata("s1", "last_summarized_index", 42)
        assert session.metadata["last_summarized_index"] == 42
        mgr.store.connection.execute.assert_called_once()
        mgr.store.connection.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_connection_skips_save(self) -> None:
        """当 store.connection is None, save is skipped."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {}
        mgr.store.get_session = AsyncMock(return_value=session)
        mgr.store.connection = None

        await mgr.update_session_metadata("s1", "key", "value")
        assert session.metadata["key"] == "value"
        # No error despite no connection

    @pytest.mark.asyncio
    async def test_db_error_handled(self) -> None:
        """当 DB write fails, error is caught and logged."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {}
        mgr.store.get_session = AsyncMock(return_value=session)
        mgr.store.connection = AsyncMock()
        mgr.store.connection.execute = AsyncMock(side_effect=Exception("DB error"))
        mgr.store.connection.commit = AsyncMock()

        # Should not raise
        await mgr.update_session_metadata("s1", "key", "value")
        assert session.metadata["key"] == "value"


# ---------------------------------------------------------------------------
# get_session_metadata tests
# ---------------------------------------------------------------------------


class TestGetSessionMetadata:
    """测试 get_session_metadata 异步方法。"""

    @pytest.mark.asyncio
    async def test_session_not_found_returns_default(self) -> None:
        """当 session not found, returns default value."""
        mgr = _TestRangeManager()
        mgr.store.get_session = AsyncMock(return_value=None)

        result = await mgr.get_session_metadata("missing", "key", default="fallback")
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_returns_metadata_value(self) -> None:
        """Returns the stored metadata value."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {"theme": "dark", "lang": "zh"}
        mgr.store.get_session = AsyncMock(return_value=session)

        result = await mgr.get_session_metadata("s1", "theme")
        assert result == "dark"

    @pytest.mark.asyncio
    async def test_missing_key_returns_default(self) -> None:
        """当 key not in metadata, returns default."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {"theme": "dark"}
        mgr.store.get_session = AsyncMock(return_value=session)

        result = await mgr.get_session_metadata("s1", "missing_key", default=None)
        assert result is None


# ---------------------------------------------------------------------------
# reset_session_metadata tests
# ---------------------------------------------------------------------------


class TestResetSessionMetadata:
    """测试 reset_session_metadata 异步方法。"""

    @pytest.mark.asyncio
    async def test_session_not_found(self) -> None:
        """当 session not found, no reset occurs."""
        mgr = _TestRangeManager()
        mgr.store.get_session = AsyncMock(return_value=None)

        await mgr.reset_session_metadata("missing")
        # Should complete without error

    @pytest.mark.asyncio
    async def test_resets_to_empty_dict(self) -> None:
        """会话 metadata is reset to empty dict."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {"old_key": "old_val", "last_summarized_index": 100}
        mgr.store.get_session = AsyncMock(return_value=session)
        mgr.store.connection = AsyncMock()
        mgr.store.connection.execute = AsyncMock()
        mgr.store.connection.commit = AsyncMock()

        await mgr.reset_session_metadata("s1")
        assert session.metadata == {}
        mgr.store.connection.execute.assert_called_once()
        mgr.store.connection.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_connection_skips_save(self) -> None:
        """当 store.connection is None, save is skipped."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {"old": "data"}
        mgr.store.get_session = AsyncMock(return_value=session)
        mgr.store.connection = None

        await mgr.reset_session_metadata("s1")
        assert session.metadata == {}

    @pytest.mark.asyncio
    async def test_db_error_handled(self) -> None:
        """当 DB write fails, error is caught."""
        mgr = _TestRangeManager()
        session = MagicMock()
        session.metadata = {"old": "data"}
        mgr.store.get_session = AsyncMock(return_value=session)
        mgr.store.connection = AsyncMock()
        mgr.store.connection.execute = AsyncMock(side_effect=Exception("DB down"))
        mgr.store.connection.commit = AsyncMock()

        # Should not raise
        await mgr.reset_session_metadata("s1")
        assert session.metadata == {}
