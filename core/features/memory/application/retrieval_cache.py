"""检索结果缓存与语义缓存键辅助。"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from ....shared.number_utils import safe_float
from ....shared.temporal import reference_time_key
from ...retrieval.rrf_fusion import HybridResult


class RetrievalCacheMixin:
    """为 RetrievalOptimizer 提供完整语义键控的两级缓存。"""

    _config: dict[str, Any]
    _dual_route_retriever: Any
    _cache_enabled: bool
    _cache_ttl: float
    _cache_max_size: int
    _cache_generation: int
    _cache: Any

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join(query.casefold().split())

    @staticmethod
    def _normalize_string(value: Any) -> str:
        return str(value or "").casefold().strip()

    @classmethod
    def _normalize_sequence(
        cls, values: list[Any] | tuple[Any, ...] | None
    ) -> tuple[str, ...]:
        if not values:
            return ()
        return tuple(
            sorted(cls._normalize_string(v) for v in values if str(v or "").strip())
        )

    @classmethod
    def _query_intent_cache_key(cls, query_intent: Any | None) -> tuple[Any, ...]:
        """把 QueryIntent 或 QueryPlan 收敛为完整且可哈希的缓存键。"""

        if query_intent is None:
            return ()

        # entities: 兼容 QueryIntent.extracted_entities 和 QueryPlan.entities
        entities = getattr(query_intent, "extracted_entities", None)
        if entities is None:
            entities = getattr(query_intent, "entities", None)
        try:
            entities_key = json.dumps(
                list(entities) if entities else [],
                sort_keys=True,
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            entities_key = str(entities or "")

        # query variants: 兼容 QueryIntent.rewritten_queries 和 QueryPlan.queries
        queries = getattr(query_intent, "rewritten_queries", None)
        if queries is None:
            queries = getattr(query_intent, "queries", None)
        temporal_anchor = getattr(query_intent, "temporal_anchor", None)
        if temporal_anchor is None:
            temporal_anchor = getattr(query_intent, "time_reference", None)

        return (
            cls._normalize_query(getattr(query_intent, "original_query", "")),
            cls._normalize_string(getattr(query_intent, "intent", "")),
            round(safe_float(getattr(query_intent, "confidence", 0.0)), 4),
            entities_key,
            cls._normalize_sequence(queries),
            cls._normalize_sequence(getattr(query_intent, "memory_types", None)),
            cls._normalize_sequence(getattr(query_intent, "focus_terms", None)),
            cls._normalize_string(temporal_anchor),
            cls._normalize_sequence(getattr(query_intent, "required_facets", None)),
            cls._normalize_sequence(getattr(query_intent, "ambiguity_flags", None)),
            reference_time_key(getattr(query_intent, "reference_time", None)),
        )

    @staticmethod
    def _strategy_cache_key(recall_strategy: Any | None) -> str:
        if recall_strategy is None:
            return ""
        return str(getattr(recall_strategy, "name", recall_strategy)).casefold()

    def cache_key(
        self,
        query: str,
        k: int,
        session_id: str | None,
        persona_id: str | None,
        user_id: str | None = None,
        chat_type: str = "private",
        memory_types: list[str] | None = None,
        query_intent: Any | None = None,
        chain_depth: int = 0,
        emotion_context: list[str] | None = None,
        recall_strategy: Any | None = None,
        reference_time: Any | None = None,
    ) -> tuple[Any, ...]:
        return (
            self._cache_generation,
            self._normalize_query(query),
            int(k),
            session_id or "",
            persona_id or "",
            user_id or "",
            self._normalize_string(chat_type),
            self._normalize_sequence(memory_types),
            self._query_intent_cache_key(query_intent),
            int(chain_depth or 0),
            self._normalize_sequence(emotion_context),
            self._strategy_cache_key(recall_strategy),
            reference_time_key(reference_time),
            bool(self._dual_route_retriever is not None),
            round(float(self._config.get("document_route_weight", 0.65)), 4),
            round(float(self._config.get("graph_route_weight", 0.35)), 4),
            int(self._config.get("graph_expansion_hops", 1)),
        )

    def get_cached(self, cache_key: tuple[Any, ...]) -> list[HybridResult] | None:
        if not self._cache_enabled or self._cache_ttl <= 0 or self._cache_max_size <= 0:
            return None

        cached = self._cache.get(cache_key)
        if cached is None:
            return None

        cached_at, results = cached
        if time.time() - cached_at > self._cache_ttl:
            self._cache.pop(cache_key, None)
            return None

        self._cache.move_to_end(cache_key)
        return copy.deepcopy(results)

    def set_cached(
        self,
        cache_key: tuple[Any, ...],
        results: list[HybridResult],
    ) -> None:
        if not self._cache_enabled or self._cache_ttl <= 0 or self._cache_max_size <= 0:
            return

        self._cache[cache_key] = (time.time(), copy.deepcopy(results))
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)

    def invalidate_cache(self) -> None:
        """在记忆写入后使检索缓存失效。"""
        self._cache_generation += 1
        self._cache.clear()
        self._session_cache.clear()

    # ---- 请求级会话缓存（消除 Bridge→RecallHandler 重复搜索）----

    @classmethod
    def _session_cache_key(
        cls,
        query: str,
        k: int,
        session_id: str | None,
        persona_id: str | None,
        user_id: str | None = None,
        chat_type: str = "private",
        memory_types: list[str] | None = None,
        query_intent: Any | None = None,
        chain_depth: int = 0,
        emotion_context: list[str] | None = None,
        recall_strategy: Any | None = None,
        reference_time: Any | None = None,
    ) -> tuple[Any, ...]:
        return (
            cls._normalize_query(query),
            int(k),
            session_id or "",
            persona_id or "",
            user_id or "",
            cls._normalize_string(chat_type),
            cls._normalize_sequence(memory_types),
            cls._query_intent_cache_key(query_intent),
            int(chain_depth or 0),
            cls._normalize_sequence(emotion_context),
            cls._strategy_cache_key(recall_strategy),
            reference_time_key(reference_time),
        )

    def get_session_cached(
        self,
        query: str,
        k: int,
        session_id: str | None,
        persona_id: str | None,
        user_id: str | None = None,
        chat_type: str = "private",
        memory_types: list[str] | None = None,
        query_intent: Any | None = None,
        chain_depth: int = 0,
        emotion_context: list[str] | None = None,
        recall_strategy: Any | None = None,
        reference_time: Any | None = None,
    ) -> list[HybridResult] | None:
        """按完整检索语义键控的请求级缓存。"""
        if not self._session_cache_enabled or self._session_cache_ttl <= 0:
            return None
        key = self._session_cache_key(
            query,
            k,
            session_id,
            persona_id,
            user_id=user_id,
            chat_type=chat_type,
            memory_types=memory_types,
            query_intent=query_intent,
            chain_depth=chain_depth,
            emotion_context=emotion_context,
            recall_strategy=recall_strategy,
            reference_time=reference_time,
        )
        cached = self._session_cache.get(key)
        if cached is None:
            return None
        cached_at, results = cached
        if time.time() - cached_at > self._session_cache_ttl:
            del self._session_cache[key]
            return None
        return copy.deepcopy(results)

    def set_session_cached(
        self,
        query: str,
        k: int,
        session_id: str | None,
        persona_id: str | None,
        results: list[HybridResult],
        user_id: str | None = None,
        chat_type: str = "private",
        memory_types: list[str] | None = None,
        query_intent: Any | None = None,
        chain_depth: int = 0,
        emotion_context: list[str] | None = None,
        recall_strategy: Any | None = None,
        reference_time: Any | None = None,
    ) -> None:
        """将检索结果写入请求级会话缓存。"""
        if not self._session_cache_enabled or self._session_cache_ttl <= 0:
            return
        key = self._session_cache_key(
            query,
            k,
            session_id,
            persona_id,
            user_id=user_id,
            chat_type=chat_type,
            memory_types=memory_types,
            query_intent=query_intent,
            chain_depth=chain_depth,
            emotion_context=emotion_context,
            recall_strategy=recall_strategy,
            reference_time=reference_time,
        )
        self._session_cache[key] = (time.time(), copy.deepcopy(results))

    _session_cache_enabled: bool
    _session_cache_ttl: float
    _session_cache: Any
