"""canonical 近重复合并协调器（reinforce 语义）。

命中后只强化既有 canonical 的 metadata：``importance`` 取 max、来源证据与
topics 取并集、``merge_count``/``last_merged_at``/``merged_idempotency_keys``
记账；正文永不改写，revision 推进交由既有 ``MemoryEngine.update_memory``
语义判定（含乐观校验与派生失效重算）。

所有失败路径 fail-open：检测异常、CAS 冲突、写回异常或无法复核目标正文
时都返回非 MERGED 终态，由调用方回落普通 canonical 写入。进程内按
``session + scope_key`` 的 ``asyncio.Lock`` 串行化「检测 → 合并」，覆盖
同窗口候选并发写入。

可选 ``metrics_recorder`` 把七类终态（``checked``/``hit``/``merged``/
``fact_mismatch``/``fact_overlap``/``conflict``/``failed``）计入独立指标
Store；缺省未注入即全部 no-op，``mode=off`` 在检测前返回，因此不产生任何
指标行。``fact_overlap`` 是叠加在 ``checked`` 之上的观测信号（整段无命中但
候选事实已被既有 canonical 覆盖到阈值），``observe``/``enforce`` 都记录，
且不写回任何 canonical。

可选语义扩展（`memory_dedup.semantic_mode`，默认 off）：lexical 返回
MISS/FACT_OVERLAP 后才在有界窗口预算内调用注入的 ``semantic_search``；
``semantic_observe`` 只记录，``semantic_enforce`` 复用同一 owner CAS 写回。
端口缺失、预算耗尽、provider 失败与护栏不通过都保留 lexical 结论并回落
普通写入，语义 outcome 只记入 ``semantic_*`` 指标模式。

纯 metadata 助手已抽到 `canonical_merge_metadata.py`（原拆分点）。本模块
物理行数仍高于 AGENTS.md 的 600 行拆分评审线（低于 700 行硬上限），
``_apply_merge`` 仍在 80 行函数评审线之上（低于 120 行硬上限）；后续拆分点：
把「检测 + 终态分派」从 ``_merge_locked`` 前半段抽成单独方法，并把
`build_recent_document_search` / `build_semantic_document_search` 的端口装配
移到独立装配模块。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from astrbot.api import logger

from ....shared.memory_status import is_memory_recallable
from ...quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
    NearDuplicateVerdict,
    SimilarDocumentSearch,
    candidate_scope,
    detect_near_duplicate,
    same_dedup_scope,
    stored_scope,
)
from ...quality.application.semantic_duplicate_detector import (
    SEMANTIC_OUTCOME_FAILED,
    SemanticDocumentSearch,
    SemanticRequestBudget,
    build_semantic_document_search,
    detect_semantic_duplicate,
)
from ..domain.memory_dedup_config import MemoryDedupConfig
from ..domain.revision import memory_revision
from .canonical_merge_metadata import (
    MAX_MERGED_IDEMPOTENCY_KEYS,
    MAX_SOURCE_EVIDENCE,
    importance,
    load_metadata,
    merge_count,
    merge_fact_evidence,
    merge_keys,
    merged_keys,
    normalize_metadata,
    union,
)
from .scope_lock_registry import ScopeLockRegistry

DEDUP_REASON_MERGED: Final = "dedup_merged"
DEDUP_REASON_OBSERVED: Final = "dedup_observed"
DEDUP_REASON_DETECTOR_FAILED: Final = "dedup_detector_failed"
DEDUP_REASON_MERGE_CONFLICT: Final = "dedup_merge_conflict"
DEDUP_REASON_FACT_MISMATCH: Final = "dedup_fact_mismatch"
DEDUP_REASON_FACT_OVERLAP: Final = "dedup_fact_overlap"
DEDUP_REASON_SEMANTIC_OBSERVED: Final = "dedup_semantic_observed"
DEDUP_REASON_SEMANTIC_MERGED: Final = "dedup_semantic_merged"

MAX_SOURCE_REFS: Final = 32
MAX_TOPICS: Final = 5


class MergeStatus(str, Enum):
    """单条候选的近重复合并终态。"""

    MERGED = "merged"
    OBSERVED = "observed"
    MISS = "miss"
    FACT_MISMATCH = "fact_mismatch"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """待写入的反思候选投影。"""

    content: str
    metadata: Mapping[str, Any]
    importance: Any
    session_id: str
    persona_id: str | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """合并结论；只有 ``merged`` 为真才表示候选已并入 owner。"""

    status: MergeStatus
    memory_id: int | None = None
    score: float = 0.0
    reason_code: str = ""

    @property
    def merged(self) -> bool:
        """返回候选是否已并入既有 canonical。"""

        return self.status is MergeStatus.MERGED


LoadMemory = Callable[[int], Awaitable[Mapping[str, Any] | None]]
UpdateMemory = Callable[[int, dict[str, Any], "str | None"], Awaitable[Any]]
ConfigProvider = Callable[[], MemoryDedupConfig]
# 指标记录端口：``(mode, outcome)`` → 持久化；缺省不注入 ⇒ 全部记录为 no-op。
DedupMetricsRecorder = Callable[[str, str], Awaitable[object]]


class CanonicalMergeCoordinator:
    """把候选并入同 scope 的既有 canonical，失败时保持写入可用。"""

    def __init__(
        self,
        *,
        config_provider: ConfigProvider,
        search_similar: SimilarDocumentSearch,
        load_memory: LoadMemory,
        update_memory: UpdateMemory,
        metrics_recorder: DedupMetricsRecorder | None = None,
        semantic_search: SemanticDocumentSearch | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """绑定配置读取器与 canonical 检索/读写端口。

        ``semantic_search`` 缺省为 ``None``：语义检测是可选能力，端口缺失时
        等价于 `semantic_unavailable`，不发起任何 provider 查询。
        """

        self._config_provider = config_provider
        self._search_similar = search_similar
        self._load_memory = load_memory
        self._update_memory = update_memory
        self._metrics_recorder = metrics_recorder
        self._semantic_search = semantic_search
        self._clock = clock
        # 语义 provider 调用的按窗口预算；窗口键只在内存中，不落库不记录。
        self._semantic_budget = SemanticRequestBudget()
        # 引用计数注册表：同一 scope 串行合并，空闲后立即回收条目。
        self._locks = ScopeLockRegistry()

    async def merge(self, candidate: MergeCandidate) -> MergeOutcome:
        """检测并合并候选；不确定的中间状态一律返回可回落的终态。"""

        config = self._resolve_config()
        if config.mode == "off":
            return MergeOutcome(MergeStatus.MISS)
        scope = candidate_scope(
            candidate.metadata,
            session_id=candidate.session_id,
            persona_id=candidate.persona_id,
        )
        if scope is None:
            return MergeOutcome(MergeStatus.MISS)
        async with self._locks.hold(f"{scope.session_id}\x00{scope.scope_key}"):
            return await self._merge_locked(candidate, config)

    def _resolve_config(self) -> MemoryDedupConfig:
        """读取配置快照；异常或类型不符时按关闭处理。"""

        try:
            config = self._config_provider()
        except Exception as error:
            logger.error(
                "近重复合并配置读取失败，按关闭处理",
                extra={"reason_code": DEDUP_REASON_DETECTOR_FAILED},
            )
            logger.debug("近重复合并配置异常类型=%s", error.__class__.__name__)
            return MemoryDedupConfig()
        return config if isinstance(config, MemoryDedupConfig) else MemoryDedupConfig()

    async def _merge_locked(
        self,
        candidate: MergeCandidate,
        config: MemoryDedupConfig,
    ) -> MergeOutcome:
        """在 scope 锁内执行检测、观测或写回。"""

        try:
            detection = await detect_near_duplicate(
                content=candidate.content,
                metadata=candidate.metadata,
                session_id=candidate.session_id,
                persona_id=candidate.persona_id,
                search_similar=self._search_similar,
                similarity_threshold=config.similarity_threshold,
                candidate_limit=config.candidate_limit,
                min_tokens=config.min_tokens,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "近重复检测失败，回落普通写入",
                extra={"reason_code": DEDUP_REASON_DETECTOR_FAILED},
            )
            logger.debug("近重复检测异常类型=%s", error.__class__.__name__)
            await self._record_metrics(config.mode, "checked", "failed")
            return MergeOutcome(
                MergeStatus.FAILED,
                reason_code=DEDUP_REASON_DETECTOR_FAILED,
            )
        if detection.verdict is NearDuplicateVerdict.MISS:
            await self._record_metrics(config.mode, "checked")
            semantic = await self._semantic_outcome(candidate, config)
            return semantic or MergeOutcome(MergeStatus.MISS)
        if detection.verdict is NearDuplicateVerdict.FACT_MISMATCH:
            logger.info(
                "同 scope 近重复候选的事实不同，不合并",
                extra={"reason_code": DEDUP_REASON_FACT_MISMATCH},
            )
            await self._record_metrics(config.mode, "checked", "fact_mismatch")
            return MergeOutcome(
                MergeStatus.FACT_MISMATCH,
                detection.memory_id,
                detection.score,
                DEDUP_REASON_FACT_MISMATCH,
            )
        if detection.verdict is NearDuplicateVerdict.FACT_OVERLAP:
            # 事实粒度重叠只作观测：追加 fact_overlap 计数，绝不写回。
            logger.info(
                "同 scope 候选与既有 canonical 部分共享事实，仅观测记录",
                extra={"reason_code": DEDUP_REASON_FACT_OVERLAP},
            )
            await self._record_metrics(config.mode, "checked", "fact_overlap")
            overlap_outcome = MergeOutcome(
                MergeStatus.OBSERVED,
                detection.memory_id,
                detection.score,
                DEDUP_REASON_FACT_OVERLAP,
            )
            semantic = await self._semantic_outcome(candidate, config)
            return semantic or overlap_outcome
        document = detection.document
        if document is None:
            await self._record_metrics(config.mode, "checked", "failed")
            return MergeOutcome(
                MergeStatus.FAILED,
                reason_code=DEDUP_REASON_DETECTOR_FAILED,
            )
        if config.mode != "enforce":
            logger.info(
                "观测到同 scope 近重复候选，observe 模式不合并",
                extra={"reason_code": DEDUP_REASON_OBSERVED},
            )
            await self._record_metrics(config.mode, "checked", "hit")
            return MergeOutcome(
                MergeStatus.OBSERVED,
                document.memory_id,
                detection.score,
                DEDUP_REASON_OBSERVED,
            )
        await self._record_metrics(config.mode, "checked", "hit")
        return await self._apply_merge(
            candidate, document, detection.score, config.mode
        )

    async def _semantic_outcome(
        self,
        candidate: MergeCandidate,
        config: MemoryDedupConfig,
    ) -> MergeOutcome | None:
        """lexical 未命中/事实重叠后的可选语义检测。

        返回 ``None`` 表示调用方保留 lexical 结论：``semantic_mode=off``、
        端口缺失、预算耗尽、provider 失败或候选未通过作用域/事实/用户来源
        证据护栏都不会改变普通写入语义。``observe`` 只记录命中，``enforce``
        复用既有 owner CAS 写回路径。
        """

        if config.semantic_mode == "off":
            return None
        metric_mode = f"semantic_{config.semantic_mode}"
        try:
            decision = await detect_semantic_duplicate(
                content=candidate.content,
                metadata=candidate.metadata,
                session_id=candidate.session_id,
                persona_id=candidate.persona_id,
                search_semantic=self._semantic_search,
                load_memory=self._load_memory,
                budget=self._semantic_budget,
                window_key=_semantic_window_key(candidate),
                threshold=config.semantic_threshold,
                candidate_limit=config.candidate_limit,
                min_tokens=config.min_tokens,
                fact_evidence_guard=lambda owner_metadata: (
                    merge_fact_evidence(owner_metadata, candidate.metadata) is not None
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "语义近重复检测失败，回落普通写入",
                extra={"reason_code": SEMANTIC_OUTCOME_FAILED},
            )
            logger.debug("语义近重复检测异常类型=%s", error.__class__.__name__)
            await self._record_metrics(metric_mode, SEMANTIC_OUTCOME_FAILED)
            return None
        for outcome in decision.outcomes:
            await self._record_metrics(metric_mode, outcome)
        document = decision.document
        if document is None:
            return None
        if config.semantic_mode != "enforce":
            logger.info(
                "语义观测到同 scope 近重复候选，observe 模式不合并",
                extra={"reason_code": DEDUP_REASON_SEMANTIC_OBSERVED},
            )
            return MergeOutcome(
                MergeStatus.OBSERVED,
                document.memory_id,
                decision.score,
                DEDUP_REASON_SEMANTIC_OBSERVED,
            )
        outcome = await self._apply_merge(
            candidate,
            document,
            decision.score,
            metric_mode,
        )
        if not outcome.merged:
            return outcome
        return MergeOutcome(
            MergeStatus.MERGED,
            outcome.memory_id,
            outcome.score,
            DEDUP_REASON_SEMANTIC_MERGED,
        )

    async def _apply_merge(
        self,
        candidate: MergeCandidate,
        document: DedupDocument,
        score: float,
        mode: str,
    ) -> MergeOutcome:
        """把候选记账写回既有 canonical；CAS 失败或异常时回落。"""

        owner_id = document.memory_id
        try:
            fresh = await self._load_memory(owner_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "近重复合并目标读取失败，回落普通写入",
                extra={"reason_code": DEDUP_REASON_MERGE_CONFLICT},
            )
            logger.debug("近重复合并目标读取异常类型=%s", error.__class__.__name__)
            await self._record_metrics(mode, "failed")
            return MergeOutcome(
                MergeStatus.FAILED,
                owner_id,
                score,
                DEDUP_REASON_MERGE_CONFLICT,
            )
        metadata = normalize_metadata(fresh.get("metadata") if fresh else None)
        incoming_scope = candidate_scope(
            candidate.metadata,
            session_id=candidate.session_id,
            persona_id=candidate.persona_id,
        )
        owner_scope = stored_scope(metadata)
        fact_evidence = merge_fact_evidence(metadata, candidate.metadata)
        if (
            not fresh
            or not is_memory_recallable(metadata)
            or fresh.get("text") != document.content
            or incoming_scope is None
            or owner_scope is None
            or not same_dedup_scope(incoming_scope, owner_scope)
            or fact_evidence is None
            or any(
                candidate.metadata.get(key) != metadata.get(key)
                for key in ("revision_token", "resolver_revision")
            )
        ):
            # 目标已被改写、失效或消失：不合并，交由调用方普通写入。
            await self._record_metrics(mode, "conflict")
            return MergeOutcome(
                MergeStatus.CONFLICT,
                owner_id,
                score,
                DEDUP_REASON_MERGE_CONFLICT,
            )
        if candidate.idempotency_key and candidate.idempotency_key in merged_keys(
            metadata
        ):
            # 重放：同一候选已经并入过该 canonical，不再重复记账。
            await self._record_metrics(mode, "merged")
            return MergeOutcome(
                MergeStatus.MERGED,
                owner_id,
                score,
                DEDUP_REASON_MERGED,
            )
        expected_revision = memory_revision(dict(fresh))
        if not expected_revision:
            await self._record_metrics(mode, "conflict")
            return MergeOutcome(
                MergeStatus.CONFLICT,
                owner_id,
                score,
                DEDUP_REASON_MERGE_CONFLICT,
            )
        updates = self._merge_updates(candidate, metadata)
        updates["metadata"]["fact_source_evidence"] = fact_evidence
        baseline_merge_count = merge_count(metadata)
        try:
            applied = await self._update_memory(owner_id, updates, expected_revision)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "近重复合并写回失败，回落普通写入",
                extra={"reason_code": DEDUP_REASON_MERGE_CONFLICT},
            )
            logger.debug("近重复合并写回异常类型=%s", error.__class__.__name__)
            applied = False
        if not applied:
            # 派生重建失败可能发生在 metadata 已提交之后：以回读结果为准。
            landed = await self._merge_landed(
                owner_id,
                candidate,
                baseline_merge_count=baseline_merge_count,
            )
            if not landed:
                logger.info(
                    "近重复合并 CAS 冲突，回落普通写入",
                    extra={"reason_code": DEDUP_REASON_MERGE_CONFLICT},
                )
                await self._record_metrics(mode, "conflict")
                return MergeOutcome(
                    MergeStatus.CONFLICT,
                    owner_id,
                    score,
                    DEDUP_REASON_MERGE_CONFLICT,
                )
        logger.info(
            "近重复候选已并入既有 canonical",
            extra={"reason_code": DEDUP_REASON_MERGED},
        )
        await self._record_metrics(mode, "merged")
        return MergeOutcome(
            MergeStatus.MERGED,
            owner_id,
            score,
            DEDUP_REASON_MERGED,
        )

    async def _record_metrics(self, mode: str, *outcomes: str) -> None:
        """按顺序写入持久化指标；记录失败绝不影响合并主流程。

        ``mode=off`` 在 ``merge`` 入口提前返回，这里再防御一次，保证关闭
        模式不产生任何指标行。``CancelledError`` 继续向上传播，其余异常
        只降级为 debug 日志。
        """

        recorder = self._metrics_recorder
        if recorder is None or mode == "off":
            return
        for outcome in outcomes:
            try:
                await recorder(mode, outcome)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.debug(
                    "近重复指标记录失败，异常类型=%s", error.__class__.__name__
                )

    async def _merge_landed(
        self,
        owner_id: int,
        candidate: MergeCandidate,
        *,
        baseline_merge_count: int,
    ) -> bool:
        """回读目标 canonical，确认写回是否已经生效。"""

        try:
            current = await self._load_memory(owner_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        metadata = normalize_metadata(current.get("metadata") if current else None)
        if candidate.idempotency_key:
            return candidate.idempotency_key in merged_keys(metadata)
        return merge_count(metadata) > baseline_merge_count

    def _merge_updates(
        self,
        candidate: MergeCandidate,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """构造 metadata-only 合并增量（importance 只升、并集去重、计数）。"""

        incoming = candidate.metadata
        merged_metadata: dict[str, Any] = {
            "merge_count": merge_count(metadata) + 1,
            "last_merged_at": self._clock(),
            "merged_idempotency_keys": merge_keys(
                metadata,
                candidate.idempotency_key,
            ),
        }
        for field, limit in (
            ("source_refs", MAX_SOURCE_REFS),
            ("topics", MAX_TOPICS),
        ):
            merged_items = union(metadata.get(field), incoming.get(field), limit=limit)
            if merged_items:
                merged_metadata[field] = merged_items
        return {
            "importance": max(
                importance(metadata.get("importance")),
                importance(candidate.importance),
            ),
            "metadata": merged_metadata,
        }


def build_canonical_merge_coordinator(
    engine: Any,
    *,
    config_provider: ConfigProvider,
    metrics_recorder: DedupMetricsRecorder | None = None,
    clock: Callable[[], float] = time.time,
) -> CanonicalMergeCoordinator:
    """按 MemoryEngine 既有能力装配生产端口。"""

    async def _update_memory(
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None,
    ) -> Any:
        """以既有乐观校验语义提交 metadata-only 更新。"""

        return await engine.update_memory(
            memory_id,
            updates,
            expected_revision=expected_revision,
        )

    return CanonicalMergeCoordinator(
        config_provider=config_provider,
        search_similar=build_recent_document_search(engine),
        load_memory=engine.get_memory,
        update_memory=_update_memory,
        metrics_recorder=metrics_recorder,
        semantic_search=build_semantic_document_search(engine),
        clock=clock,
    )


def build_recent_document_search(engine: Any) -> SimilarDocumentSearch:
    """构造按 ``session_id`` 有界读取近期 canonical 的检索端口。

    使用 ``documents`` 表已有的 ``json_extract(metadata, '$.session_id')``
    索引，按 ``id DESC`` 取有界窗口；scope/privacy/主体过滤留在 Python 侧，
    因此不新增索引。
    """

    async def search(query: DedupQuery) -> list[DedupDocument]:
        """返回同一会话最近写入的 canonical 投影。"""

        connection = getattr(engine, "db_connection", None)
        if connection is None:
            return []
        limit = max(1, int(query.limit))
        cursor = await connection.execute(
            "SELECT id, text, metadata FROM documents "
            "WHERE json_extract(metadata, '$.session_id') = ? "
            "ORDER BY id DESC LIMIT ?",
            (query.session_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        documents: list[DedupDocument] = []
        for row in rows:
            memory_id = row[0]
            text = row[1]
            if (
                isinstance(memory_id, bool)
                or not isinstance(memory_id, int)
                or memory_id <= 0
            ):
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            metadata = load_metadata(row[2])
            if metadata is None:
                continue
            documents.append(
                DedupDocument(memory_id=memory_id, content=text, metadata=metadata)
            )
        return documents

    return search


def _semantic_window_key(candidate: MergeCandidate) -> str:
    """返回语义预算的窗口键：来源窗口摘要优先，缺省退化为候选幂等键。

    两者都是既有稳定摘要，只作为内存预算的分桶键，不写入日志、指标或
    canonical metadata；缺省退化时每个候选自成一个预算窗口，仍然有界。
    """

    digest = candidate.metadata.get("source_digest")
    if isinstance(digest, str) and digest.strip():
        return digest.strip()
    return candidate.idempotency_key


__all__ = [
    "DEDUP_REASON_DETECTOR_FAILED",
    "DEDUP_REASON_FACT_MISMATCH",
    "DEDUP_REASON_FACT_OVERLAP",
    "DEDUP_REASON_MERGED",
    "DEDUP_REASON_MERGE_CONFLICT",
    "DEDUP_REASON_OBSERVED",
    "DEDUP_REASON_SEMANTIC_MERGED",
    "DEDUP_REASON_SEMANTIC_OBSERVED",
    "MAX_MERGED_IDEMPOTENCY_KEYS",
    "MAX_SOURCE_EVIDENCE",
    "MAX_SOURCE_REFS",
    "MAX_TOPICS",
    "CanonicalMergeCoordinator",
    "DedupMetricsRecorder",
    "MergeCandidate",
    "MergeOutcome",
    "MergeStatus",
    "build_canonical_merge_coordinator",
    "build_recent_document_search",
]
