"""
MMR去重模块 - 最大边际相关性（Maximum Marginal Relevance）算法

使用内容词袋相似度作为轻量代理（无需额外向量计算）。
mmr_lambda 越高越偏向相关性，越低越偏向多样性。
"""

from .rrf_fusion import HybridResult


def apply_mmr(
    results: list[HybridResult], k: int, mmr_lambda: float
) -> list[HybridResult]:
    """
    最大边际相关性（MMR）去重，避免多条语义重复的记忆占据 Top-K。

    使用内容词袋相似度作为轻量代理（无需额外向量计算）。
    mmr_lambda 越高越偏向相关性，越低越偏向多样性。

    Args:
        results: 已按 final_score 降序排列的候选结果
        k: 最终返回数量
        mmr_lambda: 相关性 vs 多样性权衡参数（0~1）

    Returns:
        List[HybridResult]: 去重后的结果
    """
    if len(results) <= k:
        return results

    def _token_set(text: str) -> set[str]:
        tokens = set(text.lower().split())
        return tokens if tokens else {"<empty>"}

    selected: list[HybridResult] = []
    candidates = list(results)

    while candidates and len(selected) < k:
        if not selected:
            # 第一条直接选最高分
            selected.append(candidates.pop(0))
            continue

        best_idx = -1
        best_mmr = -1.0
        selected_tokens = [_token_set(s.content) for s in selected]

        for i, cand in enumerate(candidates):
            cand_tokens = _token_set(cand.content)
            # 与已选结果的最大 Jaccard 相似度
            max_sim = max(
                len(cand_tokens & st) / max(len(cand_tokens | st), 1)
                for st in selected_tokens
            )
            mmr_score = mmr_lambda * cand.final_score - (1 - mmr_lambda) * max_sim
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_idx >= 0:
            selected.append(candidates.pop(best_idx))
        else:
            break

    return selected


__all__ = ["apply_mmr"]
