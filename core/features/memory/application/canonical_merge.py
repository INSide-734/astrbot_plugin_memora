"""canonical 近重复合并协调器（reinforce 语义）。

命中后只强化既有 canonical 的 metadata：``importance`` 取 max、来源证据与
topics 取并集、``merge_count``/``last_merged_at``/``merged_idempotency_keys``
记账；正文永不改写，revision 推进交由既有 ``MemoryEngine.update_memory``
语义判定（含乐观校验与派生失效重算）。

所有失败路径 fail-open：检测异常、CAS 冲突、写回异常或无法复核目标正文
时都返回非 MERGED 终态，由调用方回落普通 canonical 写入。进程内按
``session + scope_key`` 的 ``asyncio.Lock`` 串行化「检测 → 合并」，覆盖
同窗口候选并发写入。
"""

from __future__ import annotations

import asyncio
import json
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
)
from ..domain.memory_dedup_config import MemoryDedupConfig
from ..domain.revision import memory_revision

DEDUP_REASON_MERGED: Final = "dedup_merged"
DEDUP_REASON_OBSERVED: Final = "dedup_observed"
DEDUP_REASON_DETECTOR_FAILED: Final = "dedup_detector_failed"
DEDUP_REASON_MERGE_CONFLICT: Final = "dedup_merge_conflict"
DEDUP_REASON_FACT_MISMATCH: Final = "dedup_fact_mismatch"

MAX_SOURCE_REFS: Final = 32
MAX_SOURCE_EVIDENCE: Final = 32
MAX_TOPICS: Final = 5
MAX_MERGED_IDEMPOTENCY_KEYS: Final = 16


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


