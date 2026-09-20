"""按 scope key 串行化的引用计数锁注册表。

锁条目在最后一个持有者（含尚在等待的调用方）退出后立即回收，避免长驻进程为
每个 scope 累积永不释放的 ``asyncio.Lock``。登记与回收都是无 ``await`` 的同步
字典操作，取消只能落在 ``await entry.lock.acquire()`` 或持有区间内，因此等待者
计数不会被破坏，也不会出现「已删除条目仍被等待」的窗口。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager


class _ScopeLockEntry:
    """单个 scope 的锁与已登记引用数。"""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


class ScopeLockRegistry:
    """按 key 串行化异步副作用，并在无引用时回收锁条目。"""

    def __init__(self) -> None:
        self._entries: dict[str, _ScopeLockEntry] = {}

    @property
    def size(self) -> int:
        """返回当前保留的锁条目数；全部调用退出后应为 0。"""

        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncGenerator[None, None]:
        """登记引用并持有 key 的锁；等待期取消同样归还引用。"""

        entry = self._entries.get(key)
        if entry is None:
            entry = _ScopeLockEntry()
            self._entries[key] = entry
        entry.users += 1
        try:
            await entry.lock.acquire()
        except BaseException:
            # 尚未持有锁：只归还引用，绝不释放不属于本次调用的锁。
            self._release(key, entry, held=False)
            raise
        try:
            yield
        finally:
            self._release(key, entry, held=True)

    def _release(self, key: str, entry: _ScopeLockEntry, *, held: bool) -> None:
        """释放锁并在计数归零时删除条目；本方法不引入 ``await``。"""

        if held:
            entry.lock.release()
        entry.users -= 1
        if (
            entry.users <= 0
            and not entry.lock.locked()
            and self._entries.get(key) is entry
        ):
            del self._entries[key]


__all__ = ["ScopeLockRegistry"]
