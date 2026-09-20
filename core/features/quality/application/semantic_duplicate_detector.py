"""可选的语义近重复窄端口（默认关闭、有界、只读）。

lexical `near_duplicate_detector` 仍是第一判据：协调器只在 lexical 返回
``MISS``/``FACT_OVERLAP`` 之后才调用本模块，把候选正文交给已装配的
``VectorRetriever.search``（生产 adapter：`build_semantic_document_search`）。
语义分数只作为候选排序与阈值输入：每条候选都要重新回读 canonical，并通过
active/可召回、``scope_key``/privacy/会话/人格/主体边界、正文长度护栏与
``key_facts`` 事实护栏；未通过或不可回读的候选一律跳过，因此「向量相近」
本身不构成命中。

成本边界：`SemanticRequestBudget` 按窗口固定上限（默认 8 次）限制 provider
查询，窗口标识只存在于内存；端口缺失、预算耗尽与 provider 异常各自返回稳定
outcome。provider 与回读异常由协调器捕获并 fail-open；本模块不写任何状态，
也不把 query、正文、记忆 ID、scope 或异常原文写入日志或指标。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from .near_duplicate_detector import (
    DedupDocument,
    candidate_scope,
    document_is_comparable,
    fact_guard_passes,
)

# 语义检测的稳定闭集 outcome：指标 outcome 与合并终态 reason_code 共用取值。
SEMANTIC_OUTCOME_CHECKED: Final = "semantic_checked"
SEMANTIC_OUTCOME_HIT: Final = "semantic_hit"
SEMANTIC_OUTCOME_FAILED: Final = "semantic_failed"
SEMANTIC_OUTCOME_UNAVAILABLE: Final = "semantic_unavailable"
SEMANTIC_OUTCOME_BUDGET_EXHAUSTED: Final = "semantic_budget_exhausted"
SEMANTIC_OUTCOMES: Final = (
    SEMANTIC_OUTCOME_CHECKED,
    SEMANTIC_OUTCOME_HIT,
    SEMANTIC_OUTCOME_FAILED,
    SEMANTIC_OUTCOME_UNAVAILABLE,
    SEMANTIC_OUTCOME_BUDGET_EXHAUSTED,
)
# 指标模式标签：语义结果与 lexical 计数分模式存放，避免污染既有比率分母。
SEMANTIC_METRIC_MODES: Final = ("semantic_observe", "semantic_enforce")

SEMANTIC_REQUESTS_PER_WINDOW: Final = 8
MAX_TRACKED_WINDOWS: Final = 64


@dataclass(frozen=True, slots=True)
class SemanticQuery:
    """语义检索请求：候选正文、可信会话边界与返回数量上限。"""

    content: str
    session_id: str
    persona_id: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    """语义检索返回的候选：只保留整数 canonical ID 与相似度分数。"""

    memory_id: int
    score: float


SemanticDocumentSearch = Callable[
    [SemanticQuery], Awaitable[Sequence[SemanticCandidate]]
]
SemanticLoadMemory = Callable[[int], Awaitable[Mapping[str, Any] | None]]
# 用户来源证据护栏：由协调器注入（quality 不反向依赖 memory 领域类型）。
FactEvidenceGuard = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class SemanticDecision:
    """一次语义检测的结论；``document`` 只在全部护栏通过后存在。

    ``outcomes`` 是需要按顺序记入语义指标模式的闭集 outcome；不产生 provider
    查询的前置拒绝（例如作用域不完整）返回空元组，避免伪造「已检测」计数。
    """

    document: DedupDocument | None = None
    score: float = 0.0
    outcomes: tuple[str, ...] = ()
    reason_code: str = ""


class SemanticRequestBudget:
    """按窗口固定次数的语义查询预算；窗口键只存在于内存。"""

    def __init__(
        self,
        limit: int = SEMANTIC_REQUESTS_PER_WINDOW,
        max_windows: int = MAX_TRACKED_WINDOWS,
    ) -> None:
        """绑定单窗口上限与窗口记忆容量。

        Args:
            limit: 单个窗口允许的 provider 查询次数，必须为正整数。
            max_windows: 最多同时记住预算的窗口数；超出时淘汰最旧窗口。

        Raises:
            ValueError: limit 或 max_windows 不是正整数。
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if (
            isinstance(max_windows, bool)
            or not isinstance(max_windows, int)
            or max_windows < 1
        ):
            raise ValueError("max_windows must be a positive integer")
        self._limit = limit
        self._max_windows = max_windows
        self._remaining: dict[str, int] = {}

    @property
    def tracked_windows(self) -> int:
        """返回当前记住预算的窗口数（诊断与测试用）。"""

        return len(self._remaining)

    def consume(self, window_key: str) -> bool:
        """占用一次窗口预算；预算耗尽返回 ``False`` 且不再发起查询。"""

        key = window_key or ""
        remaining = self._remaining.get(key)
        if remaining is None:
            if len(self._remaining) >= self._max_windows:
                self._remaining.pop(next(iter(self._remaining)))
            remaining = self._limit
        if remaining <= 0:
            return False
        self._remaining[key] = remaining - 1
        return True


