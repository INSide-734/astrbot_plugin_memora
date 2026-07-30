"""多查询预算切分与 RRF 融合。

将 QueryPlan 的多条查询分别检索后，通过 Reciprocal Rank Fusion
跨查询合并候选记忆，并附加 cross_query_support 奖励。
"""

from __future__ import annotations

from .rrf_fusion import HybridResult


def split_candidate_budget(total: int, query_count: int) -> tuple[int, ...]:
    """确定性切分单路总候选预算。

    Args:
        total: 可分配的候选总数。
        query_count: 查询数量（内部上限 3）。

    Returns:
        tuple[int, ...]: 每路查询的预算，总和等于 `total`，
        差值不超过 1。空列表返回 (total,)。
    """
    count = min(max(1, query_count), 3)
    safe_total = max(0, total)
    base, remainder = divmod(safe_total, count)
    return tuple(base + (1 if index < remainder else 0) for index in range(count))


def fuse_query_results(
    per_query_results: list[list[HybridResult]],
    limit: int,
    *,
    rrf_k: int = 60,
) -> list[HybridResult]:
    """多查询 RRF 融合，附加 bounded cross_query_support 奖励。

    融合策略：
    - 使用 RRF 公式在查询维度累积分数，k=60（论文推荐值）。
    - 计算跨查询支持度奖励（≤0.08），文档出现在越多查询中奖励越高。
    - 按计划顺序选取最先出现的 HybridResult 作为代表。
    - 永不修改输入对象。

    Args:
        per_query_results: 每条查询检索并合并后的 HybridResult 列表。
        limit: 返回数量上限。
        rrf_k: RRF 衰减常数（默认 60）。

    Returns:
        list[HybridResult]: 按融合分数降序排列的候选。
    """
    if not per_query_results:
        return []

    doc_rrf: dict[int, float] = {}
    doc_representative: dict[int, HybridResult] = {}
    doc_query_mask: dict[int, int] = {}  # bitmask of queries that returned this doc
    doc_best_rank: dict[int, int] = {}
    doc_breakdowns: dict[int, dict[str, float]] = {}

    for query_idx, results in enumerate(per_query_results):
        for rank, result in enumerate(results):
            doc_id = result.doc_id
            rrf_contrib = 1.0 / (rrf_k + rank + 1)
            doc_rrf[doc_id] = doc_rrf.get(doc_id, 0.0) + rrf_contrib
            doc_query_mask[doc_id] = doc_query_mask.get(doc_id, 0) | (1 << query_idx)
            doc_best_rank[doc_id] = min(doc_best_rank.get(doc_id, rank), rank)
            breakdown = doc_breakdowns.setdefault(doc_id, {})
            for key, value in (result.score_breakdown or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    breakdown[key] = max(
                        breakdown.get(key, float("-inf")), float(value)
                    )
            # 按计划顺序：第一个出现的作为代表
            if doc_id not in doc_representative:
                doc_representative[doc_id] = result

    # 添加 cross_query_support 奖励
    for doc_id in list(doc_rrf):
        appearances = doc_query_mask[doc_id].bit_count()
        cross_query_bonus = round(min(0.08, (appearances - 1) * 0.04), 6)
        doc_rrf[doc_id] = round(doc_rrf[doc_id] + cross_query_bonus, 6)

    # 按融合分数降序排列
    sorted_docs = sorted(
        doc_rrf.items(),
        key=lambda item: (-item[1], doc_best_rank[item[0]], item[0]),
    )

    fused: list[HybridResult] = []
    for doc_id, rrf_total in sorted_docs[:limit]:
        best = doc_representative[doc_id]
        appearances = doc_query_mask[doc_id].bit_count()
        cross_query_bonus = round(min(0.08, (appearances - 1) * 0.04), 6)

        # 合并 score_breakdown（不修改原始对象）
        score_breakdown = dict(doc_breakdowns.get(doc_id, {}))
        score_breakdown["cross_query_support"] = cross_query_bonus
        score_breakdown["multi_query_rrf"] = rrf_total

        fused.append(
            HybridResult(
                doc_id=doc_id,
                final_score=min(1.0, rrf_total),
                rrf_score=rrf_total,
                bm25_score=best.bm25_score,
                vector_score=best.vector_score,
                content=best.content,
                metadata=dict(best.metadata),
                score_breakdown=score_breakdown,
            )
        )

    return fused


__all__ = ["split_candidate_budget", "fuse_query_results"]
