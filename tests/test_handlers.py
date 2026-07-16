"""core/handlers/ 测试 — recall_handler.py 和 reflection_handler.py。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# RecallHandler tests
# ============================================================================


class TestRecallHandlerConstruction:
    """Tests for RecallHandler.__init__."""

    def test_stores_all_dependencies(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        ctx = MagicMock()
        cfg = MagicMock()
        engine = MagicMock()
        conv = MagicMock()
        adapter = MagicMock()
        enforce_cb = MagicMock()

        handler = RecallHandler(
            context=ctx,
            config_manager=cfg,
            memory_engine=engine,
            conversation_manager=conv,
            injection_adapter=adapter,
            enforce_limit_cb=enforce_cb,
        )
        assert handler._context is ctx
        assert handler._config_manager is cfg
        assert handler._memory_engine is engine
        assert handler._conversation_manager is conv
        assert handler._injection_adapter is adapter
        assert handler._enforce_limit_cb is enforce_cb

    def test_creates_sub_components(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        assert handler._cleaner is not None
        assert handler._extractor is not None
        assert handler._query_rewriter is not None


class TestRecallHandlerFallbackQuery:
    """Tests for _build_fallback_query()."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_recent_messages(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        conv = MagicMock()
        conv.get_context = AsyncMock(return_value=[])

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=conv,
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._build_fallback_query("test-session")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_with_single_message(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        conv = MagicMock()
        conv.get_context = AsyncMock(
            return_value=[{"content": "hello", "role": "user"}]
        )

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=conv,
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._build_fallback_query("test-session")
        assert result is None

    @pytest.mark.asyncio
    async def test_builds_query_from_multiple_messages(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        conv = MagicMock()
        conv.get_context = AsyncMock(
            return_value=[
                {"content": "hi", "role": "user"},
                {"content": "how are you", "role": "user"},
                {"content": "I'm good", "role": "assistant"},
            ]
        )

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=conv,
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._build_fallback_query("test-session")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        conv = MagicMock()
        conv.get_context = AsyncMock(side_effect=RuntimeError("DB error"))

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=conv,
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._build_fallback_query("test-session")
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_empty_content_messages(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        conv = MagicMock()
        conv.get_context = AsyncMock(
            return_value=[
                {"content": "", "role": "user"},
                {"content": "  ", "role": "user"},
                {"content": "actual message", "role": "user"},
                {"content": None, "role": "system"},
                {"content": "another one", "role": "assistant"},
            ]
        )

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=conv,
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._build_fallback_query("test-session")
        assert result is not None
        # Only non-empty messages should be included, max 3
        assert "actual message" in result


class TestRecallHandlerSpontaneousRecall:
    """Tests for _maybe_spontaneous_recall()."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        cfg = MagicMock()
        cfg.get.return_value = False  # spontaneous_recall_enabled = False

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._maybe_spontaneous_recall(
            session_id="test", persona_id=None, chat_type="group"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_probability_not_met(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "recall_engine.spontaneous_recall_enabled": True,
            "recall_engine.spontaneous_recall_probability": 0.0,  # 0% probability
            "recall_engine.spontaneous_recall_k": 2,
        }.get(key, default)

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._maybe_spontaneous_recall(
            session_id="test", persona_id=None, chat_type="group"
        )
        assert result == []


class TestRecallHandlerProspectiveRecall:
    """Tests for _maybe_prospective_recall()."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        cfg = MagicMock()
        cfg.get.return_value = False  # prospective_recall_enabled = False

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._maybe_prospective_recall(
            session_id="test", persona_id=None, chat_type="group"
        )
        assert result == []


class TestRecallHandlerSearchParameters:
    """Tests for parameters passed from RecallHandler to MemoryEngine."""

    @pytest.mark.asyncio
    async def test_sender_id_is_forwarded_as_user_id(self) -> None:
        from astrbot.api.platform import MessageType
        from core.handlers.recall_handler import RecallHandler
        from core.monitoring.perf_tracker import PerfTracker

        cfg = MagicMock()
        cfg.filtering_settings = {
            "use_persona_filtering": True,
            "use_session_filtering": True,
        }
        cfg.get.side_effect = lambda key, default=None: {
            "recall_engine.query_rewrite_enabled": False,
            "recall_engine.top_k": 3,
            "recall_engine.inject_with_recent_context": False,
            "recall_engine.spontaneous_recall_enabled": False,
            "recall_engine.prospective_recall_enabled": False,
        }.get(key, default)

        engine = MagicMock()
        engine.search_memories = AsyncMock(return_value=[])
        conv = MagicMock()
        conv.add_message_from_event = AsyncMock()
        adapter = MagicMock()
        perf_tracker = PerfTracker()

        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event.get_message_type.return_value = MessageType.PRIVATE_MESSAGE
        event.get_sender_id.return_value = "user-1"
        req = MagicMock()
        req.prompt = "hello"
        req.extra_user_content_parts = []

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=engine,
            conversation_manager=conv,
            injection_adapter=adapter,
            enforce_limit_cb=AsyncMock(),
            perf_tracker=perf_tracker,
        )
        handler._extractor.get_event_message_str = AsyncMock(return_value="hello")

        await handler.handle_memory_recall(event, req)

        engine.search_memories.assert_awaited_once()
        assert engine.search_memories.await_args.kwargs["user_id"] == "user-1"
        assert perf_tracker.get_perf_data()["count_total_ms"] == 1


class TestRecallHandlerFinalizeCandidates:
    """Tests for final recall candidate de-duplication and budget enforcement."""

    def test_finalizes_unique_candidates_with_top_k_limit(self) -> None:
        from core.handlers.recall_handler import RecallHandler
        from core.retrieval.rrf_fusion import HybridResult

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        candidates = [
            HybridResult(doc_id=1, final_score=0.5, rrf_score=0.5, bm25_score=None, vector_score=None, content="main-1", metadata={}),
            HybridResult(doc_id=2, final_score=0.4, rrf_score=0.4, bm25_score=None, vector_score=None, content="main-2", metadata={}),
            HybridResult(doc_id=3, final_score=0.3, rrf_score=0.3, bm25_score=None, vector_score=None, content="main-3", metadata={}),
            HybridResult(doc_id=4, final_score=0.2, rrf_score=0.2, bm25_score=None, vector_score=None, content="main-4", metadata={}),
            HybridResult(doc_id=5, final_score=0.1, rrf_score=0.1, bm25_score=None, vector_score=None, content="main-5", metadata={}),
            HybridResult(
                doc_id=3,
                final_score=0.95,
                rrf_score=0.95,
                bm25_score=None,
                vector_score=None,
                content="prospective duplicate",
                metadata={"recall_source": "prospective"},
            ),
            HybridResult(
                doc_id=6,
                final_score=0.9,
                rrf_score=0.9,
                bm25_score=None,
                vector_score=None,
                content="spontaneous",
                metadata={"recall_source": "spontaneous"},
            ),
            HybridResult(
                doc_id=7,
                final_score=0.85,
                rrf_score=0.85,
                bm25_score=None,
                vector_score=None,
                content="prospective",
                metadata={"recall_source": "prospective"},
            ),
        ]

        finalized = handler._finalize_recall_candidates(candidates, top_k=5)

        assert len(finalized) == 5
        assert len({item.doc_id for item in finalized}) == 5
        assert finalized[0].doc_id == 3
        assert finalized[0].metadata["recall_source"] == "prospective"

    def test_enabled_prefers_recall_engine_key(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "recall_engine.prospective_recall_enabled": False,
            "prospective.enabled": True,
        }.get(key, default)

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )

        assert handler._prospective_recall_enabled() is False

    def test_enabled_falls_back_to_legacy_key(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "prospective.enabled": False,
        }.get(key, default)

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )

        assert handler._prospective_recall_enabled() is False


