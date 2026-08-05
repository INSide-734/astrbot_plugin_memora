"""core/handlers/ 测试 — recall_handler.py 和 reflection_handler.py。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api.platform import MessageType

from core.injection.models import (
    DeliveryMode,
    InjectionExecutionResult,
    InjectionOutcome,
)
from core.retrieval.rrf_fusion import HybridResult

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

        async def search_memories(**kwargs):
            """模拟引擎向当前请求的局部 sink 写入阶段计时。"""

            kwargs["timing_sink"].update(
                {
                    "retrieval_total_ms": 12.5,
                    "query_count": 1,
                    "cache_hit": False,
                }
            )
            return []

        engine.search_memories = AsyncMock(side_effect=search_memories)
        conv = MagicMock()
        conv.add_message_from_event = AsyncMock()
        adapter = MagicMock()
        adapter.capabilities.return_value = ("", "", False)
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
        sample = perf_tracker.get_samples(after_sequence=0)["items"][0]
        assert sample["retrieval_total_ms"] == 12.5
        assert sample["query_count"] == 1


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
            HybridResult(
                doc_id=1,
                final_score=0.5,
                rrf_score=0.5,
                bm25_score=None,
                vector_score=None,
                content="main-1",
                metadata={},
            ),
            HybridResult(
                doc_id=2,
                final_score=0.4,
                rrf_score=0.4,
                bm25_score=None,
                vector_score=None,
                content="main-2",
                metadata={},
            ),
            HybridResult(
                doc_id=3,
                final_score=0.3,
                rrf_score=0.3,
                bm25_score=None,
                vector_score=None,
                content="main-3",
                metadata={},
            ),
            HybridResult(
                doc_id=4,
                final_score=0.2,
                rrf_score=0.2,
                bm25_score=None,
                vector_score=None,
                content="main-4",
                metadata={},
            ),
            HybridResult(
                doc_id=5,
                final_score=0.1,
                rrf_score=0.1,
                bm25_score=None,
                vector_score=None,
                content="main-5",
                metadata={},
            ),
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


class TestRecallHandlerProspectiveStore:
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

    @pytest.mark.asyncio
    async def test_registered_injection_is_removed_from_visible_and_stored_response(
        self,
    ) -> None:
        from core.handlers.reflection_handler import ReflectionHandler
        from core.injection.executor import InjectionExecutionContext, InjectionExecutor
        from core.injection.models import PresetName, RequestSignals, RoutingMode
        from core.injection.router import (
            InjectionRoutingConfig,
            InjectionStrategyRouter,
        )
        from core.security.prompt_sanitizer import PromptProtectionService
        from core.utils.injection_adapter import InjectionAdapter

        secret = "outbound unique secret alpha beta gamma delta epsilon"
        service = PromptProtectionService(enable_double_check=False)
        req = SimpleNamespace(
            prompt="question",
            contexts=[],
            extra_user_content_parts=[],
        )
        await InjectionExecutor(InjectionAdapter(), service).execute(
            req,
            InjectionStrategyRouter().route_final(
                InjectionRoutingConfig(
                    mode=RoutingMode.MANUAL,
                    manual_preset=PresetName.BALANCED,
                ),
                RequestSignals(candidate_count=1, top_confidence=0.9),
            ),
            InjectionExecutionContext(
                query="question",
                memories=[{"content": secret, "score": 1.0, "metadata": {}}],
                scope_id="scope-visible",
            ),
        )
        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.sanitize_llm_response": True,
            "security.double_check_enabled": False,
        }.get(key, default)
        conversation = MagicMock()
        conversation.add_message_from_event = AsyncMock()
        conversation.get_session_info = AsyncMock(return_value=None)
        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=conversation,
            enforce_limit_cb=AsyncMock(),
            prompt_protection_service=service,
        )
        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event._memora_prompt_protection_scope = "scope-visible"
        event._memora_prompt_protection_required = True
        resp = SimpleNamespace(
            role="assistant",
            tools_call_name=None,
            tools_call_extra_content=None,
            completion_text=f"safe prefix {secret} safe suffix",
        )

        await handler.handle_memory_reflection(event, resp)

        assert secret not in resp.completion_text
        stored = conversation.add_message_from_event.await_args.kwargs["content"]
        assert secret not in stored

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
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ]
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
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ]
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
        conv.update_session_metadata = AsyncMock(return_value=True)
        conv.update_session_metadata_fields = AsyncMock(return_value=True)

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
    async def test_partial_memory_write_keeps_pending_and_does_not_advance(
        self,
    ) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        conv = MagicMock()
        conv.get_session_metadata = AsyncMock(return_value=0)
        conv.update_session_metadata = AsyncMock(return_value=True)
        conv.update_session_metadata_fields = AsyncMock(return_value=True)

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
        handler._prepare_message_batches = AsyncMock(
            return_value=[[MagicMock(group_id=None)]]
        )

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
        conv.update_session_metadata = AsyncMock(return_value=True)
        conv.update_session_metadata_fields = AsyncMock(return_value=True)

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
        handler._prepare_message_batches = AsyncMock(
            return_value=[[MagicMock(group_id=None)]]
        )

        with patch("core.handlers.reflection_handler.report_debug_event") as report:
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
        write_events = [
            call.kwargs
            for call in report.call_args_list
            if call.args == ("storage_task",)
            and call.kwargs.get("stage") == "memory_write"
            and call.kwargs.get("status") == "completed"
        ]
        assert write_events[-1]["success_count"] == 1
        assert write_events[-1]["skipped_count"] == 1
        assert write_events[-1]["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_successful_retry_advances_and_clears_pending(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        conv = MagicMock()
        conv.get_session_metadata = AsyncMock(return_value=0)
        conv.update_session_metadata = AsyncMock(return_value=True)
        conv.update_session_metadata_fields = AsyncMock(return_value=True)

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
        handler._prepare_message_batches = AsyncMock(
            return_value=[[MagicMock(group_id=None)]]
        )

        await handler._storage_task(
            session_id="session-1",
            history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
            persona_id="persona",
            start_index=0,
            end_index=4,
            retry_count=1,
        )

        conv.update_session_metadata_fields.assert_awaited_once_with(
            "session-1",
            {
                "last_summarized_index": 4,
                "pending_summary": None,
            },
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
        recorder.close = AsyncMock(
            side_effect=lambda **_kwargs: order.append("recorder")
        )
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


# 自适应注入热路径契约


def strategy_config(**overrides):
    values = {
        "recall_engine.top_k": 5,
        "recall_engine.injection_routing_mode": "manual",
        "recall_engine.injection_manual_preset": "balanced",
        "recall_engine.injection_auto_fallback_preset": "balanced",
        "recall_engine.injection_hybrid_base_preset": "balanced",
        "recall_engine.injection_hybrid_min_preset": "low_cost",
        "recall_engine.injection_hybrid_max_preset": "quality",
        "recall_engine.injection_delivery_override": "auto",
        "recall_engine.injection_preset_overrides_enabled": False,
        "recall_engine.auto_remove_injected": False,
        "recall_engine.query_rewrite_enabled": False,
        "recall_engine.spontaneous_recall_enabled": False,
        "recall_engine.prospective_recall_enabled": False,
    }
    values.update(overrides)
    return values


def high_confidence_memories() -> list[HybridResult]:
    return [
        HybridResult(
            doc_id=index,
            final_score=score,
            rrf_score=score,
            bm25_score=None,
            vector_score=None,
            content=f"memory-{index}",
            metadata={"importance": 0.8, "create_time": 1_783_150_200},
        )
        for index, score in enumerate((0.95, 0.82, 0.61), start=1)
    ]


@pytest.fixture
def handler_case(monkeypatch):
    from core.handlers.recall_handler import RecallHandler

    def build(
        *,
        config: dict[str, object],
        memory_tool_available: bool = False,
        provider_tools_supported: bool = False,
        query_intent: str = "default",
        memories: list[HybridResult] | None = None,
        prompt_protection_service=None,
    ):
        manager = MagicMock()
        manager.filtering_settings = {
            "use_persona_filtering": True,
            "use_session_filtering": True,
        }
        manager.runtime_injection_fallback = False
        manager.get.side_effect = lambda key, default=None: config.get(key, default)
        engine = MagicMock()
        engine.search_memories = AsyncMock(return_value=list(memories or []))
        conversation = MagicMock()
        conversation.add_message_from_event = AsyncMock()
        adapter = MagicMock()
        adapter.capabilities.return_value = (
            "openai",
            "test-model",
            provider_tools_supported,
        )
        adapter.resolve.return_value = (DeliveryMode.EXTRA_USER_CONTENT, None)
        recorder = MagicMock()
        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event.get_message_type.return_value = MessageType.PRIVATE_MESSAGE
        event.get_sender_id.return_value = "user-1"
        recall_tool = SimpleNamespace(name="recall_long_term_memory", active=True)
        request_tools = SimpleNamespace(
            get_tool=lambda name: (
                recall_tool
                if memory_tool_available and name == "recall_long_term_memory"
                else None
            )
        )
        request = SimpleNamespace(
            prompt="remember coffee",
            system_prompt="system prompt must be byte-identical",
            contexts=[],
            extra_user_content_parts=[],
            func_tool=request_tools,
            provider=None,
            context_headroom_chars=10_000,
        )

        context = MagicMock()
        handler = RecallHandler(
            context=context,
            config_manager=manager,
            memory_engine=engine,
            conversation_manager=conversation,
            injection_adapter=adapter,
            enforce_limit_cb=AsyncMock(),
            injection_recorder=recorder,
            memory_tool_available=memory_tool_available,
            prompt_protection_service=prompt_protection_service,
        )
        handler._extractor.get_event_message_str = AsyncMock(
            return_value="remember coffee"
        )
        handler._query_rewriter.rewrite = AsyncMock(
            return_value=SimpleNamespace(
                intent=query_intent,
                rewritten_queries=[],
                memory_types=[],
                extracted_entities=[],
            )
        )
        handler._maybe_spontaneous_recall = AsyncMock(return_value=[])
        handler._maybe_prospective_recall = AsyncMock(return_value=[])
        handler._build_cognitive_context = AsyncMock(return_value="")
        monkeypatch.setattr(
            "core.handlers.recall_handler.get_persona_id",
            AsyncMock(return_value="persona-1"),
        )
        return SimpleNamespace(
            handler=handler,
            event=event,
            request=request,
            memory_engine=engine,
            recorder=recorder,
            adapter=adapter,
            context=context,
        )

    return build


@pytest.mark.asyncio
async def test_manual_tool_first_skips_passive_and_spontaneous(handler_case) -> None:
    case = handler_case(
        config=strategy_config(
            **{"recall_engine.injection_manual_preset": "tool_first"}
        ),
        memory_tool_available=True,
        provider_tools_supported=True,
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    case.memory_engine.search_memories.assert_not_awaited()
    case.handler._maybe_spontaneous_recall.assert_not_awaited()
    assert case.recorder.record.call_args.args[0].outcome == "skipped"


@pytest.mark.asyncio
async def test_tool_first_checks_the_current_request_toolset(handler_case) -> None:
    case = handler_case(
        config=strategy_config(
            **{"recall_engine.injection_manual_preset": "tool_first"}
        ),
        memory_tool_available=True,
        provider_tools_supported=True,
    )
    case.request.func_tool = SimpleNamespace(get_tool=lambda _name: None)

    await case.handler.handle_memory_recall(case.event, case.request)

    case.memory_engine.search_memories.assert_awaited_once()
    record = case.recorder.record.call_args.args[0]
    assert record.resolved_preset == "low_cost"
    assert "PROVIDER_TOOL_UNAVAILABLE" in record.reason_codes


def test_preflight_estimates_headroom_from_real_provider_request_fields(
    handler_case,
) -> None:
    from astrbot.api.provider import ProviderRequest

    case = handler_case(config=strategy_config())
    request = ProviderRequest(
        prompt="p" * 300,
        system_prompt="s" * 100,
        contexts=[{"role": "user", "content": "c" * 200}],
        extra_user_content_parts=[SimpleNamespace(text="e" * 100)],
    )
    provider = SimpleNamespace(
        provider_config={"max_context_tokens": 1_000, "max_tokens": 100}
    )

    signals = case.handler._preflight_signals(
        SimpleNamespace(intent="default"),
        provider,
        request,
        "private",
    )

    assert signals.context_headroom_chars == 185

    request.context_headroom_chars = float("inf")
    signals = case.handler._preflight_signals(
        SimpleNamespace(intent="default"), provider, request, "private"
    )
    assert signals.context_headroom_chars == 185

    request.context_headroom_chars = None
    signals = case.handler._preflight_signals(
        SimpleNamespace(intent="default"),
        SimpleNamespace(provider_config={}),
        request,
        "private",
    )
    assert signals.context_headroom_chars == 13_000


@pytest.mark.asyncio
async def test_tool_first_unavailable_falls_back_to_search(handler_case) -> None:
    case = handler_case(
        config=strategy_config(
            **{"recall_engine.injection_manual_preset": "tool_first"}
        ),
        memory_tool_available=True,
        provider_tools_supported=False,
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    case.memory_engine.search_memories.assert_awaited_once()


@pytest.mark.asyncio
async def test_hybrid_clamps_quality_and_records_sanitized_decision(
    handler_case,
) -> None:
    case = handler_case(
        config=strategy_config(
            **{
                "recall_engine.injection_routing_mode": "hybrid",
                "recall_engine.injection_hybrid_max_preset": "balanced",
            }
        ),
        query_intent="temporal",
        memories=high_confidence_memories(),
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    record = case.recorder.record.call_args.args[0]
    assert record.recommended_preset == "quality"
    assert record.resolved_preset == "balanced"
    assert "HYBRID_CLAMPED_MAX" in record.reason_codes
    assert not hasattr(record, "query")
    assert not hasattr(record, "memory_ids")


@pytest.mark.asyncio
async def test_no_candidates_records_empty_without_request_mutation(
    handler_case,
) -> None:
    case = handler_case(config=strategy_config())
    before = (
        case.request.prompt,
        list(case.request.contexts),
        list(case.request.extra_user_content_parts),
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    record = case.recorder.record.call_args.args[0]
    assert record.outcome == "empty"
    assert record.primary_reason == "MANUAL_SELECTED"
    assert (
        case.request.prompt,
        case.request.contexts,
        case.request.extra_user_content_parts,
    ) == before


@pytest.mark.asyncio
async def test_provider_delivery_fallback_is_recorded(handler_case) -> None:
    case = handler_case(config=strategy_config(), memories=high_confidence_memories())
    case.adapter.resolve.return_value = (DeliveryMode.EXTRA_USER_CONTENT, "unsupported")
    await case.handler.handle_memory_recall(case.event, case.request)
    record = case.recorder.record.call_args.args[0]
    assert record.outcome == "fallback"
    assert record.fallback_applied is True
    assert record.resolved_delivery == DeliveryMode.EXTRA_USER_CONTENT.value
    assert record.primary_reason == "MANUAL_SELECTED"
    assert record.reason_codes.count("PROVIDER_DELIVERY_DOWNGRADED") == 1


@pytest.mark.asyncio
async def test_executor_error_leaves_request_atomic(handler_case) -> None:
    case = handler_case(config=strategy_config(), memories=high_confidence_memories())
    case.handler._executor.execute = AsyncMock(
        return_value=InjectionExecutionResult(
            outcome=InjectionOutcome.ERROR,
            error_code="MUTATION_FAILED",
        )
    )
    before = (
        case.request.prompt,
        list(case.request.contexts),
        list(case.request.extra_user_content_parts),
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    assert (
        case.request.prompt,
        case.request.contexts,
        case.request.extra_user_content_parts,
    ) == before
    assert case.recorder.record.call_args.args[0].error_code == "MUTATION_FAILED"


@pytest.mark.asyncio
async def test_recorder_exception_is_isolated(handler_case) -> None:
    case = handler_case(config=strategy_config())
    case.recorder.record.side_effect = RuntimeError("queue unavailable")
    await case.handler.handle_memory_recall(case.event, case.request)


@pytest.mark.asyncio
async def test_global_auxiliary_budget_and_system_prompt_equality(handler_case) -> None:
    case = handler_case(
        config=strategy_config(
            **{
                "recall_engine.injection_budget_chars": 120,
                "recall_engine.cognitive_context_budget_chars": 80,
                "recall_engine.proactive_plan_budget_chars": 80,
            }
        ),
        memories=high_confidence_memories(),
    )
    case.request.context_headroom_chars = 150
    case.handler._build_cognitive_context.return_value = "c" * 80
    case.handler._maybe_prospective_recall.return_value = high_confidence_memories()[:1]
    system_prompt = case.request.system_prompt
    await case.handler.handle_memory_recall(case.event, case.request)
    record = case.recorder.record.call_args.args[0]
    assert record.effective_budget_chars == 150
    assert record.actual_payload_chars <= 150
    assert case.request.system_prompt == system_prompt


@pytest.mark.asyncio
async def test_true_cancellation_propagates(handler_case) -> None:
    case = handler_case(config=strategy_config(), memories=high_confidence_memories())
    case.handler._executor.execute = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await case.handler.handle_memory_recall(case.event, case.request)


@pytest.mark.asyncio
async def test_tool_first_prospective_only_executes_without_search(
    handler_case,
) -> None:
    case = handler_case(
        config=strategy_config(
            **{"recall_engine.injection_manual_preset": "tool_first"}
        ),
        memory_tool_available=True,
        provider_tools_supported=True,
    )
    case.handler._maybe_prospective_recall.return_value = high_confidence_memories()[:1]
    case.handler._executor.execute = AsyncMock(
        return_value=InjectionExecutionResult(
            outcome=InjectionOutcome.INJECTED,
            actual_payload_chars=20,
        )
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    case.memory_engine.search_memories.assert_not_awaited()
    execution_context = case.handler._executor.execute.await_args.args[2]
    assert execution_context.memories == []
    assert execution_context.prospective_context.startswith("[Upcoming Plans]")
    assert case.recorder.record.call_args.args[0].outcome == "injected"


@pytest.mark.asyncio
async def test_auto_final_tool_first_still_executes_once(handler_case) -> None:
    case = handler_case(
        config=strategy_config(
            **{
                "recall_engine.injection_routing_mode": "auto",
                "recall_engine.injection_auto_fallback_preset": "balanced",
            }
        ),
        memory_tool_available=True,
        provider_tools_supported=True,
    )
    case.handler._executor.execute = AsyncMock(
        return_value=InjectionExecutionResult(
            outcome=InjectionOutcome.EMPTY,
        )
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    case.memory_engine.search_memories.assert_awaited_once()
    case.handler._executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_recall_logs_only_sanitized_counts(handler_case, caplog) -> None:
    private_query = "secret-query-7349"
    private_entity = "private-entity-9821"
    case = handler_case(config=strategy_config())
    case.handler._extractor.get_event_message_str.return_value = private_query
    case.handler._query_rewriter.rewrite.return_value = SimpleNamespace(
        intent="default",
        rewritten_queries=[private_query],
        memory_types=[],
        extracted_entities=[private_entity],
    )
    with caplog.at_level("INFO"):
        await case.handler.handle_memory_recall(case.event, case.request)
    log_text = caplog.text
    assert private_query not in log_text
    assert private_entity not in log_text
    assert "rewritten_count=1" in log_text
    assert "entity_count=1" in log_text


def test_safe_candidates_keep_only_stable_scalar_ids() -> None:
    from core.handlers.recall_handler import RecallHandler

    candidates = [
        SimpleNamespace(doc_id="b", content="same", final_score=1.0, metadata={}),
        SimpleNamespace(doc_id=2, content="same", final_score=1.0, metadata={}),
        SimpleNamespace(doc_id=object(), content="same", final_score=1.0, metadata={}),
    ]
    safe = RecallHandler._safe_candidates(candidates)
    assert [item.get("id") for item in safe] == ["b", 2, None]


@pytest.mark.asyncio
async def test_provider_getter_exception_continues_recall_and_record(
    handler_case,
) -> None:
    case = handler_case(config=strategy_config(), memories=high_confidence_memories())
    case.context.get_using_provider.side_effect = RuntimeError(
        "provider registry failed"
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    case.memory_engine.search_memories.assert_awaited_once()
    case.recorder.record.assert_called_once()


@pytest.mark.asyncio
async def test_provider_get_model_exception_uses_conservative_capabilities(
    handler_case,
) -> None:
    from core.utils.injection_adapter import InjectionAdapter

    case = handler_case(config=strategy_config(), memories=high_confidence_memories())
    provider = MagicMock()
    provider.provider_config = {"type": "openai_chat_completion"}
    provider.get_model.side_effect = RuntimeError("model unavailable")
    case.request.provider = provider
    case.handler._injection_adapter = InjectionAdapter()
    await case.handler.handle_memory_recall(case.event, case.request)
    case.memory_engine.search_memories.assert_awaited_once()
    case.recorder.record.assert_called_once()


@pytest.mark.asyncio
async def test_provider_getter_cancellation_propagates(handler_case) -> None:
    case = handler_case(config=strategy_config())
    case.context.get_using_provider.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await case.handler.handle_memory_recall(case.event, case.request)


@pytest.mark.asyncio
async def test_fake_tool_execution_uses_transient_query_without_recording_it(
    handler_case,
) -> None:
    private_query = "transient-private-query-9482"
    case = handler_case(
        config=strategy_config(
            **{
                "recall_engine.injection_delivery_override": "fake_tool_call",
            }
        ),
        memories=high_confidence_memories(),
        provider_tools_supported=True,
    )
    case.request.provider = object()
    case.handler._extractor.get_event_message_str.return_value = private_query
    case.adapter.resolve.return_value = (DeliveryMode.FAKE_TOOL_CALL, None)

    await case.handler.handle_memory_recall(case.event, case.request)

    assistant = case.request.contexts[-2]
    arguments = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
    assert arguments["query"] == private_query
    record = case.recorder.record.call_args.args[0]
    assert not hasattr(record, "query")
    assert private_query not in repr(record)


@pytest.mark.asyncio
async def test_recall_correlates_scope_without_recording_token(handler_case) -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    case = handler_case(
        config=strategy_config(),
        memories=high_confidence_memories(),
        prompt_protection_service=service,
    )
    case.handler._executor.execute = AsyncMock(
        return_value=InjectionExecutionResult(
            outcome=InjectionOutcome.INJECTED,
        )
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    execution_context = case.handler._executor.execute.await_args.args[2]
    scope_id = execution_context.scope_id
    assert isinstance(scope_id, str) and scope_id
    assert case.event._memora_prompt_protection_scope == scope_id
    assert case.event._memora_prompt_protection_required is True
    record = case.recorder.record.call_args.args[0]
    assert scope_id not in repr(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("early_gate", ["tools", "empty_session", "writes_blocked"])
async def test_reflection_sanitizes_visible_response_before_early_gates(
    early_gate,
) -> None:
    from core.handlers.reflection_handler import ReflectionHandler
    from core.security.prompt_sanitizer import PromptProtectionService

    secret = f"{early_gate} scoped secret alpha beta gamma delta epsilon"
    service = PromptProtectionService(enable_double_check=False)
    service.wrap_prompt(secret, scope_id=f"scope-{early_gate}")
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "security.sanitize_llm_response": True,
        "security.double_check_enabled": False,
    }.get(key, default)
    event = MagicMock()
    event.unified_msg_origin = "" if early_gate == "empty_session" else "session-1"
    event.get_extra.return_value = f"scope-{early_gate}"
    resp = SimpleNamespace(
        role="assistant",
        tools_call_name=["tool"] if early_gate == "tools" else None,
        tools_call_extra_content=None,
        completion_text=secret,
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=cfg,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=AsyncMock(),
        prompt_protection_service=service,
        write_guard_cb=(lambda: early_gate == "writes_blocked"),
    )
    await handler.handle_memory_reflection(event, resp)
    assert secret not in resp.completion_text
    after_consume, _ = service.sanitize_response(
        secret,
        scope_id=f"scope-{early_gate}",
    )
    assert after_consume == secret


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "validation"])
async def test_reflection_visible_sanitizer_failures_are_closed(failure) -> None:
    from core.handlers.reflection_handler import ReflectionHandler

    service = MagicMock()
    if failure == "exception":
        service.sanitize_response.side_effect = RuntimeError("sanitize failed")
    else:
        service.sanitize_response.return_value = (
            "unsafe",
            {"leaks_removed": [], "validation_passed": False},
        )
    cfg = MagicMock()
    cfg.get.return_value = True
    event = MagicMock()
    event.unified_msg_origin = ""
    event.get_extra.return_value = "scope-fail"
    resp = SimpleNamespace(
        role="assistant",
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text="unsafe",
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=cfg,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=AsyncMock(),
        prompt_protection_service=service,
    )
    await handler.handle_memory_reflection(event, resp)
    assert resp.completion_text == ""


@pytest.mark.asyncio
async def test_recall_scope_setter_exception_uses_private_scope_without_leaking_token(
    handler_case,
) -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    case = handler_case(
        config=strategy_config(),
        memories=high_confidence_memories(),
        prompt_protection_service=service,
    )
    case.event.set_extra.side_effect = RuntimeError("event seam failed")
    case.handler._executor.execute = AsyncMock(
        return_value=InjectionExecutionResult(
            outcome=InjectionOutcome.INJECTED,
        )
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    context = case.handler._executor.execute.await_args.args[2]
    assert context.scope_id == case.event._memora_prompt_protection_scope
    assert context.scope_id
    assert "event seam failed" not in repr(case.recorder.record.call_args.args[0])


@pytest.mark.asyncio
async def test_reflection_scope_getter_exception_uses_private_fallback() -> None:
    from core.handlers.reflection_handler import ReflectionHandler
    from core.security.prompt_sanitizer import PromptProtectionService

    secret = "private fallback secret alpha beta gamma delta epsilon"
    service = PromptProtectionService(enable_double_check=False)
    service.wrap_prompt(secret, scope_id="scope-private")
    event = MagicMock()
    event.get_extra.side_effect = RuntimeError("event seam failed")
    event._memora_prompt_protection_scope = "scope-private"
    event._memora_prompt_protection_required = True
    event.unified_msg_origin = ""
    resp = SimpleNamespace(
        role="assistant",
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text=secret,
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=AsyncMock(),
        prompt_protection_service=service,
    )
    await handler.handle_memory_reflection(event, resp)
    assert resp.completion_text == ""


def _reflection_handler_for_scope(service, *, now_config=True):
    from core.handlers.reflection_handler import ReflectionHandler

    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "security.sanitize_llm_response": now_config,
        "security.double_check_enabled": False,
    }.get(key, default)
    return ReflectionHandler(
        context=MagicMock(),
        config_manager=cfg,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        enforce_limit_cb=AsyncMock(),
        prompt_protection_service=service,
    )


def _scoped_event(scope_id, *, required=True, role="assistant"):
    event = MagicMock()
    event.unified_msg_origin = ""
    event._memora_prompt_protection_scope = scope_id
    event._memora_prompt_protection_required = required
    response = SimpleNamespace(
        role=role,
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text="",
    )
    return event, response


@pytest.mark.asyncio
async def test_interleaved_scopes_sanitize_only_their_own_responses() -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    secret_a = "request A amber birch cedar dogwood elm"
    secret_b = "request B falcon granite harbor island juniper"
    service.wrap_prompt(secret_a, scope_id="scope-a")
    service.wrap_prompt(secret_b, scope_id="scope-b")
    handler = _reflection_handler_for_scope(service)
    event_a, response_a = _scoped_event("scope-a")
    event_b, response_b = _scoped_event("scope-b")
    response_a.completion_text = f"A {secret_a} keeps {secret_b}"
    response_b.completion_text = f"B {secret_b} keeps {secret_a}"

    await handler.handle_memory_reflection(event_a, response_a)
    await handler.handle_memory_reflection(event_b, response_b)

    assert secret_a not in response_a.completion_text
    assert secret_b in response_a.completion_text
    assert secret_b not in response_b.completion_text
    assert secret_a in response_b.completion_text
    assert service.scoped_scope_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["expired", "evicted"])
async def test_required_expired_or_evicted_scope_fails_closed(unavailable) -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    now = [10.0]
    service = PromptProtectionService(
        enable_double_check=False,
        clock=lambda: now[0],
        scope_ttl_seconds=5.0,
        max_scopes=1,
    )
    service.wrap_prompt("protected secret", scope_id="missing-scope")
    if unavailable == "expired":
        now[0] += 6.0
    else:
        service.wrap_prompt("new secret", scope_id="new-scope")
    handler = _reflection_handler_for_scope(service)
    event, response = _scoped_event("missing-scope")
    response.completion_text = "visible output"
    await handler.handle_memory_reflection(event, response)
    assert response.completion_text == ""


@pytest.mark.asyncio
async def test_nonassistant_response_discards_scope_without_sanitizing() -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    secret = "nonassistant secret alpha beta gamma delta epsilon"
    service.wrap_prompt(secret, scope_id="scope-tool")
    handler = _reflection_handler_for_scope(service)
    event, response = _scoped_event("scope-tool", role="tool")
    response.completion_text = secret
    await handler.handle_memory_reflection(event, response)
    assert response.completion_text == secret
    assert service.has_scope("scope-tool") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("first_role", ["assistant", "tool"])
async def test_reflection_clears_event_markers_before_event_reuse(first_role) -> None:
    from core.security.prompt_sanitizer import (
        PROMPT_PROTECTION_REQUIRED_ATTR,
        PROMPT_PROTECTION_REQUIRED_EXTRA_KEY,
        PROMPT_PROTECTION_SCOPE_ATTR,
        PROMPT_PROTECTION_SCOPE_EXTRA_KEY,
        PromptProtectionService,
    )

    service = PromptProtectionService(enable_double_check=False)
    secret = "reused event secret alpha beta gamma delta epsilon"
    service.wrap_prompt(secret, scope_id="scope-reused")
    handler = _reflection_handler_for_scope(service)
    extras = {
        PROMPT_PROTECTION_SCOPE_EXTRA_KEY: "scope-reused",
        PROMPT_PROTECTION_REQUIRED_EXTRA_KEY: True,
    }
    event, first = _scoped_event("scope-reused", role=first_role)
    event.get_extra.side_effect = lambda key: extras.get(key)
    event.set_extra.side_effect = lambda key, value: extras.__setitem__(key, value)
    first.completion_text = secret

    await handler.handle_memory_reflection(event, first)

    assert extras[PROMPT_PROTECTION_SCOPE_EXTRA_KEY] is None
    assert extras[PROMPT_PROTECTION_REQUIRED_EXTRA_KEY] is False
    assert getattr(event, PROMPT_PROTECTION_SCOPE_ATTR, None) is None
    assert getattr(event, PROMPT_PROTECTION_REQUIRED_ATTR, None) is None

    second = SimpleNamespace(
        role="assistant",
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text="ordinary second response",
    )
    await handler.handle_memory_reflection(event, second)
    assert second.completion_text == "ordinary second response"


@pytest.mark.asyncio
async def test_reflection_setter_error_still_clears_private_markers() -> None:
    from core.security.prompt_sanitizer import (
        PROMPT_PROTECTION_REQUIRED_ATTR,
        PROMPT_PROTECTION_SCOPE_ATTR,
        PromptProtectionService,
    )

    service = PromptProtectionService(enable_double_check=False)
    service.wrap_prompt("setter error secret", scope_id="scope-setter-error")
    handler = _reflection_handler_for_scope(service)
    event, response = _scoped_event("scope-setter-error", role="tool")
    event.set_extra.side_effect = RuntimeError("setter unavailable")
    await handler.handle_memory_reflection(event, response)
    assert getattr(event, PROMPT_PROTECTION_SCOPE_ATTR, None) is None
    assert getattr(event, PROMPT_PROTECTION_REQUIRED_ATTR, None) is None
    assert service.has_scope("scope-setter-error") is False


@pytest.mark.asyncio
async def test_no_injection_missing_scope_keys_does_not_clear_ordinary_response() -> (
    None
):
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    handler = _reflection_handler_for_scope(service)
    event = MagicMock()
    event.get_extra.return_value = None
    event.unified_msg_origin = ""
    response = SimpleNamespace(
        role="assistant",
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text="ordinary visible response",
    )
    await handler.handle_memory_reflection(event, response)
    assert response.completion_text == "ordinary visible response"


@pytest.mark.asyncio
async def test_scope_getter_error_without_fallback_fails_visible_response_closed() -> (
    None
):
    from core.security.prompt_sanitizer import PromptProtectionService

    handler = _reflection_handler_for_scope(
        PromptProtectionService(enable_double_check=False)
    )
    event = MagicMock()
    event.get_extra.side_effect = RuntimeError("getter unavailable")
    event.unified_msg_origin = ""
    response = SimpleNamespace(
        role="assistant",
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text="potentially unsafe response",
    )
    await handler.handle_memory_reflection(event, response)
    assert response.completion_text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [InjectionOutcome.EMPTY, InjectionOutcome.ERROR])
async def test_recall_empty_or_error_clears_event_scope(handler_case, outcome) -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    case = handler_case(
        config=strategy_config(),
        memories=high_confidence_memories(),
        prompt_protection_service=service,
    )
    case.handler._executor.execute = AsyncMock(
        return_value=InjectionExecutionResult(
            outcome=outcome,
            error_code="TEST" if outcome is InjectionOutcome.ERROR else None,
        )
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    assert getattr(case.event, "_memora_prompt_protection_scope", None) is None
    assert getattr(case.event, "_memora_prompt_protection_required", None) is None
    assert service.scoped_scope_count == 0


@pytest.mark.asyncio
async def test_recall_both_scope_channels_failure_skips_protected_executor(
    handler_case,
) -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    class EventWithoutStorage:
        __slots__ = ("unified_msg_origin",)

        def __init__(self):
            self.unified_msg_origin = "session-1"

        def set_extra(self, *_args):
            raise RuntimeError("setter unavailable")

        def get_extra(self, *_args):
            raise RuntimeError("getter unavailable")

        def get_message_type(self):
            return MessageType.PRIVATE_MESSAGE

        def get_sender_id(self):
            return "user-1"

    service = PromptProtectionService(enable_double_check=False)
    case = handler_case(
        config=strategy_config(),
        memories=high_confidence_memories(),
        prompt_protection_service=service,
    )
    case.event = EventWithoutStorage()
    case.handler._executor.execute = AsyncMock()
    snapshot = (
        case.request.prompt,
        list(case.request.contexts),
        list(case.request.extra_user_content_parts),
    )
    await case.handler.handle_memory_recall(case.event, case.request)
    case.handler._executor.execute.assert_not_awaited()
    assert (
        case.request.prompt,
        case.request.contexts,
        case.request.extra_user_content_parts,
    ) == snapshot
    assert (
        case.recorder.record.call_args.args[0].error_code == "PROTECTION_SCOPE_FAILED"
    )


@pytest.mark.asyncio
async def test_setter_exception_real_executor_never_registers_unscoped(
    handler_case,
) -> None:
    from core.security.prompt_sanitizer import PromptProtectionService

    service = PromptProtectionService(enable_double_check=False)
    case = handler_case(
        config=strategy_config(),
        memories=high_confidence_memories(),
        prompt_protection_service=service,
    )
    case.event.set_extra.side_effect = RuntimeError("setter unavailable")
    await case.handler.handle_memory_recall(case.event, case.request)
    scope_id = case.event._memora_prompt_protection_scope
    assert scope_id
    assert service.has_scope(scope_id)
    assert service.sanitizer._original_instructions == []
    assert case.recorder.record.call_args.args[0].outcome in {"injected", "fallback"}
