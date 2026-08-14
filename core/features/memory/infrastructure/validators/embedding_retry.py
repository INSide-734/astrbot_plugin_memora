"""具备速率限制感知的 Embedding 重试逻辑。"""

import asyncio
from typing import Any

from astrbot.api import logger

from .....platform.provider.adapters import EmbeddingProviderAdapter


class EmbeddingRetryMixin:
    """Embedding API 调用的重试逻辑，支持指数退避与速率限制检测。"""

    async def _embed_batch_with_retry(
        self,
        provider: Any,
        contents: list[str],
        options: dict[str, Any],
    ) -> list[Any]:
        """按配置切分输入，并复用一个冻结 adapter 执行全部子请求。

        参数:
            provider: 当前 Embedding Provider。
            contents: 保持顺序的待向量化文本。
            options: 批大小、重试次数、退避与请求间隔配置。

        返回:
            与输入顺序一致的向量列表。

        异常:
            asyncio.CancelledError: 批处理或等待被取消。
            RuntimeError: Provider 能力不足或重试耗尽。
        """

        if not contents:
            return []

        max_retries = int(options["max_retries"])
        retry_base_delay = float(options["retry_base_delay"])
        embedding_batch_size = int(options["embedding_batch_size"])
        request_delay = float(options["request_delay"])
        vectors: list[Any] = []
        adapter = EmbeddingProviderAdapter.from_provider(provider)

        for start in range(0, len(contents), embedding_batch_size):
            chunk = contents[start : start + embedding_batch_size]
            logger.debug(
                "Embedding 子请求: "
                f"offset={start}, size={len(chunk)}, total={len(contents)}"
            )
            vectors.extend(
                await self._embed_request_with_retry(
                    adapter,
                    chunk,
                    max_retries=max_retries,
                    retry_base_delay=retry_base_delay,
                )
            )
            if request_delay > 0 and start + embedding_batch_size < len(contents):
                await asyncio.sleep(request_delay)

        return vectors

    async def _embed_request_with_retry(
        self,
        provider: Any,
        contents: list[str],
        *,
        max_retries: int,
        retry_base_delay: float,
    ) -> list[Any]:
        """执行单个 Embedding 子请求并按现有策略重试普通失败。

        参数:
            provider: Embedding Provider 或已冻结的 adapter。
            contents: 当前子请求的文本列表。
            max_retries: 最大调用次数。
            retry_base_delay: 指数退避的基础秒数。

        返回:
            已验证数量、维度和有限性的向量列表。

        异常:
            asyncio.CancelledError: Provider 调用或退避被取消。
            RuntimeError: 重试耗尽。
        """

        last_error: Exception | None = None
        adapter = (
            provider
            if isinstance(provider, EmbeddingProviderAdapter)
            else EmbeddingProviderAdapter.from_provider(provider)
        )

        for attempt in range(max_retries):
            try:
                return await adapter.embed(contents)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt >= max_retries - 1:
                    break
                wait_seconds = retry_base_delay * (2**attempt)
                if self._is_rate_limit_error(e):
                    wait_seconds = max(wait_seconds, self.RATE_LIMIT_RETRY_MIN_DELAY)
                logger.warning(
                    f"Embedding 批次失败，{wait_seconds:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries})，异常类型={e.__class__.__name__}"
                )
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

        raise RuntimeError("Embedding 批次重试失败") from last_error
