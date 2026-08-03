"""执行自动反思候选的质量路由与限流持久化。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from .continuity_hooks import record_continuity_topics
from .reflection_storage_outcomes import (
    ReflectionStoreOutcome,
    ReflectionStoreResult,
)

_MAX_CONCURRENT_WRITES = 3


def build_reflection_idempotency_key(
    *,
    session_id: str,
    start_index: int,
    end_index: int,
    batch_index: int,
    memory_index: int,
    content: str,
) -> str:
    """为固定反思窗口中的候选生成稳定幂等键。

    Args:
        session_id: 候选来源会话标识。
        start_index: 来源窗口起始索引。
        end_index: 来源窗口结束索引（不包含）。
        batch_index: 候选所属反思批次索引。
        memory_index: 候选在合并结果中的索引。
        content: 候选正文，仅以 SHA-256 摘要参与键计算。

    Returns:
        不暴露候选正文的稳定 SHA-256 十六进制键。
    """

    content_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    raw = (
        f"{session_id}:{start_index}:{end_index}:"
        f"{batch_index}:{memory_index}:{content_hash}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def store_reflection_candidates(
    memories: list[dict[str, Any]],
    *,
    completed_idempotency_keys: set[str],
    session_id: str,
    persona_id: str | None,
    start_index: int,
    end_index: int,
    is_group_chat: bool,
    memory_engine: Any,
    memory_quality_gate: Any | None,
    schedule_evolution_after_write: Callable[[int], Awaitable[None]],
) -> list[ReflectionStoreResult]:
    """并发执行候选质量门与写入，并返回与输入一一对应的终态。

    Args:
        memories: 当前窗口抽取出的候选列表。
        completed_idempotency_keys: 先前重试已完成的候选幂等键。
        session_id: 当前会话标识，仅用于持久化作用域和运行日志。
        persona_id: 候选关联的人格标识。
        start_index: 当前来源窗口起始下标。
        end_index: 当前来源窗口固定高水位。
        is_group_chat: 当前窗口是否来自群聊。
        memory_engine: canonical 记忆引擎。
        memory_quality_gate: 可选的候选质量路由器。
        schedule_evolution_after_write: canonical 写后的兼容演化调度回调。

    Returns:
        与候选顺序一致的互斥存储终态。取消会继续向上传播，普通失败转为
        ``FAILED``，canonical 成功后的普通派生处理失败不改变写入终态。
    """

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_WRITES)

    async def _store_one(memory: dict[str, Any]) -> ReflectionStoreResult:
        """在质量门后返回一条候选的单一持久化终态。"""

        metadata = memory.setdefault("metadata", {})
        idempotency_key = str(metadata.get("idempotency_key") or "")
        if idempotency_key in completed_idempotency_keys:
            return ReflectionStoreResult(
                ReflectionStoreOutcome.SKIPPED_IDEMPOTENT,
                idempotency_key,
            )
        source_window = {
            "session_id": session_id,
            "start_index": start_index,
            "end_index": end_index,
            "message_count": end_index - start_index,
        }
        metadata["source_window"] = source_window
        try:
            if memory_quality_gate is not None:
                gate_result = await memory_quality_gate.route_candidate(
                    memory,
                    session_id=session_id,
                    persona_id=persona_id,
                    source_window=source_window,
                    is_group_chat=is_group_chat,
                )
                if gate_result.action == "quarantined":
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.QUARANTINED,
                        idempotency_key,
                    )
            memory_id = await memory_engine.add_memory(
                content=memory["content"],
                session_id=session_id,
                persona_id=persona_id,
                importance=memory["importance"],
                metadata=metadata,
                atoms=memory.get("atoms", []),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "[%s] 记忆写入失败，异常类型=%s",
                session_id,
                error.__class__.__name__,
                exc_info=True,
            )
            return ReflectionStoreResult(ReflectionStoreOutcome.FAILED)

        try:
            record_continuity_topics(memory_engine, session_id, memory)
            await schedule_evolution_after_write(memory_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "[%s] canonical 写后处理失败，异常类型=%s",
                session_id,
                error.__class__.__name__,
                exc_info=True,
            )
        return ReflectionStoreResult(
            ReflectionStoreOutcome.CANONICAL,
            idempotency_key,
        )

    async def _store_with_semaphore(
        memory: dict[str, Any],
    ) -> ReflectionStoreResult:
        """在单窗口并发上限内执行一条候选写入。"""

        async with semaphore:
            return await _store_one(memory)

    gathered = await asyncio.gather(
        *[_store_with_semaphore(memory) for memory in memories],
        return_exceptions=True,
    )
    results: list[ReflectionStoreResult] = []
    for result in gathered:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.error(
                "[%s] 批量写入异常，异常类型=%s",
                session_id,
                result.__class__.__name__,
            )
            results.append(ReflectionStoreResult(ReflectionStoreOutcome.FAILED))
        else:
            results.append(result)
    return results


__all__ = ["build_reflection_idempotency_key", "store_reflection_candidates"]
