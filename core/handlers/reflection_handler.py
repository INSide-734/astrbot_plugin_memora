"""记忆反思处理器 —— 在 LLM 响应后执行反思并后台存储记忆。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ..base.config_manager import ConfigManager
from ..managers.conversation_manager import ConversationManager
from ..managers.memory_engine import MemoryEngine
from ..processors.memory_processor import MemoryProcessor
from ..utils import OperationContext, get_persona_id
from .topic_batch_preparer import TopicBatchPreparer
from ..security.prompt_sanitizer import PROMPT_PROTECTION_SCOPE_EXTRA_KEY

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse


class ReflectionHandler:
    """在 LLM 响应后执行反思与后台记忆存储。"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        memory_processor: MemoryProcessor,
        conversation_manager: ConversationManager,
        enforce_limit_cb: Callable,
        affection_manager: Any | None = None,
        expression_learner: Any | None = None,
        jargon_miner: Any | None = None,
        relation_manager: Any | None = None,
        prompt_protection_service: Any | None = None,
        write_guard_cb: Any | None = None,
    ) -> None:
        self._context = context
        self._config_manager = config_manager
        self._memory_engine = memory_engine
        self._memory_processor = memory_processor
        self._conversation_manager = conversation_manager
        self._enforce_limit_cb = enforce_limit_cb
        self._affection_manager = affection_manager
        self._expression_learner = expression_learner
        self._jargon_miner = jargon_miner
        self._relation_manager = relation_manager
        self._prompt_protection = prompt_protection_service
        self._write_guard_cb = write_guard_cb

        self._storage_tasks: set[asyncio.Task] = set()
        self._storage_sessions_inflight: set[str] = set()
        self._storage_state_lock = asyncio.Lock()
        self._shutting_down = False

        self._batch_preparer = TopicBatchPreparer(
            config_manager=config_manager,
            memory_engine=memory_engine,
            memory_processor=memory_processor,
        )

    async def handle_memory_reflection(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
    ) -> None:
        """在 LLM 响应后检查是否需要反思与记忆存储。"""
        logger.debug(
            f"[反思处理] 进入 handle_memory_reflection，resp.role={resp.role}"
        )

        if resp.role != "assistant":
            return

        scope_id, scope_lookup_failed = self._get_prompt_protection_scope(event)
        if scope_lookup_failed:
            resp.completion_text = ""
            return
        session_id = getattr(event, "unified_msg_origin", "") or ""
        response_text = str(getattr(resp, "completion_text", "") or "")
        response_text = self._sanitize_response_text(
            response_text,
            session_id,
            scope_id=scope_id,
        )
        resp.completion_text = response_text

        if resp.tools_call_name:
            logger.debug(
                f"[反思处理] 检测到工具调用响应（tools={resp.tools_call_name}），跳过记录"
            )
            return

        if resp.tools_call_extra_content:
            logger.debug(
                "[反思处理] 检测到工具循环总结响应（tools_call_extra_content 非空），跳过记录"
            )
            return

        try:
            logger.debug(f"[反思处理] 获取到 unified_msg_origin: {session_id}")

            if not session_id:
                logger.warning("[反思处理] 会话 ID 为空，跳过反思")
                return

            if self._writes_blocked():
                logger.warning(f"[{session_id}] 备份恢复待应用，跳过 LLM 回复写入")
                return

            if "Error:" in session_id or "error:" in session_id.lower():
                logger.warning(
                    f"[{session_id}] 检测到异常的会话 ID，这可能导致记忆总结异常。"
                )

            if not response_text or not response_text.strip():
                logger.warning(
                    f"[{session_id}] 模型回复经安全清洗后为空，跳过记录"
                )
                return
            error_indicators = [
                "api error",
                "request failed",
                "rate limit",
                "timeout",
                "connection error",
                "服务暂时不可用",
                "请求失败",
                "接口错误",
            ]
            response_lower = response_text.lower()
            if any(indicator in response_lower for indicator in error_indicators):
                logger.debug(
                    f"[{session_id}] 检测到错误响应，跳过记录: {response_text[:50]}..."
                )
                return

            await self._conversation_manager.add_message_from_event(
                event=event,
                role="assistant",
                content=response_text,
            )
            await self._feed_cognitive_components(event, response_text)
            logger.debug(f"[反思处理] [{session_id}] 已添加助手响应消息")

            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
            if not is_group:
                await self._enforce_limit_cb(session_id)

            session_info = await self._conversation_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(
                    f"[反思处理] [{session_id}] session_info 为空，跳过反思"
                )
                return

            actual_message_count = (
                await self._conversation_manager.store.get_message_count(session_id)
            )

            if session_info.message_count != actual_message_count:
                logger.warning(
                    f"[反思处理] [{session_id}] 数据不一致！"
                    f"sessions表记录={session_info.message_count}, "
                    f"实际消息数={actual_message_count}"
                )

            total_messages = actual_message_count

            trigger_rounds = self._config_manager.get(
                "reflection_engine.summary_trigger_rounds", 10
            )

            last_summarized_index = (
                await self._conversation_manager.get_session_metadata(
                    session_id,
                    "last_summarized_index",
                    0,
                )
            )

            if last_summarized_index > total_messages:
                logger.warning(
                    f"[反思处理] [{session_id}] "
                    f"last_summarized_index({last_summarized_index}) "
                    f"> 实际消息数({total_messages})，调整为当前消息总数"
                )
                last_summarized_index = total_messages
                await self._conversation_manager.update_session_metadata(
                    session_id,
                    "last_summarized_index",
                    total_messages,
                )

            unsummarized_messages = total_messages - last_summarized_index
            unsummarized_rounds = unsummarized_messages // 2

            pending_summary = await self._conversation_manager.get_session_metadata(
                session_id,
                "pending_summary",
                None,
            )

            logger.info(
                f"[反思处理] [{session_id}] 总消息数：{total_messages}，"
                f"上次总结位置：{last_summarized_index}，"
                f"未总结轮数：{unsummarized_rounds}，"
                f"触发阈值：{trigger_rounds} 轮，"
                f"存在待处理失败总结：{pending_summary is not None}"
            )

            if unsummarized_rounds >= trigger_rounds:
                logger.info(
                    f"[{session_id}] 未总结轮数达到 {unsummarized_rounds} 轮，启动记忆反思任务"
                )

                start_index = last_summarized_index
                end_index = total_messages
                retry_count = 0

                if pending_summary:
                    pending_start = pending_summary.get("start_index", start_index)
                    retry_count = pending_summary.get("retry_count", 0)

                    if retry_count >= 3:
                        logger.warning(
                            f"[{session_id}] 待处理总结已连续失败 {retry_count} 次，放弃该范围 "
                            f"[{pending_start}:{pending_summary.get('end_index', end_index)}]"
                        )
                        await self._conversation_manager.update_session_metadata(
                            session_id,
                            "pending_summary",
                            None,
                        )
                        await self._conversation_manager.update_session_metadata(
                            session_id,
                            "last_summarized_index",
                            end_index,
                        )
                        return

                    start_index = pending_start
                    logger.info(
                        f"[{session_id}] 合并待处理失败总结，新范围 [{start_index}:{end_index}]，"
                        f"重试次数：{retry_count + 1}/3"
                    )

                if end_index - start_index < 2:
                    logger.debug(f"[{session_id}] 消息数不足一轮对话，跳过总结")
                    return

                messages_to_summarize = end_index - start_index
                rounds_to_summarize = messages_to_summarize // 2

                logger.info(
                    f"[{session_id}] 滑动窗口总结："
                    f"消息范围 [{start_index}:{end_index}]/{total_messages}，"
                    f"本次总结 {rounds_to_summarize} 轮"
                )

                history_messages = await self._conversation_manager.get_messages_range(
                    session_id=session_id,
                    start_index=start_index,
                    end_index=end_index,
                )

                logger.info(
                    f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结"
                )

                persona_id = await get_persona_id(self._context, event)

                if not self._shutting_down:
                    if not await self.try_begin_summary_window(session_id):
                        logger.info(
                            f"[{session_id}] 已有记忆总结任务在执行，跳过本次触发"
                        )
                        return

                    try:
                        task = asyncio.create_task(
                            self._storage_task(
                                session_id,
                                history_messages,
                                persona_id,
                                start_index,
                                end_index,
                                retry_count,
                            )
                        )
                    except Exception:
                        self.finish_summary_window(session_id)
                        raise

                    self._storage_tasks.add(task)
                    task.add_done_callback(
                        lambda t, sid=session_id: self._on_storage_task_done(t, sid)
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理 on_llm_response 钩子时发生错误：{e}", exc_info=True)

    def _sanitize_response_text(
        self,
        response_text: str,
        session_id: str,
        *,
        scope_id: str | None = None,
    ) -> str:
        """Sanitize the user-visible response and consume its request scope."""
        try:
            if not self._config_manager.get("security.sanitize_llm_response", True):
                self._discard_prompt_protection_scope(scope_id)
                return response_text
            if self._prompt_protection is None:
                return response_text
            sanitized, report = self._prompt_protection.sanitize_response(
                response_text,
                enable_validation=self._config_manager.get(
                    "security.double_check_enabled",
                    True,
                ),
                scope_id=scope_id,
                consume_scope=True,
            )
            leaks = report.get("leaks_removed") or []
            validation_passed = report.get("validation_passed", True)
            if leaks or not validation_passed:
                logger.warning(
                    f"[{session_id}] LLM 回复触发安全清洗："
                    f"移除项数量={len(leaks)}, 校验通过={validation_passed}"
                )
            return sanitized if validation_passed else ""
        except asyncio.CancelledError:
            self._discard_prompt_protection_scope(scope_id)
            raise
        except Exception:
            self._discard_prompt_protection_scope(scope_id)
            logger.warning(
                f"[{session_id}] LLM 回复安全清洗失败，已阻止输出",
                exc_info=True,
            )
            return ""

    def _discard_prompt_protection_scope(self, scope_id: str | None) -> None:
        if self._prompt_protection is None or not scope_id:
            return
        discard = getattr(self._prompt_protection, "discard_scope", None)
        if callable(discard):
            try:
                discard(scope_id)
            except Exception:
                logger.warning("[反思处理] 请求安全关联清理失败", exc_info=True)

    @staticmethod
    def _get_prompt_protection_scope(
        event: AstrMessageEvent,
    ) -> tuple[str | None, bool]:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return None, False
        try:
            scope_id = getter(PROMPT_PROTECTION_SCOPE_EXTRA_KEY)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[反思处理] 请求安全关联读取失败，已阻止输出", exc_info=True)
            return None, True
        return (scope_id, False) if isinstance(scope_id, str) and scope_id else (None, False)

    def _writes_blocked(self) -> bool:
        if self._write_guard_cb is None:
            return False
        try:
            return bool(self._write_guard_cb())
        except Exception:
            logger.error("[反思处理] 写入维护状态检查失败", exc_info=True)
            return True

    async def _feed_cognitive_components(
        self,
        event: AstrMessageEvent,
        response_text: str,
    ) -> None:
        """尽力将助手回复投喂给可选认知模块。"""
        session_id = event.unified_msg_origin or "default"
        sender_id = event.get_sender_id()
        persona_id = await get_persona_id(self._context, event)
        try:
            if self._expression_learner is not None:
                self._expression_learner.buffer_message(
                    group_id=session_id,
                    sender_id=getattr(self._expression_learner, "bot_id", "bot"),
                    content=response_text,
                )
                await self._expression_learner.maybe_learn(
                    session_id,
                    persona_id=persona_id or "default",
                    user_id=None,
                )
        except Exception:
            logger.debug("[认知模块] 助手回复投喂到表达模式学习器失败", exc_info=True)

        try:
            if self._affection_manager is not None:
                user_text = await self._latest_user_text(session_id)
                await self._affection_manager.process_interaction(
                    user_id=sender_id,
                    group_id=session_id,
                    message=user_text,
                    bot_response=response_text,
                )
        except Exception:
            logger.debug("[认知模块] 好感度更新失败", exc_info=True)

        try:
            if self._jargon_miner is not None and event.get_message_type() == MessageType.GROUP_MESSAGE:
                await self._jargon_miner.run_once(session_id, limit=2)
        except Exception:
            logger.debug("[认知模块] 基于助手回复触发黑话挖掘失败", exc_info=True)

    async def _latest_user_text(self, session_id: str) -> str:
        try:
            recent = await self._conversation_manager.get_context(
                session_id,
                max_messages=4,
            )
            for msg in reversed(recent or []):
                if msg.get("role") == "user" and msg.get("content"):
                    return str(msg["content"])
        except Exception:
            logger.debug("[认知模块] 查询最近一条用户消息失败", exc_info=True)
        return ""

    def _on_storage_task_done(self, task: asyncio.Task, session_id: str) -> None:
        """存储任务完成回调：回收任务状态并记录异常"""
        self._storage_tasks.discard(task)
        self.finish_summary_window(session_id)

        if task.cancelled():
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if exc:
            logger.error(f"[{session_id}] 记忆存储任务异常退出: {exc}")

    async def try_begin_summary_window(self, session_id: str) -> bool:
        """为后台或手动提交预留会话总结窗口。"""
        if self._shutting_down:
            return False
        async with self._storage_state_lock:
            if session_id in self._storage_sessions_inflight:
                return False
            self._storage_sessions_inflight.add(session_id)
            return True

    def finish_summary_window(self, session_id: str) -> None:
        """释放会话总结窗口占用。"""
        self._storage_sessions_inflight.discard(session_id)

    async def _prepare_message_batches(
        self, history_messages: list, is_group_chat: bool
    ) -> list[list]:
        """通过 ``TopicBatchPreparer`` 准备消息批次。"""
        return await self._batch_preparer.prepare_batches(
            history_messages, is_group_chat
        )

    async def _storage_task(
        self,
        session_id: str,
        history_messages: list,
        persona_id: str | None,
        start_index: int,
        end_index: int,
        retry_count: int = 0,
    ) -> None:
        """后台存储任务"""
        async with OperationContext("记忆存储", session_id):
            try:
                current_summarized = (
                    await self._conversation_manager.get_session_metadata(
                        session_id,
                        "last_summarized_index",
                        0,
                    )
                )
                try:
                    summarized_index = int(current_summarized)
                except (TypeError, ValueError):
                    summarized_index = 0
                pending_summary = (
                    await self._conversation_manager.get_session_metadata(
                        session_id,
                        "pending_summary",
                        None,
                    )
                    if self._conversation_manager
                    else None
                )
                completed_idempotency_keys: set[str] = set()
                if isinstance(pending_summary, dict):
                    completed_idempotency_keys = {
                        str(item)
                        for item in (
                            pending_summary.get("completed_idempotency_keys") or []
                        )
                    }

                if summarized_index >= end_index:
                    logger.info(
                        f"[{session_id}] 检测到过期总结任务，跳过："
                        f"current={summarized_index}, target_end={end_index}"
                    )
                    return

                is_group_chat = bool(
                    history_messages[0].group_id if history_messages else False
                )
                if not is_group_chat and "GroupMessage" in session_id:
                    is_group_chat = True

                logger.info(
                    f"[{session_id}] 开始处理记忆，类型={'群聊' if is_group_chat else '私聊'}, "
                    f"范围=[{start_index}:{end_index}], 重试次数={retry_count}, "
                    f"当前人格={persona_id or '未设置'}"
                )

                if not self._memory_processor:
                    logger.error(f"[{session_id}] 记忆处理器未初始化，记录待重试")
                    await self._record_pending_summary(
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                    )
                    return

                try:
                    # 准备消息批次（A/B 策略单批次，C/D 策略多批次）
                    batches = await self._prepare_message_batches(
                        history_messages, is_group_chat
                    )
                    logger.info(
                        f"[{session_id}] 调用记忆处理器，"
                        f"{len(history_messages)} 条消息 → {len(batches)} 个批次"
                    )

                    all_memories: list[dict[str, Any]] = []
                    batch_processing_failed = False
                    if len(batches) == 1:
                        batch_memories = (
                            await self._memory_processor.process_conversation(
                                messages=batches[0],
                                is_group_chat=is_group_chat,
                                persona_id=persona_id,
                            )
                        )
                        all_memories.extend(batch_memories)
                    else:
                        # 多批次（策略 C/D 场景）并行调用 LLM
                        async def _process_batch(batch):
                            return await self._memory_processor.process_conversation(
                                messages=batch,
                                is_group_chat=is_group_chat,
                                persona_id=persona_id,
                            )

                        batch_results = await asyncio.gather(
                            *[_process_batch(b) for b in batches],
                            return_exceptions=True,
                        )
                        for i, result in enumerate(batch_results):
                            if isinstance(result, BaseException):
                                batch_processing_failed = True
                                logger.error(
                                    f"[{session_id}] 批次 {i + 1}/{len(batches)} "
                                    f"LLM 处理失败：{result}"
                                )
                            else:
                                all_memories.extend(result)

                    if batch_processing_failed:
                        await self._record_pending_summary(
                            session_id,
                            start_index,
                            end_index,
                            retry_count,
                            failed_stage="llm_batch",
                        )
                        return

                    memories = all_memories
                    for memory_index, mem in enumerate(memories):
                        metadata = mem.setdefault("metadata", {})
                        key = self._memory_idempotency_key(
                            session_id=session_id,
                            start_index=start_index,
                            end_index=end_index,
                            batch_index=int(metadata.get("batch_index", 0) or 0),
                            memory_index=memory_index,
                            content=str(mem.get("content", "") or ""),
                        )
                        metadata["idempotency_key"] = key
                    logger.info(
                        f"[{session_id}] LLM 生成 {len(memories)} 条独立记忆"
                        f"（来自 {len(batches)} 个批次）"
                    )
                except Exception as e:
                    logger.error(
                        f"[{session_id}] LLM 处理失败（重试 {retry_count + 1}/3）：{e}",
                        exc_info=True,
                    )
                    await self._record_pending_summary(
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                    )
                    return

                if self._memory_engine:
                    # 并行写入记忆（受写锁串行化约束，但消除了 await 调度开销）
                    stored_count = 0
                    successful_keys = set(completed_idempotency_keys)
                    _MAX_CONCURRENT_WRITES = 3

                    async def _store_one(mem: dict[str, Any]) -> bool:
                        metadata = mem.setdefault("metadata", {})
                        idempotency_key = str(metadata.get("idempotency_key") or "")
                        if idempotency_key in completed_idempotency_keys:
                            return True
                        metadata["source_window"] = {
                            "session_id": session_id,
                            "start_index": start_index,
                            "end_index": end_index,
                            "message_count": end_index - start_index,
                        }
                        try:
                            await self._memory_engine.add_memory(
                                content=mem["content"],
                                session_id=session_id,
                                persona_id=persona_id,
                                importance=mem["importance"],
                                metadata=metadata,
                                atoms=mem.get("atoms", []),
                            )
                            if idempotency_key:
                                successful_keys.add(idempotency_key)
                            return True
                        except Exception as e:
                            logger.error(
                                f"[{session_id}] 记忆写入失败：{e}",
                                exc_info=True,
                            )
                            return False

                    sem = asyncio.Semaphore(_MAX_CONCURRENT_WRITES)

                    async def _store_with_sem(mem):
                        async with sem:
                            return await _store_one(mem)

                    write_results = await asyncio.gather(
                        *[_store_with_sem(m) for m in memories],
                        return_exceptions=True,
                    )
                    for r in write_results:
                        if isinstance(r, BaseException):
                            logger.error(
                                f"[{session_id}] 批量写入异常：{r}",
                                exc_info=True,
                            )
                        elif r is True:
                            stored_count += 1

                    logger.info(
                        f"[{session_id}] 成功存储 {stored_count}/{len(memories)} 条记忆"
                        f"（{len(history_messages)}条消息）"
                    )
                else:
                    stored_count = len(memories)

                if stored_count < len(memories):
                    logger.warning(
                        f"[{session_id}] 记忆仅部分落库，保留待重试窗口："
                        f"{stored_count}/{len(memories)}，范围=[{start_index}:{end_index}]"
                    )
                    await self._record_pending_summary(
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                        failed_stage="memory_write",
                        failed_count=len(memories) - stored_count,
                        completed_idempotency_keys=successful_keys,
                    )
                    return

                if self._conversation_manager:
                    try:
                        await self._conversation_manager.update_session_metadata(
                            session_id,
                            "last_summarized_index",
                            end_index,
                        )
                        await self._conversation_manager.update_session_metadata(
                            session_id,
                            "pending_summary",
                            None,
                        )
                        logger.info(
                            f"[{session_id}] 更新滑动窗口位置："
                            f"last_summarized_index = {end_index}"
                        )
                    except Exception as meta_err:
                        logger.error(
                            f"[{session_id}] 记忆已存储但元数据更新失败：{meta_err}。"
                            "下次触发时将跳过本段消息，避免重复总结。",
                            exc_info=True,
                        )
                        try:
                            await self._conversation_manager.update_session_metadata(
                                session_id,
                                "last_summarized_index",
                                end_index,
                            )
                            await self._conversation_manager.update_session_metadata(
                                session_id,
                                "pending_summary",
                                None,
                            )
                        except Exception:
                            logger.error(
                                f"[{session_id}] 重试元数据更新仍然失败，"
                                "可能出现重复总结。",
                                exc_info=True,
                            )

            except Exception as e:
                logger.error(f"[{session_id}] 存储记忆失败：{e}", exc_info=True)
                await self._record_pending_summary(
                    session_id,
                    start_index,
                    end_index,
                    retry_count,
                )

    async def _record_pending_summary(
        self,
        session_id: str,
        start_index: int,
        end_index: int,
        current_retry_count: int,
        failed_stage: str = "unknown",
        failed_count: int | None = None,
        completed_idempotency_keys: set[str] | list[str] | None = None,
    ) -> None:
        """记录待处理的失败总结信息"""
        if not self._conversation_manager:
            return

        new_retry_count = current_retry_count + 1
        pending_summary = {
            "start_index": start_index,
            "end_index": end_index,
            "retry_count": new_retry_count,
            "failed_stage": failed_stage,
        }
        if failed_count is not None:
            pending_summary["failed_count"] = failed_count
        if completed_idempotency_keys:
            pending_summary["completed_idempotency_keys"] = sorted(
                str(item) for item in completed_idempotency_keys
            )

        await self._conversation_manager.update_session_metadata(
            session_id,
            "pending_summary",
            pending_summary,
        )

        logger.warning(
            f"[{session_id}] 记录待重试总结：范围=[{start_index}:{end_index}]，"
            f"重试次数={new_retry_count}/3"
        )

    @staticmethod
    def _memory_idempotency_key(
        *,
        session_id: str,
        start_index: int,
        end_index: int,
        batch_index: int,
        memory_index: int,
        content: str,
    ) -> str:
        content_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
        raw = (
            f"{session_id}:{start_index}:{end_index}:"
            f"{batch_index}:{memory_index}:{content_hash}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def shutdown(self) -> None:
        """关闭反思处理器，并等待所有存储任务完成。"""
        self._shutting_down = True
        if self._storage_tasks:
            logger.info(f"等待 {len(self._storage_tasks)} 个存储任务完成……")
            await asyncio.gather(*self._storage_tasks, return_exceptions=True)
            self._storage_tasks.clear()
        self._storage_sessions_inflight.clear()
        logger.info("反思处理器已关闭")
