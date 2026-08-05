"""
评分加权模块 - 对RRF融合结果应用重要性加权和时间衰减

使用加权求和（而非乘法）避免任何单一维度低分导致整体清零。
时间衰减基于 max(create_time, last_access_time)，高频访问记忆衰减更慢。
"""

import json
import math

from astrbot.api import logger

from ..utils.number_utils import clamp_float, safe_float
from .rrf_fusion import FusedResult, HybridResult


class ScoreWeighting:
    """
    评分加权器

    对RRF融合后的结果应用三阶段后处理：
    1. 元数据安全解析（兼容字符串/None/字典类型）
    2. 重要性提取 + 时间衰减计算
    3. 加权求和排序（检索相关性 + 重要性 + 时间新鲜度）
    """

    def __init__(
        self,
        decay_rate: float = 0.01,
        importance_weight: float = 1.0,
        score_alpha: float = 0.5,
        score_beta: float = 0.25,
        score_gamma: float = 0.25,
        recency_bump_enabled: bool = True,
    ) -> None:
        """
        初始化评分加权器

        参数:
            decay_rate: 时间衰减率,默认0.01
            importance_weight: 重要性权重,默认1.0
            score_alpha: 检索相关性维度权重,默认0.5
            score_beta: 重要性维度权重,默认0.25
            score_gamma: 时间新鲜度维度权重,默认0.25
            recency_bump_enabled: 是否对三十天内记忆应用近因额外加成。
        """
        self.decay_rate = decay_rate
        self.importance_weight = importance_weight
        self.score_alpha = score_alpha
        self.score_beta = score_beta
        self.score_gamma = score_gamma
        self.recency_bump_enabled = recency_bump_enabled

    @staticmethod
    def _recency_bump_score(days_old: float | None) -> float:
        """按记忆年龄返回近因加成倍率，非法年龄保持中性。"""

        if days_old is None or days_old < 0:
            return 1.0
        if days_old <= 7:
            return 1.5
        elif days_old <= 30:
            return 1.2
        return 1.0

    def apply_weighting(
        self, fused_results: list[FusedResult], current_time: float
    ) -> list[HybridResult]:
        """
        应用重要性和时间衰减加权

        使用加权求和（而非乘法）避免任何单一维度低分导致整体清零。
        时间衰减基于 max(create_time, last_access_time)，高频访问记忆衰减更慢。

        参数:
            fused_results: RRF融合后的结果
            current_time: 当前时间戳

        返回:
            加权后的结果列表，按最终分数降序排列。
        """
        if not fused_results:
            return []

        # 先归一化 RRF 分数到 [0, 1]
        max_rrf = max(r.rrf_score for r in fused_results)
        if max_rrf <= 0:
            max_rrf = 1.0

        hybrid_results = []

        for result in fused_results:
            # 安全解析metadata，确保它是字典类型
            metadata = result.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                    logger.debug(
                        f"[hybrid_retriever] 将字符串metadata转换为字典: doc_id={result.doc_id}"
                    )
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(
                        f"[hybrid_retriever] 解析metadata JSON失败: {e}, doc_id={result.doc_id}, "
                        f"metadata类型={type(metadata)}, 使用空字典"
                    )
                    metadata = {}
            elif metadata is None:
                logger.debug(
                    f"[hybrid_retriever] metadata为None, doc_id={result.doc_id}, 使用空字典"
                )
                metadata = {}
            elif not isinstance(metadata, dict):
                logger.warning(
                    f"[hybrid_retriever] metadata类型不支持: {type(metadata)}, doc_id={result.doc_id}, "
                    f"使用空字典"
                )
                metadata = {}

            # 获取重要性(默认0.5)，限制在 [0, 1]
            importance = clamp_float(metadata.get("importance"), default=0.5)

            # 时间衰减：取 create_time 与 last_access_time 的较大值
            # 高频访问的记忆衰减更慢，符合"记忆强化"认知规律
            create_time = safe_float(metadata.get("create_time"), current_time)
            last_access_time = safe_float(metadata.get("last_access_time"), 0.0)
            reference_time = max(create_time, last_access_time)
            days_old = max(0.0, (current_time - reference_time) / 86400)
            recency_bump = (
                self._recency_bump_score(days_old) if self.recency_bump_enabled else 1.0
            )
            recency_weight = math.exp(-self.decay_rate * days_old) * recency_bump

            # 归一化 RRF 分数
            rrf_normalized = result.rrf_score / max_rrf

            # 加权求和：各维度互补而非互斥
            final_score = (
                self.score_alpha * rrf_normalized
                + self.score_beta * importance
                + self.score_gamma * recency_weight
            )

            score_breakdown = {
                "rrf_normalized": round(rrf_normalized, 4),
                "importance": round(importance, 4),
                "recency_weight": round(recency_weight, 4),
                "days_old": round(days_old, 2),
                "final_score": round(final_score, 4),
            }

            hybrid_results.append(
                HybridResult(
                    doc_id=result.doc_id,
                    final_score=final_score,
                    rrf_score=result.rrf_score,
                    bm25_score=result.bm25_score,
                    vector_score=result.vector_score,
                    content=result.content,
                    metadata=metadata,
                    score_breakdown=score_breakdown,
                )
            )

        # 按最终分数降序排序
        hybrid_results.sort(key=lambda x: x.final_score, reverse=True)

        return hybrid_results


__all__ = ["ScoreWeighting"]
