"""测试会话管理 — SessionCacheMixin and SessionLifecycleMixin."""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.conversation.application.session_cache import SessionCacheMixin
from core.features.conversation.application.session_lifecycle import (
    SessionLifecycleMixin,
)

# ---------------------------------------------------------------------------
# Concrete test class combining both mixins
# ---------------------------------------------------------------------------


class _TestSessionManager(SessionCacheMixin, SessionLifecycleMixin):
    """具体 class for testing session mixins."""

    def __init__(self, store=None):
        self.store = store or MagicMock()
        self._cache: OrderedDict[str, tuple[list, float]] = OrderedDict()
        self._cache_lock = MagicMock()
        self.max_cache_size = 10
        self.session_ttl = 3600

    async def _get_from_cache(self, session_id):
        """Delegate to mixin."""
        return await SessionCacheMixin._get_from_cache(self, session_id)

    async def _update_cache(self, session_id, messages):
        """Delegate to mixin."""
        return await SessionCacheMixin._update_cache(self, session_id, messages)


# ---------------------------------------------------------------------------
# SessionCacheMixin tests
# ---------------------------------------------------------------------------


class TestSessionCacheMixin:
    """Behavioral tests for SessionCacheMixin LRU cache."""

    @pytest.mark.asyncio
    async def test_update_cache_adds_entry(self) -> None:
        """_update_cache stores messages with timestamp."""
        mgr = _TestSessionManager()
        from core.models.conversation_models import Message

        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="hello",
                sender_id="u1",
                platform="test",
            )
        ]
        await mgr._update_cache("s1", msgs)
        assert "s1" in mgr._cache

    @pytest.mark.asyncio
    async def test_update_cache_moves_existing_to_end(self) -> None:
        """Existing entries are moved to the end (most recent)."""
        mgr = _TestSessionManager()
        from core.models.conversation_models import Message

        msgs1 = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="a",
                sender_id="u1",
                platform="test",
            )
        ]
        msgs2 = [
            Message(
                id=2,
                session_id="s2",
                role="user",
                content="b",
                sender_id="u2",
                platform="test",
            )
        ]
        msgs3 = [
            Message(
                id=3,
                session_id="s1",
                role="user",
                content="c",
                sender_id="u1",
                platform="test",
            )
        ]

        await mgr._update_cache("s1", msgs1)
        await mgr._update_cache("s2", msgs2)
        await mgr._update_cache("s1", msgs3)  # s1 moves to end

        keys = list(mgr._cache.keys())
        assert keys[-1] == "s1"  # s1 is now most recent

    @pytest.mark.asyncio
    async def test_get_from_cache_returns_messages(self) -> None:
        """_get_from_cache returns stored messages."""
        mgr = _TestSessionManager()
        from core.models.conversation_models import Message

        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="test",
                sender_id="u1",
                platform="test",
            )
        ]
        await mgr._update_cache("s1", msgs)
        result = await mgr._get_from_cache("s1")
        assert result is not None
        assert len(result) == 1
        assert result[0].content == "test"

    @pytest.mark.asyncio
    async def test_get_from_cache_moves_to_end(self) -> None:
        """_get_from_cache treats as access and moves to end."""
        mgr = _TestSessionManager()
        from core.models.conversation_models import Message

        msgs1 = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="a",
                sender_id="u1",
                platform="test",
            )
        ]
        msgs2 = [
            Message(
                id=2,
                session_id="s2",
                role="user",
                content="b",
                sender_id="u2",
                platform="test",
            )
        ]

        await mgr._update_cache("s1", msgs1)
        await mgr._update_cache("s2", msgs2)

        # Access s1 — moves to end
        await mgr._get_from_cache("s1")
        keys = list(mgr._cache.keys())
        assert keys[-1] == "s1"

    @pytest.mark.asyncio
    async def test_get_from_cache_missing_returns_none(self) -> None:
        """当 session is not in cache, returns None."""
        mgr = _TestSessionManager()
        result = await mgr._get_from_cache("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_cache_removes_entry(self) -> None:
        """invalidate_cache removes the session from cache."""
        mgr = _TestSessionManager()
        from core.models.conversation_models import Message

        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="x",
                sender_id="u1",
                platform="test",
            )
        ]
        await mgr._update_cache("s1", msgs)
        assert "s1" in mgr._cache

        await mgr.invalidate_cache("s1")
        assert "s1" not in mgr._cache

    @pytest.mark.asyncio
    async def test_invalidate_cache_nonexistent_noop(self) -> None:
        """Invalidating a non-existent session is a no-op."""
        mgr = _TestSessionManager()
        await mgr.invalidate_cache("nonexistent")  # should not error

    @pytest.mark.asyncio
    async def test_eviction_on_max_size(self) -> None:
        """当 cache exceeds max_cache_size, oldest entry is evicted."""
        mgr = _TestSessionManager()
        mgr.max_cache_size = 3
        from core.models.conversation_models import Message

        for i in range(5):
            msgs = [
                Message(
                    id=i,
                    session_id=f"s{i}",
                    role="user",
                    content=str(i),
                    sender_id="u",
                    platform="test",
                )
            ]
            await mgr._update_cache(f"s{i}", msgs)

        assert len(mgr._cache) <= 3
        # s0 and s1 should be evicted (oldest)
        assert "s0" not in mgr._cache
        assert "s1" not in mgr._cache
        assert "s4" in mgr._cache


