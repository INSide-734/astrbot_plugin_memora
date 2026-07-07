"""core/cleaners/injection_cleaner.py 测试 — InjectionCleaner。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.base.constants import (
    FAKE_TOOL_CALL_ID_PREFIX,
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)
from core.cleaners.injection_cleaner import InjectionCleaner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(**attrs: Any) -> MagicMock:
    """Create a MagicMock ProviderRequest with given attributes."""
    req = MagicMock()
    for k, v in attrs.items():
        setattr(req, k, v)
    # Ensure hasattr works for unspecified attrs
    if not hasattr(req, "system_prompt"):
        req.system_prompt = None
    if not hasattr(req, "extra_user_content_parts"):
        req.extra_user_content_parts = None
    if not hasattr(req, "prompt"):
        req.prompt = None
    if not hasattr(req, "contexts"):
        req.contexts = None
    return req


def _injected_text(body: str) -> str:
    return f"{MEMORY_INJECTION_HEADER}\n{body}\n{MEMORY_INJECTION_FOOTER}"


# ---------------------------------------------------------------------------
# remove_injected_memories_from_context tests
# ---------------------------------------------------------------------------

class TestRemoveInjectedMemoriesFromContext:

    # -- system_prompt --

    def test_cleans_system_prompt_with_injection_markers(self) -> None:
        raw = f"Before text\n{_injected_text('memory content')}\nAfter text"
        req = _make_request(system_prompt=raw)
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert MEMORY_INJECTION_HEADER not in req.system_prompt
        assert MEMORY_INJECTION_FOOTER not in req.system_prompt
        assert "Before text" in req.system_prompt
        assert "After text" in req.system_prompt
        # The regex removes everything between header and footer (DOTALL),
        # so "memory content" is also removed

    def test_no_change_when_no_markers_in_system_prompt(self) -> None:
        raw = "Plain system prompt without markers"
        req = _make_request(system_prompt=raw)
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0
        assert req.system_prompt == raw

    def test_no_change_with_header_only_no_footer(self) -> None:
        raw = f"text {MEMORY_INJECTION_HEADER} but no footer"
        req = _make_request(system_prompt=raw)
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0
        assert req.system_prompt == raw

    def test_system_prompt_becomes_empty_after_cleaning(self) -> None:
        raw = _injected_text("only memory")
        req = _make_request(system_prompt=raw)
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert req.system_prompt == ""

    def test_collapses_multiple_newlines_after_cleaning(self) -> None:
        # content that when cleaned would leave 3+ newlines
        raw = f"First\n\n{_injected_text('mem')}\n\n\nSecond"
        req = _make_request(system_prompt=raw)
        InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        # Triple+ newlines should be collapsed to double
        assert "\n\n\n" not in req.system_prompt

    def test_cleans_multiple_injection_blocks(self) -> None:
        raw = f"{_injected_text('mem1')}\nmiddle\n{_injected_text('mem2')}"
        req = _make_request(system_prompt=raw)
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert MEMORY_INJECTION_HEADER not in req.system_prompt

    # -- extra_user_content_parts --

    def test_removes_parts_with_injection_markers(self) -> None:
        part1 = MagicMock()
        part1.text = _injected_text("mem")
        part2 = MagicMock()
        part2.text = "clean part"

        req = _make_request(extra_user_content_parts=[part1, part2])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert len(req.extra_user_content_parts) == 1
        assert req.extra_user_content_parts[0] is part2

    def test_keeps_parts_without_markers(self) -> None:
        part = MagicMock()
        part.text = "clean text"
        req = _make_request(extra_user_content_parts=[part])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0
        assert len(req.extra_user_content_parts) == 1

    # -- prompt --

    def test_cleans_prompt_with_injection_markers(self) -> None:
        raw = f"prompt before\n{_injected_text('mem')}\nprompt after"
        req = _make_request(prompt=raw)
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert MEMORY_INJECTION_HEADER not in req.prompt

    # -- contexts (str messages) --

    def test_cleans_context_str_with_injection(self) -> None:
        raw = f"Hello\n{_injected_text('memory')}\nWorld"
        req = _make_request(contexts=[raw])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert len(req.contexts) == 1
        assert MEMORY_INJECTION_HEADER not in req.contexts[0]
        assert "Hello" in req.contexts[0]
        assert "World" in req.contexts[0]

    def test_removes_context_str_that_becomes_empty_after_cleaning(self) -> None:
        req = _make_request(contexts=[_injected_text("only mem")])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert len(req.contexts) == 0

    # -- contexts (dict messages) --

    def test_cleans_context_dict_with_injection_in_content(self) -> None:
        raw_content = f"User said:\n{_injected_text('mem')}\nEnd"
        msg = {"role": "user", "content": raw_content}
        req = _make_request(contexts=[msg])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        cleaned_content = req.contexts[0]["content"]
        assert MEMORY_INJECTION_HEADER not in cleaned_content
        assert "User said" in cleaned_content
        assert "End" in cleaned_content

    def test_removes_context_dict_with_empty_result(self) -> None:
        msg = {"role": "user", "content": _injected_text("only mem")}
        req = _make_request(contexts=[msg])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed >= 1
        assert len(req.contexts) == 0

    def test_preserves_context_dict_without_markers(self) -> None:
        msg = {"role": "user", "content": "normal message"}
        req = _make_request(contexts=[msg])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0
        assert req.contexts[0] is msg

    def test_preserves_non_string_context_dict_content(self) -> None:
        msg = {"role": "user", "content": 12345}
        req = _make_request(contexts=[msg])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0
        assert req.contexts[0] is msg

    # -- contexts (dict messages with list content — multi-part) --

    def test_cleans_context_dict_with_list_content(self) -> None:
        part1 = {"type": "text", "text": "normal part"}
        part2 = {"type": "text", "text": _injected_text("mem")}
        part3 = {"type": "text", "text": "another part"}
        msg = {"role": "user", "content": [part1, part2, part3]}
        req = _make_request(contexts=[msg])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        # The injected part is dropped, not modified, so removed_count may be 0
        # but the context message is still updated with cleaned parts
        assert len(req.contexts) == 1
        cleaned_content = req.contexts[0]["content"]
        assert len(cleaned_content) == 2
        texts = [p["text"] for p in cleaned_content]
        assert "normal part" in texts
        assert "another part" in texts

    def test_removes_context_dict_when_all_list_parts_cleaned(self) -> None:
        part = {"type": "text", "text": _injected_text("mem")}
        msg = {"role": "user", "content": [part]}
        req = _make_request(contexts=[msg])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        # All parts removed -> cleaned_parts empty -> removed_count += 1
        assert removed >= 1
        assert len(req.contexts) == 0

    # -- contexts (non-dict, non-str) messages are kept as-is --

    def test_preserves_non_str_non_dict_contexts(self) -> None:
        obj = object()
        req = _make_request(contexts=[obj])
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0
        assert req.contexts[0] is obj

    # -- error handling --

    def test_handles_exceptions_gracefully(self) -> None:
        req = _make_request()
        # Make contexts iteration raise
        req.contexts = None  # type: ignore[assignment]
        # The function handles None for hasattr check
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0

    def test_returns_zero_when_request_is_bare(self) -> None:
        req = _make_request()
        removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert removed == 0


# ---------------------------------------------------------------------------
# remove_fake_tool_call_from_context tests
# ---------------------------------------------------------------------------

class TestRemoveFakeToolCallFromContext:

    def test_removes_fake_tool_call_pair(self) -> None:
        fake_call_id = f"{FAKE_TOOL_CALL_ID_PREFIX}abc123"
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": fake_call_id, "type": "function", "function": {"name": "test", "arguments": "{}"}}
            ],
        }
        tool_msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": fake_call_id,
            "name": "test",
            "content": "{}",
        }
        req = _make_request(contexts=[assistant_msg, tool_msg])
        removed = InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
        assert removed == 2
        assert len(req.contexts) == 0

    def test_removes_only_fake_pairs_keeps_others(self) -> None:
        fake_id = f"{FAKE_TOOL_CALL_ID_PREFIX}xyz"
        real_id = "call_real_123"
        fake_assistant: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": [{"id": fake_id, "type": "function", "function": {"name": "t", "arguments": "{}"}}],
        }
        fake_tool: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": fake_id,
            "content": "{}",
        }
        normal_msg: dict[str, Any] = {"role": "user", "content": "hello"}
        real_assistant: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": [{"id": real_id, "type": "function", "function": {"name": "t", "arguments": "{}"}}],
        }
        real_tool: dict[str, Any] = {"role": "tool", "tool_call_id": real_id, "content": "{}"}

        req = _make_request(contexts=[normal_msg, fake_assistant, fake_tool, real_assistant, real_tool])
        removed = InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
        assert removed == 2
        assert len(req.contexts) == 3
        assert req.contexts[0] is normal_msg

    def test_no_contexts_returns_zero(self) -> None:
        req = _make_request()
        removed = InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
        assert removed == 0

    def test_contexts_without_dict_messages_ignored_returns_zero(self) -> None:
        req = _make_request(contexts=["just a string", 123])
        removed = InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
        assert removed == 0

    def test_handles_exceptions_gracefully(self) -> None:
        # Create a request that will raise during iteration
        req = _make_request()
        req.contexts = None  # type: ignore[assignment]
        removed = InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
        assert removed == 0


# ---------------------------------------------------------------------------
# cleanup_injected_memories_from_db tests
# ---------------------------------------------------------------------------

class _AsyncCursor:
    """A combined mock for aiosqlite cursor + execute return value.

    Works both as an async context manager (for SELECT via
    ``async with connection.execute(...) as cursor``) and as a
    standalone coroutine (for ``await connection.execute(...)``
    used by DELETE/UPDATE).
    """

    def __init__(self, fetchall_result: list[dict]) -> None:
        self._result = fetchall_result
        self.execute_args: list[tuple] = []

    def __await__(self) -> "_AsyncCursorAwaitable":
        """Support ``await connection.execute(...)`` (DELETE/UPDATE)."""
        return _AsyncCursorAwaitable(self)._await_impl().__await__()

    async def __aenter__(self) -> "_AsyncCursor":
        """Support ``async with connection.execute(...) as cursor``."""
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def fetchall(self) -> list[dict]:
        return self._result


class _AsyncCursorAwaitable:
    """Minimal awaitable wrapper so _AsyncCursor can be awaited directly."""

    def __init__(self, cursor: _AsyncCursor) -> None:
        self._cursor = cursor

    async def _await_impl(self) -> _AsyncCursor:
        return self._cursor


class TestCleanupInjectedMemoriesFromDb:

    @pytest.mark.asyncio
    async def test_null_connection_returns_error(self) -> None:
        lock = asyncio.Lock()
        result = await InjectionCleaner.cleanup_injected_memories_from_db(None, lock)
        assert result["error"] == 1
        assert "message" in result

    @pytest.mark.asyncio
    async def test_no_matching_rows(self) -> None:
        connection = MagicMock()
        connection.execute.return_value = _AsyncCursor([])
        connection.commit = AsyncMock()  # type: ignore[assignment]

        lock = asyncio.Lock()
        result = await InjectionCleaner.cleanup_injected_memories_from_db(connection, lock)
        assert result["scanned"] == 0
        assert result["matched"] == 0
        assert result["cleaned"] == 0
        assert result["deleted"] == 0

    @pytest.mark.asyncio
    async def test_cleans_matching_row(self) -> None:
        raw_content = f"User said hi\n{_injected_text('mem content')}\nBye"

        connection = MagicMock()
        connection.execute.return_value = _AsyncCursor(
            [{"id": 1, "session_id": "s1", "content": raw_content}]
        )
        connection.commit = AsyncMock()  # type: ignore[assignment]

        lock = asyncio.Lock()
        result = await InjectionCleaner.cleanup_injected_memories_from_db(connection, lock)
        assert result["scanned"] == 1
        assert result["matched"] == 1
        assert result["cleaned"] == 1
        assert result["deleted"] == 0

    @pytest.mark.asyncio
    async def test_deletes_row_with_only_injection_content(self) -> None:
        raw_content = _injected_text("pure memory")

        connection = MagicMock()
        connection.execute.return_value = _AsyncCursor(
            [{"id": 2, "session_id": "s2", "content": raw_content}]
        )
        connection.commit = AsyncMock()  # type: ignore[assignment]

        lock = asyncio.Lock()
        result = await InjectionCleaner.cleanup_injected_memories_from_db(connection, lock)
        assert result["scanned"] == 1
        assert result["matched"] == 1
        assert result["deleted"] == 1
        assert result["cleaned"] == 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify_db(self) -> None:
        raw_content = f"x\n{_injected_text('mem')}\ny"

        connection = MagicMock()
        selection_cursor = _AsyncCursor(
            [{"id": 3, "session_id": "s3", "content": raw_content}]
        )
        connection.execute.return_value = selection_cursor
        connection.commit = AsyncMock()  # type: ignore[assignment]

        lock = asyncio.Lock()
        result = await InjectionCleaner.cleanup_injected_memories_from_db(
            connection, lock, dry_run=True
        )
        # In dry_run, cleaned count still increments but no DB writes
        assert result["cleaned"] == 1
        # connection.execute was only called for the SELECT, not UPDATE/DELETE
        assert connection.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_filters_by_session_id(self) -> None:
        connection = MagicMock()
        connection.execute.return_value = _AsyncCursor([])
        connection.commit = AsyncMock()  # type: ignore[assignment]

        lock = asyncio.Lock()
        await InjectionCleaner.cleanup_injected_memories_from_db(
            connection, lock, session_id="target_session"
        )
        # Verify the query includes the session_id parameter
        call_args = connection.execute.call_args
        query = call_args[0][0]
        assert "AND session_id = ?" in query
        params = call_args[0][1]
        assert "target_session" in params
