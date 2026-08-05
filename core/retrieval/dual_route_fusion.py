"""双路召回候选的权重选择、缺失数据回填与分数融合。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ..models.recall_strategy import RecallStrategy
from .intent_keywords import FACTUAL_TERMS, RELATION_TERMS, TEMPORAL_TERMS
from .rrf_fusion import HybridResult

if TYPE_CHECKING:
    from .graph_retriever import GraphResult
    from .query_rewriter import QueryIntent


async def merge_dual_results(
    doc_results: list[HybridResult],
    graph_results: list[GraphResult],
    query: str,
    *,
    memory_loader: Callable[[int], Awaitable[dict[str, Any] | None]],
    document_route_weight: float,
    graph_route_weight: float,
    cross_route_bonus: float,
    dynamic_route_weighting: bool,
    atom_route_weight: float,
    strategy: RecallStrategy | None = None,
    query_intent: QueryIntent | None = None,
    atom_scores: dict[int, float] | None = None,
) -> list[HybridResult]:
    """把文档、图与 Atom 父级证据统一融合为 canonical 结果。

    Args:
        doc_results: 文档路候选。
        graph_results: 图路候选。
        query: 当前召回查询，仅用于选择动态路由权重。
        memory_loader: 按 canonical ID 回填正文和元数据的异步函数。
        document_route_weight: 文档路基础权重。
        graph_route_weight: 图路基础权重。
        cross_route_bonus: 同一候选被两路命中时的加分。
        dynamic_route_weighting: 是否按意图动态调整路由权重。
        atom_route_weight: 存在 Atom 证据时为 Atom 路保留的权重。
        strategy: 可选的显式召回策略，优先于查询意图。
        query_intent: 可选的结构化查询意图。
        atom_scores: canonical ID 到 Atom 证据分数的映射。

    Returns:
        按最终分数降序、canonical ID 升序排列的候选副本列表。

    Notes:
        回填失败的候选会被跳过；协程取消仍由 ``asyncio.gather`` 向上传播。
    """

    normalized_atom_scores = atom_scores or {}
    document_weight, graph_weight, intent = _resolve_route_weights(
        query,
        strategy=strategy,
        query_intent=query_intent,
        document_route_weight=document_route_weight,
        graph_route_weight=graph_route_weight,
        dynamic_route_weighting=dynamic_route_weighting,
    )
    atom_weight = (
        max(0.0, min(0.5, float(atom_route_weight))) if normalized_atom_scores else 0.0
    )
    base_route_scale = 1.0 - atom_weight
    document_weight *= base_route_scale
    graph_weight *= base_route_scale

    doc_map = {item.doc_id: item for item in doc_results}
    graph_map = {item.doc_id: item for item in graph_results}
    all_doc_ids = set(doc_map) | set(graph_map) | set(normalized_atom_scores)
    loaded = await _load_missing_memories(all_doc_ids, doc_map, memory_loader)

    document_max = max((item.final_score for item in doc_results), default=1.0) or 1.0
    graph_max = max((item.final_score for item in graph_results), default=1.0) or 1.0
    atom_max = max(normalized_atom_scores.values(), default=1.0) or 1.0

    merged_results = [
        merged
        for doc_id in all_doc_ids
        if (
            merged := _merge_candidate(
                doc_id,
                doc_result=doc_map.get(doc_id),
                graph_result=graph_map.get(doc_id),
                loaded_memory=loaded.get(doc_id),
                atom_score=normalized_atom_scores.get(doc_id, 0.0),
                document_max=document_max,
                graph_max=graph_max,
                atom_max=atom_max,
                document_weight=document_weight,
                graph_weight=graph_weight,
                atom_weight=atom_weight,
                cross_route_bonus=cross_route_bonus,
                intent=intent,
            )
        )
        is not None
    ]
    merged_results.sort(key=lambda item: (-item.final_score, item.doc_id))
    return merged_results


def build_score_breakdown(
    *,
    doc_result: HybridResult | None,
    graph_result: GraphResult | None,
    doc_signal: float,
    graph_signal: float,
    document_weight: float,
    graph_weight: float,
    route_bonus: float,
    final_score: float,
    intent: str,
) -> dict[str, float]:
    """合并两条检索路的内部证据并补充双路融合解释字段。

    Args:
        doc_result: 可选的文档路候选。
        graph_result: 可选的图路候选。
        doc_signal: 归一化后的文档路分数。
        graph_signal: 归一化后的图路分数。
        document_weight: 本次查询使用的文档路权重。
        graph_weight: 本次查询使用的图路权重。
        route_bonus: 双路同时命中的加分。
        final_score: 融合后的最终分数。
        intent: 选中当前权重的意图标识。

    Returns:
        保留原始路内证据并追加双路解释字段的字典。
    """

    score_breakdown: dict[str, float] = {}
    if doc_result and doc_result.score_breakdown:
        score_breakdown.update(doc_result.score_breakdown)
    if graph_result and graph_result.score_breakdown:
        score_breakdown.update(graph_result.score_breakdown)
    score_breakdown.update(
        {
            "document_route_score": round(doc_signal, 4),
            "graph_route_score": round(graph_signal, 4),
            "document_route_weight": round(document_weight, 4),
            "graph_route_weight": round(graph_weight, 4),
            "cross_route_bonus": round(route_bonus, 4),
            "dual_route_final_score": round(final_score, 4),
        }
    )
    if intent:
        score_breakdown["query_intent"] = intent
    if doc_result is not None:
        score_breakdown["document_keyword_score"] = round(
            float(doc_result.bm25_score or 0.0), 4
        )
        score_breakdown["document_vector_score"] = round(
            float(doc_result.vector_score or 0.0), 4
        )
    if graph_result is not None:
        score_breakdown["graph_keyword_score"] = round(
            float(graph_result.keyword_score or 0.0), 4
        )
        score_breakdown["graph_vector_score"] = round(
            float(graph_result.vector_score or 0.0), 4
        )
    return score_breakdown


def route_weights_for_query(
    query: str,
    *,
    document_route_weight: float,
    graph_route_weight: float,
    dynamic_route_weighting: bool,
    query_intent: QueryIntent | None = None,
) -> tuple[float, float, str]:
    """根据结构化意图或关键词降级结果选择文档路与图路权重。

    Args:
        query: 当前召回查询。
        document_route_weight: 文档路基础权重。
        graph_route_weight: 图路基础权重。
        dynamic_route_weighting: 是否允许动态调整权重。
        query_intent: 可选的结构化查询意图。

    Returns:
        文档路权重、图路权重和可解释意图标识。
    """

    base_document = document_route_weight
    base_graph = graph_route_weight
    if not dynamic_route_weighting:
        return base_document, base_graph, "fixed"

    if query_intent is not None and query_intent.intent != "default":
        intent = query_intent.intent
        if intent == "relationship":
            return (
                max(0.15, base_document - 0.25),
                min(0.85, base_graph + 0.25),
                "llm:relationship",
            )
        if intent == "temporal":
            return (
                max(0.15, base_document - 0.15),
                min(0.85, base_graph + 0.15),
                "llm:temporal",
            )
        if intent == "factual":
            return (
                min(0.9, base_document + 0.2),
                max(0.1, base_graph - 0.2),
                "llm:factual",
            )
        if intent == "preference":
            return (
                min(0.9, base_document + 0.1),
                max(0.1, base_graph - 0.1),
                "llm:preference",
            )

    normalized = query.casefold()
    relation_hit = any(term in normalized for term in RELATION_TERMS)
    temporal_hit = any(term in normalized for term in TEMPORAL_TERMS)
    factual_hit = any(term in normalized for term in FACTUAL_TERMS)

    document_weight = base_document
    graph_weight = base_graph
    intent = "default"
    if relation_hit:
        graph_weight += 0.2
        document_weight -= 0.2
        intent = "relationship"
    if temporal_hit:
        graph_weight += 0.1
        document_weight -= 0.1
        intent = "temporal" if intent == "default" else f"{intent}+temporal"
    if factual_hit and not relation_hit:
        document_weight += 0.15
        graph_weight -= 0.15
        intent = "factual" if intent == "default" else f"{intent}+factual"

    document_weight = max(0.15, min(0.9, document_weight))
    graph_weight = max(0.1, min(0.85, graph_weight))
    total = document_weight + graph_weight
    if total <= 0:
        return base_document, base_graph, "fixed"
    return document_weight / total, graph_weight / total, intent


def compute_strategy_weights(strategy: RecallStrategy) -> tuple[float, float]:
    """把显式召回策略映射为稳定的文档路与图路权重。

    Args:
        strategy: 调用方选择的召回策略。

    Returns:
        文档路与图路权重；未知策略使用均衡权重。
    """

    weights = {
        RecallStrategy.CONTEXTUAL_SIMILARITY: (0.70, 0.30),
        RecallStrategy.TOPIC_ASSOCIATION: (0.65, 0.35),
        RecallStrategy.PREFERENCE_QUERY: (0.80, 0.20),
        RecallStrategy.RELATIONSHIP_REVIEW: (0.30, 0.70),
    }
    return weights.get(strategy, (0.50, 0.50))


def _resolve_route_weights(
    query: str,
    *,
    strategy: RecallStrategy | None,
    query_intent: QueryIntent | None,
    document_route_weight: float,
    graph_route_weight: float,
    dynamic_route_weighting: bool,
) -> tuple[float, float, str]:
    """按显式策略优先级解析一次融合使用的路由权重。"""

    if strategy is not None:
        document_weight, graph_weight = compute_strategy_weights(strategy)
        return document_weight, graph_weight, "strategy"
    return route_weights_for_query(
        query,
        document_route_weight=document_route_weight,
        graph_route_weight=graph_route_weight,
        dynamic_route_weighting=dynamic_route_weighting,
        query_intent=query_intent,
    )


async def _load_missing_memories(
    doc_ids: set[int],
    doc_map: dict[int, HybridResult],
    memory_loader: Callable[[int], Awaitable[dict[str, Any] | None]],
) -> dict[int, dict[str, Any] | None]:
    """并行回填缺少正文或元数据的 canonical 记忆。"""

    need_load_ids = []
    for doc_id in doc_ids:
        doc_result = doc_map.get(doc_id)
        content = doc_result.content if doc_result is not None else ""
        metadata = (
            dict(doc_result.metadata)
            if doc_result is not None and isinstance(doc_result.metadata, dict)
            else {}
        )
        if not content or not metadata:
            need_load_ids.append(doc_id)

    loaded: dict[int, dict[str, Any] | None] = {}
    if not need_load_ids:
        return loaded
    loaded_list = await asyncio.gather(
        *(memory_loader(doc_id) for doc_id in need_load_ids),
        return_exceptions=True,
    )
    for doc_id, memory in zip(need_load_ids, loaded_list, strict=False):
        if isinstance(memory, asyncio.CancelledError):
            raise memory
        loaded[doc_id] = (
            None if isinstance(memory, BaseException) or memory is None else memory
        )
    return loaded


def _merge_candidate(
    doc_id: int,
    *,
    doc_result: HybridResult | None,
    graph_result: GraphResult | None,
    loaded_memory: dict[str, Any] | None,
    atom_score: float,
    document_max: float,
    graph_max: float,
    atom_max: float,
    document_weight: float,
    graph_weight: float,
    atom_weight: float,
    cross_route_bonus: float,
    intent: str,
) -> HybridResult | None:
    """将同一 canonical ID 的各路信号合成为单个候选。"""

    doc_signal = doc_result.final_score / document_max if doc_result else 0.0
    graph_signal = graph_result.final_score / graph_max if graph_result else 0.0
    atom_signal = atom_score / atom_max
    route_bonus = (
        cross_route_bonus
        if doc_result is not None and graph_result is not None
        else 0.0
    )

    memory_content = doc_result.content if doc_result is not None else ""
    memory_metadata = (
        dict(doc_result.metadata)
        if doc_result is not None and isinstance(doc_result.metadata, dict)
        else {}
    )
    if not memory_content or not memory_metadata:
        if not loaded_memory:
            return None
        memory_content = str(loaded_memory.get("text") or memory_content)
        raw_metadata = loaded_memory.get("metadata") or memory_metadata
        memory_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

    final_score = min(
        1.0,
        document_weight * doc_signal
        + graph_weight * graph_signal
        + atom_weight * atom_signal
        + route_bonus,
    )
    return HybridResult(
        doc_id=doc_id,
        final_score=final_score,
        rrf_score=max(
            doc_result.rrf_score if doc_result is not None else 0.0,
            graph_result.rrf_score if graph_result is not None else 0.0,
        ),
        bm25_score=doc_result.bm25_score if doc_result is not None else None,
        vector_score=doc_result.vector_score if doc_result is not None else None,
        content=memory_content,
        metadata=memory_metadata,
        score_breakdown=build_score_breakdown(
            doc_result=doc_result,
            graph_result=graph_result,
            doc_signal=doc_signal,
            graph_signal=graph_signal,
            document_weight=document_weight,
            graph_weight=graph_weight,
            route_bonus=route_bonus,
            final_score=final_score,
            intent=intent,
        ),
    )
