"""LLM 调用客户端"""

import asyncio
import random
from typing import Any

from astrbot.api import logger

from ..adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
)
from ..provider_adapters import LLMProviderAdapter


class LLMClient:
    """动态解析 LLM Provider + 带指数退避的调用"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.LLM_CLIENT,
        caller_enforced=frozenset(
            {
                AdapterCapability.TEXT_GENERATION,
                AdapterCapability.RETRY,
                AdapterCapability.CANCELLATION,
            }
        ),
    )

    def __init__(self, context=None, llm_provider: Any = None):
        """初始化 Provider 来源和按实例缓存的冻结 adapter。

        参数:
            context: AstrBot 上下文；未提供时只使用显式 Provider。
            llm_provider: Provider 实例或需要动态解析的 Provider ID。
        """

        self.context = context
        self._llm_provider = llm_provider
        self._provider_adapter_source: Any = None
        self._provider_adapter: LLMProviderAdapter | None = None

    def get_current_llm_provider(self):
        """动态解析当前 LLM Provider；不可用时返回 ``None``。"""
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
            except Exception as exc:
                logger.debug(
                    f"按 ID 获取 LLM Provider 失败，异常类型={exc.__class__.__name__}"
                )

        try:
            provider = self.context.get_using_provider()
            if provider:
                return provider
        except Exception as exc:
            logger.debug(
                f"获取默认 LLM Provider 失败，异常类型={exc.__class__.__name__}"
            )

        return None

    def get_current_llm_adapter(self) -> LLMProviderAdapter | None:
        """返回当前 Provider 的冻结 adapter；Provider 不可用时返回 ``None``。

        异常:
            UnsupportedAdapterCapability: 当前 Provider 缺少文本生成入口。
        """

        provider = self.get_current_llm_provider()
        if provider is None:
            self._provider_adapter_source = None
            self._provider_adapter = None
            return None
        if (
            provider is self._provider_adapter_source
            and self._provider_adapter is not None
        ):
            return self._provider_adapter
        adapter = LLMProviderAdapter.from_provider(provider)
        self._provider_adapter_source = provider
        self._provider_adapter = adapter
        return adapter

    async def call_llm_with_retry(
        self, prompt: str, system_prompt: str, max_retries: int = 3
    ) -> str:
        """调用当前 Provider，并对普通失败执行有界指数退避。

        参数:
            prompt: 发送给 Provider 的用户提示。
            system_prompt: 发送给 Provider 的系统约束。
            max_retries: 最大调用次数。

        返回:
            Provider 返回的文本内容。

        异常:
            asyncio.CancelledError: 调用或退避被取消。
            Exception: 最后一次 Provider 调用失败，或 Provider 不可用。
        """

        last_error = None
        for attempt in range(max_retries):
            try:
                adapter = self.get_current_llm_adapter()
                if adapter is None:
                    raise RuntimeError("LLM Provider 不可用")
                return await adapter.generate(prompt, system_prompt)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt == max_retries - 1:
                    raise
                wait_time = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"[MemoryProcessor] LLM 调用失败，{wait_time:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries})，异常类型={e.__class__.__name__}"
                )
                await asyncio.sleep(wait_time)
        if last_error:
            raise last_error
        raise RuntimeError("LLM 调用失败，未捕获到具体异常")
