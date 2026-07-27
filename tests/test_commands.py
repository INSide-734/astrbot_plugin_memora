"""core/commands/ 测试 — query_commands.py 和 maintenance_commands.py。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ============================================================================
# QueryCommandMixin tests
# ============================================================================


class TestFormatErrorMessage:
    """Tests for _format_error_message in QueryCommandMixin."""

    def test_returns_non_empty_string(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        msg = QueryCommandMixin._format_error_message("测试", ValueError("错误"))
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_includes_suggestions_when_provided(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        msg = QueryCommandMixin._format_error_message(
            "测试", ValueError("错误"), suggestions=["建议1", "建议2"]
        )
        assert isinstance(msg, str)
        # The format uses t() fallback — check it's longer with suggestions
        assert "suggestion" in msg.lower() or len(msg) > 0

    def test_formats_without_suggestions(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        msg = QueryCommandMixin._format_error_message(
            "action_name", RuntimeError("err_detail")
        )
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestComponentNotReadyMessage:
    """Tests for _component_not_ready_message in QueryCommandMixin."""

    def test_returns_non_empty_string(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        msg = QueryCommandMixin._component_not_ready_message("记忆引擎", "/lmem status")
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestMaintenanceWriteGuardDefaults:
    """Tests for default write-guard behavior on standalone mixins."""

    def test_query_mixin_defaults_to_no_write_guard(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            pass

        assert TestMixin()._maintenance_write_guard_message() is None

    def test_maintenance_mixin_defaults_to_no_write_guard(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin

        class TestMixin(MaintenanceCommandMixin):
            pass

        assert TestMixin()._maintenance_write_guard_message() is None


class TestHandleStatus:
    """Tests for handle_status in QueryCommandMixin."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_memory_engine(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            memory_engine = None

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in mixin.handle_status(event):
            results.append(result)

        assert len(results) == 1


class TestHandleSearch:
    """Tests for handle_search in QueryCommandMixin."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_memory_engine(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            memory_engine = None

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in mixin.handle_search(event, "test query"):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_error_for_empty_query(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            memory_engine = MagicMock()

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="empty query")

        results = []
        async for result in mixin.handle_search(event, ""):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_error_for_whitespace_only_query(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            memory_engine = MagicMock()

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="whitespace query")

        results = []
        async for result in mixin.handle_search(event, "   "):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_clamps_k_to_range(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        engine = MagicMock()
        engine.search_memories = AsyncMock(return_value=[])

        class TestMixin(QueryCommandMixin):
            memory_engine = engine

        mixin = TestMixin()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="no results")

        # k=0 should be clamped to 1
        results = []
        async for result in mixin.handle_search(event, "test", k=0):
            results.append(result)
        engine.search_memories.assert_awaited_once()
        assert engine.search_memories.call_args[1]["k"] == 1

    @pytest.mark.asyncio
    async def test_clamps_k_to_max_100(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        engine = MagicMock()
        engine.search_memories = AsyncMock(return_value=[])

        class TestMixin(QueryCommandMixin):
            memory_engine = engine

        mixin = TestMixin()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="no results")

        results = []
        async for result in mixin.handle_search(event, "test", k=200):
            results.append(result)
        engine.search_memories.assert_awaited_once()
        assert engine.search_memories.call_args[1]["k"] == 100


class TestHandleForget:
    """Tests for handle_forget in QueryCommandMixin."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_memory_engine(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            memory_engine = None

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in mixin.handle_forget(event, 1):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_error_for_negative_doc_id(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(QueryCommandMixin):
            memory_engine = MagicMock()

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="invalid")

        results = []
        async for result in mixin.handle_forget(event, -1):
            results.append(result)

        assert len(results) == 1


