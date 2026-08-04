"""注册 Memora 黑盒测试所需的低敏 readiness 路由。"""

import asyncio
import ipaddress
import os
import secrets
import signal
from types import FrameType

from astrbot.api.star import Context, Star
from astrbot.api.web import request

from .platform import MemoraTestPlatform  # noqa: F401
from .provider import (  # noqa: F401
    CHAT_PROVIDER_ID,
    EMBEDDING_PROVIDER_ID,
    MemoraTestChatProvider,
    MemoraTestEmbeddingProvider,
)

DRIVER_ROOT_NAME = "astrbot_plugin_memora_test_driver"
MEMORA_ROOT_NAME = "astrbot_plugin_memora"
PLATFORM_ID = "memora-test-platform"
TOKEN_ENV = "MEMORA_TEST_DRIVER_TOKEN"
TOKEN_HEADER = "X-Memora-Test-Token"


class MemoraTestDriver(Star):
    """仅通过 AstrBot 公开注册表报告黑盒测试 readiness。"""

    def __init__(self, context: Context) -> None:
        """注册受 Dashboard 鉴权、回环地址和测试令牌保护的路由。"""
        super().__init__(context)
        self._loop = asyncio.get_running_loop()
        self._owner_task = asyncio.current_task()
        if os.name == "nt":
            signal.signal(signal.SIGBREAK, self._cancel_owner_task)
        context.register_web_api(
            f"/{DRIVER_ROOT_NAME}/page/status",
            self.status,
            ["GET"],
            "Memora 黑盒测试 readiness",
        )

    def _cancel_owner_task(
        self,
        _signum: int,
        _frame: FrameType | None,
    ) -> None:
        """把 Windows 控制中断转为 AstrBot 顶层任务的有序取消。"""
        task = self._owner_task
        if task is not None and not task.done():
            self._loop.call_soon_threadsafe(task.cancel)

    async def status(self) -> tuple[dict[str, bool] | dict[str, str], int] | dict:
        """验证双重测试保护后返回低敏注册表状态。"""
        if not self._request_is_loopback() or not self._token_matches():
            return {"error": "forbidden"}, 403

        stars = self.context.get_all_stars()
        return {
            "driver_loaded": self._star_loaded(stars, DRIVER_ROOT_NAME),
            "memora_loaded": self._star_loaded(stars, MEMORA_ROOT_NAME),
            "chat_provider_loaded": (
                self.context.get_provider_by_id(CHAT_PROVIDER_ID) is not None
            ),
            "embedding_provider_loaded": (
                self.context.get_provider_by_id(EMBEDDING_PROVIDER_ID) is not None
            ),
            "platform_loaded": (
                self.context.get_platform_inst(PLATFORM_ID) is not None
            ),
        }

    @staticmethod
    def _star_loaded(stars: list, root_name: str) -> bool:
        """判断指定根目录的插件是否已由 AstrBot 激活。"""
        return any(star.root_dir_name == root_name and star.activated for star in stars)

    @staticmethod
    def _request_is_loopback() -> bool:
        """仅接受来源可解析且属于回环网段的请求。"""
        remote_addr = request.client_host
        if not remote_addr:
            return False
        try:
            return ipaddress.ip_address(remote_addr).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _token_matches() -> bool:
        """使用常量时间比较验证场景专用测试令牌。"""
        expected = os.environ.get(TOKEN_ENV, "")
        provided = request.headers.get(TOKEN_HEADER, "")
        return bool(expected) and secrets.compare_digest(provided, expected)
