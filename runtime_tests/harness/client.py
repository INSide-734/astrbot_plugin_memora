"""AstrBot Dashboard 与插件 Page API 的同步 HTTP 客户端。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .process import AstrBotProcess

LOGIN_PATH = "/api/v1/auth/login"
DRIVER_PATH = "/api/v1/plugins/extensions/astrbot_plugin_memora_test_driver/page/status"
MESSAGE_PATH = (
    "/api/v1/plugins/extensions/astrbot_plugin_memora_test_driver/page/messages"
)
MESSAGE_RESULT_PATH = f"{MESSAGE_PATH}/result"
MEMORA_READY_PATH = (
    "/api/v1/plugins/extensions/astrbot_plugin_memora/page/metrics/summary"
)
MEMORA_MEMORIES_PATH = "/api/v1/plugins/extensions/astrbot_plugin_memora/page/memories"
_TEST_TOKEN_HEADER = "X-Memora-Test-Token"
_REQUEST_TIMEOUT = httpx.Timeout(1.0, connect=0.5)
_LOGIN_TIMEOUT = httpx.Timeout(5.0, connect=0.5)
_POLL_INTERVAL_SECONDS = 0.5


class AstrBotClient:
    """通过 AstrBot 公开 HTTP 边界驱动单个测试场景。"""

    def __init__(
        self,
        port: int,
        password: str,
        test_token: str,
        process: AstrBotProcess,
    ) -> None:
        """建立只访问回环 Dashboard 的短超时客户端。"""
        self._password = password
        self._test_token = test_token
        self._process = process
        self._dashboard_token: str | None = None
        self._closed = False
        self._client = self._make_client(port)

    @staticmethod
    def _make_client(port: int) -> httpx.Client:
        """为指定端口创建忽略用户代理设置的 HTTP 客户端。"""
        return httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            timeout=_REQUEST_TIMEOUT,
            trust_env=False,
        )

    def retarget(self, port: int) -> None:
        """在仅限端口冲突重试时把客户端切换到新的回环端口。"""
        self._client.close()
        self._client = self._make_client(port)
        self._dashboard_token = None
        self._closed = False

    def login(self) -> httpx.Response:
        """使用场景密码登录 Dashboard 并保存返回的 JWT。"""
        response = self._client.post(
            LOGIN_PATH,
            json={"username": "astrbot", "password": self._password},
            timeout=_LOGIN_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("data", {}).get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Dashboard 登录响应缺少 data.token")
        self._dashboard_token = token
        return response

    def driver_status(
        self,
        *,
        authenticated: bool = True,
        include_test_token: bool = True,
    ) -> httpx.Response:
        """读取测试驱动状态，并可独立关闭两层鉴权头。"""
        headers = self._driver_headers(
            authenticated=authenticated,
            include_test_token=include_test_token,
        )
        return self._client.get(DRIVER_PATH, headers=headers)

    def submit_group_message(
        self,
        text: str,
        *,
        session_id: str = "memora-blackbox-session",
        sender_id: str = "memora-test-user",
        include_test_token: bool = True,
    ) -> httpx.Response:
        """通过受保护 HTTP 入口向测试 Platform 提交一条群消息。"""
        self._process.add_sensitive_value(text, "[TEST_MESSAGE]")
        return self._client.post(
            MESSAGE_PATH,
            headers=self._driver_headers(
                authenticated=True,
                include_test_token=include_test_token,
            ),
            json={
                "text": text,
                "session_id": session_id,
                "sender_id": sender_id,
            },
        )

    def wait_for_message_result(
        self,
        message_id: str,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """条件轮询测试 Platform，直到指定消息产生可观察回复。"""
        deadline = time.monotonic() + timeout
        last_observation = "尚未收到 HTTP 响应"
        while True:
            self._raise_if_process_exited("消息回复")
            try:
                response = self._client.get(
                    MESSAGE_RESULT_PATH,
                    headers=self._driver_headers(
                        authenticated=True,
                        include_test_token=True,
                    ),
                    params={"message_id": message_id},
                )
                last_observation = f"HTTP {response.status_code}"
                if response.status_code == 200:
                    payload = response.json()
                    replies = payload.get("replies")
                    if payload.get("status") == "completed" and isinstance(
                        replies, list
                    ):
                        for reply in replies:
                            if isinstance(reply, str):
                                self._process.add_sensitive_value(
                                    reply,
                                    "[PROVIDER_RESPONSE]",
                                )
                        return payload
            except httpx.RequestError as exc:
                last_observation = exc.__class__.__name__
            self._wait_for_next_poll(deadline, "消息回复", last_observation)

    def list_memories(
        self,
        *,
        session_id: str,
        keyword: str,
    ) -> httpx.Response:
        """通过 Memora Page API 查询当前场景的隔离会话记忆。"""
        return self._client.get(
            MEMORA_MEMORIES_PATH,
            headers=self._headers(authenticated=True),
            params={
                "session_id": session_id,
                "keyword": keyword,
                "page": 1,
                "page_size": 20,
            },
        )

    def wait_for_memory(
        self,
        *,
        session_id: str,
        keyword: str,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """轮询公开 Page API，直到目标会话出现匹配的持久化记忆。"""
        deadline = time.monotonic() + timeout
        last_observation = "尚未收到 HTTP 响应"
        while True:
            self._raise_if_process_exited("记忆落库")
            try:
                response = self.list_memories(session_id=session_id, keyword=keyword)
                last_observation = f"HTTP {response.status_code}"
                if response.status_code == 200:
                    envelope = response.json()
                    data = envelope.get("data", {})
                    if (
                        envelope.get("status") == "ok"
                        and isinstance(data, dict)
                        and int(data.get("total", 0)) > 0
                    ):
                        for item in data.get("items", []):
                            if not isinstance(item, dict):
                                continue
                            content = item.get("content")
                            if isinstance(content, str):
                                self._process.add_sensitive_value(
                                    content,
                                    "[MEMORY_CONTENT]",
                                )
                        return data
            except (httpx.RequestError, TypeError, ValueError) as exc:
                last_observation = exc.__class__.__name__
            self._wait_for_next_poll(deadline, "记忆落库", last_observation)

    def wait_for_driver_ready(self, timeout: float = 60.0) -> dict[str, bool]:
        """在单调时限内轮询驱动注册表，直到全部组件均已加载。"""
        deadline = time.monotonic() + timeout
        last_observation = "尚未收到 HTTP 响应"
        while True:
            self._raise_if_process_exited("测试驱动就绪")
            try:
                response = self.driver_status()
                last_observation = f"HTTP {response.status_code}"
                if response.status_code == 200:
                    payload = response.json()
                    if (
                        isinstance(payload, dict)
                        and payload
                        and all(value is True for value in payload.values())
                    ):
                        return payload
            except httpx.RequestError as exc:
                last_observation = exc.__class__.__name__
            self._wait_for_next_poll(deadline, "测试驱动", last_observation)

    def wait_for_memora_ready(self, timeout: float = 90.0) -> dict[str, Any]:
        """在单调时限内轮询 Memora 指标，直到 Provider 完成初始化。"""
        deadline = time.monotonic() + timeout
        last_observation = "尚未收到 HTTP 响应"
        while True:
            self._raise_if_process_exited("Memora 就绪")
            try:
                response = self._client.get(
                    MEMORA_READY_PATH,
                    headers=self._headers(authenticated=True),
                )
                last_observation = f"HTTP {response.status_code}"
                if response.status_code == 200:
                    envelope = response.json()
                    data = envelope.get("data", {})
                    provider = data.get("provider", {})
                    if (
                        envelope.get("status") == "ok"
                        and provider.get("status") == "ready"
                        and provider.get("is_initialized") is True
                    ):
                        return data
            except httpx.RequestError as exc:
                last_observation = exc.__class__.__name__
            self._wait_for_next_poll(deadline, "Memora", last_observation)

    def close(self) -> None:
        """幂等关闭底层 HTTP 连接池并清除 Dashboard 令牌。"""
        if self._closed:
            return
        self._client.close()
        self._dashboard_token = None
        self._closed = True

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        """按调用意图生成 Dashboard Authorization 请求头。"""
        if authenticated and self._dashboard_token:
            return {"Authorization": f"Bearer {self._dashboard_token}"}
        return {}

    def _driver_headers(
        self,
        *,
        authenticated: bool,
        include_test_token: bool,
    ) -> dict[str, str]:
        """为测试驱动路由组合 Dashboard JWT 与场景专用令牌。"""
        headers = self._headers(authenticated=authenticated)
        if include_test_token:
            headers[_TEST_TOKEN_HEADER] = self._test_token
        return headers

    def _raise_if_process_exited(self, operation: str) -> None:
        """发现子进程提前退出时立即附带脱敏日志终止轮询。"""
        returncode = self._process.returncode
        if returncode is None:
            return
        logs = self._process.read_sanitized_log()
        raise RuntimeError(
            f"AstrBot 在等待{operation}时提前退出（退出码 {returncode}）。\n{logs}"
        )

    def _wait_for_next_poll(
        self,
        deadline: float,
        operation: str,
        last_observation: str,
    ) -> None:
        """按剩余单调时限安排下一次条件轮询，超时则报告脱敏日志。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logs = self._process.read_sanitized_log()
            raise TimeoutError(
                f"等待{operation}超时，最后观测：{last_observation}。\n{logs}"
            )
        time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
