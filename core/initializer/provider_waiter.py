"""Provider 等待与后台重试"""

import asyncio
import contextlib
import time

from astrbot.api import logger


class ProviderWaiter:
    """非阻塞 Provider 检查 + 指数退避后台重试"""

    def __init__(self, max_attempts: int = 60):
        self._retry_task: asyncio.Task | None = None
        self._max_attempts = max_attempts
        self._attempts = 0
        self._providers_ready = False
        self.on_ready_callback = None

    @property
    def providers_ready(self) -> bool:
        return self._providers_ready

    @property
    def attempts(self) -> int:
        return self._attempts

    def reset(self):
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
        """非阻塞地检查 Provider 是否可用。返回 (embedding, llm, ready_bool)"""
        start_time = time.time()
        emb, llm = embedding_provider, llm_provider

        while time.time() - start_time < max_wait:
            emb, llm = provider_loader.initialize_providers(emb, llm, silent=True)
            if emb and llm:
                logger.info(
                    "Provider check passed: embedding and llm providers are ready."
                )
                self._providers_ready = True
                return emb, llm, True
            await asyncio.sleep(1.0)
            self._attempts += 1

        logger.debug(
            f"Provider 在 {max_wait}秒内未就绪（已尝试 {self._attempts} 次）"
            f"：embedding={'ready' if emb else 'not ready'}, "
            f"llm={'ready' if llm else 'not ready'}"
        )
        return emb, llm, False

    def start_retry_if_needed(self, provider_loader, embedding_provider, llm_provider):
        if self._retry_task and not self._retry_task.done():
            return
        self._retry_task = asyncio.create_task(
            self._retry_loop(provider_loader, embedding_provider, llm_provider)
        )
        self._retry_task.add_done_callback(self._on_retry_done)

    def _on_retry_done(self, task: asyncio.Task) -> None:
        self._retry_task = None
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Provider 重试任务异常退出: {exc}")
        except Exception as e:
            logger.debug(f"Provider 重试任务异常检查失败: {e}")

    async def _retry_loop(self, provider_loader, embedding_provider, llm_provider):
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
                    f"等待 Provider 就绪（未就绪: {', '.join(missing)}）..."
                    f"（已尝试 {self._attempts}/{self._max_attempts} 次，"
                    f"下次重试间隔 {current_interval:.1f}s）"
                )

            if emb and llm:
                logger.info(
                    f"Provider 在第 {self._attempts} 次尝试后就绪，继续初始化。"
                )
                self._providers_ready = True
                if self.on_ready_callback:
                    await self.on_ready_callback(emb, llm)
                break

            current_interval = min(current_interval * 1.5, max_interval)

        if not self._providers_ready:
            missing = []
            if not emb:
                missing.append("Embedding Provider（请配置向量嵌入模型）")
            if not llm:
                missing.append("LLM Provider（请配置语言模型）")
            logger.error(
                f"以下 Provider 在 {self._attempts} 次尝试后仍未就绪，初始化失败: "
                f"{', '.join(missing) if missing else '未知'}"
            )

    async def cancel(self):
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
        self._retry_task = None
