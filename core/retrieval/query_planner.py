"""检索查询计划器 — 将 QueryIntent 展开为结构化 QueryPlan。

根据规范化的意图、实体和时间锚点构建确定性查询计划，
包含焦点词、必需维度、歧义标记和去重后的查询变体。
"""

from dataclasses import dataclass
from datetime import datetime

from .query_rewriter import QueryIntent, normalize_query_intent

# ---------------------------------------------------------------------------
# 维度枚举
# ---------------------------------------------------------------------------

_VALID_FACETS = frozenset({"entity", "role", "time", "event", "focus", "relation"})
_VALID_AMBIGUITY = frozenset(
    {"pronoun", "role_conflict", "temporal_competition", "focus_missing"}
)

# ---------------------------------------------------------------------------
# QueryPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """不可变查询计划 — 一次构建后不可改变。"""

    original_query: str
    intent: str
    entities: tuple[str, ...]
    focus_terms: tuple[str, ...]
    temporal_anchor: str | None
    reference_time: datetime | None
    queries: tuple[str, ...]
    required_facets: tuple[str, ...]
    ambiguity_flags: tuple[str, ...]
    memory_types: tuple[str, ...]

    @property
    def rewritten_queries(self) -> tuple[str, ...]:
        """为现有缓存键读取提供兼容查询变体。"""

        return self.queries


# ---------------------------------------------------------------------------
# QueryPlanner
# ---------------------------------------------------------------------------


