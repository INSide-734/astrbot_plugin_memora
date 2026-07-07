"""
检索优化器
搜索缓存 + 检索后增强 + 干扰衰减 + 链式扩展 + 梦境整合 + 触发词注册
+ 记忆驱动情绪回路 + 测试效应
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ..retrieval.emotion_scorer import compute_emotion_boost, emotion_similarity
from ..retrieval.rrf_fusion import HybridResult
from ..retrieval.seasonal_recall import seasonal_boost
from ..utils.number_utils import safe_float

if TYPE_CHECKING:
    pass


# 情绪反馈回路中各情绪对应的效价增量
_VALENCE_MAP: dict[str, float] = {
    "joy": 0.15,
    "excited": 0.20,
    "grateful": 0.12,
    "happy": 0.12,
    "love": 0.15,
    "proud": 0.10,
    "hopeful": 0.08,
    "relieved": 0.08,
    "sad": -0.15,
    "angry": -0.20,
    "anxious": -0.10,
    "frustrated": -0.12,
    "disappointed": -0.10,
    "fear": -0.15,
    "guilty": -0.12,
    "embarrassed": -0.08,
    "nostalgic": 0.05,
    "neutral": 0.0,
}

# 测试效应：重复召回时 TTL 的最大倍数
_MAX_REINFORCEMENT_MULTIPLIER = 2.0


class RetrievalOptimizer:
    """检索优化 — 缓存、增强、衰减、扩展、整合、触发词"""

    def __init__(
        self,
        config: dict[str, Any],
        db_connection: Any | None = None,
        dual_route_retriever: Any | None = None,
        search_memories_cb: Callable | None = None,
        get_memory_cb: Callable | None = None,
        update_memory_cb: Callable | None = None,
        create_tracked_task_cb: Callable | None = None,
    ) -> None:
        self._config = config
        self._db = db_connection
        self._dual_route_retriever = dual_route_retriever
        self._search_memories = search_memories_cb
        self._get_memory = get_memory_cb
        self._update_memory = update_memory_cb
        self._create_tracked_task = create_tracked_task_cb

        self._cache_enabled = bool(config.get("search_cache_enabled", True))
        self._cache_ttl = float(config.get("search_cache_ttl_seconds", 45.0))
        self._cache_max_size = int(config.get("search_cache_max_size", 256))
        self._cache_generation = 0
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, list[HybridResult]]] = (
            OrderedDict()
        )

        # 请求级会话缓存：消除同一请求内 Bridge→RecallHandler 的重复搜索
        # 键=(session_id, persona_id)，TTL 极短（10s），仅在 MemoryEngine 层使用
        self._session_cache_enabled = bool(config.get("session_cache_enabled", True))
        self._session_cache_ttl = float(config.get("session_cache_ttl_seconds", 10.0))
        self._session_cache: dict[
            tuple[str, str], tuple[float, list[HybridResult]]
        ] = {}

        self._trigger_registry: dict[str, int] = {}

        # 测试效应配置：后台异步 + top-K 限制，避免阻塞检索热点路径
        self._testing_effect_async = bool(config.get("testing_effect_async", True))
        self._testing_effect_top_k = int(config.get("testing_effect_top_k", 5))

        # 由 apply_boosts 填充、供调用方读取的情绪反馈回路状态
        self._last_mood_delta: float = 0.0
        self._last_mood_tags: list[str] = []
        # 情感传染：带权重的情绪标签计数与主导情绪
        self._last_weighted_tags: dict[str, float] = {}
        self._last_dominant_emotion: str = "neutral"

    # ---- 搜索缓存 ----

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join(query.casefold().split())

    @staticmethod
    def _normalize_string(value: Any) -> str:
        return str(value or "").casefold().strip()

    @classmethod
    def _normalize_sequence(cls, values: list[Any] | tuple[Any, ...] | None) -> tuple[str, ...]:
        if not values:
            return ()
        return tuple(sorted(cls._normalize_string(v) for v in values if str(v or "").strip()))

    @classmethod
    def _query_intent_cache_key(cls, query_intent: Any | None) -> tuple[Any, ...]:
        if query_intent is None:
            return ()
        entities = getattr(query_intent, "entities", None)
        try:
            entities_key = json.dumps(entities or {}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            entities_key = str(entities or "")
        return (
            cls._normalize_string(getattr(query_intent, "intent", "")),
            round(safe_float(getattr(query_intent, "confidence", 0.0)), 4),
            entities_key,
            cls._normalize_sequence(getattr(query_intent, "rewritten_queries", None)),
            cls._normalize_sequence(getattr(query_intent, "memory_types", None)),
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
        )
        self._session_cache[key] = (time.time(), copy.deepcopy(results))

    # ---- 检索后增强 ----

    async def apply_boosts(
        self,
        results: list[HybridResult],
        emotion_context: list[str] | None,
        debug_trace: list[dict[str, Any]] | None = None,
    ) -> list[HybridResult]:
        if not results:
            self._last_mood_delta = 0.0
            self._last_mood_tags = []
            if debug_trace is not None:
                debug_trace.clear()
            return results

        filtered: list[HybridResult] = []
        for r in results:
            metadata = r.metadata or {}
            mem_status = metadata.get("memory_status", "active")
            if mem_status in ("dormant", "archived"):
                continue
            filtered.append(r)

        trace_by_id = self._initialize_boost_trace(filtered, debug_trace)

        # 记忆驱动情绪回路 — 聚合并保存 mood delta
        self._collect_mood_delta(filtered)

        # 测试效应 — 每次成功召回强化记忆
        await self._apply_testing_effect(filtered)

        before_scores = self._score_snapshot(filtered)
        filtered = self._apply_emotion_boost(filtered, emotion_context)
        self._append_boost_trace_stage(trace_by_id, "emotion_boost", before_scores, filtered)

        before_scores = self._score_snapshot(filtered)
        filtered = self._apply_seasonal_boost(filtered)
        self._append_boost_trace_stage(trace_by_id, "seasonal_boost", before_scores, filtered)

        filtered.sort(key=lambda x: x.final_score, reverse=True)
        self._finalize_boost_trace(trace_by_id, filtered)
        return filtered

    @staticmethod
    def _score_snapshot(results: list[HybridResult]) -> dict[int, float]:
        return {r.doc_id: float(r.final_score) for r in results}

    @staticmethod
    def _initialize_boost_trace(
        results: list[HybridResult],
        debug_trace: list[dict[str, Any]] | None,
    ) -> dict[int, dict[str, Any]]:
        if debug_trace is None:
            return {}
        debug_trace.clear()
        trace_by_id: dict[int, dict[str, Any]] = {}
        for result in results:
            entry = {
                "doc_id": result.doc_id,
                "initial_score": round(float(result.final_score), 6),
                "final_score": round(float(result.final_score), 6),
                "stages": [],
            }
            debug_trace.append(entry)
            trace_by_id[result.doc_id] = entry
        return trace_by_id

    @staticmethod
    def _append_boost_trace_stage(
        trace_by_id: dict[int, dict[str, Any]],
        name: str,
        before_scores: dict[int, float],
        results: list[HybridResult],
    ) -> None:
        if not trace_by_id:
            return
        for result in results:
            entry = trace_by_id.get(result.doc_id)
            if entry is None:
                continue
            before = float(before_scores.get(result.doc_id, result.final_score))
            after = float(result.final_score)
            entry["stages"].append(
                {
                    "name": name,
                    "before": round(before, 6),
                    "after": round(after, 6),
                    "delta": round(after - before, 6),
                    "multiplier": round(after / before, 6) if before else None,
                }
            )

    @staticmethod
    def _finalize_boost_trace(
        trace_by_id: dict[int, dict[str, Any]],
        results: list[HybridResult],
    ) -> None:
        if not trace_by_id:
            return
        for result in results:
            entry = trace_by_id.get(result.doc_id)
            if entry is not None:
                entry["final_score"] = round(float(result.final_score), 6)

    def _collect_mood_delta(self, results: list[HybridResult]) -> None:
        """按分数加权聚合情绪标签，并计算情绪偏移量。"""
        all_tags: list[str] = []
        weighted_counts: dict[str, float] = {}

        # 按最终得分排序，前 3 条结果赋予更高权重
        sorted_results = sorted(results, key=lambda x: x.final_score, reverse=True)
        for i, r in enumerate(sorted_results):
            metadata = r.metadata or {}
            tags = metadata.get("emotion_tags", []) or []
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = []
            if not isinstance(tags, list):
                continue

            # top-3 权重 1.0，其余 0.5
            weight = 1.0 if i < 3 else 0.5
            for t in tags:
                t_clean = t.lower().strip()
                all_tags.append(t)
                weighted_counts[t_clean] = weighted_counts.get(t_clean, 0.0) + weight

        self._last_mood_tags = all_tags
        self._last_weighted_tags = weighted_counts

        # 计算带权重的情感价
        if weighted_counts:
            total_weight = sum(weighted_counts.values())
            self._last_mood_delta = sum(
                _VALENCE_MAP.get(tag, 0.0) * count / max(total_weight, 0.001)
                for tag, count in weighted_counts.items()
            )
            # 主导情绪：取加权计数最高的标签
            self._last_dominant_emotion = max(
                weighted_counts, key=lambda t: weighted_counts[t]
            )
        else:
            self._last_mood_delta = 0.0
            self._last_dominant_emotion = "neutral"

    async def _apply_testing_effect(self, results: list[HybridResult]) -> None:
        """对成功召回的记忆施加测试效应强化。"""
        if self._update_memory is None:
            return

        top_results = results[: self._testing_effect_top_k]
        use_async = self._testing_effect_async and self._create_tracked_task is not None

        for r in top_results:
            metadata = r.metadata or {}
            current_count = int(metadata.get("reinforcement_count", 0) or 0)
            original_ttl = float(metadata.get("ttl_days", 30.0) or 30.0)

            new_count = current_count + 1
            new_ttl = min(
                original_ttl * _MAX_REINFORCEMENT_MULTIPLIER,
                original_ttl * (1.05**new_count),
            )

            metadata["reinforcement_count"] = new_count
            metadata["ttl_days"] = round(new_ttl, 2)

            try:
                coro = self._update_memory(
                    r.doc_id,
                    {"metadata": metadata},
                    skip_graph_reindex=True,
                )
                if use_async:
                    self._create_tracked_task(coro)
                else:
                    await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(f"[TestingEffect] 更新 doc={r.doc_id} 失败", exc_info=True)

    @property
    def last_mood_delta(self) -> float:
        """最近一次 ``apply_boosts()`` 产生的情感价偏移量。"""
        return self._last_mood_delta

    @property
    def last_mood_tags(self) -> list[str]:
        """最近一次 ``apply_boosts()`` 聚合得到的情绪标签列表。"""
        return self._last_mood_tags

    # 情感传染：结构化情绪反馈
    @property
    def last_dominant_emotion(self) -> str:
        """按分数加权聚合后得到的主导情绪。"""
        return self._last_dominant_emotion

    @property
    def last_weighted_tags(self) -> dict[str, float]:
        """最近一次 ``apply_boosts()`` 的加权情绪标签计数。"""
        return dict(self._last_weighted_tags)

    def get_mood_contagion(self) -> dict[str, Any]:
        """返回供人设情绪更新使用的结构化情感传染结果。"""
        top_tags = sorted(
            self._last_weighted_tags.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]
        return {
            "valence_delta": round(self._last_mood_delta, 4),
            "dominant_emotion": self._last_dominant_emotion,
            "top_tags": [{"tag": t, "weight": round(w, 2)} for t, w in top_tags],
            "tag_count": len(self._last_mood_tags),
        }

    @staticmethod
    def _apply_emotion_boost(
            results: list[HybridResult],
        emotion_context: list[str] | None,
    ) -> list[HybridResult]:
        if not emotion_context:
            return results

        for r in results:
            metadata = r.metadata or {}
            memory_tags = metadata.get("emotion_tags", []) or []
            if isinstance(memory_tags, str):
                try:
                    memory_tags = json.loads(memory_tags)
                except (json.JSONDecodeError, TypeError):
                    memory_tags = []
            if not isinstance(memory_tags, list):
                memory_tags = []

            memory_intensity = safe_float(metadata.get("emotional_intensity"), 0.5)

            sim = emotion_similarity(emotion_context, memory_tags, memory_intensity)
            boost = compute_emotion_boost(sim)
            r.final_score = r.final_score * boost

        results.sort(key=lambda x: x.final_score, reverse=True)
        return results

    @staticmethod
    def _apply_seasonal_boost(
            results: list[HybridResult],
    ) -> list[HybridResult]:
        for r in results:
            metadata = r.metadata or {}
            ts = (
                metadata.get("event_time")
                or metadata.get("create_time")
                or metadata.get("timestamp")
            )
            if ts is not None:
                boost = seasonal_boost(float(ts))
                r.final_score = r.final_score * boost
        return results

    # ---- 干扰衰减 ----

    async def apply_interference(self, new_memory_id: int, new_content: str) -> None:
        """应用逆行干扰：与新记忆相似的旧记忆会轻微衰减。"""
        if not new_content.strip() or self._db is None:
            return

        try:
            new_tokens = set(new_content.lower().split())
            if len(new_tokens) < 3:
                chars = new_content.replace(" ", "")
                if len(chars) >= 4:
                    new_tokens = {chars[i : i + 2] for i in range(len(chars) - 1)}
            if len(new_tokens) < 3:
                return

            search_query = " ".join(list(new_tokens)[:10])
            if self._search_memories is None:
                return
            similar = await self._search_memories(
                search_query, k=5, recall_type="passive"
            )

            for result in similar:
                if result.doc_id == new_memory_id:
                    continue
                if self._get_memory is None:
                    continue
                old_memory = await self._get_memory(result.doc_id)
                if not old_memory:
                    continue
                old_text = str(old_memory.get("text", ""))
                old_tokens = set(old_text.lower().split())
                if len(old_tokens) < 3:
                    continue
                jaccard = len(new_tokens & old_tokens) / max(
                    1, len(new_tokens | old_tokens)
                )
                if jaccard >= 0.6:
                    old_metadata = old_memory.get("metadata", {})
                    if isinstance(old_metadata, str):
                        try:
                            old_metadata = json.loads(old_metadata)
                        except (json.JSONDecodeError, TypeError):
                            old_metadata = {}
                    old_importance = float(old_metadata.get("importance", 0.5))
                    old_metadata["importance"] = round(
                        max(0.05, old_importance * 0.9), 4
                    )
                    old_metadata["revised_by"] = new_memory_id
                    if self._update_memory is None:
                        continue
                    await self._update_memory(result.doc_id, {"metadata": old_metadata})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("[RetrievalOptimizer] 干扰衰减失败", exc_info=True)

    # ---- 链式扩展 ----

    # R2: 多跳检索默认参数
    _DEFAULT_MAX_HOPS = 2
    _DEFAULT_HOP_DECAY = 0.65  # 每跳衰减因子（逐跳平方递减）

    def _config_bool(self, key: str, default: bool) -> bool:
        return bool(self._config.get(key, default))

    async def chain_expand(
        self,
        direct_results: list[HybridResult],
        k: int,
        session_id: str | None,
        persona_id: str | None,
    ) -> list[HybridResult]:
        """用关联记忆扩展顶部结果（单跳，兼容旧行为）。"""
        return await self.chain_expand_multi_hop(
            direct_results,
            k,
            session_id,
            persona_id,
            max_hops=1,
        )

    async def chain_expand_multi_hop(
        self,
        direct_results: list[HybridResult],
        k: int,
        session_id: str | None,
        persona_id: str | None,
        max_hops: int = 2,
        hop_decay: float | None = None,
    ) -> list[HybridResult]:
        """R2: 多跳检索 — 沿图边 + 话题关联做多层扩展。

        每跳衰减 hop_decay 的平方（hop 1: ×0.65, hop 2: ×0.42, hop 3: ×0.27）。

        参数:
            direct_results: 首轮检索结果
            k: 最终返回数量上限
            session_id: 会话过滤
            persona_id: 人设过滤
            max_hops: 最大扩展跳数（默认 2）
            hop_decay: 每跳衰减因子（默认 0.65，逐跳平方递减）
        """
        decay = hop_decay if hop_decay is not None else self._DEFAULT_HOP_DECAY
        hops = max(1, min(5, max_hops))
        graph_expansion_enabled = self._config_bool(
            "recall_engine.chain_graph_expansion_enabled",
            True,
        )
        topic_expansion_enabled = self._config_bool(
            "recall_engine.chain_topic_expansion_enabled",
            True,
        )

        seen_ids: set[int] = {r.doc_id for r in direct_results}
        all_expanded: list[tuple[HybridResult, int]] = []  # （结果, 跳数深度）

        # 种子集合：每一跳都以新发现的结果继续扩展
        seed_pool = list(direct_results[:3])  # 最多 3 个种子
        for hop in range(1, hops + 1):
            if not seed_pool:
                break

            hop_multiplier = decay**hop
            next_seeds: list[HybridResult] = []

            for seed in seed_pool:
                metadata = seed.metadata or {}
                # 优先通过图边做关联扩展
                if graph_expansion_enabled:
                    linked_via_graph = await self._expand_via_graph_edges(
                        seed,
                        seen_ids,
                        session_id,
                        persona_id,
                    )
                    for lr in linked_via_graph:
                        if lr.doc_id not in seen_ids:
                            lr.final_score *= hop_multiplier
                            seen_ids.add(lr.doc_id)
                            all_expanded.append((lr, hop))
                            next_seeds.append(lr)

                # 补充基于话题关键词的扩展
                if not topic_expansion_enabled:
                    continue
                topics = metadata.get("topics", [])
                emotion_tags = metadata.get("emotion_tags", [])
                chain_query = " ".join(
                    (topics if isinstance(topics, list) else [])[:3]
                    + (emotion_tags if isinstance(emotion_tags, list) else [])[:2]
                )
                if not chain_query.strip():
                    continue

                if self._search_memories is None:
                    continue
                linked = await self._search_memories(
                    chain_query,
                    k=3,
                    session_id=session_id,
                    persona_id=persona_id,
                    recall_type="passive",
                    chain_depth=0,
                )
                for lr in linked:
                    if lr.doc_id not in seen_ids:
                        lr.final_score *= hop_multiplier
                        seen_ids.add(lr.doc_id)
                        all_expanded.append((lr, hop))
                        next_seeds.append(lr)

            seed_pool = next_seeds[:2]  # 每跳最多 2 个新种子

        # 按得分排序，并截断到 k
        all_expanded.sort(key=lambda x: x[0].final_score, reverse=True)
        chained = [cr[0] for cr in all_expanded[:k]]

        combined = list(direct_results) + chained
        combined.sort(key=lambda x: x.final_score, reverse=True)

        if chained:
            logger.debug(
                f"[MultiHop] {len(direct_results)} 直接结果 + "
                f"{len(chained)} 多跳扩展 (max_hops={hops}, decay={decay})"
            )
        return combined

    async def _expand_via_graph_edges(
        self,
        seed: HybridResult,
        seen_ids: set[int],
        session_id: str | None,
        persona_id: str | None,
    ) -> list[HybridResult]:
        """R2：通过图边遍历找到关联记忆。

        查找与种子记忆共享图节点的其他记忆（co_occurs_with / describes /
        before / after / during 等边类型）。
        """
        results: list[HybridResult] = []
        if self._db is None:
            return results

        try:
            # 查找与该记忆共享节点或边的其他记忆 ID
            cursor = await self._db.execute(
                """
                SELECT DISTINCT ge2.source_memory_id, ge2.content, ge2.metadata
                FROM graph_entry_nodes gen1
                JOIN graph_entry_nodes gen2 ON gen1.node_id = gen2.node_id
                    AND gen1.entry_id != gen2.entry_id
                JOIN graph_entries ge1 ON gen1.entry_id = ge1.id
                JOIN graph_entries ge2 ON gen2.entry_id = ge2.id
                WHERE ge1.source_memory_id = ?
                AND ge2.source_memory_id != ?
                LIMIT 5
                """,
                (seed.doc_id, seed.doc_id),
            )
            rows = await cursor.fetchall()
            for row in rows:
                doc_id = int(row["source_memory_id"])
                if doc_id in seen_ids:
                    continue
                meta_raw = row["metadata"] or "{}"
                import json

                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                results.append(
                    HybridResult(
                        doc_id=doc_id,
                        final_score=0.5,  # 基础分，会再由 hop_decay 继续衰减
                        content=row["content"] or "",
                        metadata=meta if isinstance(meta, dict) else {},
                    )
                )
        except Exception:
            logger.debug(
                f"[MultiHop] 图边扩展失败 (seed={seed.doc_id})",
                exc_info=True,
            )

        return results

    # ---- 梦境整合 ----

    async def consolidate(self) -> dict[str, int]:
        """夜间整合：基于共享话题关联高重要度记忆。"""
        if self._db is None:
            return {"paired": 0}

        try:
            now = time.time()
            recent_cutoff = now - 7 * 86400.0
            cursor = await self._db.execute(
                "SELECT id, metadata FROM documents "
                "WHERE json_extract(metadata, '$.importance') >= 0.6"
            )
            rows = list(await cursor.fetchall())
            if len(rows) < 2:
                return {"paired": 0}

            high_imp: list[tuple[int, dict]] = []
            for row in rows:
                metadata = _safe_json(row["metadata"])
                last_access = safe_float(metadata.get("last_access_time"), 0.0)
                if last_access >= recent_cutoff:
                    high_imp.append((int(row["id"]), metadata))

            paired = 0
            for i in range(len(high_imp)):
                for j in range(i + 1, min(i + 4, len(high_imp))):
                    t_i = set(high_imp[i][1].get("topics", []) or [])
                    t_j = set(high_imp[j][1].get("topics", []) or [])
                    if t_i & t_j:
                        meta_i = high_imp[i][1]
                        pairs = list(meta_i.get("consolidated_pairs", []) or [])
                        if high_imp[j][0] not in pairs and len(pairs) < 5:
                            pairs.append(high_imp[j][0])
                            meta_i["consolidated_pairs"] = pairs
                            meta_i["importance"] = min(
                                0.95,
                                float(meta_i.get("importance", 0.5)) + 0.02,
                            )
                            await self._db.execute(
                                "UPDATE documents SET metadata = ? WHERE id = ?",
                                (
                                    json.dumps(meta_i, ensure_ascii=False),
                                    high_imp[i][0],
                                ),
                            )
                            paired += 1
            if paired:
                await self._db.commit()
                logger.info(f"[梦境整合] {paired} 对记忆已关联巩固")
            return {"paired": paired}
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[梦境整合] 失败", exc_info=True)
            return {"paired": 0}

    # ---- 触发词注册 ----

    async def register_trigger(self, word: str, memory_id: int) -> None:
        """将词语注册为指定记忆的触发词。"""
        self._trigger_registry[word.strip().lower()] = memory_id

    async def extract_triggers(self, content: str, memory_id: int) -> None:
        """从内容中提取高频名词并注册为触发词。"""
        if not content.strip():
            return
        words = content.lower().split()
        from collections import Counter

        counts = Counter(w for w in words if len(w) >= 2)
        for word, cnt in counts.most_common(3):
            if cnt >= 1:
                await self.register_trigger(word, memory_id)

    async def apply_trigger_boost(
        self, query: str, results: list[HybridResult]
    ) -> list[HybridResult]:
        """提升与查询触发词匹配的结果分数。"""
        if not self._trigger_registry:
            return results
        query_words = set(query.lower().split())
        triggered_ids: set[int] = set()
        for w in query_words:
            if w in self._trigger_registry:
                triggered_ids.add(self._trigger_registry[w])
        if not triggered_ids:
            return results
        for r in results:
            if r.doc_id in triggered_ids:
                r.final_score *= 1.5
        results.sort(key=lambda x: x.final_score, reverse=True)
        return results

    # ---- R5: 叙事连贯性 ----

    # 过渡短语映射
    _TRANSITIONS: dict[str, str] = {
        "same_topic": "还有，",
        "topic_switch": "另外，",
        "time_jump": "那之后，",
        "introduction": "我记得：",
    }

    def arrange_narrative(
        self,
        results: list[HybridResult],
        max_length: int = 500,
    ) -> str:
        """R5: 将平铺记忆列表转为时间线排序 + topic 聚类 + 过渡短语的连贯叙事。

        参数:
            results: 检索结果列表
            max_length: 输出最大字符数（截断点以完整句子为界）

        返回:
            格式化叙事字符串，如 "我记得：xxx。还有，yyy。那之后，zzz。"
        """
        if not results:
            return ""

        # 1. 按时间线排序（优先 create_time，其次 timestamp）
        def _sort_key(r: HybridResult) -> float:
            meta = r.metadata or {}
            ts = meta.get("create_time") or meta.get("timestamp") or 0.0
            try:
                return float(ts)
            except (TypeError, ValueError):
                return 0.0

        sorted_results = sorted(results, key=_sort_key)

        # 2. 按 topic 聚类：相邻同 topic 的记忆归为一组
        segments: list[tuple[str | None, list[str]]] = []
        current_topic: str | None = None
        current_texts: list[str] = []

        for r in sorted_results:
            meta = r.metadata or {}
            topics = meta.get("topics", []) or []
            primary_topic = topics[0] if topics else None
            text = (r.content or "").strip()
            if not text:
                continue

            if primary_topic == current_topic and current_texts:
                current_texts.append(text)
            else:
                if current_texts:
                    segments.append((current_topic, current_texts))
                current_topic = primary_topic
                current_texts = [text]

        if current_texts:
            segments.append((current_topic, current_texts))

        # 3. 拼接过渡短语
        parts: list[str] = []
        prev_time: float | None = None

        for i, (_topic, texts) in enumerate(segments):
            if i == 0:
                parts.append(self._TRANSITIONS["introduction"])
            else:
                # 判断时间跳跃（> 7 天）
                first_ts = None
                if i < len(sorted_results):
                    try:
                        first_ts = float(
                            (sorted_results[i].metadata or {}).get("create_time")
                            or (sorted_results[i].metadata or {}).get("timestamp")
                            or 0
                        )
                    except (TypeError, ValueError):
                        first_ts = None
                if prev_time is not None and first_ts is not None:
                    gap_days = abs(first_ts - prev_time) / 86400.0
                    if gap_days > 7:
                        parts.append(self._TRANSITIONS["time_jump"])
                    else:
                        parts.append(self._TRANSITIONS["topic_switch"])
                else:
                    parts.append(self._TRANSITIONS["topic_switch"])

            # 同 topic 下多条记忆用 "还有，" 连接
            for j, text in enumerate(texts):
                parts.append(text.rstrip("。！？.!?") + "。")
                if j < len(texts) - 1:
                    parts.append(self._TRANSITIONS["same_topic"])

            # 更新 prev_time
            try:
                prev_time = float(
                    (
                        sorted_results[min(i + 1, len(sorted_results) - 1)].metadata
                        or {}
                    ).get("create_time")
                    or 0
                )
            except (TypeError, ValueError):
                prev_time = None

        # 4. 截断到 max_length，保持句子完整
        narrative = "".join(parts)
        if len(narrative) > max_length:
            cutoff = narrative.rfind("。", 0, max_length)
            narrative = narrative[: cutoff + 1] if cutoff > 0 else narrative[:max_length]

        return narrative


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
