"""
数据辅助工具模块
提供元数据解析/序列化、时间戳验证、重试机制和操作上下文管理。
"""

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger


def safe_parse_metadata(metadata_raw: Any) -> dict[str, Any]:
    """
    安全解析元数据，统一处理字符串和字典类型。

    参数：
        metadata_raw: 原始元数据，可能是字符串或字典。

    返回：
        解析后的元数据字典；解析失败时返回空字典。
    """
    if isinstance(metadata_raw, dict):
        return metadata_raw
    elif isinstance(metadata_raw, str):
        try:
            return json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"解析元数据 JSON 失败: {e}, 原始数据: {metadata_raw}")
            return {}
    else:
        logger.warning(f"不支持的元数据类型: {type(metadata_raw)}")
        return {}


def safe_serialize_metadata(metadata: dict[str, Any]) -> str:
    """
    安全序列化元数据为JSON字符串。

    参数：
        metadata: 元数据字典。

    返回：
        JSON 字符串。
    """
    try:
        return json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.error(f"序列化元数据失败: {e}, 数据: {metadata}")
        return "{}"


def validate_timestamp(timestamp: Any, default_time: float | None = None) -> float:
    """
    验证和标准化时间戳。

    参数：
        timestamp: 时间戳，可能是字符串、数字或其他类型。
        default_time: 默认时间；若为 None 则使用当前时间。

    返回：
        标准化后的时间戳。
    """
    if default_time is None:
        default_time = time.time()

    if isinstance(timestamp, (int, float)):
        return float(timestamp)
    elif isinstance(timestamp, str):
        try:
            return float(timestamp)
        except (ValueError, TypeError):
            logger.warning(f"无法解析时间戳字符串: {timestamp}")
            return default_time
    elif hasattr(timestamp, "timestamp"):  # datetime 对象
        try:
            return timestamp.timestamp()
        except Exception as e:
            logger.warning(f"无法从 datetime 对象获取时间戳: {e}")
            return default_time
    else:
        logger.warning(f"不支持的时间戳类型: {type(timestamp)}")
        return default_time


async def retry_on_failure(
    func,
    *args,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    exceptions: tuple = (Exception,),
    **kwargs,
):
    """
    带重试机制的函数执行器。

    参数：
        func: 要执行的函数。
        *args: 函数位置参数。
        max_retries: 最大重试次数。
        backoff_factor: 退避因子。
        exceptions: 需要重试的异常类型。
        **kwargs: 函数关键字参数。

    返回：
        函数执行结果。
    """
    last_exception: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                wait_time = backoff_factor * (2**attempt)
                logger.warning(
                    f"函数 {func.__name__} 执行失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}"
                )
                logger.info(f"等待 {wait_time:.2f} 秒后重试……")
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"函数 {func.__name__} 重试 {max_retries} 次后仍然失败: {e}"
                )

    # 所有重试都失败时，抛出最后一个异常
    if last_exception is not None:
        raise last_exception
    return None


class OperationContext:
    """操作上下文管理器，用于错误处理与资源清理。"""

    def __init__(self, operation_name: str, session_id: str | None = None):
        self.operation_name = operation_name
        self.session_id = session_id
        self.start_time = None

    async def __aenter__(self):
        self.start_time = time.time()
        session_info = f"[{self.session_id}] " if self.session_id else ""
        logger.debug(f"{session_info}开始执行操作: {self.operation_name}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time if self.start_time else 0
        session_info = f"[{self.session_id}] " if self.session_id else ""

        if exc_type is None:
            logger.debug(
                f"{session_info}操作成功完成: {self.operation_name} (耗时 {duration:.3f}s)"
            )
        else:
            logger.error(
                f"{session_info}操作失败: {self.operation_name} (耗时 {duration:.3f}s) - {exc_val}"
            )

        # 不抑制异常，让调用者处理
        return False
