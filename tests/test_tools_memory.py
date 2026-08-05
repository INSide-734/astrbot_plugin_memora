"""测试 core/tools/memory_search_tool.py and memory_memorize_tool.py."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api.platform import MessageType

from core.base.config_manager import ConfigManager
from core.tools.memory_memorize_tool import MemoryMemorizeTool
from core.tools.memory_search_tool import MemorySearchTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_ctx_with_event(session_id: str = "s-001") -> MagicMock:
    """构建 a ContextWrapper-compatible mock with a nested event."""
    event = MagicMock()
    event.unified_msg_origin = session_id
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_sender_id.return_value = "user-001"
    event.session_id = session_id

    inner_ctx = MagicMock()
    inner_ctx.event = event

    wrapper = MagicMock()
    wrapper.context = inner_ctx
    return wrapper


def _make_qq_official_ctx() -> tuple[MagicMock, str]:
    """构造包含完整证据的 QQ Official C2C Agent 上下文。"""

    platform_id = "official-bot-1"
    openid = "OPENID-1"
    author = {"id": openid, "user_openid": openid}
    event = MagicMock()
    event.unified_msg_origin = "qq-official:c2c:OPENID-1"
    event.message_obj = SimpleNamespace(
        raw_message=SimpleNamespace(
            raw_data={"author": author},
            author=SimpleNamespace(user_openid=openid),
        ),
        sender=SimpleNamespace(user_id=openid),
        group_id=None,
    )
    event.get_platform_name.return_value = "qq_official"
    event.get_platform_id.return_value = platform_id
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
    event.get_sender_id.return_value = openid
    wrapper = MagicMock()
    wrapper.context.event = event
    instance_key = hashlib.sha256(platform_id.encode("ascii")).hexdigest()[:24]
    return wrapper, f"qq-official:{instance_key}:{openid}"


def _make_test_config_manager(
    top_k: int = 5,
    max_k: int = 10,
    use_persona_filtering: bool = True,
    use_session_filtering: bool = True,
) -> ConfigManager:
    """构建 a ConfigManager instance with test-appropriate recall settings."""
    data = {
        "recall_engine": {
            "top_k": top_k,
            "max_k": max_k,
        },
        "filtering_settings": {
            "use_persona_filtering": use_persona_filtering,
            "use_session_filtering": use_session_filtering,
        },
    }
    return ConfigManager(user_config=data)


def _make_mock_memory_item(
    doc_id: str = "mem-1",
    content: str = "Test memory content.",
    final_score: float = 0.85,
    metadata: dict | None = None,
) -> MagicMock:
    mem = MagicMock()
    mem.doc_id = doc_id
    mem.content = content
    mem.final_score = final_score
    mem.metadata = (
        metadata
        if metadata is not None
        else {
            "importance": 0.75,
            "session_id": "s-001",
            "persona_id": "p-001",
            "create_time": 1719000000.0,
            "last_access_time": 1719100000.0,
        }
    )
    return mem


# ---------------------------------------------------------------------------
# MemorySearchTool
# ---------------------------------------------------------------------------


class TestMemorySearchTool:
    """测试 MemorySearchTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具 should expose name, description, and parameters schema requiring query."""
        tool = MemorySearchTool()

        assert tool.name == "recall_long_term_memory"
        assert "Recall long-term memory" in tool.description
        assert tool.parameters["type"] == "object"
        assert "query" in tool.parameters["properties"]
        assert "k" in tool.parameters["properties"]
        assert "emotion_context" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_search_happy_path_returns_memories(self):
        """当 memory_engine.search_memories() returns results, tool should serialize them."""
        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(
            return_value=[
                _make_mock_memory_item("mem-1", "Memory one"),
                _make_mock_memory_item("mem-2", "Memory two"),
            ]
        )

        ctx = _make_mock_ctx_with_event()
        mock_plugin_ctx = MagicMock()
        mock_plugin_ctx.get_using_provider.return_value = None

        cm = _make_test_config_manager(
            use_persona_filtering=True, use_session_filtering=True
        )

        with patch(
            "core.tools.memory_search_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p-test"

            tool = MemorySearchTool(
                context=mock_plugin_ctx,
                config_manager=cm,
                memory_engine=mock_engine,
            )
            result = await tool.call(ctx, query="test query", k=3)

        data = json.loads(result)
        assert data["query"] == "test query"
        assert data["count"] == 2
        assert data["applied_filters"]["session_filtered"] is True
        assert data["applied_filters"]["persona_filtered"] is True
        assert len(data["results"]) == 2
        assert data["results"][0]["id"] == "mem-1"
        assert data["results"][0]["content"] == "Memory one"
        assert "formatted_recall" in data
        call_kwargs = mock_engine.search_memories.call_args.kwargs
        assert call_kwargs["chat_type"] == "group"
        assert call_kwargs["user_id"] == "user-001"

    @pytest.mark.asyncio
    async def test_group_recall_passes_privacy_scope_to_engine(self):
        """群聊回忆必须把群聊和发送者作用域传给引擎，交由隐私过滤器拒绝机密记忆。"""
        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(return_value=[])
        tool = MemorySearchTool(
            context=MagicMock(),
            config_manager=_make_test_config_manager(),
            memory_engine=mock_engine,
        )

        await tool.call(_make_mock_ctx_with_event("group:42"), query="private fact")

        kwargs = mock_engine.search_memories.call_args.kwargs
        assert kwargs["chat_type"] == "group"
        assert kwargs["user_id"] == "user-001"

    @pytest.mark.asyncio
    async def test_qq_official_recall_passes_canonical_user_id_to_engine(self):
        """Agent recall 到引擎边界必须携带实例命名空间 canonical ID。"""

        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(return_value=[])
        tool = MemorySearchTool(
            context=MagicMock(),
            config_manager=_make_test_config_manager(),
            memory_engine=mock_engine,
        )
        context, canonical_user_id = _make_qq_official_ctx()

        await tool.call(context, query="private fact")

        kwargs = mock_engine.search_memories.call_args.kwargs
        assert kwargs["chat_type"] == "private"
        assert kwargs["user_id"] == canonical_user_id

    @pytest.mark.asyncio
    async def test_group_recall_without_sender_fails_closed(self):
        """群聊缺少可信发送者时不得进入检索引擎。"""

        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(return_value=[])
        context = _make_mock_ctx_with_event("group:42")
        context.context.event.get_sender_id.return_value = None
        tool = MemorySearchTool(
            context=MagicMock(),
            config_manager=_make_test_config_manager(),
            memory_engine=mock_engine,
        )

        result = await tool.call(context, query="private fact")

        assert json.loads(result)["error"] == "event_scope_unavailable"
        mock_engine.search_memories.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_private_recall_without_sender_fails_closed(self):
        """私聊缺少可信发送者时同样不得进入检索引擎。"""

        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(return_value=[])
        context = _make_mock_ctx_with_event("private:user-42")
        context.context.event.get_message_type.return_value = (
            MessageType.PRIVATE_MESSAGE
        )
        context.context.event.get_sender_id.return_value = None
        tool = MemorySearchTool(
            context=MagicMock(),
            config_manager=_make_test_config_manager(),
            memory_engine=mock_engine,
        )

        result = await tool.call(context, query="private fact")

        assert json.loads(result)["error"] == "event_scope_unavailable"
        mock_engine.search_memories.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_error(self):
        """工具 should return an error when query is empty or whitespace."""
        tool = MemorySearchTool(
            context=MagicMock(),
            config_manager=_make_test_config_manager(),
            memory_engine=MagicMock(),
        )
        result = await tool.call(_make_mock_ctx_with_event(), query="   ")

        data = json.loads(result)
        assert data["count"] == 0
        assert data["error"] == "query is empty"

    @pytest.mark.asyncio
    async def test_search_not_initialized(self):
        """当 any dependency is None, tool should return error."""
        tool = MemorySearchTool(
            context=None,
            config_manager=None,
            memory_engine=None,
        )
        result = await tool.call(_make_mock_ctx_with_event(), query="test")

        data = json.loads(result)
        assert data["error"] == "memory search tool is not initialized"

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """当 search returns empty list, tool should report count=0."""
        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(return_value=[])

        ctx = _make_mock_ctx_with_event()
        cm = _make_test_config_manager(
            use_persona_filtering=False, use_session_filtering=False
        )

        with patch(
            "core.tools.memory_search_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p-test"

            tool = MemorySearchTool(
                context=MagicMock(),
                config_manager=cm,
                memory_engine=mock_engine,
            )
            result = await tool.call(ctx, query="nothing")

        data = json.loads(result)
        assert data["count"] == 0
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_respects_k_clamping(self):
        """工具 should clamp k between 1 and max_k."""
        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(return_value=[])

        ctx = _make_mock_ctx_with_event()
        cm = _make_test_config_manager(
            top_k=5, max_k=10, use_persona_filtering=False, use_session_filtering=False
        )

        with patch(
            "core.tools.memory_search_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p-test"

            tool = MemorySearchTool(
                context=MagicMock(),
                config_manager=cm,
                memory_engine=mock_engine,
            )

            # k=0 should be clamped to 1
            await tool.call(ctx, query="q", k=0)
            first_call_k = mock_engine.search_memories.call_args_list[0].kwargs["k"]
            assert first_call_k == 1

            mock_engine.search_memories.reset_mock()

            # k=100 should be clamped to max_k=10
            await tool.call(ctx, query="q", k=100)
            second_call_k = mock_engine.search_memories.call_args_list[0].kwargs["k"]
            assert second_call_k == 10

    @pytest.mark.asyncio
    async def test_search_catches_exceptions(self):
        """当 search_memories raises an exception, tool should catch and return error."""
        mock_engine = MagicMock()
        mock_engine.search_memories = AsyncMock(side_effect=RuntimeError("crash"))

        ctx = _make_mock_ctx_with_event()
        cm = _make_test_config_manager(
            use_persona_filtering=False, use_session_filtering=False
        )

        with patch(
            "core.tools.memory_search_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p-test"

            tool = MemorySearchTool(
                context=MagicMock(),
                config_manager=cm,
                memory_engine=mock_engine,
            )
            result = await tool.call(ctx, query="test")

        data = json.loads(result)
        assert data["error"] == "internal_error"


# ---------------------------------------------------------------------------
# MemoryMemorizeTool
# ---------------------------------------------------------------------------


class TestMemoryMemorizeTool:
    """测试 MemoryMemorizeTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具 should expose name, description, and parameters schema requiring memory."""
        tool = MemoryMemorizeTool()

        assert tool.name == "memorize_long_term_memory"
        assert "Memorize durable long-term memory" in tool.description
        assert tool.parameters["type"] == "object"
        assert "memory" in tool.parameters["properties"]
        assert "topics" in tool.parameters["properties"]
        assert "key_facts" in tool.parameters["properties"]
        assert "sentiment" in tool.parameters["properties"]
        assert "importance" in tool.parameters["properties"]
        assert "reason" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["memory"]

    @pytest.mark.asyncio
    async def test_memorize_happy_path(self):
        """当 all dependencies are available, tool should write memory and return success."""
        mock_processor = MagicMock()
        mock_processor.build_memory_from_structured_data.return_value = {
            "content": "Test memory content",
            "metadata": {"topics": ["test"]},
            "importance": 0.8,
            "atoms": [{"atom_type": "FACTUAL"}],
        }

        mock_engine = MagicMock()
        mock_engine.add_memory = AsyncMock(return_value="mem-new-001")

        event = MagicMock()
        event.unified_msg_origin = "session-xyz"
        event.get_message_type.return_value = MagicMock(value="GROUP_MESSAGE")

        inner_ctx = MagicMock()
        inner_ctx.event = event

        wrapper = MagicMock()
        wrapper.context = inner_ctx

        mock_plugin_ctx = MagicMock()

        with patch(
            "core.tools.memory_memorize_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "persona-abc"

            tool = MemoryMemorizeTool(
                context=mock_plugin_ctx,
                memory_engine=mock_engine,
                memory_processor=mock_processor,
            )
            result = await tool.call(
                wrapper,
                memory="User prefers dark mode.",
                topics=["ui", "preference"],
                sentiment="positive",
                importance=0.9,
                reason="user explicitly stated preference",
            )

        data = json.loads(result)
        assert data["memorized"] is True
        assert data["id"] == "mem-new-001"
        assert data["content"] == "Test memory content"
        assert data["importance"] == 0.8
        assert data["session_id"] == "session-xyz"
        assert data["persona_id"] == "persona-abc"

        # Verify engine was called
        mock_engine.add_memory.assert_called_once()
        call_kwargs = mock_engine.add_memory.call_args.kwargs
        assert call_kwargs["session_id"] == "session-xyz"
        assert call_kwargs["persona_id"] == "persona-abc"
        assert call_kwargs["importance"] == 0.8
        metadata = call_kwargs["metadata"]
        assert metadata["memory_origin"] == "agent_memorize_tool"
        assert metadata["source_window"]["triggered_by"] == "agent_tool"
        assert metadata["memorize_reason"] == "user explicitly stated preference"

    @pytest.mark.asyncio
    async def test_memorize_empty_memory(self):
        """当 memory string is empty or whitespace, tool should return error."""
        tool = MemoryMemorizeTool(
            context=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
        )
        result = await tool.call(_make_mock_ctx_with_event(), memory="   ")

        data = json.loads(result)
        assert data["memorized"] is False
        assert data["error"] == "memory is empty"

    @pytest.mark.asyncio
    async def test_memorize_not_initialized(self):
        """当 any dependency is None, tool should return error."""
        tool = MemoryMemorizeTool(
            context=None,
            memory_engine=None,
            memory_processor=None,
        )
        result = await tool.call(_make_mock_ctx_with_event(), memory="test")

        data = json.loads(result)
        assert data["memorized"] is False
        assert data["error"] == "memory memorize tool is not initialized"

    @pytest.mark.asyncio
    async def test_memorize_invalid_sentiment_normalized(self):
        """工具 should normalize invalid sentiment values to 'neutral'."""
        mock_processor = MagicMock()
        mock_processor.build_memory_from_structured_data.return_value = {
            "content": "x",
            "metadata": {},
            "importance": 0.5,
            "atoms": [],
        }
        mock_engine = MagicMock()
        mock_engine.add_memory = AsyncMock(return_value="id-1")

        event = MagicMock()
        event.unified_msg_origin = "s"
        event.get_message_type.return_value = MagicMock(value="PRIVATE_MESSAGE")

        inner_ctx = MagicMock()
        inner_ctx.event = event

        wrapper = MagicMock()
        wrapper.context = inner_ctx

        with patch(
            "core.tools.memory_memorize_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p"

            tool = MemoryMemorizeTool(
                context=MagicMock(),
                memory_engine=mock_engine,
                memory_processor=mock_processor,
            )
            result = await tool.call(wrapper, memory="test", sentiment="angry")

        data = json.loads(result)
        assert data["memorized"] is True
        # sentiment "angry" should have been normalized to "neutral" in structured_data
        call_kwargs = mock_processor.build_memory_from_structured_data.call_args.kwargs
        assert call_kwargs["structured_data"]["sentiment"] == "neutral"

    @pytest.mark.asyncio
    async def test_memorize_catches_exceptions(self):
        """当 add_memory raises, tool should catch and return error."""
        mock_processor = MagicMock()
        mock_processor.build_memory_from_structured_data.return_value = {
            "content": "x",
            "metadata": {},
            "importance": 0.5,
            "atoms": [],
        }
        mock_engine = MagicMock()
        mock_engine.add_memory = AsyncMock(side_effect=RuntimeError("crash"))

        event = MagicMock()
        event.unified_msg_origin = "s"
        event.get_message_type.return_value = MagicMock(value="PRIVATE_MESSAGE")

        inner_ctx = MagicMock()
        inner_ctx.event = event

        wrapper = MagicMock()
        wrapper.context = inner_ctx

        with patch(
            "core.tools.memory_memorize_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p"

            tool = MemoryMemorizeTool(
                context=MagicMock(),
                memory_engine=mock_engine,
                memory_processor=mock_processor,
            )
            result = await tool.call(wrapper, memory="test")

        data = json.loads(result)
        assert data["memorized"] is False
        assert data["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_memorize_passes_reason_in_metadata(self):
        """当 reason is provided, it should be stored in metadata."""
        mock_processor = MagicMock()
        mock_processor.build_memory_from_structured_data.return_value = {
            "content": "x",
            "metadata": {},
            "importance": 0.5,
            "atoms": [],
        }
        mock_engine = MagicMock()
        mock_engine.add_memory = AsyncMock(return_value="id-1")

        event = MagicMock()
        event.unified_msg_origin = "s"
        event.get_message_type.return_value = MagicMock(value="PRIVATE_MESSAGE")

        inner_ctx = MagicMock()
        inner_ctx.event = event

        wrapper = MagicMock()
        wrapper.context = inner_ctx

        with patch(
            "core.tools.memory_memorize_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p"

            tool = MemoryMemorizeTool(
                context=MagicMock(),
                memory_engine=mock_engine,
                memory_processor=mock_processor,
            )
            await tool.call(wrapper, memory="test", reason="user said so")

        metadata = mock_engine.add_memory.call_args.kwargs["metadata"]
        assert metadata["memorize_reason"] == "user said so"

    @pytest.mark.asyncio
    async def test_memorize_blank_reason_not_stored(self):
        """当 reason is blank/whitespace, it should not appear in metadata."""
        mock_processor = MagicMock()
        mock_processor.build_memory_from_structured_data.return_value = {
            "content": "x",
            "metadata": {},
            "importance": 0.5,
            "atoms": [],
        }
        mock_engine = MagicMock()
        mock_engine.add_memory = AsyncMock(return_value="id-1")

        event = MagicMock()
        event.unified_msg_origin = "s"
        event.get_message_type.return_value = MagicMock(value="PRIVATE_MESSAGE")

        inner_ctx = MagicMock()
        inner_ctx.event = event

        wrapper = MagicMock()
        wrapper.context = inner_ctx

        with patch(
            "core.tools.memory_memorize_tool.get_persona_id", new_callable=AsyncMock
        ) as mock_gpi:
            mock_gpi.return_value = "p"

            tool = MemoryMemorizeTool(
                context=MagicMock(),
                memory_engine=mock_engine,
                memory_processor=mock_processor,
            )
            await tool.call(wrapper, memory="test", reason="   ")

        metadata = mock_engine.add_memory.call_args.kwargs["metadata"]
        assert "memorize_reason" not in metadata


# ---------------------------------------------------------------------------
# _normalize_list helper (imported from memory_memorize_tool)
# ---------------------------------------------------------------------------


def test_normalize_list_trims_and_limits():
    """_normalize_list should strip whitespace, filter empty strings, limit to 5."""
    from core.tools.memory_memorize_tool import _normalize_list

    assert _normalize_list([" a ", " b ", " c "]) == ["a", "b", "c"]
    assert _normalize_list(["  ", "\t"]) == []
    assert _normalize_list("  single  ") == ["single"]
    assert _normalize_list(["a", "b", "c", "d", "e", "f", "g"]) == [
        "a",
        "b",
        "c",
        "d",
        "e",
    ]
    assert _normalize_list([]) == []
    assert _normalize_list(None) == []
    assert _normalize_list(42) == []
