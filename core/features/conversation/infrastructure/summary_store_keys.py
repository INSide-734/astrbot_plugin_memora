"""总结候选 slot 的安装级不透明密钥。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import wraps
from typing import Any, Awaitable, Callable, cast


async def ensure_summary_secret(connection: Any) -> bytes:
    """读取或原子创建当前安装的候选 slot HMAC 密钥。"""
    cursor = await connection.execute(
        "SELECT meta_value FROM summary_store_meta WHERE meta_key='slot_hmac_secret'"
    )
    row = await cursor.fetchone()
    if row is not None:
        return str(row[0]).encode("ascii")
    secret = secrets.token_hex(32)
    await connection.execute(
        "INSERT INTO summary_store_meta(meta_key, meta_value) VALUES ('slot_hmac_secret', ?)"
        "ON CONFLICT(meta_key) DO NOTHING",
        (secret,),
    )
    cursor = await connection.execute(
        "SELECT meta_value FROM summary_store_meta WHERE meta_key='slot_hmac_secret'"
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("summary_slot_secret_unavailable")
    return str(row[0]).encode("ascii")


async def owned_slot_key(
    connection: Any, job_id: str, slot: int, content_digest: str
) -> str:
    """根据安装密钥生成不可离线推导的候选 slot key。"""
    secret = await ensure_summary_secret(connection)
    payload = f"{job_id}:{int(slot)}:{content_digest}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def source_guarded(
    method: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """在隔离候选写入与来源删除之间复用进程内协调锁。"""

    @wraps(method)
    async def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        quarantine = getattr(self, "quarantine_store", None)
        guard: Any = getattr(quarantine, "source_guard", None)
        if callable(guard):
            async with cast(Any, guard()):
                return await method(self, *args, **kwargs)
        return await method(self, *args, **kwargs)

    return guarded


__all__ = ["ensure_summary_secret", "owned_slot_key", "source_guarded"]
