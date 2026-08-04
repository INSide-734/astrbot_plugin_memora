"""为真实 AstrBot 黑盒测试注册空闲 Platform。"""

import asyncio

from astrbot.api.event import MessageChain
from astrbot.api.platform import Platform, PlatformMetadata, register_platform_adapter
from astrbot.core.platform.message_session import MessageSesion


@register_platform_adapter(
    "memora_test_platform",
    "Memora 测试 Platform",
    support_streaming_message=False,
)
class MemoraTestPlatform(Platform):
    """由 AstrBot PlatformManager 管理生命周期的最小测试 Platform。"""

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
