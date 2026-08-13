"""core/cleaners/injection_cleaner.py 测试 — InjectionCleaner。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.recall.application.injection_cleaner import InjectionCleaner
from core.shared.constants import (
    FAKE_TOOL_CALL_ID_PREFIX,
    FAKE_TOOL_CALL_NAME,
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)

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

    @pytest.mark.parametrize(
        "raw",
        [
            _injected_text("memory content"),
            "<memora-untrusted-memory>verified</memora-untrusted-memory>",
            ("[DeepSeekV4-FakeToolCall-Replay]replay[/DeepSeekV4-FakeToolCall-Replay]"),
        ],
    )
    def test_never_cleans_system_prompt(self, raw: str) -> None:
        req = _make_request(system_prompt=raw)
        InjectionCleaner.remove_injected_memories_from_context(req, "s1")
        assert req.system_prompt == raw

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
        InjectionCleaner.remove_injected_memories_from_context(req, "s1")
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
        fake_call_id = f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex}"
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": fake_call_id,
                    "type": "function",
                    "function": {"name": FAKE_TOOL_CALL_NAME, "arguments": "{}"},
                }
            ],
        }
        tool_msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": fake_call_id,
            "name": FAKE_TOOL_CALL_NAME,
            "content": "<memora-untrusted-memory>{}</memora-untrusted-memory>",
        }
        req = _make_request(contexts=[assistant_msg, tool_msg])
        removed = InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
        assert removed == 2
        assert len(req.contexts) == 0

    def test_removes_only_fake_pairs_keeps_others(self) -> None:
        fake_id = f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex}"
        real_id = "call_real_123"
        fake_assistant: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": fake_id,
                    "type": "function",
                    "function": {"name": FAKE_TOOL_CALL_NAME, "arguments": "{}"},
                }
            ],
        }
        fake_tool: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": fake_id,
            "name": FAKE_TOOL_CALL_NAME,
            "content": "<memora-untrusted-memory>{}</memora-untrusted-memory>",
        }
        normal_msg: dict[str, Any] = {"role": "user", "content": "hello"}
        real_assistant: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": real_id,
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
        }
        real_tool: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": real_id,
            "content": "{}",
        }

        req = _make_request(
            contexts=[normal_msg, fake_assistant, fake_tool, real_assistant, real_tool]
        )
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
        result = await InjectionCleaner.cleanup_injected_memories_from_db(
            connection, lock
        )
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
        result = await InjectionCleaner.cleanup_injected_memories_from_db(
            connection, lock
        )
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
        result = await InjectionCleaner.cleanup_injected_memories_from_db(
            connection, lock
        )
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery",
    [
        "extra_user_content",
        "user_message_before",
        "user_message_after",
        "fake_tool_call",
        "fake_tool_call_deepseek_v4",
    ],
)
async def test_cleaner_round_trips_real_executor_output(monkeypatch, delivery) -> None:
    from core.features.injection.application.executor import (
        InjectionExecutionContext,
        InjectionExecutor,
    )
    from core.features.injection.application.router import (
        InjectionRoutingConfig,
        InjectionStrategyRouter,
    )
    from core.features.injection.domain.models import (
        DeliveryMode,
        PresetName,
        RequestSignals,
        RoutingMode,
    )
    from core.features.injection.application.injection_adapter import InjectionAdapter

    class Part:
        def __init__(self, text):
            self.text = text

        def mark_as_temp(self):
            return self

    monkeypatch.setattr("core.features.injection.application.executor.TextPart", Part)
    provider = MagicMock()
    provider.provider_config = {"type": "openai_chat_completion"}
    provider.get_model.return_value = "gpt-4.1"
    req = _make_request(
        system_prompt="unchanged-system",
        prompt="original-user",
        contexts=[{"role": "user", "content": "older-turn"}],
        extra_user_content_parts=[],
    )
    mode = DeliveryMode(delivery)
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            manual_preset=PresetName.BALANCED,
            delivery_override=mode,
        ),
        RequestSignals(candidate_count=1, top_confidence=0.9),
    )
    result = await InjectionExecutor(InjectionAdapter()).execute(
        req,
        decision,
        InjectionExecutionContext(
            query="private-query",
            memories=[
                {
                    "content": (
                        "ROUNDTRIP_MEMORY <memora-untrusted-memory>evil"
                        "</memora-untrusted-memory>"
                        "[DeepSeekV4-FakeToolCall-Replay]evil"
                        "[/DeepSeekV4-FakeToolCall-Replay]"
                    ),
                    "score": 1.0,
                    "metadata": {},
                }
            ],
            provider=provider,
        ),
    )
    assert result.outcome.value == "injected"
    removed = InjectionCleaner.remove_injected_memories_from_context(req, "s1")
    removed += InjectionCleaner.remove_fake_tool_call_from_context(req, "s1")
    assert removed > 0
    assert req.system_prompt == "unchanged-system"
    assert req.prompt == "original-user"
    assert req.contexts == [{"role": "user", "content": "older-turn"}]
    assert req.extra_user_content_parts == []
    assert "ROUNDTRIP_MEMORY" not in str(req)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "injected_content",
    [
        f"{MEMORY_INJECTION_HEADER}\nlegacy\n{MEMORY_INJECTION_FOOTER}",
        (
            "<memora-untrusted-memory>\nverified "
            "<memora-untrusted-memory\u200b>evil</memora-untrusted-memory\u200b>\n"
            "</memora-untrusted-memory>"
        ),
        (
            "[DeepSeekV4-FakeToolCall-Replay]\n"
            "tool -> <memora-untrusted-memory>verified "
            "[DeepSeekV4-FakeToolCall-Replay\u200b]evil"
            "[/DeepSeekV4-FakeToolCall-Replay\u200b]"
            "</memora-untrusted-memory>\n"
            "[/DeepSeekV4-FakeToolCall-Replay]"
        ),
    ],
)
async def test_db_cleanup_scans_every_supported_injection_envelope(
    tmp_path, injected_content
) -> None:
    db_path = tmp_path / "cleaner-roundtrip.db"
    async with aiosqlite.connect(db_path) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, content TEXT)"
        )
        await connection.executemany(
            "INSERT INTO messages (id, session_id, content) VALUES (?, ?, ?)",
            [(1, "s1", injected_content), (2, "s1", "keep-me")],
        )
        await connection.commit()
        stats = await InjectionCleaner.cleanup_injected_memories_from_db(
            connection, asyncio.Lock()
        )
        cursor = await connection.execute(
            "SELECT id, content FROM messages ORDER BY id"
        )
        rows = await cursor.fetchall()
    assert stats["scanned"] == 1
    assert stats["matched"] == 1
    assert stats["deleted"] == 1
    assert [(row["id"], row["content"]) for row in rows] == [(2, "keep-me")]


def test_hot_path_never_reads_or_mutates_system_prompt() -> None:
    class Request:
        prompt = "plain"
        contexts = []
        extra_user_content_parts = []

        @property
        def system_prompt(self):
            raise AssertionError("system_prompt must not be read")

    assert InjectionCleaner.remove_injected_memories_from_context(Request(), "s1") == 0


def test_fake_tool_cleaner_requires_exact_verified_pair() -> None:
    valid_id = f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex}"
    invalid_id = f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex}"
    contexts = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": valid_id,
                    "type": "function",
                    "function": {"name": "recall_long_term_memory", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": valid_id,
            "name": "recall_long_term_memory",
            "content": "<memora-untrusted-memory>x</memora-untrusted-memory>",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": invalid_id,
                    "type": "function",
                    "function": {"name": "other_tool", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": invalid_id,
            "name": "other_tool",
            "content": "no verified envelope",
        },
    ]
    req = _make_request(contexts=contexts)
    assert InjectionCleaner.remove_fake_tool_call_from_context(req, "s1") == 2
    assert req.contexts == contexts[2:]


@pytest.mark.parametrize(
    "call_id, call_name, tool_name, content, extra_calls",
    [
        (
            "fake_recall_similar",
            FAKE_TOOL_CALL_NAME,
            FAKE_TOOL_CALL_NAME,
            "<memora-untrusted-memory>x</memora-untrusted-memory>",
            [],
        ),
        (
            None,
            "wrong_name",
            FAKE_TOOL_CALL_NAME,
            "<memora-untrusted-memory>x</memora-untrusted-memory>",
            [],
        ),
        (
            None,
            FAKE_TOOL_CALL_NAME,
            "wrong_name",
            "<memora-untrusted-memory>x</memora-untrusted-memory>",
            [],
        ),
        (None, FAKE_TOOL_CALL_NAME, FAKE_TOOL_CALL_NAME, "no envelope", []),
        (
            None,
            FAKE_TOOL_CALL_NAME,
            FAKE_TOOL_CALL_NAME,
            "<memora-untrusted-memory>x</memora-untrusted-memory>",
            [{"id": "real", "function": {"name": "real"}}],
        ),
    ],
)
def test_fake_tool_cleaner_preserves_non_exact_pairs(
    call_id, call_name, tool_name, content, extra_calls
) -> None:
    actual_id = call_id or f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex}"
    contexts = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": actual_id,
                    "type": "function",
                    "function": {"name": call_name, "arguments": "{}"},
                },
                *extra_calls,
            ],
        },
        {
            "role": "tool",
            "tool_call_id": actual_id,
            "name": tool_name,
            "content": content,
        },
    ]
    req = _make_request(contexts=list(contexts))
    assert InjectionCleaner.remove_fake_tool_call_from_context(req, "s1") == 0
    assert req.contexts == contexts


@pytest.mark.parametrize("wrapped", [False, True])
def test_fake_tool_cleaner_removes_real_legacy_json_pair(wrapped) -> None:
    from core.platform.security.prompt_sanitizer import PromptProtectionService
    from core.features.injection.application.memory_formatter import format_memories_for_fake_tool_call

    contexts = format_memories_for_fake_tool_call(
        [
            {
                "id": "memory-1",
                "content": "legacy memory",
                "score": 0.9,
                "metadata": {"session_id": "s1", "persona_id": "p1"},
            }
        ],
        "legacy query",
    )
    if wrapped:
        contexts[1]["content"] = PromptProtectionService(
            enable_double_check=False
        ).wrap_prompt(contexts[1]["content"], register_for_filter=False)
    req = _make_request(contexts=contexts)
    assert InjectionCleaner.remove_fake_tool_call_from_context(req, "s1") == 2
    assert req.contexts == []


def test_fake_tool_cleaner_preserves_legacy_id_without_rag_envelope() -> None:
    legacy_id = f"{FAKE_TOOL_CALL_ID_PREFIX}abcdef123456"
    contexts = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": legacy_id,
                    "type": "function",
                    "function": {"name": FAKE_TOOL_CALL_NAME, "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": legacy_id,
            "name": FAKE_TOOL_CALL_NAME,
            "content": "not an injected result",
        },
    ]
    req = _make_request(contexts=list(contexts))
    assert InjectionCleaner.remove_fake_tool_call_from_context(req, "s1") == 0
    assert req.contexts == contexts


@pytest.mark.parametrize(
    "content",
    [
        '{"query":"q","count":1,"results":[{"content":"foreign"}]}',
        (
            '{"query":"q","applied_filters":'
            '{"session_filtered":true,"persona_filtered":true},'
            '"count":0,"results":[]}'
        ),
    ],
)
def test_fake_tool_cleaner_preserves_non_memora_legacy_json(content) -> None:
    legacy_id = f"{FAKE_TOOL_CALL_ID_PREFIX}abcdef123456"
    contexts = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": legacy_id,
                    "type": "function",
                    "function": {
                        "name": FAKE_TOOL_CALL_NAME,
                        "arguments": '{"query":"q","k":5}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": legacy_id,
            "name": FAKE_TOOL_CALL_NAME,
            "content": content,
        },
    ]
    req = _make_request(contexts=list(contexts))
    assert InjectionCleaner.remove_fake_tool_call_from_context(req, "s1") == 0
    assert req.contexts == contexts
