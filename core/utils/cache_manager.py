"""
集中缓存管理 — 9 命名空间 TTL/LRU 缓存 + 命中率监控

设计原则：
- 优先使用 cachetools (TTLCache / LRUCache)
- cachetools 不可用时降级为纯 OrderedDict 实现
- 全局单例模式，提供同步/异步装饰器
- 惰性过期检查 + LRU 淘汰
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import OrderedDict, defaultdict
from collections.abc import MutableMapping
from typing import Any, Callable, TypeVar

from astrbot.api import logger

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# 纯 OrderedDict 降级实现
# ---------------------------------------------------------------------------


class _TTLCache(MutableMapping):
    """纯 OrderedDict 实现的 TTL 缓存。

    - 访问时惰性扫描过期条目
    - 超过 maxsize 时按照 LRU 淘汰 (popitem last=False)
    """

    def __init__(self, maxsize: int = 1000, ttl: float = 300) -> None:
        self._data: OrderedDict = OrderedDict()
        self._expiry: dict[str, float] = {}
        self.maxsize = maxsize
        self.ttl = ttl

    def _evict_expired(self) -> None:
        """惰性扫描：逐个检查 OrderedDict 中最旧的条目是否过期。"""
        now = time.monotonic()
        expired_keys: list[str] = []
        for key in self._data:
            if self._expiry.get(key, 0) < now:
                expired_keys.append(key)
            else:
                # OrderedDict 保持插入序，但无关过期序；这里只扫描前半段
                if len(expired_keys) > 10:
                    break

        for k in expired_keys:
            del self._data[k]
            self._expiry.pop(k, None)

    def _ensure_capacity(self) -> None:
        """超过 maxsize 时淘汰最旧条目 (LRU)。"""
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __getitem__(self, key: str) -> Any:
        self._evict_expired()
        value = self._data[key]
        # 移到末尾 → 更新 LRU 顺序
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._expiry[key] = time.monotonic() + self.ttl
        self._data.move_to_end(key)
        self._ensure_capacity()

    def __delitem__(self, key: str) -> None:
        del self._data[key]
        self._expiry.pop(key, None)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        self._evict_expired()
        return key in self._data

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._data)

    def __iter__(self):
        self._evict_expired()
        return iter(self._data)


class _LRUCache(MutableMapping):
    """纯 OrderedDict 实现的 LRU 缓存 (无 TTL)。"""

    def __init__(self, maxsize: int = 1000) -> None:
        self._data: OrderedDict = OrderedDict()
        self.maxsize = maxsize

    def _ensure_capacity(self) -> None:
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __getitem__(self, key: str) -> Any:
        value = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        self._ensure_capacity()

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)


# ---------------------------------------------------------------------------
# CacheManager 单例
# ---------------------------------------------------------------------------


class CacheManager:
    """集中缓存管理器。

    提供命名空间缓存创建、命中率监控、同步/异步装饰器。

    用法::

        cm = get_cache_manager()
        cache = cm.get_cache("llm_responses", maxsize=500, ttl=300)
        cache["key"] = value

        @cm.cached(ttl=60, key_func=lambda a, b: f"{a}:{b}")
        def expensive(a, b): ...

        hit_rates = cm.get_hit_rates()
    """

    def __init__(self) -> None:
        self._caches: dict[str, MutableMapping] = {}
        self._hits: defaultdict[str, int] = defaultdict(int)
        self._misses: defaultdict[str, int] = defaultdict(int)

    def get_cache(self, name: str, maxsize: int = 1000, ttl: float | None = None) -> MutableMapping:
        """获取或创建命名缓存。

        Args:
            name: 缓存命名空间 (e.g. "llm_responses")。
            maxsize: 最大条目数。
            ttl: TTL 秒数。None 表示纯 LRU 无 TTL。

        Returns:
            MutableMapping 缓存实例。
        """
        if name not in self._caches:
            try:
                from cachetools import LRUCache, TTLCache

                if ttl is not None:
                    self._caches[name] = TTLCache(maxsize=maxsize, ttl=ttl)  # type: ignore[assignment]
                else:
                    self._caches[name] = LRUCache(maxsize=maxsize)  # type: ignore[assignment]
                logger.debug(f"CacheManager: 使用 cachetools 创建缓存 '{name}'")
            except ImportError:
                if ttl is not None:
                    self._caches[name] = _TTLCache(maxsize=maxsize, ttl=ttl)
                else:
                    self._caches[name] = _LRUCache(maxsize=maxsize)
                logger.debug(f"CacheManager: 降级为 OrderedDict 创建缓存 '{name}'")
        return self._caches[name]

    def _record_hit(self, name: str) -> None:
        self._hits[name] += 1

    def _record_miss(self, name: str) -> None:
        self._misses[name] += 1

    def get_hit_rates(self) -> dict[str, float]:
        """返回各缓存命中率。

        Returns:
            {cache_name: hit_rate}，未命中过的缓存返回 1.0。
        """
        rates: dict[str, float] = {}
        all_names = set(self._hits.keys()) | set(self._misses.keys())
        for name in all_names:
            total = self._hits[name] + self._misses[name]
            rates[name] = self._hits[name] / total if total > 0 else 1.0
        return rates

    def cached(
        self,
        ttl: float | None = None,
        maxsize: int = 1000,
        key_func: Callable[..., str] | None = None,
    ) -> Callable[[F], F]:
        """同步函数缓存装饰器。

        Args:
            ttl: 缓存 TTL 秒数。None 表示 LRU 无过期。
            maxsize: 缓存最大条目。
            key_func: 自定义缓存键生成函数，接收与装饰函数相同的参数。

        Returns:
            装饰器函数。
        """

        def decorator(func: F) -> F:
            cache_name = f"cached_{func.__module__}.{func.__qualname__}"
            cache = self.get_cache(cache_name, maxsize=maxsize, ttl=ttl)

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                cache_key = (
                    key_func(*args, **kwargs)
                    if key_func
                    else _default_key(args, kwargs)
                )
                if cache_key in cache:
                    self._record_hit(cache_name)
                    return cache[cache_key]

                self._record_miss(cache_name)
                result = func(*args, **kwargs)
                cache[cache_key] = result
                return result

            return wrapper  # type: ignore[return-value]

        return decorator

    def async_cached(
        self,
        ttl: float | None = None,
        maxsize: int = 1000,
        key_func: Callable[..., str] | None = None,
    ) -> Callable[[F], F]:
        """异步函数缓存装饰器。

        Args:
            ttl: 缓存 TTL 秒数。None 表示 LRU 无过期。
            maxsize: 缓存最大条目。
            key_func: 自定义缓存键生成函数，接收与装饰函数相同的参数。

        Returns:
            装饰器函数。
        """

        def decorator(func: F) -> F:
            cache_name = f"async_cached_{func.__module__}.{func.__qualname__}"
            cache = self.get_cache(cache_name, maxsize=maxsize, ttl=ttl)

            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                cache_key = (
                    key_func(*args, **kwargs)
                    if key_func
                    else _default_key(args, kwargs)
                )
                if cache_key in cache:
                    self._record_hit(cache_name)
                    return cache[cache_key]

                self._record_miss(cache_name)
                result = await func(*args, **kwargs)
                cache[cache_key] = result
                return result

            return wrapper  # type: ignore[return-value]

        return decorator


def _default_key(args: tuple, kwargs: dict) -> str:
    """默认缓存键生成：hash(args) + sorted kwargs。"""
    return str(hash((args, tuple(sorted(kwargs.items())))))


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_cache_manager: CacheManager | None = None


def get_cache_manager() -> CacheManager:
    """获取 CacheManager 全局单例。

    Returns:
        CacheManager 实例。
    """
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


__all__ = [
    "CacheManager",
    "get_cache_manager",
    "_TTLCache",
    "_LRUCache",
]
