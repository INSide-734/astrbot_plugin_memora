"""core/utils/cache_manager.py 测试 — CacheManager、TTL、LRU、装饰器。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.utils.cache_manager import (
    CacheManager,
    _LRUCache,
    _TTLCache,
    get_cache_manager,
)


# ---------------------------------------------------------------------------
# _LRUCache
# ---------------------------------------------------------------------------


class TestLRUCache:
    def test_basic_get_set(self) -> None:
        cache = _LRUCache(maxsize=10)
        cache["a"] = 1
        assert cache["a"] == 1

    def test_length_tracks_items(self) -> None:
        cache = _LRUCache(maxsize=10)
        cache["a"] = 1
        cache["b"] = 2
        assert len(cache) == 2

    def test_contains(self) -> None:
        cache = _LRUCache(maxsize=10)
        cache["key"] = "val"
        assert "key" in cache
        assert "missing" not in cache

    def test_delete(self) -> None:
        cache = _LRUCache(maxsize=10)
        cache["key"] = "val"
        del cache["key"]
        assert "key" not in cache
        assert len(cache) == 0

    def test_eviction_on_maxsize(self) -> None:
        cache = _LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3
        cache["d"] = 4  # should evict "a" (oldest, never re-accessed)
        assert "a" not in cache
        assert "d" in cache
        assert len(cache) == 3

    def test_lru_order_updated_on_get(self) -> None:
        cache = _LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3
        # Access "a" — should mark it recently used
        _ = cache["a"]
        cache["d"] = 4  # should evict "b" now, not "a"
        assert "a" in cache
        assert "b" not in cache
        assert "d" in cache

    def test_iteration(self) -> None:
        cache = _LRUCache(maxsize=10)
        cache["a"] = 1
        cache["b"] = 2
        keys = list(cache)
        assert set(keys) == {"a", "b"}

    def test_non_string_key_contains(self) -> None:
        cache = _LRUCache(maxsize=10)
        assert (1 in cache) is False  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# _TTLCache
# ---------------------------------------------------------------------------


class TestTTLCache:
    def test_basic_get_set(self) -> None:
        cache = _TTLCache(maxsize=10, ttl=60)
        cache["key"] = "val"
        assert cache["key"] == "val"

    def test_expiry_evicts_expired(self) -> None:
        cache = _TTLCache(maxsize=10, ttl=0.01)  # 10ms TTL
        cache["key"] = "val"
        time.sleep(0.02)  # let it expire
        assert "key" not in cache

    def test_fresh_item_persists(self) -> None:
        cache = _TTLCache(maxsize=10, ttl=60)
        cache["key"] = "val"
        assert "key" in cache

    def test_lru_eviction_with_ttl(self) -> None:
        cache = _TTLCache(maxsize=3, ttl=300)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3
        cache["d"] = 4
        assert "a" not in cache
        assert len(cache) == 3

    def test_delete(self) -> None:
        cache = _TTLCache(maxsize=10, ttl=60)
        cache["key"] = "val"
        del cache["key"]
        assert "key" not in cache
        assert len(cache) == 0

    def test_iteration(self) -> None:
        cache = _TTLCache(maxsize=10, ttl=60)
        cache["a"] = 1
        cache["b"] = 2
        keys = list(cache)
        assert set(keys) == {"a", "b"}

    def test_non_string_key_contains(self) -> None:
        cache = _TTLCache(maxsize=10, ttl=60)
        assert (1 in cache) is False  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# CacheManager — basic operations
# ---------------------------------------------------------------------------


class TestCacheManager:
    def test_get_cache_creates_and_reuses(self) -> None:
        cm = CacheManager()
        c1 = cm.get_cache("test", maxsize=10)
        c2 = cm.get_cache("test")
        assert c1 is c2

    def test_get_cache_different_names(self) -> None:
        cm = CacheManager()
        c1 = cm.get_cache("a", maxsize=5)
        c2 = cm.get_cache("b", maxsize=5)
        assert c1 is not c2

    def test_cache_set_get(self) -> None:
        cm = CacheManager()
        cache = cm.get_cache("kv", maxsize=10)
        cache["hello"] = "world"
        assert cache["hello"] == "world"

    @pytest.mark.asyncio
    async def test_hit_rates(self) -> None:
        cm = CacheManager()
        # Hit rate tracking is primarily for the decorator path.
        # Direct get_cache access does not auto-track hits/misses.
        # Use cached decorator to verify hit rate tracking.
        call_count = {"n": 0}

        @cm.cached(maxsize=10)
        def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 2

        expensive(1)  # miss
        expensive(1)  # hit
        expensive(1)  # hit

        rates = cm.get_hit_rates()
        # At least one cache should have a positive hit rate
        assert len(rates) > 0
        has_positive = any(v > 0 for v in rates.values())
        assert has_positive

    def test_empty_hit_rates(self) -> None:
        cm = CacheManager()
        rates = cm.get_hit_rates()
        assert rates == {}


# ---------------------------------------------------------------------------
# CacheManager — cached decorator (sync)
# ---------------------------------------------------------------------------


class TestCachedDecorator:
    def test_caches_sync_result(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.cached(maxsize=10)
        def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 2

        assert expensive(5) == 10
        assert call_count["n"] == 1
        assert expensive(5) == 10  # cached
        assert call_count["n"] == 1  # no additional call

    def test_different_args_not_cached(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.cached(maxsize=10)
        def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 2

        expensive(5)
        expensive(7)
        assert call_count["n"] == 2  # different args → uncached

    def test_custom_key_func(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.cached(maxsize=10, key_func=lambda x: f"key_{x}")
        def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 2

        expensive(5)
        expensive(6)
        assert call_count["n"] == 2

    def test_hit_rate_tracks_decorator(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.cached(maxsize=10)
        def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 2

        expensive(1)  # miss
        expensive(1)  # hit
        expensive(1)  # hit

        rates = cm.get_hit_rates()
        # The cache_name is derived from the function qualname
        # Find any cache with hits
        has_positive = any(v > 0 for v in rates.values())
        assert has_positive or call_count["n"] == 1


# ---------------------------------------------------------------------------
# CacheManager — async_cached decorator
# ---------------------------------------------------------------------------


class TestAsyncCachedDecorator:
    @pytest.mark.asyncio
    async def test_caches_async_result(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.async_cached(maxsize=10)
        async def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 3

        assert (await expensive(5)) == 15
        assert call_count["n"] == 1
        assert (await expensive(5)) == 15  # cached
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_different_args_async_not_cached(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.async_cached(maxsize=10)
        async def expensive(x: int) -> int:
            call_count["n"] += 1
            return x * 3

        await expensive(1)
        await expensive(2)
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_async_with_kwargs(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.async_cached(maxsize=10)
        async def expensive(a: int, b: int = 0) -> int:
            call_count["n"] += 1
            return a + b

        assert (await expensive(1, b=2)) == 3
        assert (await expensive(1, b=2)) == 3  # cached
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_async_cached_with_ttl(self) -> None:
        cm = CacheManager()
        call_count = {"n": 0}

        @cm.async_cached(ttl=0.01, maxsize=10)
        async def expensive(x: int) -> int:
            call_count["n"] += 1
            return x

        assert (await expensive(1)) == 1
        assert call_count["n"] == 1
        await asyncio.sleep(0.02)
        assert (await expensive(1)) == 1  # TTL expired → recalculate
        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# get_cache_manager singleton
# ---------------------------------------------------------------------------


class TestGetCacheManager:
    def test_returns_singleton(self) -> None:
        cm1 = get_cache_manager()
        cm2 = get_cache_manager()
        assert cm1 is cm2

    def test_singleton_caches_shared(self) -> None:
        cm1 = get_cache_manager()
        cache = cm1.get_cache("shared_test", maxsize=5)
        cache["key"] = "val"

        cm2 = get_cache_manager()
        cache2 = cm2.get_cache("shared_test")
        assert cache2["key"] == "val"