class CanonicalMergeCoordinator:
    """把候选并入同 scope 的既有 canonical，失败时保持写入可用。"""

    def __init__(
        self,
        *,
        config_provider: ConfigProvider,
        search_similar: SimilarDocumentSearch,
        load_memory: LoadMemory,
        update_memory: UpdateMemory,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """绑定配置读取器与 canonical 检索/读写端口。"""

        self._config_provider = config_provider
        self._search_similar = search_similar
        self._load_memory = load_memory
        self._update_memory = update_memory
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}

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
        lock = self._locks.setdefault(
            f"{scope.session_id}\x00{scope.scope_key}",
            asyncio.Lock(),
        )
        async with lock:
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
            return MergeOutcome(
                MergeStatus.FAILED,
                reason_code=DEDUP_REASON_DETECTOR_FAILED,
            )
        if detection.verdict is NearDuplicateVerdict.MISS:
            return MergeOutcome(MergeStatus.MISS)
        if detection.verdict is NearDuplicateVerdict.FACT_MISMATCH:
            logger.info(
                "同 scope 近重复候选的事实不同，不合并",
                extra={"reason_code": DEDUP_REASON_FACT_MISMATCH},
            )
            return MergeOutcome(
                MergeStatus.FACT_MISMATCH,
                detection.memory_id,
                detection.score,
                DEDUP_REASON_FACT_MISMATCH,
            )
        document = detection.document
        if document is None:
            return MergeOutcome(
                MergeStatus.FAILED,
                reason_code=DEDUP_REASON_DETECTOR_FAILED,
            )
        if config.mode != "enforce":
            logger.info(
                "观测到同 scope 近重复候选，observe 模式不合并",
                extra={"reason_code": DEDUP_REASON_OBSERVED},
            )
            return MergeOutcome(
                MergeStatus.OBSERVED,
                document.memory_id,
                detection.score,
                DEDUP_REASON_OBSERVED,
            )
        return await self._apply_merge(candidate, document, detection.score)

    async def _apply_merge(
        self,
        candidate: MergeCandidate,
        document: DedupDocument,
        score: float,
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
            return MergeOutcome(
                MergeStatus.FAILED,
                owner_id,
                score,
                DEDUP_REASON_MERGE_CONFLICT,
            )
        metadata = _normalize_metadata(fresh.get("metadata") if fresh else None)
        if (
            not fresh
            or not is_memory_recallable(metadata)
            or fresh.get("text") != document.content
        ):
            # 目标已被改写、失效或消失：不合并，交由调用方普通写入。
            return MergeOutcome(
                MergeStatus.CONFLICT,
                owner_id,
                score,
                DEDUP_REASON_MERGE_CONFLICT,
            )
        if candidate.idempotency_key and candidate.idempotency_key in _merged_keys(
            metadata
        ):
            # 重放：同一候选已经并入过该 canonical，不再重复记账。
            return MergeOutcome(
                MergeStatus.MERGED,
                owner_id,
                score,
                DEDUP_REASON_MERGED,
            )
        expected_revision = memory_revision(dict(fresh))
        if not expected_revision:
            return MergeOutcome(
                MergeStatus.CONFLICT,
                owner_id,
                score,
                DEDUP_REASON_MERGE_CONFLICT,
            )
        updates = self._merge_updates(candidate, metadata)
        baseline_merge_count = _merge_count(metadata)
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
        return MergeOutcome(
            MergeStatus.MERGED,
            owner_id,
            score,
            DEDUP_REASON_MERGED,
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
        metadata = _normalize_metadata(current.get("metadata") if current else None)
        if candidate.idempotency_key:
            return candidate.idempotency_key in _merged_keys(metadata)
        return _merge_count(metadata) > baseline_merge_count

    def _merge_updates(
        self,
        candidate: MergeCandidate,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """构造 metadata-only 合并增量（importance 只升、并集去重、计数）。"""

        incoming = candidate.metadata
        merged_metadata: dict[str, Any] = {
            "merge_count": _merge_count(metadata) + 1,
            "last_merged_at": self._clock(),
            "merged_idempotency_keys": _merge_keys(
                metadata,
                candidate.idempotency_key,
            ),
        }
        for field, limit in (
            ("source_refs", MAX_SOURCE_REFS),
            ("source_evidence", MAX_SOURCE_EVIDENCE),
            ("topics", MAX_TOPICS),
        ):
            union = _union(metadata.get(field), incoming.get(field), limit=limit)
            if union:
                merged_metadata[field] = union
        return {
            "importance": max(
                _importance(metadata.get("importance")),
                _importance(candidate.importance),
            ),
            "metadata": merged_metadata,
        }


def build_canonical_merge_coordinator(
    engine: Any,
    *,
    config_provider: ConfigProvider,
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
            metadata = _load_metadata(row[2])
            if metadata is None:
                continue
            documents.append(
                DedupDocument(memory_id=memory_id, content=text, metadata=metadata)
            )
        return documents

    return search


def _load_metadata(value: Any) -> dict[str, Any] | None:
    """解析落库 metadata；非法 JSON 或非对象返回 ``None``。"""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(value, dict):
        return None
    return value


def _normalize_metadata(value: Any) -> dict[str, Any]:
    """把 canonical metadata 规范化为字典；缺失或非法时返回空字典。"""

    return _load_metadata(value) or {}


def _importance(value: Any) -> float:
    """把重要性规范化为 0..1 浮点；非法值按 0 处理（max 只升不降）。"""

    if isinstance(value, bool):
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _merge_count(metadata: Mapping[str, Any]) -> int:
    """读取既有合并计数；非法值按 0 处理。"""

    value = metadata.get("merge_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _items(value: Any) -> tuple[Any, ...]:
    """把并集输入规范化为元组；非法类型按空处理。"""

    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _identity(item: Any) -> str:
    """构造稳定去重键；不可 JSON 序列化时回退 repr。"""

    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(item)


def _union(existing: Any, incoming: Any, *, limit: int) -> list[Any]:
    """按“既有在前、新增在后”合并去重并截断到上限。"""

    merged: list[Any] = []
    seen: set[str] = set()
    for item in (*_items(existing), *_items(incoming)):
        key = _identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:limit]


def _merged_keys(metadata: Mapping[str, Any]) -> list[str]:
    """读取 canonical metadata 中已合并的幂等键，忽略非法项。"""

    return [
        item
        for item in _items(metadata.get("merged_idempotency_keys"))
        if isinstance(item, str) and item.strip()
    ]


def _merge_keys(metadata: Mapping[str, Any], key: str) -> list[str]:
    """追加候选幂等键并保留最近 ``MAX_MERGED_IDEMPOTENCY_KEYS`` 项。"""

    keys = _merged_keys(metadata)
    if key and key not in keys:
        keys.append(key)
    return keys[-MAX_MERGED_IDEMPOTENCY_KEYS:]


__all__ = [
    "DEDUP_REASON_DETECTOR_FAILED",
    "DEDUP_REASON_FACT_MISMATCH",
    "DEDUP_REASON_MERGED",
    "DEDUP_REASON_MERGE_CONFLICT",
    "DEDUP_REASON_OBSERVED",
    "MAX_MERGED_IDEMPOTENCY_KEYS",
    "MAX_SOURCE_EVIDENCE",
    "MAX_SOURCE_REFS",
    "MAX_TOPICS",
    "CanonicalMergeCoordinator",
    "MergeCandidate",
    "MergeOutcome",
    "MergeStatus",
    "build_canonical_merge_coordinator",
    "build_recent_document_search",
]
