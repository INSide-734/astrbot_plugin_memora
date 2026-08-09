"""协议身份运行时的组合根失败清理。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from ...identity.runtime import ProtocolIdentityRuntime


async def close_identity_runtime_after_failure(
    runtime: ProtocolIdentityRuntime | None,
) -> None:
    """初始化发布失败后关闭已转交给组合根的身份运行时。

    参数:
        runtime: 组件工厂已经创建并返回的唯一身份运行时。

    返回:
        无返回值；运行时缺失时直接结束。

    异常:
        asyncio.CancelledError: 关闭过程被取消时继续传播取消信号。
    """

    if runtime is None:
        return
    try:
        await runtime.close()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("初始化失败后关闭协议身份运行时失败", exc_info=True)


__all__ = ["close_identity_runtime_after_failure"]