async def detect_semantic_duplicate(
    *,
    content: str,
    metadata: Mapping[str, Any],
    session_id: str,
    persona_id: str | None,
    search_semantic: SemanticDocumentSearch | None,
    load_memory: SemanticLoadMemory,
    budget: SemanticRequestBudget,
    window_key: str,
    threshold: float,
    candidate_limit: int,
    min_tokens: int,
    fact_evidence_guard: FactEvidenceGuard | None = None,
) -> SemanticDecision:
    """在有界预算内做一次语义近重复检测；所有拒绝都返回无命中的安全结论。

    Args:
        fact_evidence_guard: 可选的用户来源证据护栏（由协调器注入）。返回
            ``False`` 的候选不计入命中，``semantic_hit`` 只在全部护栏通过后
            才记录。

    Returns:
        ``document`` 只在通过了作用域、可召回、长度、事实与来源证据护栏的
        候选上给出；``outcomes`` 是待记录的语义指标 outcome（provider 未发起
        时为空）。
    """

    if search_semantic is None:
        return SemanticDecision(
            outcomes=(SEMANTIC_OUTCOME_UNAVAILABLE,),
            reason_code=SEMANTIC_OUTCOME_UNAVAILABLE,
        )
    if not isinstance(content, str) or not content.strip():
        return SemanticDecision()
    incoming = candidate_scope(metadata, session_id=session_id, persona_id=persona_id)
    if incoming is None:
        return SemanticDecision()
    if not budget.consume(window_key):
        return SemanticDecision(
            outcomes=(SEMANTIC_OUTCOME_BUDGET_EXHAUSTED,),
            reason_code=SEMANTIC_OUTCOME_BUDGET_EXHAUSTED,
        )

    limit = max(1, int(candidate_limit))
    # provider/FAISS 异常不在此吞掉：协调器捕获后按 fail-open 记录安全 reason。
    fetched = await search_semantic(
        SemanticQuery(
            content=content,
            session_id=session_id,
            persona_id=persona_id,
            limit=limit,
        )
    )

    ranked = _ranked_candidates(fetched, threshold=threshold)
    for score, memory_id in ranked[:limit]:
        document = await _load_document(
            memory_id,
            load_memory=load_memory,
            incoming=incoming,
            min_tokens=min_tokens,
        )
        if document is None:
            continue
        if not fact_guard_passes(metadata, document.metadata):
            continue
        if fact_evidence_guard is not None and not fact_evidence_guard(
            document.metadata
        ):
            continue
        return SemanticDecision(
            document=document,
            score=score,
            outcomes=(SEMANTIC_OUTCOME_CHECKED, SEMANTIC_OUTCOME_HIT),
            reason_code=SEMANTIC_OUTCOME_HIT,
        )
    return SemanticDecision(outcomes=(SEMANTIC_OUTCOME_CHECKED,))


