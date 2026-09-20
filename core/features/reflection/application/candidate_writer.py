"""执行自动反思候选的质量路由与限流持久化。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, cast

from astrbot.api import logger

from ....shared.summary_source_fence import SummarySourceFence
from ...memory.application.canonical_merge import (
    DEDUP_REASON_MERGE_CONFLICT,
    CanonicalMergeCoordinator,
    MergeCandidate,
    MergeOutcome,
)
from ..domain.storage_outcomes import ReflectionStoreOutcome, ReflectionStoreResult
from .continuity import record_continuity_topics

_MAX_CONCURRENT_WRITES = 3

# canonical 写入失败分类：区分「来源/claim 已失效的 fail-closed 预期跳过」与真实失败。
# 只读取 ``str(error)`` 中的稳定标识符，异常文本本身绝不写入日志、指标或返回值。
_STORE_FAILURE_SKIPPED_FENCED = "skipped_fenced"
_STORE_FAILURE_FAILED = "failed"
_STORE_FAILURE_UNKNOWN_CODE = "canonical_write_failed"

_EXPECTED_FENCED_CODES: Final[frozenset[str]] = frozenset(
    {
        "claim_lost",
        "epoch_fenced",
        "generation_fenced",
        "summary_source_fenced",
        "summary_source_mismatch",
        # 由质量门在路由候选前做会话 epoch 校验时抛出
        # （memory_quality_gate），与 canonical 来源 fence 同属按设计失效。
        "summary_epoch_fenced",
    }
)
_KNOWN_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "summary_scope_mismatch",
        "summary_source_activation_failed",
        "canonical_idempotency_mapping_invalid",
        "source_validation_unavailable",
    }
)
# 既有实现下不做 canonical owner 复核的 fence 码：保持既有终态语义不变。
_NO_OWNER_LOOKUP_FENCED_CODES: Final[frozenset[str]] = frozenset(
    {"claim_lost", "epoch_fenced", "generation_fenced"}
)
_SAFE_CODE: Final = re.compile(r"^[a-z][a-z0-9_]{2,64}$")

# 复用 MemoryEngine 既有写入失败指标的 stage 取值（同族：atom/graph/document）。
_STORE_FAILURE_STAGE_FENCED = "candidate_fenced"
_STORE_FAILURE_STAGE_FAILED = "candidate_write"


def classify_store_failure(error: BaseException) -> tuple[str, str]:
    """把 canonical 写入异常归约为 ``(终态类别, 稳定原因码)``。

    只把 ``str(error)`` 当作稳定标识符读取：命中 ``_EXPECTED_FENCED_CODES``
    视为 fail-closed 的预期跳过；命中 ``_KNOWN_FAILURE_CODES`` 或满足
    ``_SAFE_CODE`` 的原样作为原因码；其余（含任何携带正文、ID 或 scope 的
    文本）一律回落 ``canonical_write_failed``。
    """

    code = str(error)
    if code in _EXPECTED_FENCED_CODES:
        return _STORE_FAILURE_SKIPPED_FENCED, code
    if code in _KNOWN_FAILURE_CODES or _SAFE_CODE.fullmatch(code):
        return _STORE_FAILURE_FAILED, code
    return _STORE_FAILURE_FAILED, _STORE_FAILURE_UNKNOWN_CODE


def _record_store_write_failure(stage: str) -> None:
    """累加既有写入失败计数；观测自身失败只降级 debug，不影响写入终态。"""

    try:
        from ...observability.infrastructure.metrics import (
            MEMORY_WRITE_FAILURES_TOTAL,
        )

        MEMORY_WRITE_FAILURES_TOTAL.labels(stage=stage).inc()
    except Exception:
        logger.debug("candidate_writer 写入失败指标记录失败", exc_info=True)


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


def _merge_scope_metadata(
    metadata: dict[str, Any],
    *,
    scope_key: str | None,
    privacy_level: str | None,
    resolver_revision: str | None,
    chat_type: str | None = None,
    source_provenance_complete: bool | None = None,
) -> bool:
    """合并并校验候选 scope 快照，返回是否具备完整来源证据。

    ``chat_type`` 与 scope 三元组同源（resolver 固化的可信快照），
    一并写入 canonical metadata，供 catalog 聚合的读取侧复核。
    """

    if source_provenance_complete is not None and not isinstance(
        source_provenance_complete, bool
    ):
        raise TypeError("source_provenance_complete_invalid")
    values = (scope_key, privacy_level, resolver_revision)
    if any(value is not None for value in values):
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("scope_snapshot_incomplete")
        if chat_type is not None and chat_type not in {"private", "group"}:
            raise ValueError("chat_type_invalid")
        normalized = {
            "scope_key": str(scope_key).strip(),
            "privacy_level": str(privacy_level).strip(),
            "resolver_revision": str(resolver_revision).strip(),
        }
        if normalized["privacy_level"] not in {
            "public",
            "shared",
            "confidential",
        }:
            raise ValueError("privacy_level_invalid")
        for key, value in normalized.items():
            existing = metadata.get(key)
            if existing not in (None, value):
                raise ValueError("scope_snapshot_conflict")
            metadata[key] = value
        if chat_type is not None:
            existing_chat_type = metadata.get("chat_type")
            if existing_chat_type not in (None, chat_type):
                raise ValueError("scope_snapshot_conflict")
            metadata["chat_type"] = chat_type
        complete = source_provenance_complete is True
        metadata["source_provenance_complete"] = complete
        return complete
    if source_provenance_complete is True:
        raise ValueError("scope_snapshot_incomplete")
    # 模型输出中的 scope 字段不是可信来源证据；没有 resolver 快照时清除。
    for key in (
        "scope_key",
        "privacy_level",
        "resolver_revision",
        "chat_type",
    ):
        metadata.pop(key, None)
    if source_provenance_complete is False:
        metadata["source_provenance_complete"] = False
    else:
        metadata.pop("source_provenance_complete", None)
    return False


async def store_reflection_candidates(
    memories: list[dict[str, Any]],
    *,
    completed_idempotency_keys: set[str] | Mapping[str, int],
    merged_idempotency_keys: Mapping[str, int] | None = None,
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
    scope_key: str | None = None,
    privacy_level: str | None = None,
    resolver_revision: str | None = None,
    chat_type: str | None = None,
    source_provenance_complete: bool | None = None,
    before_side_effect: Callable[[], Awaitable[bool]] | None = None,
    run_claim_side_effect: Callable[
        [Callable[[], Awaitable[object]]], Awaitable[object]
    ]
    | None = None,
    memory_engine: Any,
    memory_quality_gate: Any | None,
    schedule_evolution_after_write: Callable[[int], Awaitable[None]],
    canonical_merge: CanonicalMergeCoordinator | None = None,
) -> list[ReflectionStoreResult]:
    """并发执行候选质量门与写入，并返回与输入一一对应的终态。

    canonical 写入可由 Store 提供的 claim runner 包围；runner 不得持有
    ConversationStore 事务，只负责 epoch/source fence。
    Args:
        memories: 当前窗口抽取出的候选列表。
        completed_idempotency_keys: 先前重试已完成的候选幂等键；Mapping
            还可携带已经发现的 canonical ID。
        merged_idempotency_keys: 已并入既有 canonical 的候选幂等键及 owner；
            命中时直接返回 ``MERGED``，不重复写回也不新增 canonical。
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
        scope_key: resolver 固化的 canonical scope；缺失时不具备候选来源证据。
        privacy_level: resolver 固化的隐私等级。
        resolver_revision: resolver 快照修订号。
        chat_type: resolver 固化的聊天类型（private/group），随可信 scope
            快照一并写入 metadata，供 catalog 读取侧复核。
        source_provenance_complete: 调用方声明的来源证据状态，只接受布尔值。
        before_side_effect: canonical 或隔离副作用前的 claim fence 回调。
        run_claim_side_effect: Store 提供的 epoch/source fence runner。
        memory_engine: canonical 记忆引擎。
        memory_quality_gate: 可选的候选质量路由器。
        schedule_evolution_after_write: canonical 写后的兼容演化调度回调。
        canonical_merge: 可选的跨窗口近重复合并协调器；缺省时不检测近重复，
            命中时返回 ``MERGED`` 并跳过 canonical 插入。

    Returns:
        与候选顺序一致的互斥存储终态。取消会继续向上传播，普通失败转为
        ``FAILED``，canonical 成功后的普通派生处理失败不改变写入终态。
    """

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_WRITES)

    async def _store_one(memory: dict[str, Any]) -> ReflectionStoreResult:
        """在质量门后返回一条候选的单一持久化终态。"""
        metadata_value = memory.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
        _merge_scope_metadata(
            metadata,
            scope_key=scope_key,
            privacy_level=privacy_level,
            resolver_revision=resolver_revision,
            chat_type=chat_type,
            source_provenance_complete=source_provenance_complete,
        )

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
            verifier = getattr(memory_engine, "is_memory_source_accepted", None)
            if callable(verifier):
                accepted = verifier(owner)
                if inspect.isawaitable(accepted):
                    accepted = await accepted
                if accepted is not True:
                    # 暂存/拒绝 owner 不算幂等成功，避免把未接受来源当已提交。
                    return None
            return owner

        merged_owner = (
            merged_idempotency_keys.get(idempotency_key)
            if isinstance(merged_idempotency_keys, Mapping)
            else None
        )
        if merged_owner is not None:
            if (
                isinstance(merged_owner, bool)
                or not isinstance(merged_owner, int)
                or merged_owner <= 0
            ):
                raise ValueError("canonical_owner_invalid")
            return ReflectionStoreResult(
                ReflectionStoreOutcome.MERGED,
                idempotency_key,
                merged_owner,
            )

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
        if scope_key is not None:
            source_window["scope_key"] = str(scope_key).strip()
        if privacy_level is not None:
            source_window["privacy_level"] = str(privacy_level).strip()
        if resolver_revision is not None:
            source_window["resolver_revision"] = str(resolver_revision).strip()
        source_window["source_provenance_complete"] = bool(
            metadata.get("source_provenance_complete") is True
        )
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

            async def _merge_near_duplicate(
                coordinator: CanonicalMergeCoordinator,
            ) -> MergeOutcome | None:
                """在 canonical 插入前执行近重复合并；失败回落普通写入。"""

                try:
                    return await coordinator.merge(
                        MergeCandidate(
                            content=memory["content"],
                            metadata=metadata,
                            importance=memory["importance"],
                            session_id=session_id,
                            persona_id=persona_id,
                            idempotency_key=idempotency_key,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.error(
                        "跨窗口近重复合并失败，回落普通写入",
                        extra={"reason_code": DEDUP_REASON_MERGE_CONFLICT},
                    )
                    logger.debug("近重复合并异常类型=%s", error.__class__.__name__)
                    return None

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
                        scope_key=scope_key,
                        privacy_level=privacy_level,
                        resolver_revision=resolver_revision,
                        scope_provenance_complete=metadata.get(
                            "source_provenance_complete"
                        ),
                    )
                    # canonical metadata 固化 claim 窗口边界，使单条证据
                    # 脱离 job 也能定位来源区间；取值只认已校验的 fence。
                    metadata["source_start_seq"] = source_fence.start_seq
                    metadata["source_end_seq"] = source_fence.end_seq
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

            async def _merge_or_write() -> int | MergeOutcome:
                """在同一 claim/source fence 内先尝试合并，未命中的再插入。

                合并与插入必须同属一个受 fence 保护的副作用：post-fence 校验
                失败时既不能回落插入第二条 canonical，也不能把不确定的合并
                当成成功。mark_write 是低置信候选，不得强化既有可信 canonical。
                """

                if canonical_merge is not None and not is_mark_write:
                    outcome = await _merge_near_duplicate(canonical_merge)
                    if outcome is not None and outcome.merged:
                        owner = outcome.memory_id
                        if (
                            isinstance(owner, bool)
                            or not isinstance(owner, int)
                            or owner <= 0
                        ):
                            # 合并已报告命中却没有可信 owner：不得回落插入第二条
                            # canonical，交由 fence 失败/重试与 merged 键恢复处理。
                            raise ValueError("canonical_owner_invalid")
                        return outcome
                return await _write_canonical()

            if run_claim_side_effect is not None:
                fenced_result = await run_claim_side_effect(_merge_or_write)
                if isinstance(fenced_result, MergeOutcome):
                    # ``_merge_or_write`` 只返回已校验 owner 的合并终态。
                    assert fenced_result.memory_id is not None
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.MERGED,
                        idempotency_key,
                        fenced_result.memory_id,
                    )
                if (
                    isinstance(fenced_result, bool)
                    or not isinstance(fenced_result, int)
                    or fenced_result <= 0
                ):
                    raise ValueError("canonical_owner_invalid")
                memory_id = fenced_result
            else:
                stored = await _merge_or_write()
                if isinstance(stored, MergeOutcome):
                    assert stored.memory_id is not None
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.MERGED,
                        idempotency_key,
                        stored.memory_id,
                    )
                memory_id = stored
        except asyncio.CancelledError:
            raise
        except Exception as error:
            category, reason_code = classify_store_failure(error)
            if category == _STORE_FAILURE_SKIPPED_FENCED:
                logger.warning(
                    "记忆写入按来源 fence 跳过，reason_code=%s",
                    reason_code,
                    extra={"reason_code": reason_code},
                )
                _record_store_write_failure(_STORE_FAILURE_STAGE_FENCED)
                if reason_code in _NO_OWNER_LOOKUP_FENCED_CODES:
                    # 既有语义：这三个 fence 码不复核 canonical owner。
                    return ReflectionStoreResult(
                        ReflectionStoreOutcome.FAILED,
                        idempotency_key,
                    )
            else:
                logger.error(
                    "记忆写入失败，reason_code=%s, 异常类型=%s",
                    reason_code,
                    error.__class__.__name__,
                    extra={"reason_code": reason_code},
                )
                _record_store_write_failure(_STORE_FAILURE_STAGE_FAILED)
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
            _, reason_code = classify_store_failure(result)
            logger.error(
                "批量写入异常，reason_code=%s, 异常类型=%s",
                reason_code,
                result.__class__.__name__,
                extra={"reason_code": reason_code},
            )
            results.append(ReflectionStoreResult(ReflectionStoreOutcome.FAILED))
        else:
            results.append(result)
    return results


__all__ = [
    "build_reflection_idempotency_key",
    "classify_store_failure",
    "store_reflection_candidates",
]
