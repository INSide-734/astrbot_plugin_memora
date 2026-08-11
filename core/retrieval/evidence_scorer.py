"""证据评分器 — 对检索候选在 temporal/entity/focus/cross-query 维度上打分，
产生有界的证据加分，不删除候选、不改 ID、不绕过 scope/privacy/revision/validity。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..shared.temporal import normalize_datetime, parse_datetime
from ..utils.number_utils import clamp_float
from .rrf_fusion import HybridResult

if TYPE_CHECKING:
    from .query_planner import QueryPlan


@dataclass(frozen=True, slots=True)
class EvidenceWeights:
    """证据维度权重配置。

    所有权重加和为 0.25（留给其他评分维度空间）；
    mismatch_penalty 独立计入扣分通道。
    """

    temporal: float = 0.06
    entity_role: float = 0.06
    focus: float = 0.05
    cross_query: float = 0.04
    mismatch_penalty: float = 0.04


class RetrievalEvidenceScorer:
    """对检索候选进行多维度证据评分。

    评分维度：
    - temporal_fit: 候选时间戳与查询时间意图的匹配度
    - entity_role_fit: 候选实体/角色与查询实体的匹配度
    - focus_fit: 候选内容对查询主题的聚焦程度
    - cross_query_support: 多查询变体对该候选的支持度

    加分上限 0.15，扣分上限 0.08，终分 clamp 到 [0, 1]。
    """

    def __init__(self, weights: EvidenceWeights | None = None) -> None:
        self._weights = weights or EvidenceWeights()

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def score(
        self,
        candidates: list[HybridResult],
        query_plan: QueryPlan,
    ) -> list[HybridResult]:
        """计算每个候选的证据得分并返回更新后的候选副本。

        不删除候选、不改 doc_id、不绕过 scope/privacy/revision/validity。
        """
        if not candidates or query_plan is None:
            return candidates

        queries = getattr(query_plan, "queries", []) or []
        intent = getattr(query_plan, "intent", "default") or "default"
        entities = list(getattr(query_plan, "entities", ()) or ())
        temporal_anchor = getattr(query_plan, "temporal_anchor", None)
        reference_time = normalize_datetime(
            getattr(query_plan, "reference_time", None)
        ) or datetime.now(timezone.utc)
        focus_terms = list(getattr(query_plan, "focus_terms", ()) or ())

        scored: list[HybridResult] = []

        for candidate in candidates:
            copied = replace(
                candidate,
                metadata=dict(candidate.metadata or {}),
                score_breakdown=dict(candidate.score_breakdown or {}),
            )
            temporal_fit = self._compute_temporal_fit(
                copied,
                intent,
                temporal_anchor,
                reference_time,
            )
            entity_role_fit = self._compute_entity_role_fit(copied, entities, intent)
            focus_fit = self._compute_focus_fit(copied, queries, focus_terms)
            cross_query_support = self._compute_cross_query_support(copied, queries)

            evidence_bonus = min(
                0.15,
                temporal_fit * self._weights.temporal
                + entity_role_fit * self._weights.entity_role
                + focus_fit * self._weights.focus
                + cross_query_support * self._weights.cross_query,
            )

            mismatch = self._compute_mismatch_penalty(
                copied,
                entities,
                intent,
                temporal_anchor,
            )
            evidence_penalty = min(0.08, mismatch * self._weights.mismatch_penalty)

            copied.final_score = clamp_float(
                copied.final_score + evidence_bonus - evidence_penalty,
                minimum=0.0,
                maximum=1.0,
            )

            breakdown = copied.score_breakdown or {}
            breakdown.update(
                {
                    "temporal_fit": round(temporal_fit, 4),
                    "entity_role_fit": round(entity_role_fit, 4),
                    "focus_fit": round(focus_fit, 4),
                    "cross_query_support": round(cross_query_support, 4),
                    "evidence_bonus": round(evidence_bonus, 4),
                    "evidence_penalty": round(evidence_penalty, 4),
                    "time": round(temporal_fit, 4),
                    "entity": round(entity_role_fit, 4),
                    "role": round(entity_role_fit, 4),
                    "focus": round(focus_fit, 4),
                    "event": round(max(temporal_fit, focus_fit), 4),
                    "relation": round(entity_role_fit, 4),
                }
            )
            copied.score_breakdown = breakdown
            scored.append(copied)

        scored.sort(key=lambda item: (-item.final_score, item.doc_id))
        return scored

    # ------------------------------------------------------------------
    # 维度计算
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_temporal_fit(
        candidate: HybridResult,
        intent: str,
        temporal_anchor: str | None,
        reference_time: datetime,
    ) -> float:
        """评估候选时间戳与查询时间意图的匹配度。

        - temporal 意图 + 候选有明确时间戳 → 高分
        - 非 temporal 意图 → 中低分（时间相关性仍有一些信号价值）
        - 时间戳与 time_ref 接近 → 额外加分
        """
        meta = candidate.metadata or {}
        timestamp = (
            meta.get("event_time") or meta.get("timestamp") or meta.get("create_time")
        )
        content = candidate.content or ""

        has_timestamp = timestamp is not None
        has_time_content = any(
            kw in content.casefold()
            for kw in (
                "刚才",
                "今天",
                "昨天",
                "上周",
                "上个月",
                "去年",
                "之前",
                "最近",
                "just now",
                "today",
                "yesterday",
                "last week",
                "recently",
                "ago",
            )
        )

        base = 0.0
        if intent in ("temporal", "contextual"):
            base = 0.7 if has_timestamp else 0.3
        elif has_timestamp or has_time_content:
            base = 0.4
        else:
            base = 0.1

        candidate_time = parse_datetime(timestamp)
        if temporal_anchor and candidate_time is not None:
            anchor = temporal_anchor.casefold().strip()
            if len(anchor) >= 7 and anchor[4] == "-" and anchor[:4].isdigit():
                try:
                    year, month = (int(part) for part in anchor[:7].split("-"))
                except ValueError:
                    year, month = 0, 0
                base = (
                    1.0
                    if (candidate_time.year, candidate_time.month) == (year, month)
                    else 0.0
                )
            elif anchor == "today":
                base = 1.0 if candidate_time.date() == reference_time.date() else 0.0
            elif anchor == "yesterday":
                delta_days = (reference_time.date() - candidate_time.date()).days
                base = 1.0 if delta_days == 1 else 0.0
            elif anchor in {"recent", "this_week"}:
                delta_days = (
                    abs((reference_time - candidate_time).total_seconds()) / 86400.0
                )
                window = 7.0 if anchor == "this_week" else 30.0
                base = max(0.0, 1.0 - delta_days / window)

        return min(1.0, max(0.0, base))

    @staticmethod
    def _compute_entity_role_fit(
        candidate: HybridResult,
        entities: list[str],
        intent: str,
    ) -> float:
        """评估候选内容/元数据中实体与查询实体的匹配度。

        - 精确实体名称匹配 → 高信号
        - speaker_name 匹配 → 角色匹配信号
        - 内容中部分实体出现 → 中等信号
        """
        if not entities:
            return 0.0

        content = (candidate.content or "").casefold()
        meta = candidate.metadata or {}
        speaker_name = str(meta.get("speaker_name", "") or "").casefold()
        speaker_id = str(meta.get("speaker_id", "") or "").casefold()

        matched = 0
        for entity in entities:
            entity_lower = entity.casefold()
            # 精确内容匹配
            if entity_lower in content:
                matched += 1
                continue
            # 角色匹配（发言人）
            if speaker_name and entity_lower in speaker_name:
                matched += 1
                continue
            if speaker_id and entity_lower in speaker_id:
                matched += 1
                continue
            # 部分词匹配（entity 词在内容中出现）
            entity_words = entity_lower.split()
            if len(entity_words) > 1 and all(w in content for w in entity_words):
                matched += 0.5

        ratio = matched / max(1, len(entities))
        # 关系类查询对实体匹配更敏感
        sensitivity = 1.2 if intent in ("relationship", "relational") else 1.0
        return min(1.0, ratio * sensitivity)

    @staticmethod
    def _compute_focus_fit(
        candidate: HybridResult,
        queries: list[str],
        focus_terms: list[str],
    ) -> float:
        """评估候选内容对查询主题的聚焦程度。

        使用查询词的词频-逆文档启发式：查询词在候选内容中出现越集中越聚焦。
        focus 字段提供额外的查询焦点词。
        """
        content = (candidate.content or "").casefold()
        if not content:
            return 0.0

        normalized_focus_terms: set[str] = {
            term.casefold().strip()
            for term in focus_terms
            if isinstance(term, str) and len(term.strip()) > 1
        }
        for q in queries or []:
            normalized_focus_terms.update(
                term for term in q.casefold().split() if len(term) > 1
            )

        if not normalized_focus_terms:
            return 0.0
        hits = sum(1 for term in normalized_focus_terms if term in content)
        return min(1.0, hits / len(normalized_focus_terms))

    @staticmethod
    def _compute_cross_query_support(
        candidate: HybridResult,
        queries: list[str],
    ) -> float:
        """评估候选获得多少查询变体的支持。

        每个查询变体检查候选内容是否包含其关键词；
        支持查询越多，cross_query_support 越高。
        """
        existing = (candidate.score_breakdown or {}).get("cross_query_support")
        if isinstance(existing, (int, float)) and not isinstance(existing, bool):
            return min(1.0, max(0.0, float(existing)) / 0.08)
        if not queries or len(queries) <= 1:
            return 0.0

        content = (candidate.content or "").casefold()
        if not content:
            return 0.0

        supporting = 0
        for q in queries:
            q_lower = q.casefold()
            # 至少一半的查询词出现在内容中
            q_words = [w for w in q_lower.split() if len(w) > 1]
            if not q_words:
                continue
            hits = sum(1 for w in q_words if w in content)
            if hits >= max(1, len(q_words) // 2):
                supporting += 1

        ratio = supporting / max(1, len(queries))
        # 二次方放大：1/3 支持 → 0.11，2/3 支持 → 0.44，3/3 支持 → 1.0
        return ratio * ratio

    @staticmethod
    def _compute_mismatch_penalty(
        candidate: HybridResult,
        entities: list[str],
        intent: str,
        temporal_anchor: str | None,
    ) -> float:
        """计算候选与查询之间的不匹配惩罚。

        - 查询有明确实体但候选完全不涉及 → 小罚
        - temporal 查询但候选无时间信息 → 小罚
        - 最终乘 mismatch_penalty 权重后上限 0.08
        """
        penalty = 0.0
        content = (candidate.content or "").casefold()
        meta = candidate.metadata or {}

        # 实体缺失惩罚
        if entities:
            any_match = any(e.casefold() in content for e in entities)
            speaker = str(meta.get("speaker_name", "") or "").casefold()
            any_role_match = any(e.casefold() in speaker for e in entities)
            if not any_match and not any_role_match:
                penalty += 0.5

        timestamp = (
            meta.get("event_time")
            or meta.get("timestamp")
            or meta.get("create_time")
            or meta.get("last_access_time")
        )

        # 时间意图但候选没有任何时间证据。
        if intent in ("temporal",):
            if not timestamp:
                penalty += 0.3

        # 已指定时间锚点但候选无时间信息。
        if temporal_anchor and not timestamp:
            penalty += 0.15

        return min(1.0, penalty)


__all__ = ["EvidenceWeights", "RetrievalEvidenceScorer"]