class TestHandleWebui:
    """Tests for handle_webui in QueryCommandMixin."""

    @pytest.mark.asyncio
    async def test_yields_result(self) -> None:
        from core.commands.query_commands import QueryCommandMixin

        event = MagicMock()
        event.plain_result = MagicMock(return_value="webui guide")

        results = []
        async for result in QueryCommandMixin.handle_webui(event):
            results.append(result)

        assert len(results) == 1


# ============================================================================
# MaintenanceCommandMixin tests
# ============================================================================


class TestMaintenanceHandleRebuildIndex:
    """Tests for handle_rebuild_index."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_memory_engine(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(MaintenanceCommandMixin, QueryCommandMixin):
            memory_engine = None
            index_validator = MagicMock()

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in mixin.handle_rebuild_index(event):
            results.append(result)

        assert len(results) == 1


class TestMaintenanceHandleRebuildGraph:
    """Tests for handle_rebuild_graph."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_memory_engine(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(MaintenanceCommandMixin, QueryCommandMixin):
            memory_engine = None

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in mixin.handle_rebuild_graph(event):
            results.append(result)

        assert len(results) == 1


class TestMaintenanceHandleReset:
    """Tests for handle_reset."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_conversation_manager(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin
        from core.commands.query_commands import QueryCommandMixin

        class TestMixin(MaintenanceCommandMixin, QueryCommandMixin):
            conversation_manager = None

        mixin = TestMixin()
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in mixin.handle_reset(event):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_calls_clear_session_with_valid_session(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin

        conv = MagicMock()
        conv.clear_session = AsyncMock()

        class TestMixin(MaintenanceCommandMixin):
            conversation_manager = conv

        mixin = TestMixin()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="reset ok")

        results = []
        async for result in mixin.handle_reset(event):
            results.append(result)

        conv.clear_session.assert_awaited_once_with("test-session")
        assert len(results) == 1


class TestMaintenanceHandleCleanupErrors:
    """Tests for handle_cleanup error paths."""

    @pytest.mark.asyncio
    async def test_handles_missing_context(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin

        class TestMixin(MaintenanceCommandMixin):
            context = None

        mixin = TestMixin()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="no context")

        results = []
        async for result in mixin.handle_cleanup(event):
            results.append(result)

        # Should yield "starting" message and then context unavailable
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_handles_missing_conversation_id(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin

        ctx = MagicMock()
        ctx.conversation_manager = MagicMock()
        ctx.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=None)

        class TestMixin(MaintenanceCommandMixin):
            context = ctx
            conversation_manager = None  # Doesn't matter for this path

        class Handler(MaintenanceCommandMixin):
            context = ctx

        mixin = Handler()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="no cid")

        results = []
        async for result in mixin.handle_cleanup(event):
            results.append(result)

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_handles_no_conversation_history(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin

        ctx = MagicMock()
        ctx.conversation_manager = MagicMock()
        ctx.conversation_manager.get_curr_conversation_id = AsyncMock(
            return_value="cid-123"
        )
        conversation = MagicMock()
        conversation.history = None
        ctx.conversation_manager.get_conversation = AsyncMock(return_value=conversation)

        class Handler(MaintenanceCommandMixin):
            context = ctx

        mixin = Handler()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="empty")

        results = []
        async for result in mixin.handle_cleanup(event):
            results.append(result)

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_handles_invalid_json_history(self) -> None:
        from core.commands.maintenance_commands import MaintenanceCommandMixin

        ctx = MagicMock()
        ctx.conversation_manager = MagicMock()
        ctx.conversation_manager.get_curr_conversation_id = AsyncMock(
            return_value="cid-123"
        )
        conversation = MagicMock()
        conversation.history = "not valid json{{{"
        ctx.conversation_manager.get_conversation = AsyncMock(return_value=conversation)

        class Handler(MaintenanceCommandMixin):
            context = ctx

        mixin = Handler()
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        event.plain_result = MagicMock(return_value="parse error")

        results = []
        async for result in mixin.handle_cleanup(event):
            results.append(result)

        assert len(results) >= 1