class TestRecallHandlerPromptProtection:
    """Tests for recall injection prompt protection wrapping."""

    def test_wrap_injected_context_uses_prompt_protection_service(self) -> None:
        from core.handlers.recall_handler import RecallHandler
        from core.security.prompt_sanitizer import PromptProtectionService

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.prompt_protection_enabled": True,
        }.get(key, default)
        service = PromptProtectionService(enable_double_check=False)

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=service,
        )

        wrapped = handler._wrap_injected_context("记忆上下文", "session-1")
        assert "system_internal" in wrapped
        assert service.get_stats()["wrapped"] == 1

    def test_wrap_injected_context_respects_disabled_config(self) -> None:
        from core.handlers.recall_handler import RecallHandler
        from core.security.prompt_sanitizer import PromptProtectionService

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.prompt_protection_enabled": False,
        }.get(key, default)

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=PromptProtectionService(),
        )

        assert handler._wrap_injected_context("记忆上下文", "session-1") == "记忆上下文"

    def test_wrap_fake_tool_messages_wraps_tool_content_only(self) -> None:
        from core.handlers.recall_handler import RecallHandler
        from core.security.prompt_sanitizer import PromptProtectionService

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.prompt_protection_enabled": True,
        }.get(key, default)

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=PromptProtectionService(enable_double_check=False),
        )
        messages = [
            {"role": "assistant", "content": None},
            {"role": "tool", "content": '{"results": [{"content": "记忆内容"}]}'},
        ]

        wrapped = handler._wrap_fake_tool_messages(messages, "session-1")
        assert wrapped[0]["content"] is None
        assert "system_internal" in wrapped[1]["content"]
        assert "记忆内容" in wrapped[1]["content"]
        assert messages[1]["content"].startswith('{"results"')

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_atom_store(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        cfg = MagicMock()
        cfg.get.return_value = True
        engine = MagicMock()
        engine.atom_store = None

        handler = RecallHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=engine,
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        result = await handler._maybe_prospective_recall(
            session_id="test", persona_id=None, chat_type="group"
        )
        assert result == []


# ============================================================================
# ReflectionHandler tests
# ============================================================================


class TestReflectionHandlerConstruction:
    """Tests for ReflectionHandler.__init__."""

    def test_stores_all_dependencies(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        ctx = MagicMock()
        cfg = MagicMock()
        engine = MagicMock()
        proc = MagicMock()
        conv = MagicMock()
        enforce_cb = MagicMock()

        handler = ReflectionHandler(
            context=ctx,
            config_manager=cfg,
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
            enforce_limit_cb=enforce_cb,
        )
        assert handler._context is ctx
        assert handler._config_manager is cfg
        assert handler._memory_engine is engine
        assert handler._memory_processor is proc
        assert handler._conversation_manager is conv
        assert handler._enforce_limit_cb is enforce_cb

    def test_initial_state(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        assert handler._storage_tasks == set()
        assert handler._storage_sessions_inflight == set()
        assert handler._shutting_down is False


class TestReflectionHandlerShutdown:
    """Tests for ReflectionHandler.shutdown()."""

    @pytest.mark.asyncio
    async def test_sets_shutting_down_flag(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        await handler.shutdown()
        assert handler._shutting_down is True

    @pytest.mark.asyncio
    async def test_clears_storage_state(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        # Simulate some inflight state
        handler._storage_sessions_inflight.add("session-1")
        await handler.shutdown()
        assert handler._storage_sessions_inflight == set()


class TestReflectionHandlerMessageFiltering:
    """Tests for handle_memory_reflection message filtering logic."""

    @pytest.mark.asyncio
    async def test_skips_non_assistant_role(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        event = MagicMock()
        resp = MagicMock()
        resp.role = "user"  # not "assistant"
        resp.tools_call_name = None
        resp.tools_call_extra_content = None

        await handler.handle_memory_reflection(event, resp)
        # Should return early without processing

    @pytest.mark.asyncio
    async def test_skips_tool_call_responses(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        event = MagicMock()
        resp = MagicMock()
        resp.role = "assistant"
        resp.tools_call_name = ["some_tool"]
        resp.tools_call_extra_content = None

        await handler.handle_memory_reflection(event, resp)
        # Should skip tool call responses

    @pytest.mark.asyncio
    async def test_skips_empty_response_text(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        resp = MagicMock()
        resp.role = "assistant"
        resp.tools_call_name = None
        resp.tools_call_extra_content = None
        resp.completion_text = ""

        await handler.handle_memory_reflection(event, resp)
        # Should skip empty responses

    @pytest.mark.asyncio
    async def test_skips_error_responses(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        event = MagicMock()
        event.unified_msg_origin = "test-session"
        resp = MagicMock()
        resp.role = "assistant"
        resp.tools_call_name = None
        resp.tools_call_extra_content = None
        resp.completion_text = "API error: request failed"

        await handler.handle_memory_reflection(event, resp)
        # Should skip error responses


class TestReflectionHandlerPromptProtection:
    """Tests for LLM response sanitization before storage."""

    def test_sanitize_response_text_removes_internal_tags(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler
        from core.security.prompt_sanitizer import PromptProtectionService

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.sanitize_llm_response": True,
            "security.double_check_enabled": False,
        }.get(key, default)
        service = PromptProtectionService(enable_double_check=False)
        service.wrap_prompt("内部记忆上下文")

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=service,
        )

        cleaned = handler._sanitize_response_text(
            '正常回复 <system_internal do_not_output="true">内部记忆上下文</system_internal>',
            "session-1",
        )
        assert "system_internal" not in cleaned
        assert "内部记忆上下文" not in cleaned
        assert "正常回复" in cleaned

    def test_sanitize_response_text_respects_disabled_config(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler
        from core.security.prompt_sanitizer import PromptProtectionService

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.sanitize_llm_response": False,
        }.get(key, default)

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=PromptProtectionService(),
        )

        original = '<system_internal do_not_output="true">内部</system_internal> 正常'
        assert handler._sanitize_response_text(original, "session-1") == original


class TestPrepareMessageBatches:
    """Tests for _prepare_message_batches()."""

    @pytest.mark.asyncio
    async def test_single_batch_for_strategy_a_b_hybrid(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        cfg = MagicMock()
        cfg.get.return_value = "a_b_hybrid"

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        messages = [{"role": "user", "content": "msg1"}, {"role": "assistant", "content": "msg2"}]
        batches = await handler._prepare_message_batches(messages, False)
        assert len(batches) == 1
        assert batches[0] == messages

    @pytest.mark.asyncio
    async def test_single_batch_for_few_messages_strategy_c(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        cfg = MagicMock()
        cfg.get.return_value = "c"  # Strategy C

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )
        messages = [{"role": "user", "content": "msg1"}, {"role": "assistant", "content": "msg2"}]
        batches = await handler._prepare_message_batches(messages, False)
        # < 3 messages means single batch
        assert len(batches) == 1


class TestStorageTaskDone:
    """Tests for _on_storage_task_done callback."""

    @pytest.mark.asyncio
    async def test_removes_from_sets_on_success(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )

        async def simple_coro():
            return None

        task = AsyncMock(wraps=None)
        task.cancelled = MagicMock(return_value=False)
        task.exception = MagicMock(return_value=None)

        handler._storage_tasks.add(task)
        handler._storage_sessions_inflight.add("session-1")

        handler._on_storage_task_done(task, "session-1")
        assert task not in handler._storage_tasks
        assert "session-1" not in handler._storage_sessions_inflight


class TestRecordPendingSummary:
    """Tests for _record_pending_summary()."""

    @pytest.mark.asyncio
    async def test_skips_when_no_conversation_manager(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=None,
            enforce_limit_cb=MagicMock(),
        )
        # Should not raise
        await handler._record_pending_summary("session-1", 0, 10, 0)

    @pytest.mark.asyncio
    async def test_increments_retry_count(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        conv = MagicMock()
        conv.update_session_metadata = AsyncMock()

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=conv,
            enforce_limit_cb=MagicMock(),
        )
        await handler._record_pending_summary("session-1", 0, 10, 2)
        conv.update_session_metadata.assert_awaited()
        call_args = conv.update_session_metadata.call_args
        assert call_args[0][0] == "session-1"
        assert call_args[0][1] == "pending_summary"
        assert call_args[0][2]["retry_count"] == 3  # 2 + 1
        assert call_args[0][2]["start_index"] == 0
        assert call_args[0][2]["end_index"] == 10


class TestReflectionStorageTaskCommit:
    """Tests for summary window commit safety."""

    @pytest.mark.asyncio
    async def test_partial_memory_write_keeps_pending_and_does_not_advance(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        conv = MagicMock()
        conv.get_session_metadata = AsyncMock(return_value=0)
        conv.update_session_metadata = AsyncMock()

        proc = MagicMock()
        proc.process_conversation = AsyncMock(
            return_value=[
                {"content": "memory-1", "importance": 0.8, "metadata": {}},
                {"content": "memory-2", "importance": 0.7, "metadata": {}},
            ]
        )

        engine = MagicMock()
        engine.add_memory = AsyncMock(side_effect=[None, RuntimeError("write failed")])

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
            enforce_limit_cb=MagicMock(),
        )
        handler._prepare_message_batches = AsyncMock(return_value=[[MagicMock(group_id=None)]])

        await handler._storage_task(
            session_id="session-1",
            history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
            persona_id="persona",
            start_index=0,
            end_index=4,
            retry_count=0,
        )

        metadata_calls = conv.update_session_metadata.await_args_list
        assert not any(
            call.args[:3] == ("session-1", "last_summarized_index", 4)
            for call in metadata_calls
        )
        pending_call = metadata_calls[-1]
        assert pending_call.args[0] == "session-1"
        assert pending_call.args[1] == "pending_summary"
        assert pending_call.args[2]["failed_stage"] == "memory_write"
        assert pending_call.args[2]["failed_count"] == 1
        assert len(pending_call.args[2]["completed_idempotency_keys"]) == 1

    @pytest.mark.asyncio
    async def test_retry_skips_previously_completed_memory_candidates(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        conv = MagicMock()
        conv.get_session_metadata = AsyncMock(
            side_effect=[
                0,
                {
                    "completed_idempotency_keys": [
                        ReflectionHandler._memory_idempotency_key(
                            session_id="session-1",
                            start_index=0,
                            end_index=4,
                            batch_index=0,
                            memory_index=0,
                            content="memory-1",
                        )
                    ]
                },
            ]
        )
        conv.update_session_metadata = AsyncMock()

        proc = MagicMock()
        proc.process_conversation = AsyncMock(
            return_value=[
                {"content": "memory-1", "importance": 0.8, "metadata": {}},
                {"content": "memory-2", "importance": 0.7, "metadata": {}},
            ]
        )

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=22)

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
            enforce_limit_cb=MagicMock(),
        )
        handler._prepare_message_batches = AsyncMock(return_value=[[MagicMock(group_id=None)]])

        await handler._storage_task(
            session_id="session-1",
            history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
            persona_id="persona",
            start_index=0,
            end_index=4,
            retry_count=1,
        )

        engine.add_memory.assert_awaited_once()
        assert engine.add_memory.await_args.kwargs["content"] == "memory-2"

    @pytest.mark.asyncio
    async def test_successful_retry_advances_and_clears_pending(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        conv = MagicMock()
        conv.get_session_metadata = AsyncMock(return_value=0)
        conv.update_session_metadata = AsyncMock()

        proc = MagicMock()
        proc.process_conversation = AsyncMock(
            return_value=[
                {"content": "memory-1", "importance": 0.8, "metadata": {}},
            ]
        )

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=None)

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
            enforce_limit_cb=MagicMock(),
        )
        handler._prepare_message_batches = AsyncMock(return_value=[[MagicMock(group_id=None)]])

        await handler._storage_task(
            session_id="session-1",
            history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
            persona_id="persona",
            start_index=0,
            end_index=4,
            retry_count=1,
        )

        conv.update_session_metadata.assert_any_await(
            "session-1", "last_summarized_index", 4
        )
        conv.update_session_metadata.assert_any_await(
            "session-1", "pending_summary", None
        )

    @pytest.mark.asyncio
    async def test_summary_window_locker_serializes_session(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
        )

        assert await handler.try_begin_summary_window("session-1") is True
        assert await handler.try_begin_summary_window("session-1") is False
        handler.finish_summary_window("session-1")
        assert await handler.try_begin_summary_window("session-1") is True


class TestEventHandlerInjectionLifecycle:
    def test_passes_injection_dependencies_to_recall_handler(self) -> None:
        from core.event_handler import EventHandler

        recorder = MagicMock()
        with patch("core.event_handler.RecallHandler") as recall_handler_type:
            EventHandler(
                context=MagicMock(),
                config_manager=MagicMock(),
                memory_engine=MagicMock(),
                memory_processor=MagicMock(),
                conversation_manager=MagicMock(),
                injection_recorder=recorder,
                memory_tool_available=True,
            )

        kwargs = recall_handler_type.call_args.kwargs
        assert kwargs["injection_recorder"] is recorder
        assert kwargs["memory_tool_available"] is True

    @pytest.mark.asyncio
    async def test_shutdown_closes_recorder_after_reflection_and_maintenance(
        self,
    ) -> None:
        from core.event_handler import EventHandler

        order: list[str] = []
        recorder = MagicMock()
        recorder.close = AsyncMock(side_effect=lambda **_kwargs: order.append("recorder"))
        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            injection_recorder=recorder,
        )
        handler._reflection_handler.shutdown = AsyncMock(
            side_effect=lambda: order.append("reflection")
        )

        async def maintenance() -> None:
            order.append("maintenance")

        handler._create_maintenance_task(maintenance(), name="test-maintenance")
        await handler.shutdown()

        assert order == ["reflection", "maintenance", "recorder"]
        recorder.close.assert_awaited_once_with(timeout=5.0)

    def test_recall_handler_stores_injection_dependencies(self) -> None:
        from core.handlers.recall_handler import RecallHandler

        recorder = MagicMock()
        handler = RecallHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            injection_adapter=MagicMock(),
            enforce_limit_cb=MagicMock(),
            injection_recorder=recorder,
            memory_tool_available=True,
        )

        assert handler._injection_recorder is recorder
        assert handler._memory_tool_available is True
