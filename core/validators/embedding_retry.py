"""具备速率限制感知的 Embedding 重试逻辑。"""

import asyncio
from typing import Any

from astrbot.api import logger


class EmbeddingRetryMixin:
    """Embedding API 调用的重试逻辑，支持指数退避与速率限制检测。"""

    async def _embed_batch_with_retry(
        self,
        provider: Any,
        contents: list[str],
        options: dict[str, Any],
    ) -> list[Any]:
        if not contents:
            return []

        max_retries = int(options["max_retries"])
        retry_base_delay = float(options["retry_base_delay"])
        embedding_batch_size = int(options["embedding_batch_size"])
        request_delay = float(options["request_delay"])
        vectors: list[Any] = []

        for start in range(0, len(contents), embedding_batch_size):
            chunk = contents[start : start + embedding_batch_size]
            logger.debug(
                "Embedding 子请求: "
                f"offset={start}, size={len(chunk)}, total={len(contents)}"
            )
            vectors.extend(
                await self._embed_request_with_retry(
                    provider,
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
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                get_embeddings = getattr(provider, "get_embeddings", None)
                if callable(get_embeddings):
                    return await get_embeddings(contents)

                if hasattr(provider, "get_embeddings_batch"):
                    try:
                        return await provider.get_embeddings_batch(
                            contents,
                            batch_size=len(contents),
                            tasks_limit=1,
                            max_retries=1,
                        )
                    except TypeError:
                        return await provider.get_embeddings_batch(contents)

                vectors = []
                for content in contents:
                    vectors.append(await provider.get_embedding(content))
                return vectors
            except Exception as e:
                last_error = e
                if attempt >= max_retries - 1:
                    break
                wait_seconds = retry_base_delay * (2**attempt)
                if self._is_rate_limit_error(e):
                    wait_seconds = max(wait_seconds, self.RATE_LIMIT_RETRY_MIN_DELAY)
                logger.warning(
                    f"Embedding 批次失败，{wait_seconds:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries}): {e}"
                )
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

        raise RuntimeError(f"Embedding 批次重试失败: {last_error}") from last_error