def build_semantic_document_search(engine: Any) -> SemanticDocumentSearch | None:
    """按引擎既有能力装配语义检索端口；缺少向量检索时返回 ``None``。

    只使用 ``VectorRetriever.search(content, k, session_id, persona_id)`` 这一
    既有窄接口，不读取 FAISS 内部向量，也不缓存查询结果。端口缺失即
    ``semantic_unavailable``，由协调器按未命中降级。
    """

    retriever = getattr(engine, "vector_retriever", None)
    search = getattr(retriever, "search", None)
    if not callable(search):
        return None
    search_call = cast(Callable[..., Awaitable[Any]], search)

    async def search_semantic(query: SemanticQuery) -> list[SemanticCandidate]:
        """返回有界候选投影；坏行只跳过，不构造近似候选。"""

        results = await search_call(
            query.content,
            query.limit,
            query.session_id,
            query.persona_id,
        )
        candidates: list[SemanticCandidate] = []
        for result in results or ():
            memory_id = getattr(result, "doc_id", None)
            score = getattr(result, "score", None)
            if (
                isinstance(memory_id, bool)
                or not isinstance(memory_id, int)
                or memory_id <= 0
            ):
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            candidates.append(SemanticCandidate(memory_id, float(score)))
        return candidates

    return search_semantic


def _ranked_candidates(
    fetched: Any,
    *,
    threshold: float,
) -> list[tuple[float, int]]:
    """把端口返回值规范化为 ``(分数降序, ID 升序)`` 的候选列表。

    非序列返回值按不可用处理（返回空列表）；超出阈值或非有限分数的候选在
    加载 canonical 之前就被丢弃，避免无谓回读。
    """

    try:
        items = tuple(fetched)
    except TypeError:
        return []
    floor = _safe_threshold(threshold)
    ranked: list[tuple[float, int]] = []
    for item in items:
        memory_id = getattr(item, "memory_id", None)
        score = getattr(item, "score", None)
        if (
            isinstance(memory_id, bool)
            or not isinstance(memory_id, int)
            or memory_id <= 0
        ):
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        value = float(score)
        if not math.isfinite(value) or value < floor:
            continue
        ranked.append((value, memory_id))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return ranked


async def _load_document(
    memory_id: int,
    *,
    load_memory: SemanticLoadMemory,
    incoming: Any,
    min_tokens: int,
) -> DedupDocument | None:
    """回读 canonical 并复核作用域/召回/长度护栏；失败或不合规返回 ``None``。"""

    try:
        owner = await load_memory(memory_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    if not owner:
        return None
    owner_metadata = owner.get("metadata")
    owner_text = owner.get("text")
    if not isinstance(owner_metadata, Mapping):
        return None
    if not isinstance(owner_text, str) or not owner_text.strip():
        return None
    document = DedupDocument(
        memory_id=memory_id,
        content=owner_text,
        metadata=owner_metadata,
    )
    if not document_is_comparable(document, incoming, min_tokens=min_tokens):
        return None
    return document


def _safe_threshold(value: Any) -> float:
    """把阈值规范化为 0..1；非法值按最高阈值处理（不产生命中）。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    number = float(value)
    if not math.isfinite(number):
        return 1.0
    return max(0.0, min(1.0, number))


__all__ = [
    "FactEvidenceGuard",
    "MAX_TRACKED_WINDOWS",
    "SEMANTIC_METRIC_MODES",
    "SEMANTIC_OUTCOMES",
    "SEMANTIC_OUTCOME_BUDGET_EXHAUSTED",
    "SEMANTIC_OUTCOME_CHECKED",
    "SEMANTIC_OUTCOME_FAILED",
    "SEMANTIC_OUTCOME_HIT",
    "SEMANTIC_OUTCOME_UNAVAILABLE",
    "SEMANTIC_REQUESTS_PER_WINDOW",
    "SemanticCandidate",
    "SemanticDecision",
    "SemanticDocumentSearch",
    "SemanticLoadMemory",
    "SemanticQuery",
    "SemanticRequestBudget",
    "build_semantic_document_search",
    "detect_semantic_duplicate",
]
