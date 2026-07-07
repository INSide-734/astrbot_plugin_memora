"""记忆召回处理器 — LLM 请求前检索并注入长期记忆"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ..base.config_manager import ConfigManager
from ..cleaners.injection_cleaner import InjectionCleaner
from ..extractors.message_content_extractor import MessageContentExtractor
from ..managers.conversation_manager import ConversationManager
from ..managers.memory_engine import MemoryEngine
from ..monitoring import monitored
from ..retrieval.query_rewriter import QueryRewriter
from ..utils import (
    OperationContext,
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
    get_persona_id,
)
from ..utils.injection_adapter import InjectionAdapter

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import ProviderRequest


class RecallHandler:
    """LLM 请求前记忆召回 + 注入"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        conversation_manager: ConversationManager,
        injection_adapter: InjectionAdapter,
        enforce_limit_cb: Callable,
        jargon_query_service: Any | None = None,
        expression_learner: Any | None = None,
        affection_manager: Any | None = None,
        relation_manager: Any | None = None,
        prompt_protection_service: Any | None = None,
        perf_tracker: Any | None = None,
    ) -> None:
        self._context = context
        self._config_manager = config_manager
        self._memory_engine = memory_engine
        self._conversation_manager = conversation_manager
        self._injection_adapter = injection_adapter
        self._enforce_limit_cb = enforce_limit_cb
        self._jargon_query_service = jargon_query_service
        self._expression_learner = expression_learner
        self._affection_manager = affection_manager
        self._relation_manager = relation_manager
        self._prompt_protection = prompt_protection_service
        self._perf_tracker = perf_tracker
        self._cleaner = InjectionCleaner()
        self._extractor = MessageContentExtractor()
        # R1：查询改写器（无 LLM 调用方时使用关键词回退，后续再注入 LLM 调用方）
        self._query_rewriter = QueryRewriter(
            enabled=config_manager.get("recall_engine.query_rewrite_enabled", True),
        )

    @monitored
    async def handle_memory_recall(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在 LLM 请求前查询并注入长期记忆。"""
        recall_started = time.perf_counter()
        injected_count = 0
        filtered_count = 0
        try:
            session_id = event.unified_msg_origin
            logger.debug(f"[召回流程] 获取到 unified_msg_origin: {session_id}")

            if session_id and (
                "Error:" in session_id or "error:" in session_id.lower()
            ):
                logger.warning(
                    f"[{session_id}] 检测到异常的会话 ID，这可能导致记忆功能异常。"
                )

            async with OperationContext("记忆召回", session_id):
                prompt_text = getattr(req, "prompt", "")
                extra_parts = getattr(req, "extra_user_content_parts", [])
                has_prompt_text = isinstance(prompt_text, str) and bool(
                    prompt_text.strip()
                )
                has_extra_parts = bool(extra_parts)

                if not has_prompt_text and not has_extra_parts:
                    logger.debug(f"[{session_id}] 请求中无可用用户内容，跳过记忆召回")
                    return

                if self._config_manager.get("recall_engine.auto_remove_injected", True):
                    removed = self._cleaner.remove_injected_memories_from_context(
                        req,
                        session_id,
                    )
                    removed += self._cleaner.remove_fake_tool_call_from_context(
                        req,
                        session_id,
                    )
                    if removed > 0:
                        logger.info(
                            f"[{session_id}] 已清理 {removed} 处历史记忆注入片段"
                        )

                actual_query = await self._extractor.get_event_message_str(event)

                request_query = (
                    prompt_text.strip() if isinstance(prompt_text, str) else ""
                )

                is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
                if not is_group and actual_query:
                    message_to_store = request_query
                    if not message_to_store:
                        message_to_store = (
                            await self._extractor.extract_message_content(event, req)
                        )
                    if not message_to_store:
                        message_to_store = actual_query.strip()
                    await self._conversation_manager.add_message_from_event(
                        event=event,
                        role="user",
                        content=message_to_store,
                    )
                    await self._enforce_limit_cb(session_id)

                top_k = self._config_manager.get("recall_engine.top_k", 5)
                if top_k <= 0:
                    logger.info(
                        f"[{session_id}] top_k={top_k} <= 0，跳过记忆检索和注入"
                    )
                    return

                if not actual_query:
                    # 空消息（如纯 @mention）时回退到历史上下文
                    fallback_query = await self._build_fallback_query(session_id)
                    if fallback_query:
                        logger.info(
                            f"[{session_id}] 原始消息为空，使用历史上下文作为回退查询"
                        )
                        actual_query = fallback_query
                    else:
                        logger.warning(f"[{session_id}] 原始用户消息为空，跳过记忆召回")
                        return

                filtering_config = self._config_manager.filtering_settings
                use_persona_filtering = filtering_config.get(
                    "use_persona_filtering", True
                )
                use_session_filtering = filtering_config.get(
                    "use_session_filtering", True
                )

                persona_id = await get_persona_id(self._context, event)

                recall_session_id = session_id if use_session_filtering else None
                recall_persona_id = persona_id if use_persona_filtering else None

                query_for_search = actual_query

                if self._config_manager.get(
                    "recall_engine.inject_with_recent_context", False
                ):
                    try:
                        recent_messages = await self._conversation_manager.get_context(
                            session_id,
                            max_messages=5,
                        )
                        if recent_messages and len(recent_messages) > 1:
                            context_parts = []
                            for msg in reversed(recent_messages[1:]):
                                content = msg.get("content", "")
                                if content and content.strip():
                                    context_parts.append(content.strip())
                            if context_parts:
                                expanded = " | ".join(context_parts)
                                query_for_search = expanded + " " + actual_query
                                logger.info(
                                    f"[{session_id}] 上下文扩展查询: "
                                    f"{len(context_parts)}条历史消息 + 当前消息"
                                )
                    except Exception as e:
                        logger.warning(f"[{session_id}] 获取上下文扩展失败: {e}")

                # R1：语义查询改写 —— 展开模糊指代
                query_intent = await self._query_rewriter.rewrite(
                    query=actual_query,
                    recent_context=query_for_search,
                )
                # 使用改写后的第一条查询（或原始查询）作为主检索词
                rewritten_queries = query_intent.rewritten_queries
                primary_query = (
                    rewritten_queries[0] if rewritten_queries else query_for_search
                )
                memory_type_filter = query_intent.memory_types or None

                logger.info(
                    f"[{session_id}] 开始记忆召回，查询='{primary_query[:80]}...'"
                    f", intent={query_intent.intent}, entities={query_intent.extracted_entities}"
                )

                chat_type = "group" if is_group else "private"
                user_id = self._get_event_sender_id(event)
                recalled_memories = await self._memory_engine.search_memories(
                    query=primary_query,
                    k=self._config_manager.get("recall_engine.top_k", 5),
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                    query_intent=query_intent,
                    memory_types=memory_type_filter,
                    user_id=user_id,
                )

                # 自发回忆 — 6% 概率主动浮现低阈值关联记忆
                spontaneous = await self._maybe_spontaneous_recall(
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                )
                if spontaneous:
                    recalled_memories = list(recalled_memories or []) + spontaneous
                    logger.info(
                        f"[{session_id}] 自发回忆触发，额外注入 {len(spontaneous)} 条记忆"
                    )

                # 前瞻记忆 — 扫描 24h 内到期的 PLANNED 原子主动注入
                prospective = await self._maybe_prospective_recall(
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    chat_type=chat_type,
                )
                if prospective:
                    recalled_memories = list(recalled_memories or []) + prospective
                    logger.info(
                        f"[{session_id}] 前瞻记忆触发，注入 {len(prospective)} 条待办"
                    )

                recalled_memories = self._finalize_recall_candidates(
                    list(recalled_memories or []),
                    top_k=top_k,
                )
                injected_count = len(recalled_memories)

                if recalled_memories:
                    logger.info(
                        f"[{session_id}] 检索到 {len(recalled_memories)} 条记忆"
                    )

                    memory_list = [
                        {
                            "id": getattr(mem, "doc_id", None),
                            "content": mem.content,
                            "score": mem.final_score,
                            "metadata": mem.metadata,
                            "timestamp": mem.metadata.get("create_time"),
                        }
                        for mem in recalled_memories
                    ]

                    for i, mem in enumerate(recalled_memories, 1):
                        logger.debug(
                            f"[{session_id}] 记忆 #{i}: 得分={mem.final_score:.3f}, "
                            f"重要性={mem.metadata.get('importance', 0.5):.2f}, "
                            f"内容={mem.content[:100]}..."
                        )

                    configured_method = self._config_manager.get(
                        "recall_engine.injection_method", "extra_user_content"
                    )
                    provider = None
                    if configured_method == "fake_tool_call":
                        provider = self._context.get_using_provider(session_id)
                    injection_method, fallback_reason = self._injection_adapter.resolve(
                        provider, configured_method
                    )
                    if fallback_reason:
                        logger.warning(
                            f"[{session_id}] 注入模式从 {configured_method} 降级为 "
                            f"{injection_method}: {fallback_reason}"
                        )

                    memory_str = format_memories_for_injection(memory_list)
                    cognitive_context = await self._build_cognitive_context(
                        text=actual_query,
                        group_id=session_id or "default",
                        persona_id=persona_id or "default",
                    )
                    if cognitive_context:
                        memory_str = memory_str + "\n\n" + cognitive_context

                    # 主动提醒：注入即将触发的 PLANNED 原子
                    proactive = getattr(self._memory_engine, "_pending_proactive", None)
                    if proactive and self._prospective_recall_enabled():
                        injected_doc_ids = {
                            getattr(mem, "doc_id", None)
                            for mem in recalled_memories
                            if getattr(mem, "doc_id", None) is not None
                        }
                        lines = ["[Upcoming Plans]"]
                        for atom in proactive[:5]:
                            parent_id = getattr(atom, "parent_memory_id", None)
                            if parent_id in injected_doc_ids:
                                continue
                            content = getattr(atom, "content", str(atom))
                            event_time = getattr(atom, "event_time", None)
                            ts = f" (at {event_time})" if event_time else ""
                            lines.append(f"- {content}{ts}")
                        if len(lines) > 1:
                            memory_str = "\n".join(lines) + "\n\n" + memory_str
                            logger.info(f"[{session_id}] 前瞻: {len(lines) - 1} 条")

                    if injection_method == "user_message_before":
                        memory_str = self._wrap_injected_context(
                            memory_str,
                            session_id=session_id,
                        )
                        req.prompt = memory_str + "\n\n" + (req.prompt or "")
                        logger.info(
                            f"[{session_id}] 成功向用户消息前注入 "
                            f"{len(recalled_memories)} 条记忆"
                        )
                    elif injection_method == "user_message_after":
                        memory_str = self._wrap_injected_context(
                            memory_str,
                            session_id=session_id,
                        )
                        req.prompt = (req.prompt or "") + "\n\n" + memory_str
                        logger.info(
                            f"[{session_id}] 成功向用户消息后注入 "
                            f"{len(recalled_memories)} 条记忆"
                        )
                    elif injection_method == "fake_tool_call":
                        fake_messages = format_memories_for_fake_tool_call(
                            memory_list,
                            query=actual_query,
                            k=self._config_manager.get("recall_engine.top_k", 5),
                            session_filtered=use_session_filtering,
                            persona_filtered=use_persona_filtering,
                        )
                        if fake_messages:
                            fake_messages = self._wrap_fake_tool_messages(
                                fake_messages,
                                session_id=session_id,
                            )
                            req.contexts.extend(fake_messages)
                            logger.info(
                                f"[{session_id}] 成功以伪造工具调用方式注入 "
                                f"{len(recalled_memories)} 条记忆"
                            )
                    elif injection_method == "fake_tool_call_deepseek_v4":
                        fake_replay = format_memories_for_fake_tool_call_deepseek_v4(
                            memory_list,
                            query=actual_query,
                            k=self._config_manager.get("recall_engine.top_k", 5),
                            session_filtered=use_session_filtering,
                            persona_filtered=use_persona_filtering,
                        )
                        if fake_replay:
                            fake_replay = self._wrap_injected_context(
                                fake_replay,
                                session_id=session_id,
                            )
                            req.prompt = fake_replay + "\n\n" + (req.prompt or "")
                            logger.info(
                                f"[{session_id}] 成功以 DeepSeek V4 兼容伪工具转录方式注入 "
                                f"{len(recalled_memories)} 条记忆"
                            )
                    else:
                        from astrbot.core.agent.message import TextPart

                        memory_str = self._wrap_injected_context(
                            memory_str,
                            session_id=session_id,
                        )
                        req.extra_user_content_parts.append(
                            TextPart(text=memory_str).mark_as_temp()
                        )
                        logger.info(
                            f"[{session_id}] 成功向用户消息末尾注入 "
                            f"{len(recalled_memories)} 条记忆"
                        )
                else:
                    logger.info(f"[{session_id}] 未找到相关记忆")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)
        finally:
            self._record_recall_observability(
                total_ms=(time.perf_counter() - recall_started) * 1000.0,
                injected_count=injected_count,
                filtered_count=filtered_count,
            )

    def _record_recall_observability(
        self,
        *,
        total_ms: float,
        injected_count: int,
        filtered_count: int,
    ) -> None:
        try:
            from ..monitoring.metrics import RECALL_DURATION, RECALL_REQUESTS

            RECALL_REQUESTS.inc()
            RECALL_DURATION.labels(stage="total").observe(max(0.0, total_ms) / 1000.0)
        except Exception:
            logger.debug("[召回流程] 指标记录失败", exc_info=True)

        if self._perf_tracker is None:
            return
        try:
            self._perf_tracker.record({
                "total_ms": max(0.0, total_ms),
                "bm25_ms": 0.0,
                "vector_ms": 0.0,
                "graph_ms": 0.0,
                "rerank_ms": 0.0,
                "injected_count": float(injected_count),
                "filtered_count": float(filtered_count),
            })
        except Exception:
            logger.debug("[召回流程] 性能样本记录失败", exc_info=True)

    @staticmethod
    def _get_event_sender_id(event: AstrMessageEvent) -> str | None:
        getter = getattr(event, "get_sender_id", None)
        if not callable(getter):
            return None
        try:
            sender_id = getter()
        except Exception:
            return None
        if sender_id is None:
            return None
        sender_id = str(sender_id).strip()
        return sender_id or None

    @staticmethod
    def _finalize_recall_candidates(
        candidates: list[Any],
        top_k: int,
    ) -> list[Any]:
        """De-duplicate multi-source recall candidates and enforce inject budget."""
        if top_k <= 0 or not candidates:
            return []

        source_priority = {
            "prospective": 3,
            "main": 2,
            "spontaneous": 1,
        }

        def candidate_key(item: Any) -> tuple[str, Any]:
            doc_id = getattr(item, "doc_id", None)
            if doc_id is not None:
                return ("doc", doc_id)
            metadata = getattr(item, "metadata", None) or {}
            source = metadata.get("recall_source", "main")
            content = str(getattr(item, "content", "") or "")
            digest = hashlib.sha256(f"{source}\0{content}".encode("utf-8")).hexdigest()
            return ("content", digest)

        def rank_tuple(item: Any) -> tuple[int, float]:
            metadata = getattr(item, "metadata", None) or {}
            source = metadata.get("recall_source", "main")
            return (
                source_priority.get(str(source), source_priority["main"]),
                float(getattr(item, "final_score", 0.0) or 0.0),
            )

        deduped: dict[tuple[str, Any], Any] = {}
        for item in candidates:
            key = candidate_key(item)
            existing = deduped.get(key)
            if existing is None or rank_tuple(item) > rank_tuple(existing):
                deduped[key] = item

        finalized = list(deduped.values())
        finalized.sort(key=rank_tuple, reverse=True)
        return finalized[:top_k]

    def _wrap_injected_context(self, memory_str: str, session_id: str | None) -> str:
        """为注入的记忆上下文套用提示词保护包装。"""
        if not memory_str:
            return memory_str
        if not self._config_manager.get("security.prompt_protection_enabled", True):
            return memory_str
        if self._prompt_protection is None:
            return memory_str
        try:
            return self._prompt_protection.wrap_prompt(
                memory_str,
                label="memory_context",
                register_for_filter=True,
            )
        except Exception:
            logger.warning(
                f"[{session_id}] 提示词保护服务包装注入内容失败，使用原始内容",
                exc_info=True,
            )
            return memory_str

    def _wrap_fake_tool_messages(
        self,
        fake_messages: list[dict],
        session_id: str | None,
    ) -> list[dict]:
        """为伪造工具调用结果内容套用提示词保护。"""
        if not fake_messages:
            return fake_messages
        if not self._config_manager.get("security.prompt_protection_enabled", True):
            return fake_messages
        if self._prompt_protection is None:
            return fake_messages

        protected: list[dict] = []
        for message in fake_messages:
            if not isinstance(message, dict):
                protected.append(message)
                continue
            copied = dict(message)
            if copied.get("role") == "tool" and isinstance(copied.get("content"), str):
                copied["content"] = self._wrap_injected_context(
                    copied["content"],
                    session_id=session_id,
                )
            protected.append(copied)
        return protected

    async def _build_cognitive_context(
        self,
        text: str,
        group_id: str,
        persona_id: str,
    ) -> str:
        """构建来自 v1.0+ 认知模块的可选只读上下文。"""
        parts: list[str] = []
        try:
            if self._jargon_query_service is not None:
                explanation = await self._jargon_query_service.check_and_explain(
                    text, group_id
                )
                if explanation:
                    parts.append(explanation)
        except Exception:
            logger.debug("[召回流程] 黑话解释构建失败", exc_info=True)

        try:
            if self._expression_learner is not None:
                patterns = await self._expression_learner.format_patterns_for_prompt(
                    group_id=group_id,
                    persona_id=persona_id,
                    limit=3,
                )
                if patterns:
                    parts.append(patterns)
        except Exception:
            logger.debug("[召回流程] 表达模式格式化失败", exc_info=True)

        try:
            if self._affection_manager is not None:
                status = await self._affection_manager.get_group_affection_status(
                    group_id
                )
                mood = status.get("current_mood") if isinstance(status, dict) else None
                if mood:
                    parts.append(
                        "[互动状态]\n"
                        f"- 当前情绪: {mood.get('type', 'calm')} "
                        f"({mood.get('description', '')})"
                    )
        except Exception:
            logger.debug("[召回流程] 好感度上下文构建失败", exc_info=True)

        return "\n".join(parts)

    async def _build_fallback_query(self, session_id: str) -> str | None:
        """从最近历史消息构建回退查询，用于空消息场景（如纯 @mention）。

        获取最近 5 条消息，取最近 3 条非空内容拼接为查询字符串。
        """
        try:
            recent = await self._conversation_manager.get_context(
                session_id,
                max_messages=5,
            )
            if not recent or len(recent) <= 1:
                return None
            parts: list[str] = []
            for msg in reversed(recent[1:]):
                content = msg.get("content", "")
                if content and content.strip():
                    parts.append(content.strip())
            return " ".join(parts[:3]) if parts else None
        except Exception:
            logger.debug(
                f"[{session_id}] 构建回退查询失败",
                exc_info=True,
            )
            return None

    @monitored
    async def _maybe_spontaneous_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
    ) -> list[Any]:
        """自发回忆 — 以低概率主动浮现非查询驱动的关联记忆。

        约 6% 的请求会触发，使用低阈值宽泛检索，模拟人类"突然想起来"的体验。
        """
        enabled = self._config_manager.get(
            "recall_engine.spontaneous_recall_enabled", True
        )
        if not enabled:
            return []

        probability = float(
            self._config_manager.get(
                "recall_engine.spontaneous_recall_probability", 0.06
            )
        )
        if random.random() >= probability:
            return []

        try:
            # 使用宽泛的通用查询词进行低阈值检索
            seed_queries = [
                "重要的事情",
                "开心的回忆",
                "最近发生的事",
                "之前的对话",
                "难忘的经历",
            ]
            seed_query = random.choice(seed_queries)
            spontaneous_k = int(
                self._config_manager.get("recall_engine.spontaneous_recall_k", 2)
            )

            results = await self._memory_engine.search_memories(
                query=seed_query,
                k=spontaneous_k,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
            )
            # 标记为自发回忆来源
            for r in results:
                meta = r.metadata or {}
                meta["recall_source"] = "spontaneous"
                r.metadata = meta

            if results:
                logger.debug(
                    f"[{session_id}] 自发回忆触发 (p={probability:.0%}): "
                    f"seed='{seed_query}', {len(results)} 条记忆"
                )
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("自发回忆检索失败", exc_info=True)
            return []

    def _prospective_recall_enabled(self) -> bool:
        """读取标准前瞻召回开关，并兼容旧版回退配置。"""
        enabled = self._config_manager.get(
            "recall_engine.prospective_recall_enabled",
            None,
        )
        if enabled is None:
            enabled = self._config_manager.get("prospective.enabled", True)
        return bool(enabled)

    @monitored
    async def _maybe_prospective_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
    ) -> list[Any]:
        """前瞻记忆 — 扫描 24h 内到期的 PLANNED 原子并注入上下文。

        认知原理：人类会自动想起"今天要做 X"，PLANNED 原子承载此功能。
        每次 LLM 请求前扫描，将即将到期的计划注入当前上下文。
        """
        if not self._prospective_recall_enabled():
            return []

        try:
            lookahead_hours = float(
                self._config_manager.get(
                    "recall_engine.prospective_lookahead_hours", 24.0
                )
            )
            lookahead_sec = lookahead_hours * 3600.0
            prospective_k = int(
                self._config_manager.get("recall_engine.prospective_recall_k", 3)
            )

            # 使用 memory_engine 的 atom_store 查询
            engine = self._memory_engine
            if not hasattr(engine, "atom_store") or engine.atom_store is None:
                return []

            planned_atoms = await engine.atom_store.query_upcoming_planned(
                lookahead_sec=lookahead_sec,
                session_id=session_id,
                persona_id=persona_id,
                limit=prospective_k,
            )

            if not planned_atoms:
                return []

            # 将 PLANNED 原子转为 HybridResult 格式
            from ..retrieval.rrf_fusion import HybridResult

            results: list[HybridResult] = []
            for atom in planned_atoms:
                meta = atom.metadata or {}
                meta["recall_source"] = "prospective"
                meta["atom_type"] = "planned"
                meta["event_time"] = atom.event_time
                results.append(
                    HybridResult(
                        doc_id=atom.parent_memory_id,
                        final_score=0.9,  # 高优先级
                        content=f"[待办] {atom.content}",
                        metadata=meta,
                    )
                )

            if results:
                logger.info(
                    f"[{session_id}] 前瞻记忆: {len(results)} 条 PLANNED 原子在 "
                    f"{lookahead_hours:.0f}h 内到期"
                )
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(f"[{session_id}] 前瞻记忆扫描失败", exc_info=True)
            return []
