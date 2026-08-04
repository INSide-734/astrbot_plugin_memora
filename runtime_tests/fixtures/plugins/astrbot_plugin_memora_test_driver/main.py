"""注册 Memora 黑盒测试所需的低敏 readiness 与消息路由。"""

import asyncio
import ipaddress
import os
import re
import secrets
import signal
from types import FrameType

from astrbot.api.star import Context, Star
from astrbot.api.web import request

from .platform import PLATFORM_ID, MemoraTestPlatform
from .provider import (  # noqa: F401
    CHAT_PROVIDER_ID,
    EMBEDDING_PROVIDER_ID,
    MemoraTestChatProvider,
    MemoraTestEmbeddingProvider,
)

DRIVER_ROOT_NAME = "astrbot_plugin_memora_test_driver"
MEMORA_ROOT_NAME = "astrbot_plugin_memora"
TOKEN_ENV = "MEMORA_TEST_DRIVER_TOKEN"
TOKEN_HEADER = "X-Memora-Test-Token"
_MESSAGE_PATH = f"/{DRIVER_ROOT_NAME}/page/messages"
_MESSAGE_RESULT_PATH = f"/{DRIVER_ROOT_NAME}/page/messages/result"
_SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_SENDER_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MAX_MESSAGE_LENGTH = 500


class MemoraTestDriver(Star):
    """通过受保护 HTTP 边界报告 readiness 并驱动测试 Platform。"""

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
        context.register_web_api(
            _MESSAGE_PATH,
            self.submit_message,
            ["POST"],
            "Memora 黑盒测试消息注入",
        )
        context.register_web_api(
            _MESSAGE_RESULT_PATH,
            self.message_result,
            ["GET"],
            "Memora 黑盒测试消息结果",
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
        if not self._request_is_authorized():
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

    async def submit_message(self) -> tuple[dict[str, str], int]:
        """校验低敏 JSON 后经测试 Platform 向真实 EventBus 提交群消息。"""
        if not self._request_is_authorized():
            return {"error": "forbidden"}, 403
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return {"error": "invalid_payload"}, 400
        text = payload.get("text")
        session_id = payload.get("session_id", "memora-blackbox-session")
        sender_id = payload.get("sender_id", "memora-test-user")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > _MAX_MESSAGE_LENGTH
        ):
            return {"error": "invalid_text"}, 400
        if not isinstance(session_id, str) or not _SESSION_PATTERN.fullmatch(
            session_id
        ):
            return {"error": "invalid_session"}, 400
        if not isinstance(sender_id, str) or not _SENDER_PATTERN.fullmatch(sender_id):
            return {"error": "invalid_sender"}, 400
        platform = self.context.get_platform_inst(PLATFORM_ID)
        if not isinstance(platform, MemoraTestPlatform):
            return {"error": "platform_unavailable"}, 503
        return platform.submit_group_message(text.strip(), session_id, sender_id), 202

    async def message_result(
        self,
    ) -> tuple[dict[str, object] | dict[str, str], int] | dict[str, object]:
        """按关联标识读取测试 Platform 捕获的回复状态。"""
        if not self._request_is_authorized():
            return {"error": "forbidden"}, 403
        correlation_id = request.query.get("message_id", "")
        if not isinstance(correlation_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", correlation_id
        ):
            return {"error": "invalid_message_id"}, 400
        platform = self.context.get_platform_inst(PLATFORM_ID)
        if not isinstance(platform, MemoraTestPlatform):
            return {"error": "platform_unavailable"}, 503
        result = platform.get_message_result(correlation_id)
        if result is None:
            return {"error": "not_found"}, 404
        return result

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

    @classmethod
    def _request_is_authorized(cls) -> bool:
        """同时验证回环来源和场景专用令牌。"""
        return cls._request_is_loopback() and cls._token_matches()

    @staticmethod
    def _token_matches() -> bool:
        """使用常量时间比较验证场景专用测试令牌。"""
        expected = os.environ.get(TOKEN_ENV, "")
        provided = request.headers.get(TOKEN_HEADER, "")
        return bool(expected) and secrets.compare_digest(provided, expected)
