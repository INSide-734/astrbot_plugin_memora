"""Embedding 并发合并代理的能力与生命周期测试。"""

from __future__ import annotations

import asyncio

import pytest

from core.retrieval.embedding_singleflight import InFlightEmbeddingProviderProxy


class _SingleProvider:
    """只暴露逐条 Embedding 能力的测试 Provider。"""

    def __init__(self) -> None:
        """初始化调用计数。"""

        self.calls = 0

    async def get_embedding(self, content: str) -> list[float]:
        """返回与输入长度相关的固定向量。"""

        self.calls += 1
        return [float(len(content))]

    def get_dim(self) -> int:
        """返回测试向量维度。"""

        return 1


def test_proxy_preserves_original_embedding_capability_surface() -> None:
    """代理不得让原 Provider 不存在的 batch 方法变成可见能力。"""

    provider = _SingleProvider()
    proxy = InFlightEmbeddingProviderProxy(provider)

    assert callable(proxy.get_embedding)
    assert not hasattr(proxy, "get_embeddings")
    assert not hasattr(proxy, "get_embeddings_batch")
    assert proxy.get_dim() == 1


@pytest.mark.asyncio
async def test_concurrent_identical_calls_share_one_provider_task() -> None:
    """并发相同输入只调用一次底层 Provider。"""

    started = asyncio.Event()
    release = asyncio.Event()

    class Provider(_SingleProvider):
        """用事件屏障控制底层调用完成时机。"""

        async def get_embedding(self, content: str) -> list[float]:
            """登记调用并等待统一释放。"""

            self.calls += 1
            started.set()
            await release.wait()
            return [float(len(content))]

    provider = Provider()
    proxy = InFlightEmbeddingProviderProxy(provider)
    first = asyncio.create_task(proxy.get_embedding("same"))
    second = asyncio.create_task(proxy.get_embedding("same"))
    await started.wait()
    release.set()

    assert await asyncio.gather(first, second) == [[4.0], [4.0]]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_sequential_identical_calls_do_not_cache_results() -> None:
    """前一次调用完成后相同输入必须重新请求 Provider。"""

    provider = _SingleProvider()
    proxy = InFlightEmbeddingProviderProxy(provider)

    assert await proxy.get_embedding("same") == [4.0]
    assert await proxy.get_embedding("same") == [4.0]
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_failed_flight_is_removed_and_can_retry() -> None:
    """底层失败后移除飞行记录，后续相同输入能够重试。"""

    class Provider(_SingleProvider):
        """首次失败、第二次成功的测试 Provider。"""

        async def get_embedding(self, content: str) -> list[float]:
            """按调用次数返回失败或成功。"""

            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider failed")
            return [float(len(content))]

    provider = Provider()
    proxy = InFlightEmbeddingProviderProxy(provider)

    with pytest.raises(RuntimeError, match="provider failed"):
        await proxy.get_embedding("retry")
    assert await proxy.get_embedding("retry") == [5.0]
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_one_waiter_cancellation_does_not_cancel_shared_provider_task() -> None:
    """一个等待者取消时，仍有等待者的共享 Provider 任务继续运行。"""

    started = asyncio.Event()
    release = asyncio.Event()

    class Provider(_SingleProvider):
        """等待显式释放的测试 Provider。"""

        async def get_embedding(self, content: str) -> list[float]:
            """阻塞底层调用，便于取消其中一个等待者。"""

            self.calls += 1
            started.set()
            await release.wait()
            return [float(len(content))]

    provider = Provider()
    proxy = InFlightEmbeddingProviderProxy(provider)
    cancelled_waiter = asyncio.create_task(proxy.get_embedding("same"))
    surviving_waiter = asyncio.create_task(proxy.get_embedding("same"))
    await started.wait()
    cancelled_waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    release.set()
    assert await surviving_waiter == [4.0]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_last_waiter_cancellation_collects_provider_task() -> None:
    """最后一个等待者取消时取消并收束底层 Provider 任务。"""

    started = asyncio.Event()
    collected = asyncio.Event()

    class Provider(_SingleProvider):
        """用 finally 暴露底层任务已收束的测试 Provider。"""

        async def get_embedding(self, _content: str) -> list[float]:
            """持续等待，直到代理取消底层任务。"""

            self.calls += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                collected.set()

    provider = Provider()
    proxy = InFlightEmbeddingProviderProxy(provider)
    waiter = asyncio.create_task(proxy.get_embedding("same"))
    await started.wait()
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert collected.is_set()


@pytest.mark.asyncio
async def test_native_batch_calls_are_coalesced_without_changing_signature() -> None:
    """原生 batch 能力按完整输入批次合并，并保留调用返回形状。"""

    class Provider:
        """只暴露原生 batch 能力的测试 Provider。"""

        def __init__(self) -> None:
            """初始化调用计数。"""

            self.calls = 0

        async def get_embeddings(self, contents: list[str]) -> list[list[float]]:
            """为每个输入返回长度向量。"""

            self.calls += 1
            return [[float(len(content))] for content in contents]

    provider = Provider()
    proxy = InFlightEmbeddingProviderProxy(provider)

    assert await asyncio.gather(
        proxy.get_embeddings(["a", "bb"]),
        proxy.get_embeddings(["a", "bb"]),
    ) == [[[1.0], [2.0]], [[1.0], [2.0]]]
    assert provider.calls == 1
    assert not hasattr(proxy, "get_embedding")
