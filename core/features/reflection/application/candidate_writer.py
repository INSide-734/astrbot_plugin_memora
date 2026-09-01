"""执行自动反思候选的质量路由与限流持久化。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from astrbot.api import logger

from ....shared.summary_source_fence import SummarySourceFence
from ..domain.storage_outcomes import ReflectionStoreOutcome, ReflectionStoreResult
from .continuity import record_continuity_topics

_MAX_CONCURRENT_WRITES = 3


def build_reflection_idempotency_key(
    *,
    session_id: str,
    start_index: int,
    end_index: int,
    batch_index: int,
    memory_index: int,
    content: str,
    session_epoch: int = 0,
) -> str:
    """为固定反思窗口中的候选生成绑定 session epoch 的稳定幂等键。

    Args:
        session_id: 候选来源会话标识。
        start_index: 来源窗口起始索引。
        end_index: 来源窗口结束索引（不包含）。
        batch_index: 候选所属反思批次索引。
        memory_index: 候选在合并结果中的索引。
        content: 候选正文，仅以 SHA-256 摘要参与键计算。
        session_epoch: 候选所属会话 epoch；旧调用默认为中性 epoch 0。

    Returns:
        不暴露候选正文的稳定 SHA-256 十六进制键。
    """

    content_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    raw = (
        f"{session_id}:{session_epoch}:{start_index}:{end_index}:"
        f"{batch_index}:{memory_index}:{content_hash}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def store_reflection_candidates(
    memories: list[dict[str, Any]],
    *,
    completed_idempotency_keys: set[str] | Mapping[str, int],
    session_id: str,
    persona_id: str | None,
    start_index: int,
    end_index: int,
    is_group_chat: bool,
    group_id: str | None = None,
    scope_id: str | None = None,
    session_epoch: int = 0,
    source_digest: str | None = None,
    worker_generation: int | None = None,
    claim_fence: str | None = None,
    claim_token: str | None = None,
    job_id: str | None = None,
    gate_snapshot_json: str | None = None,
    before_side_effect: Callable[[], Awaitable[bool]] | None = None,
    run_claim_side_effect: Callable[
        [Callable[[], Awaitable[object]]], Awaitable[object]
    ]
    | None = None,
    memory_engine: Any,
    memory_quality_gate: Any | None,
    schedule_evolution_after_write: Callable[[int], Awaitable[None]],
) -> list[ReflectionStoreResult]:
    """并发执行候选质量门与写入，并返回与输入一一对应的终态。

    canonical 写入可由 Store 提供的 claim runner 包围；runner 不得持有
    ConversationStore 事务，只负责 epoch/source fence。
    Args:
        memories: 当前窗口抽取出的候选列表。
        completed_idempotency_keys: 先前重试已完成的候选幂等键；Mapping
            还可携带已经发现的 canonical ID。
        session_id: 当前会话标识，仅用于持久化作用域和运行日志。
        persona_id: 候选关联的人格标识。
        start_index: 当前来源窗口起始下标。
        end_index: 当前来源窗口固定高水位。
        is_group_chat: 当前窗口是否来自群聊。
        group_id: 群聊来源的群组标识，用于门禁 profile 绑定解析。
        scope_id: 当前窗口的安全作用域标识。
        session_epoch: claim 固化的会话 epoch。
        source_digest: 来源窗口的稳定摘要；提供时会写入候选元数据。
        worker_generation: claim 固化的 worker generation。
        claim_fence: 不透明 claim fence 摘要，不保存原始 claim token。
        gate_snapshot_json: 入队时固化的门禁配置 JSON。
        before_side_effect: canonical 或隔离副作用前的 claim fence 回调。
        run_claim_side_effect: Store 提供的 epoch/source fence runner。
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
        metadata_value = memory.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
        if (
            isinstance(session_epoch, bool)
            or not isinstance(session_epoch, int)
            or session_epoch < 0
        ):
            raise ValueError("session_epoch_invalid")
        normalized_source_digest: str | None = None
        if source_digest is not None:
            if not isinstance(source_digest, str) or not source_digest.strip():
                raise ValueError("source_digest_invalid")
            normalized_source_digest = source_digest.strip()
            metadata["source_digest"] = normalized_source_digest
        if session_epoch:
            metadata["source_epoch"] = session_epoch
        if worker_generation is not None:
            if (
                isinstance(worker_generation, bool)
                or not isinstance(worker_generation, int)
                or worker_generation <= 0
            ):
                raise ValueError("worker_generation_invalid")
            metadata["source_fence_generation"] = worker_generation
        if claim_fence:
            metadata["source_fence"] = str(claim_fence)
        memory["metadata"] = metadata
        idempotency_key = str(metadata.get("idempotency_key") or "")

        async def _find_owner() -> int | None:
            """按幂等键读取现有 canonical owner；异常保持失败语义。"""
            finder = getattr(memory_engine, "find_memory_id_by_idempotency_key", None)
            if not callable(finder) or not idempotency_key:
                return None
            finder_call = cast(Callable[[str], Awaitable[int | None]], finder)
            owner = await finder_call(idempotency_key)
            if owner is None:
                return None
            if isinstance(owner, bool) or not isinstance(owner, int) or owner <= 0:
                raise ValueError("canonical_owner_invalid")
            return owner

        if idempotency_key in completed_idempotency_keys:
            canonical_id = (
                completed_idempotency_keys.get(idempotency_key)
                if isinstance(completed_idempotency_keys, Mapping)
                else None
            )
            if canonical_id is None:
                try:
                    canonical_id = await _find_owner()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.error(
                        "幂等 canonical owner 查询失败，异常类型=%s",
                        error.__class__.__name__,
                    )
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.FAILED,
                        idempotency_key,
                    )
            return ReflectionStoreResult(
                ReflectionStoreOutcome.SKIPPED_IDEMPOTENT,
                idempotency_key,
                canonical_id,
            )

        source_window = {
            "session_id": session_id,
            "start_index": start_index,
            "end_index": end_index,
            "start_seq": start_index,
            "end_seq": end_index,
            "message_count": end_index - start_index,
            "scope_id": scope_id,
            "session_epoch": session_epoch,
        }
        if normalized_source_digest is not None:
            source_window["source_digest"] = normalized_source_digest
        if worker_generation is not None:
            source_window["worker_generation"] = worker_generation
        if claim_fence:
            source_window["source_fence"] = str(claim_fence)
        try:
            is_mark_write = False
            quality_gate = memory_quality_gate
            if quality_gate is not None:

                async def _route_candidate() -> object:
                    """执行可能写入隔离 Store 的质量门路由。"""

                    return await quality_gate.route_candidate(
                        memory,
                        session_id=session_id,
                        persona_id=persona_id,
                        source_window=source_window,
                        is_group_chat=is_group_chat,
                        group_id=group_id,
                        scope_id=scope_id,
                        chat_type=("group" if is_group_chat else "private"),
                        gate_snapshot_json=gate_snapshot_json,
                    )

                gate_result = cast(
                    Any,
                    await run_claim_side_effect(_route_candidate)
                    if run_claim_side_effect is not None
                    else await _route_candidate(),
                )
                if gate_result.action == "quarantined":
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.QUARANTINED,
                        idempotency_key,
                    )
                if gate_result.action == "discard":
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.DISCARDED,
                        idempotency_key,
                    )
                if gate_result.action == "mark_write":
                    is_mark_write = True
                    metadata["gate_disposition"] = "mark_write"
                    memory["atoms"] = (
                        gate_result.atoms
                        if gate_result.atoms is not None
                        else memory.get("atoms", [])
                    )
            if before_side_effect is not None and not await before_side_effect():
                return ReflectionStoreResult(
                    ReflectionStoreOutcome.FAILED,
                    idempotency_key,
                )

            async def _write_canonical() -> int:
                """在来源 fence 内执行 canonical 写入和其后处理。"""

                source_fence = None
                if job_id is not None or claim_token is not None:
                    if (
                        job_id is None
                        or claim_token is None
                        or source_digest is None
                        or worker_generation is None
                    ):
                        raise ValueError("summary_source_fence_incomplete")
                    source_fence = SummarySourceFence(
                        job_id=job_id,
                        session_id=session_id,
                        session_epoch=session_epoch,
                        start_seq=start_index,
                        end_seq=end_index,
                        expected_count=end_index - start_index,
                        source_digest=source_digest,
                        worker_generation=worker_generation,
                        claim_token=claim_token,
                    )
                write_kwargs: dict[str, Any] = {
                    "content": memory["content"],
                    "session_id": session_id,
                    "persona_id": persona_id,
                    "importance": memory["importance"],
                    "metadata": metadata,
                    "atoms": memory.get("atoms", []),
                }
                if source_fence is not None:
                    write_kwargs["source_fence"] = source_fence
                value = await memory_engine.add_memory(**write_kwargs)
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("canonical_owner_invalid")
                try:
                    if not is_mark_write:
                        record_continuity_topics(memory_engine, session_id, memory)
                        await schedule_evolution_after_write(value)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.error(
                        "canonical 写后处理失败，异常类型=%s",
                        error.__class__.__name__,
                    )
                return value

            if run_claim_side_effect is not None:
                fenced_result = await run_claim_side_effect(_write_canonical)
                if (
                    isinstance(fenced_result, bool)
                    or not isinstance(fenced_result, int)
                    or fenced_result <= 0
                ):
                    raise ValueError("canonical_owner_invalid")
                memory_id = fenced_result
            else:
                memory_id = await _write_canonical()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if str(error) in {
                "claim_lost",
                "epoch_fenced",
                "generation_fenced",
            }:
                return ReflectionStoreResult(
                    ReflectionStoreOutcome.FAILED,
                    idempotency_key,
                )
            try:
                canonical_id = await _find_owner()
            except asyncio.CancelledError:
                raise
            except Exception:
                canonical_id = None
            if canonical_id is not None:
                return ReflectionStoreResult(
                    ReflectionStoreOutcome.SKIPPED_IDEMPOTENT,
                    idempotency_key,
                    canonical_id,
                )
            logger.error(
                "记忆写入失败，异常类型=%s",
                error.__class__.__name__,
            )
            return ReflectionStoreResult(
                ReflectionStoreOutcome.FAILED,
                idempotency_key,
            )

        outcome = (
            ReflectionStoreOutcome.MARK_WRITE
            if is_mark_write
            else ReflectionStoreOutcome.CANONICAL
        )
        return ReflectionStoreResult(outcome, idempotency_key, memory_id)

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
                "批量写入异常，异常类型=%s",
                result.__class__.__name__,
            )
            results.append(ReflectionStoreResult(ReflectionStoreOutcome.FAILED))
        else:
            results.append(result)
    return results


__all__ = ["build_reflection_idempotency_key", "store_reflection_candidates"]
