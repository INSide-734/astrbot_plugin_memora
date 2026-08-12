"""
维护类命令 Mixin
提供索引重建、图重建、清理和重置命令的处理方法。
"""

import json
import re
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..i18n_backend import t, t_list
from ..shared.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER


class MaintenanceCommandMixin:
    """维护类命令的 Mixin 基类"""

    context: Any
    memory_engine: Any | None
    conversation_manager: Any | None
    index_validator: Any | None

    if TYPE_CHECKING:

        @staticmethod
        def _component_not_ready_message(component: str, command: str) -> str:
            """声明组合宿主提供的组件未就绪消息格式化接口。"""

            ...

        @staticmethod
        def _format_error_message(
            action: str,
            error: Exception,
            suggestions: list[str] | None = None,
        ) -> str:
            """声明组合宿主提供的命令错误消息格式化接口。"""

            ...

    def _maintenance_write_guard_message(self) -> str | None:
        """独立 Mixin 默认无维护写保护。"""
        return None

    async def handle_rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora rebuild-index 命令"""
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if not self.memory_engine or not self.index_validator:
            yield event.plain_result(
                self._component_not_ready_message(
                    "记忆引擎或索引验证器", "/memora rebuild-index"
                )
            )
            return

        try:
            yield event.plain_result(t("rebuild_index.checking"))

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if status.is_consistent and not status.needs_rebuild:
                yield event.plain_result(t("rebuild_index.ok", reason=status.reason))
                return

            # 显示当前状态
            status_msg = t(
                "rebuild_index.status_template",
                doc_count=status.documents_count,
                bm25_count=status.bm25_count,
                vec_count=status.vector_count,
                reason=status.reason,
            )
            yield event.plain_result(status_msg)

            # 执行重建
            result = await self.index_validator.rebuild_indexes(self.memory_engine)

            if result["success"]:
                partial_notice = ""
                if result.get("partial"):
                    partial_notice = t(
                        "rebuild_index.partial_notice",
                        ratio=result.get("failure_ratio", 0),
                    )
                switched_str = (
                    t("common.yes") if result.get("switched") else t("common.no")
                )
                result_msg = t(
                    "rebuild_index.result_template",
                    success=result["processed"],
                    failed=result["errors"],
                    total=result["total"],
                    vector_mode=result.get("vector_mode", "unknown"),
                    switched=switched_str,
                    partial_notice=partial_notice,
                )
                yield event.plain_result(result_msg)
            else:
                yield event.plain_result(
                    t(
                        "rebuild_index.failed",
                        message=result.get("message", t("common.unknown_error")),
                    )
                )

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("rebuild_index.action_name"),
                    e,
                    t_list("error.suggestions.rebuild_index"),
                )
            )

    async def handle_rebuild_graph(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora rebuild-graph 命令"""
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/memora rebuild-graph")
            )
            return

        try:
            yield event.plain_result(t("rebuild_graph.starting"))
            result = await self.memory_engine.rebuild_graph_index()
            yield event.plain_result(
                t(
                    "rebuild_graph.success",
                    rebuilt=result.get("rebuilt", 0),
                    skipped=result.get("skipped", 0),
                )
            )
        except Exception as e:
            logger.error(f"重建图记忆失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("rebuild_graph.action_name"),
                    e,
                    t_list("error.suggestions.rebuild_graph"),
                )
            )

    async def handle_reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora reset 命令"""
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if not self.conversation_manager:
            yield event.plain_result(
                self._component_not_ready_message("会话管理器", "/memora reset")
            )
            return

        session_id = event.unified_msg_origin
        try:
            await self.conversation_manager.clear_session(session_id)
            message = t("reset.success")
            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"手动重置记忆上下文失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("reset.action_name"),
                    e,
                    t_list("error.suggestions.reset"),
                )
            )

    async def handle_cleanup(
        self, event: AstrMessageEvent, dry_run: bool = False
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora cleanup 命令 - 清理 AstrBot 历史消息中的记忆注入片段"""
        if not dry_run:
            blocked_message = self._maintenance_write_guard_message()
            if blocked_message:
                yield event.plain_result(blocked_message)
                return
        session_id = event.unified_msg_origin
        try:
            mode_text = t("cleanup.mode_preview") if dry_run else t("cleanup.mode_exec")
            yield event.plain_result(t("cleanup.starting", mode_text=mode_text))

            # 检查 context 是否可用
            if not self.context:
                yield event.plain_result(t("cleanup.context_unavailable"))
                return

            # 获取当前对话 ID
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                session_id
            )
            if not cid:
                yield event.plain_result(t("cleanup.no_history"))
                return

            # 获取对话历史
            conversation = await self.context.conversation_manager.get_conversation(
                session_id, cid
            )
            if not conversation or not conversation.history:
                yield event.plain_result(t("cleanup.empty_history"))
                return

            # 清理历史消息中的记忆注入片段

            # 解析 history（字符串格式）
            try:
                history = json.loads(conversation.history)
            except json.JSONDecodeError:
                yield event.plain_result(t("cleanup.parse_failed"))
                return

            # 统计信息
            stats = {
                "scanned": len(history),
                "matched": 0,
                "cleaned": 0,
                "deleted": 0,
            }

            # 编译清理正则
            pattern = re.compile(
                re.escape(MEMORY_INJECTION_HEADER)
                + r".*?"
                + re.escape(MEMORY_INJECTION_FOOTER),
                flags=re.DOTALL,
            )

            # 清理历史消息
            cleaned_history = []
            for msg in history:
                content = msg.get("content", "")
                if not isinstance(content, str):
                    cleaned_history.append(msg)
                    continue

                # 检查是否包含注入标记
                if (
                    MEMORY_INJECTION_HEADER in content
                    and MEMORY_INJECTION_FOOTER in content
                ):
                    stats["matched"] += 1

                    # 清理内容
                    cleaned_content = pattern.sub("", content)
                    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()

                    # 如果清理后为空，跳过该消息
                    if not cleaned_content:
                        stats["deleted"] += 1
                        logger.debug(
                            f"[cleanup] 删除纯记忆注入消息: role={msg.get('role')}"
                        )
                        continue

                    # 如果清理后仍有内容，保留清理后的消息
                    if cleaned_content != content:
                        msg_copy = msg.copy()
                        msg_copy["content"] = cleaned_content
                        cleaned_history.append(msg_copy)
                        stats["cleaned"] += 1
                        logger.debug(
                            f"[cleanup] 清理消息内部记忆片段: "
                            f"原长度={len(content)}, 新长度={len(cleaned_content)}"
                        )
                        continue

                cleaned_history.append(msg)

            # 如果不是预演模式，更新数据库
            if not dry_run and (stats["cleaned"] > 0 or stats["deleted"] > 0):
                await self.context.conversation_manager.update_conversation(
                    unified_msg_origin=session_id,
                    conversation_id=cid,
                    history=cleaned_history,
                )
                logger.info(
                    f"[{session_id}] cleanup 已更新 AstrBot 对话历史: "
                    f"清理={stats['cleaned']}, 删除={stats['deleted']}"
                )

            # 格式化结果
            notice = (
                t("cleanup.notice_preview") if dry_run else t("cleanup.notice_exec")
            )
            message = t(
                "cleanup.result_template",
                mode_text=mode_text,
                scanned=stats["scanned"],
                matched=stats["matched"],
                cleaned=stats["cleaned"],
                deleted=stats["deleted"],
                notice=notice,
            )

            yield event.plain_result(message)

        except Exception as e:
            logger.error(f"清理历史消息失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("cleanup.action_name"),
                    e,
                    t_list("error.suggestions.cleanup"),
                )
            )
