"""Provider 等待与后台重试的组合根辅助模块。"""

import asyncio
import contextlib
import time

from astrbot.api import logger


class ProviderWaiter:
    """非阻塞检查 Provider，并在后台执行指数退避重试。"""

    def __init__(self, max_attempts: int = 60):
        """创建 Provider 等待器。

        参数:
            max_attempts: 后台重试允许的最大次数。
        """

        self._retry_task: asyncio.Task | None = None
        self._max_attempts = max_attempts
        self._attempts = 0
        self._providers_ready = False
        self.on_terminal_callback = None

    @property
    def providers_ready(self) -> bool:
        """返回最近一次检查是否同时获得两类 Provider。"""

        return self._providers_ready

    @property
    def attempts(self) -> int:
        """返回已执行的 Provider 检查次数。"""

        return self._attempts

    def reset(self):
        """重置等待状态；不会主动取消已经运行的任务。"""

        self._attempts = 0
        self._providers_ready = False
        self._retry_task = None

    async def wait_non_blocking(
        self,
        provider_loader,
        embedding_provider,
        llm_provider,
        max_wait: float = 5.0,
    ):
        """在有限时间内检查 Provider 是否可用。

        参数:
            provider_loader: 提供同步 Provider 初始化查询的加载器。
            embedding_provider: 当前缓存的 Embedding Provider 或空值。
            llm_provider: 当前缓存的聊天 Provider 或空值。
            max_wait: 前台检查的最长等待秒数。

        返回:
            ``(embedding_provider, llm_provider, ready)`` 三元组。
        """

        start_time = time.time()
        emb, llm = embedding_provider, llm_provider

        while time.time() - start_time < max_wait:
            emb, llm = provider_loader.initialize_providers(emb, llm, silent=True)
            if emb and llm:
                logger.info("Provider 检查通过：Embedding 和 LLM Provider 已就绪")
                self._providers_ready = True
                return emb, llm, True
            await asyncio.sleep(1.0)
            self._attempts += 1

        logger.debug(
            f"Provider 在 {max_wait} 秒内未就绪（已尝试 {self._attempts} 次）"
            f"：embedding={'就绪' if emb else '未就绪'}，"
            f"llm={'就绪' if llm else '未就绪'}"
        )
        return emb, llm, False

    def start_retry_if_needed(self, provider_loader, embedding_provider, llm_provider):
        """在没有活动重试任务时启动一个后台重试任务。"""

        if self._retry_task and not self._retry_task.done():
            return
        self._retry_task = asyncio.create_task(
            self._retry_loop(provider_loader, embedding_provider, llm_provider)
        )
        self._retry_task.add_done_callback(self._on_retry_done)

    def _on_retry_done(self, task: asyncio.Task) -> None:
        """消费后台任务终态，记录异常但保留取消语义。"""

        self._retry_task = None
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Provider 重试任务异常退出：{exc}")
        except Exception as e:
            logger.debug(f"检查 Provider 重试任务异常失败：{e}")

    async def _retry_loop(self, provider_loader, embedding_provider, llm_provider):
        """按退避间隔轮询 Provider，并在终态调用回调。"""

        base_interval = 2.0
        max_interval = 30.0
        current_interval = base_interval
        log_interval = 5
        emb, llm = embedding_provider, llm_provider

        while self._attempts < self._max_attempts:
            await asyncio.sleep(current_interval)
            emb, llm = provider_loader.initialize_providers(emb, llm, silent=True)
            self._attempts += 1

            if self._attempts % log_interval == 0:
                missing = []
                if not emb:
                    missing.append("Embedding Provider")
                if not llm:
                    missing.append("LLM Provider")
                logger.info(
                    f"等待 Provider 就绪（未就绪：{', '.join(missing)}）..."
                    f"（已尝试 {self._attempts}/{self._max_attempts} 次，"
                    f"下次重试间隔 {current_interval:.1f} 秒）"
                )

            if emb and llm:
                logger.info(f"Provider 在第 {self._attempts} 次尝试后就绪，继续初始化")
                self._providers_ready = True
                if self.on_terminal_callback:
                    await self.on_terminal_callback(emb, llm, exhausted=False)
                break

            current_interval = min(current_interval * 1.5, max_interval)

        if not self._providers_ready:
            missing = []
            if not emb:
                missing.append("Embedding Provider（请配置向量嵌入模型）")
            if not llm:
                missing.append("LLM Provider（请配置语言模型）")
            logger.error(
                f"以下 Provider 在 {self._attempts} 次尝试后仍未就绪，初始化失败："
                f"{', '.join(missing) if missing else '未知'}"
            )
            if self.on_terminal_callback:
                await self.on_terminal_callback(emb, llm, exhausted=True)

    async def cancel(self):
        """取消后台重试任务并等待其退出。"""

        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
        self._retry_task = None


__all__ = ["ProviderWaiter"]
