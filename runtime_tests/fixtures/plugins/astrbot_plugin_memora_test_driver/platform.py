"""为真实 AstrBot 黑盒测试注册可注入、可观测的 Platform。"""

from __future__ import annotations

import asyncio
import uuid

from astrbot.api.event import MessageChain
from astrbot.api.platform import Platform, PlatformMetadata, register_platform_adapter
from astrbot.core.message.components import At, BaseMessageComponent, Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSesion
from astrbot.core.platform.message_type import MessageType

PLATFORM_ID = "memora-test-platform"
_BOT_ID = "memora-test-bot"
_MAX_RESULTS = 128


class MemoraTestEvent(AstrMessageEvent):
    """把 AstrBot 出站消息回传给测试 Platform 的真实消息事件。"""

    def __init__(
        self,
        message_obj: AstrBotMessage,
        platform: MemoraTestPlatform,
        correlation_id: str,
    ) -> None:
        """使用标准事件字段初始化，并保留当前测试消息的关联标识。"""
        super().__init__(
            message_str=message_obj.message_str,
            message_obj=message_obj,
            platform_meta=platform.meta(),
            session_id=message_obj.session_id,
        )
        self._test_platform = platform
        self._correlation_id = correlation_id

    def _outline_chain(
        self,
        chain: list[BaseMessageComponent] | None,
    ) -> str:
        """用固定占位符阻止测试请求与模型回复进入 AstrBot 日志。"""
        return "[MEMORA_TEST_PAYLOAD]" if chain else ""

    async def send(self, message: MessageChain) -> None:
        """先捕获纯文本回复，再委托 AstrBot 基类维护发送指标。"""
        self._test_platform.record_response(self._correlation_id, message)
        await super().send(message)


@register_platform_adapter(
    "memora_test_platform",
    "Memora 测试 Platform",
    support_streaming_message=False,
)
class MemoraTestPlatform(Platform):
    """由 PlatformManager 管理并只通过公开 HTTP 路由驱动的测试 Platform。"""

    def __init__(
        self,
        config: dict,
        settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        """保存正式 AstrBot 注入参数并建立可终止的运行屏障。"""
        super().__init__(config, event_queue)
        self.settings = settings
        self._terminated = asyncio.Event()
        self._results: dict[str, dict[str, object]] = {}

    async def run(self) -> None:
        """保持 Platform 存活，直到 AstrBot 请求终止。"""
        await self._terminated.wait()

    async def terminate(self) -> None:
        """解除运行屏障，使 AstrBot 可以干净关闭 Platform。"""
        self._terminated.set()

    def meta(self) -> PlatformMetadata:
        """返回当前测试 Platform 实例的稳定元数据。"""
        return PlatformMetadata(
            name="memora_test_platform",
            description="Memora 测试 Platform",
            id=self.config["id"],
            support_streaming_message=False,
            support_proactive_message=False,
        )

    async def send_by_session(
        self,
        session: MessageSesion,
        message_chain: MessageChain,
    ) -> None:
        """只委托 AstrBot 基类记录发送指标，不制造出站消息。"""
        await super().send_by_session(session, message_chain)

    def submit_group_message(
        self,
        text: str,
        session_id: str,
        sender_id: str,
    ) -> dict[str, str]:
        """构造标准群消息事件、提交 EventBus，并返回低敏关联信息。"""
        correlation_id = uuid.uuid4().hex
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.self_id = _BOT_ID
        message.session_id = session_id
        message.message_id = correlation_id
        message.group_id = session_id
        message.sender = MessageMember(user_id=sender_id, nickname="测试用户")
        message.message = [
            At(qq=_BOT_ID, name="Memora"),
            Plain(text=text),
        ]
        message.message_str = text
        message.raw_message = {"source": "memora-blackbox"}
        event = MemoraTestEvent(message, self, correlation_id)
        self._results[correlation_id] = {
            "status": "pending",
            "replies": [],
            "session_id": event.unified_msg_origin,
        }
        while len(self._results) > _MAX_RESULTS:
            self._results.pop(next(iter(self._results)))
        self.commit_event(event)
        return {
            "message_id": correlation_id,
            "session_id": event.unified_msg_origin,
        }

    def record_response(self, correlation_id: str, message: MessageChain) -> None:
        """记录当前消息的纯文本回复，不持久化请求正文或富媒体载荷。"""
        result = self._results.get(correlation_id)
        if result is None:
            return
        replies = result.get("replies")
        if not isinstance(replies, list):
            replies = []
            result["replies"] = replies
        text = message.get_plain_text().strip()
        if text:
            replies.append(text)
        result["status"] = "completed"

    def get_message_result(self, correlation_id: str) -> dict[str, object] | None:
        """返回单条消息结果的浅拷贝，避免路由修改 Platform 内部状态。"""
        result = self._results.get(correlation_id)
        if result is None:
            return None
        return {
            "status": result.get("status"),
            "replies": list(result.get("replies", [])),
            "session_id": result.get("session_id"),
        }
