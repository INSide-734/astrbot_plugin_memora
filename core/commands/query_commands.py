"""
查询类命令 Mixin
提供状态查询、记忆搜索、遗忘和 WebUI 命令的处理方法。
"""

import os
from collections.abc import AsyncGenerator
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.platform import MessageType

from ..i18n_backend import t, t_list
from ..managers.feedback_signal_manager import record_explicit_correction


class QueryCommandMixin:
    """查询类命令的 Mixin 基类"""

    @staticmethod
    def _maintenance_write_guard_message() -> str | None:
        """独立 Mixin 默认无维护写保护。"""
        return None

    @staticmethod
    def _format_error_message(
        action: str, error: Exception, suggestions: list[str] | None = None
    ) -> str:
        """格式化面向用户的错误消息，包含可操作提示。"""
        message = [
            t("error.format.action_failed", action=action),
            t("error.format.details", error=error),
        ]
        if suggestions:
            message.append("")
            message.append(t("error.format.suggestions"))
            for index, suggestion in enumerate(suggestions, start=1):
                message.append(
                    t(
                        "error.format.suggestion_item",
                        index=index,
                        suggestion=suggestion,
                    )
                )
        return "\n".join(message)

    @staticmethod
    def _component_not_ready_message(component: str, command: str) -> str:
        """构建一致的组件未就绪响应消息。"""
        return t("error.component_not_ready", component=component, command=command)

    @staticmethod
    def _event_chat_type(event: AstrMessageEvent) -> str | None:
        """根据明确的平台事件类型返回群聊或私聊，未知类型拒绝检索。"""

        try:
            value = event.get_message_type()
        except Exception:
            return None
        if value == getattr(MessageType, "GROUP_MESSAGE", None):
            return "group"
        private_values = (
            getattr(MessageType, "FRIEND_MESSAGE", None),
            getattr(MessageType, "PRIVATE_MESSAGE", None),
        )
        if any(
            value == candidate for candidate in private_values if candidate is not None
        ):
            return "private"
        value_name = getattr(value, "name", None)
        value_text = getattr(value, "value", None)
        if value_name in {"FRIEND_MESSAGE", "PRIVATE_MESSAGE"} or value_text in {
            "FriendMessage",
            "PrivateMessage",
            "FRIEND_MESSAGE",
            "PRIVATE_MESSAGE",
        }:
            return "private"
        return None

    @staticmethod
    def _event_sender_id(event: AstrMessageEvent) -> str | None:
        """读取稳定发送者标识；缺失或非标量输入不参与用户级检索。"""

        try:
            value = event.get_sender_id()
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        user_id = str(value).strip()
        return user_id or None

    async def handle_status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora status 命令"""
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/memora status")
            )
            return

        try:
            stats = await self.memory_engine.get_statistics()

            # 格式化时间
            last_update = t("common.never")
            if stats.get("newest_memory"):
                last_update = datetime.fromtimestamp(stats["newest_memory"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            # 计算数据库大小
            db_size = 0.0
            if os.path.exists(self.memory_engine.db_path):
                db_size = os.path.getsize(self.memory_engine.db_path) / (1024 * 1024)

            session_count = len(stats.get("sessions", {}))

            message = t(
                "status.report",
                total=stats["total_memories"],
                session_count=session_count,
                last_update=last_update,
                db_size=db_size,
            )

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("status.action_name"),
                    e,
                    t_list("error.suggestions.status"),
                )
            )

    async def handle_search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora search 命令"""
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/memora search")
            )
            return

        # 输入验证
        if not query or not query.strip():
            yield event.plain_result(t("search.query_empty"))
            return

        # 限制k的范围为1-100
        k = max(1, min(k, 100))

        try:
            session_id = event.unified_msg_origin
            chat_type = self._event_chat_type(event)
            if chat_type is None or not session_id:
                yield event.plain_result(t("search.no_results", query=query))
                return
            results = await self.memory_engine.search_memories(
                query=query.strip(),
                k=k,
                session_id=session_id,
                chat_type=chat_type,
                user_id=self._event_sender_id(event),
            )

            if not results:
                yield event.plain_result(t("search.no_results", query=query))
                return

            message = t("search.header", count=len(results))
            for i, result in enumerate(results, 1):
                score = result.final_score
                content = (
                    result.content[:100] + "..."
                    if len(result.content) > 100
                    else result.content
                )
                raw_breakdown = getattr(result, "score_breakdown", {})
                breakdown = raw_breakdown if isinstance(raw_breakdown, dict) else {}
                message += t(
                    "search.item.score",
                    index=i,
                    score=score,
                    content=content,
                )
                message += t("search.item.id", id=result.doc_id)
                message += t(
                    "search.item.breakdown",
                    doc_kw=breakdown.get("document_keyword_score", 0.0),
                    doc_vec=breakdown.get("document_vector_score", 0.0),
                    graph_kw=breakdown.get("graph_keyword_score", 0.0),
                    graph_vec=breakdown.get("graph_vector_score", 0.0),
                )

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("search.action_name"),
                    e,
                    t_list("error.suggestions.search"),
                )
            )

    async def handle_forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora forget 命令"""
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/memora forget")
            )
            return

        # 输入验证
        if doc_id < 0:
            yield event.plain_result(t("forget.id_invalid"))
            return

        try:
            success = await self.memory_engine.delete_memory(doc_id)
            if success:
                await self._record_forget_correction(doc_id, event)
                yield event.plain_result(t("forget.success", id=doc_id))
            else:
                yield event.plain_result(t("forget.not_found", id=doc_id))
        except Exception as e:
            logger.error(f"删除失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("forget.action_name"),
                    e,
                    t_list("error.suggestions.forget"),
                )
            )

    async def _record_forget_correction(
        self,
        doc_id: int,
        event: AstrMessageEvent,
    ) -> None:
        """把显式忘记作为可信负向反馈写入隔离管线，失败不影响命令结果。"""

        manager = getattr(self.memory_engine, "feedback_signal_manager", None)
        if manager is None:
            return
        try:
            record_explicit_correction(
                manager,
                decision_key=f"forget:{doc_id}",
                scope_domain=str(getattr(event, "unified_msg_origin", "") or "unknown"),
            )
        except Exception:
            logger.warning("[忘记命令] 反馈记录失败")

    @staticmethod
    async def handle_webui(
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora webui 命令"""
        yield event.plain_result(t("webui.guide"))
