"""LLM 调用客户端"""

import asyncio
import random
from typing import Any

from astrbot.api import logger


class LLMClient:
    """动态解析 LLM Provider + 带指数退避的调用"""

    def __init__(self, context=None, llm_provider: Any = None):
        self.context = context
        self._llm_provider = llm_provider

    def get_current_llm_provider(self):
        """动态解析 LLM Provider 以避免持有过期引用"""
        if not self.context:
            if self._llm_provider is not None and not isinstance(
                self._llm_provider, str
            ):
                return self._llm_provider
            return None

        if self._llm_provider is not None and not isinstance(self._llm_provider, str):
            return self._llm_provider

        if isinstance(self._llm_provider, str) and self._llm_provider:
            try:
                provider = self.context.get_provider_by_id(self._llm_provider)
                if provider:
                    return provider
            except Exception as e:
                logger.debug(f"按 ID 获取 LLM Provider 失败: {e}")

        try:
            provider = self.context.get_using_provider()
            if provider:
                return provider
        except Exception as e:
            logger.debug(f"获取默认 LLM Provider 失败: {e}")

        return None

    async def call_llm_with_retry(
        self, prompt: str, system_prompt: str, max_retries: int = 3
    ) -> str:
        last_error = None
        for attempt in range(max_retries):
            try:
                provider = self.get_current_llm_provider()
                if not provider:
                    raise RuntimeError("LLM Provider 不可用")
                response = await provider.text_chat(
                    prompt=prompt, system_prompt=system_prompt
                )
                return response.completion_text
            except Exception as e:
                last_error = e
                if attempt == max_retries - 1:
                    raise
                wait_time = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"[MemoryProcessor] LLM 调用失败，{wait_time:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries}): {e}"
                )
                await asyncio.sleep(wait_time)
        if last_error:
            raise last_error
        raise RuntimeError("LLM 调用失败，未捕获到具体异常")