# ---------------------------------------------------------------------------
# SessionLifecycleMixin tests
# ---------------------------------------------------------------------------


class TestSessionLifecycleMixin:
    """Behavioral tests for SessionLifecycleMixin."""

    @pytest.mark.asyncio
    async def test_create_or_get_session_creates_new(self) -> None:
        """当 session does not exist, a new one is created."""
        mgr = _TestSessionManager()
        new_session = MagicMock()
        mgr.store.get_session = AsyncMock(return_value=None)
        mgr.store.create_session = AsyncMock(return_value=new_session)

        result = await mgr.create_or_get_session("new-session", platform="qq")
        assert result is new_session
        mgr.store.create_session.assert_called_once_with("new-session", "qq")

    @pytest.mark.asyncio
    async def test_create_or_get_session_returns_existing(self) -> None:
        """当 session exists, returns it and updates activity."""
        mgr = _TestSessionManager()
        existing = MagicMock()
        mgr.store.get_session = AsyncMock(return_value=existing)
        mgr.store.update_session_activity = AsyncMock()

        result = await mgr.create_or_get_session("existing-session")
        assert result is existing
        mgr.store.update_session_activity.assert_called_once_with("existing-session")
        mgr.store.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_session_info_returns_session(self) -> None:
        """get_session_info delegates to store."""
        mgr = _TestSessionManager()
        session = MagicMock()
        mgr.store.get_session = AsyncMock(return_value=session)

        result = await mgr.get_session_info("s1")
        assert result is session

    @pytest.mark.asyncio
    async def test_get_session_info_missing(self) -> None:
        """当 session not found, returns None."""
        mgr = _TestSessionManager()
        mgr.store.get_session = AsyncMock(return_value=None)

        result = await mgr.get_session_info("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_recent_sessions(self) -> None:
        """get_recent_sessions delegates to store."""
        mgr = _TestSessionManager()
        sessions = [MagicMock(), MagicMock()]
        mgr.store.get_recent_sessions = AsyncMock(return_value=sessions)

        result = await mgr.get_recent_sessions(limit=5)
        assert result == sessions
        mgr.store.get_recent_sessions.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_clear_session(self) -> None:
        """clear_session deletes messages, clears cache, resets metadata."""
        mgr = _TestSessionManager()
        mgr.store.delete_session_messages = AsyncMock()
        # Mock reset_session_metadata from RangeAndMetadataMixin
        mgr.reset_session_metadata = AsyncMock(return_value=True)

        await mgr.clear_session("s1")
        mgr.store.delete_session_messages.assert_called_once_with("s1")

    @pytest.mark.asyncio
    async def test_clear_session_rejects_unpersisted_metadata_reset(self) -> None:
        """元数据重置未提交时不得报告会话清理成功。"""
        mgr = _TestSessionManager()
        mgr.store.delete_session_messages = AsyncMock()
        mgr.reset_session_metadata = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="元数据"):
            await mgr.clear_session("s1")

    @pytest.mark.asyncio
    async def test_cleanup_expired_sessions(self) -> None:
        """cleanup_expired_sessions calls store and clears cache."""
        mgr = _TestSessionManager()
        mgr.session_ttl = 7200
        mgr.store.delete_old_sessions = AsyncMock(return_value=5)

        # Pre-populate cache
        from core.models.conversation_models import Message

        msgs = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="x",
                sender_id="u1",
                platform="test",
            )
        ]
        await mgr._update_cache("s1", msgs)

        result = await mgr.cleanup_expired_sessions()
        assert result == 5
        mgr.store.delete_old_sessions.assert_called_once_with(ttl_seconds=7200)
        # Cache should be cleared
        assert len(mgr._cache) == 0