class QueryPlanner:
    """从 QueryIntent 构建不可变 QueryPlan。

    规则确定性，无外部依赖：
    - 维度由实体/时间引用/查询文本信号派生
    - 歧义标记由模糊指代和冲突检测派生
    - 查询变体经过白空归一化、大小写去重、长度截断后最多保留 3 条
    """

    _MAX_ENTITIES = 8
    _MAX_QUERIES = 3
    _MAX_QUERY_CHARS = 256

    # -- 维度派生 ------------------------------------------------------------

    @staticmethod
    def _infer_facets(
        intent: str,
        entities: list[str],
        time_reference: str | None,
        memory_types: list[str],
        queries: list[str],
    ) -> tuple[str, ...]:
        """根据意图、实体、时间与查询文本派生有限的必需维度。"""

        facets: list[str] = []

        if entities:
            facets.append("entity")
        if time_reference:
            facets.append("time")

        intent_norm = intent.casefold()
        if intent_norm == "relationship":
            facets.append("relation")
        if intent_norm in ("temporal", "contextual"):
            facets.append("event")

        # focus: 如果查询中包含明显聚焦词
        combined = " ".join(queries).casefold()
        if any(term in combined for term in ("是什么", "定义", "解释", "怎么", "如何")):
            facets.append("focus")

        # role: 如果 memory_types 中包含与关系/角色相关的
        mem_types_upper = {t.upper() for t in memory_types}
        if "RELATIONAL" in mem_types_upper and "relation" not in facets:
            facets.append("relation")

        # 去重并只保留合法维度
        seen: set[str] = set()
        result: list[str] = []
        for f in facets:
            if f in _VALID_FACETS and f not in seen:
                seen.add(f)
                result.append(f)
        return tuple(result)

    # -- 歧义标记 ------------------------------------------------------------

    @staticmethod
    def _infer_ambiguity(
        original_query: str,
        entities: list[str],
        intent: str,
        time_reference: str | None,
        queries: list[str],
    ) -> tuple[str, ...]:
        """识别指代、角色、时间锚点与焦点缺失造成的查询歧义。"""

        flags: list[str] = []
        q_lower = original_query.casefold()

        # pronoun: 查询中使用模糊指代词
        pronoun_words = {
            "那个",
            "这个",
            "那次",
            "那次事",
            "上次那个",
            "那个事",
            "这件事",
            "那件事",
            "谁",
            "什么",
            "上次",
            "那次",
            "那个谁",
        }
        if any(p in q_lower for p in pronoun_words):
            flags.append("pronoun")

        # role_conflict: intent 是 relationship 但没有实体
        if intent.casefold() == "relationship" and not entities:
            flags.append("role_conflict")

        # 时间意图缺少锚点时，多个历史事件可能形成竞争候选。
        if intent.casefold() == "temporal" and not time_reference:
            flags.append("temporal_competition")

        # focus_missing: contextual/preference intent but no rewritten queries beyond original
        if intent.casefold() in ("contextual", "preference") and len(queries) <= 1:
            flags.append("focus_missing")

        seen: set[str] = set()
        result: list[str] = []
        for f in flags:
            if f in _VALID_AMBIGUITY and f not in seen:
                seen.add(f)
                result.append(f)
        return tuple(result)

    # -- 查询规范化 ----------------------------------------------------------

    @staticmethod
    def _normalize_and_dedupe_queries(
        rewritten_queries: list[str],
        original_query: str,
    ) -> tuple[str, ...]:
        """白空归一化、大小写去重、长度截断、切片至 3。"""
        normalized: list[str] = []
        seen: set[str] = set()

        # 原始查询总是第一条（去重基准）
        trimmed = " ".join(original_query.split())[: QueryPlanner._MAX_QUERY_CHARS]
        norm_key = trimmed.casefold()
        if trimmed and norm_key not in seen:
            seen.add(norm_key)
            normalized.append(trimmed)

        for q in rewritten_queries:
            q_str = str(q or "").strip()
            if not q_str:
                continue
            trimmed = " ".join(q_str.split())[: QueryPlanner._MAX_QUERY_CHARS]
            norm_key = trimmed.casefold()
            if trimmed and norm_key not in seen:
                seen.add(norm_key)
                normalized.append(trimmed)
            if len(normalized) >= QueryPlanner._MAX_QUERIES:
                break

        return tuple(normalized)

    # -- 焦点词提取 ----------------------------------------------------------

    @staticmethod
    def _extract_focus_terms(
        original_query: str, entities: list[str]
    ) -> tuple[str, ...]:
        """从原始查询中提取非实体的关键名词/动词作为焦点词。"""
        import re

        # 简单中文分词启发式：按常见分隔符和标点拆分
        tokens = re.split(r"[，。！？、\s,;.!?]+", original_query)
        stopwords = {
            "是",
            "的",
            "了",
            "吗",
            "呢",
            "吧",
            "啊",
            "在",
            "和",
            "与",
            "或",
            "但是",
            "因为",
            "所以",
            "如果",
            "虽然",
            "可以",
            "应该",
            "可能",
            "已经",
            "还",
            "都",
            "就",
            "也",
            "很",
            "非常",
            "比较",
            "最",
            "更",
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "of",
            "in",
            "on",
            "at",
            "to",
            "for",
            "with",
        }
        entity_set = {e.casefold() for e in entities}
        focus_terms: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            t = token.strip()
            if not t:
                continue
            key = t.casefold()
            if (
                key
                and key not in stopwords
                and key not in entity_set
                and key not in seen
            ):
                seen.add(key)
                focus_terms.append(t)
                if len(focus_terms) >= 5:
                    break
        return tuple(focus_terms)

    # -- 主入口 --------------------------------------------------------------

    @classmethod
    def build(cls, query: str, intent: QueryIntent) -> QueryPlan:
        """从查询文字和意图对象构建不可变查询计划。

        Args:
            query: 用户原始查询
            intent: 已解析的 QueryIntent（可能直接来自 LLM 或 fallback）

        Returns:
            不可变 QueryPlan
        """
        # 规范化意图
        normalized_intent = normalize_query_intent(intent.intent)

        # 限制实体数
        raw_entities = list(getattr(intent, "extracted_entities", []) or [])
        entities = tuple(
            normalized
            for item in raw_entities[: cls._MAX_ENTITIES]
            if (normalized := " ".join(str(item or "").split())[:128])
        )

        # 查询变体（原始查询 + rewritten_queries，去重后最多 3）
        queries = cls._normalize_and_dedupe_queries(
            intent.rewritten_queries or [], query
        )

        # 焦点词
        focus_terms = cls._extract_focus_terms(query, list(entities))

        # 维度
        required_facets = cls._infer_facets(
            normalized_intent,
            list(entities),
            getattr(intent, "time_reference", None),
            getattr(intent, "memory_types", ()),
            list(queries),
        )

        # 歧义标记
        ambiguity_flags = cls._infer_ambiguity(
            query,
            list(entities),
            normalized_intent,
            getattr(intent, "time_reference", None),
            list(queries),
        )

        # 时间锚点
        temporal_anchor = getattr(intent, "time_reference", None) or None

        return QueryPlan(
            original_query=query,
            intent=normalized_intent,
            entities=entities,
            focus_terms=focus_terms,
            temporal_anchor=temporal_anchor,
            reference_time=getattr(intent, "reference_time", None),
            queries=queries,
            required_facets=required_facets,
            ambiguity_flags=ambiguity_flags,
            memory_types=tuple(getattr(intent, "memory_types", ()) or []),
        )


__all__ = ["QueryPlan", "QueryPlanner"]
