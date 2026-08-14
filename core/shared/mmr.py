"""基于最大边际相关性的共享轻量重排策略。"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from .adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    ScoreDirection,
    ScoreSemantics,
)


class _MMRRankedItem(Protocol):
    """MMR 算法所需的最小候选结构。"""

    content: str
    final_score: float


_MMRItemT = TypeVar("_MMRItemT", bound=_MMRRankedItem)


def apply_mmr(results: list[_MMRItemT], k: int, mmr_lambda: float) -> list[_MMRItemT]:
    """执行最大边际相关性去重，避免重复候选占据 Top-K。

    使用内容词袋相似度作为轻量代理（无需额外向量计算）。
    mmr_lambda 越高越偏向相关性，越低越偏向多样性。

    参数:
        results: 已按 ``final_score`` 降序排列的候选结果。
        k: 最终返回数量。
        mmr_lambda: 相关性与多样性的权衡参数，取值范围为 0..1。

    返回:
        去重后的候选结果。
    """
    if len(results) <= k:
        return results

    def _token_set(text: str) -> set[str]:
        tokens = set(text.lower().split())
        return tokens if tokens else {"<empty>"}

    selected: list[_MMRItemT] = []
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


class MMRReranker:
    """不依赖外部 Provider 的 MMR 重排序器。"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.RERANKER,
        native=frozenset({AdapterCapability.SCORING}),
        score=ScoreSemantics(direction=ScoreDirection.HIGHER_IS_BETTER),
    )

    def __init__(
        self,
        mmr_lambda: float = 0.7,
        *,
        degradation_reason_code: str | None = None,
    ) -> None:
        """初始化 MMR 权重和可选的稳定降级原因码。"""

        self._lambda = mmr_lambda
        self.degradation_reason_code = degradation_reason_code

    def rerank(
        self,
        results: list[_MMRItemT],
        k: int,
        **kwargs: Any,
    ) -> list[_MMRItemT]:
        """使用词袋相似度 MMR 重排，并返回最多 ``k`` 项。"""

        return apply_mmr(results, k, self._lambda)


__all__ = ["MMRReranker", "apply_mmr"]
