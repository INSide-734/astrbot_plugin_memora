"""Jargon 系统数据模型。

定义统计预过滤器和 LLM Miner 共用的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JargonCandidate:
    """经统计预过滤器筛选出的黑话候选词。

    三信号综合评分后的单个候选词条目。
    """

    term: str
    """候选词文本"""

    group_id: str
    """来源群组 ID"""

    score: float
    """三信号综合评分 [0, 1]，越高越可能是群内黑话"""

    frequency: int
    """该词在该群的出现次数"""

    unique_users: int
    """使用该词的不同用户数"""

    idf_score: float
    """信号 1：跨群 IDF 评分（在群内常见但跨群罕见→高分）"""

    burst_score: float
    """信号 2：爆发频率评分（近期高频→高分）"""

    concentration_score: float
    """信号 3：用户集中度评分（少数人使用→高分）"""

    first_seen: float
    """首次出现时间戳（Unix epoch）"""

    context_examples: list[str] = field(default_factory=list)
    """上下文示例消息（最多 10 条）"""


@dataclass
class JargonStats:
    """群组统计摘要，供 Dashboard 和 API 使用。"""

    group_id: str
    """群组 ID"""

    total_terms: int
    """该群追踪的词条总数"""

    candidate_count: int
    """通过评分阈值的候选词数量"""

    top_candidates: list[JargonCandidate] = field(default_factory=list)
    """Top 10 候选词（按评分降序）"""


@dataclass
class JargonMeaning:
    """LLM 推断出的黑话含义。

    由 :class:`JargonMiner` 三步推断引擎产生，存储于 :class:`JargonStore`。
    """

    term: str
    """黑话词条文本"""

    group_id: str
    """来源群组 ID"""

    meaning: str = ""
    """推断出的含义描述"""

    confidence: float = 0.0
    """置信度 [0, 1]，基于信号强度和推断一致性"""

    is_jargon: bool = False
    """是否为真黑话（三步推断第 3 步判定）"""

    is_confirmed: bool = False
    """是否已人工确认"""

    is_global: bool = False
    """是否为跨群通用黑话"""

    is_complete: bool = False
    """推断是否完成（count >= 100 或人工确认）"""

    count: int = 0
    """使用次数（统计过滤器统计）"""

    last_inference_count: int = 0
    """上次触发推断时的 count 值"""

    context_examples: list[str] = field(default_factory=list)
    """上下文示例消息列表"""

    created_at: float = 0.0
    """首次创建时间戳（Unix epoch）"""

    updated_at: float = 0.0
    """最后更新时间戳（Unix epoch）"""


__all__ = [
    "JargonCandidate",
    "JargonMeaning",
    "JargonStats",
]
