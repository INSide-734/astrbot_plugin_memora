"""检索后的可见性、情绪、季节与干扰增强。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from astrbot.api import logger

from ....shared.memory_status import is_memory_recallable
from ...retrieval.rrf_fusion import HybridResult
from .human_like_recall import (
    apply_emotion_boost as apply_human_like_emotion_boost,
)
from .human_like_recall import (
    apply_seasonal_boost as apply_human_like_seasonal_boost,
)

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

_MAX_REINFORCEMENT_MULTIPLIER = 2.0


class RetrievalBoostsMixin:
    """为 RetrievalOptimizer 提供检索后增强与干扰衰减。"""

    async def apply_boosts(
        self,
        results: list[HybridResult],
        emotion_context: list[str] | None,
        debug_trace: list[dict[str, Any]] | None = None,
    ) -> list[HybridResult]:
        """过滤不可见记忆并依次应用测试效应、情感和季节增强。"""

        if not results:
            self._last_mood_delta = 0.0
            self._last_mood_tags = []
            if debug_trace is not None:
                debug_trace.clear()
            return results

        filtered: list[HybridResult] = []
        for r in results:
            metadata = r.metadata or {}
            if not is_memory_recallable(metadata):
                continue
            filtered.append(r)

        trace_by_id = self._initialize_boost_trace(filtered, debug_trace)

        # 记忆驱动情绪回路 — 聚合并保存 mood delta
        self._collect_mood_delta(filtered)

        # 测试效应 — 每次成功召回强化记忆
        await self._apply_testing_effect(filtered)

        before_scores = self._score_snapshot(filtered)
        filtered = self._apply_emotion_boost(
            filtered,
            emotion_context,
            mode=self._emotion_scoring_mode,
        )
        self._append_boost_trace_stage(
            trace_by_id, "emotion_boost", before_scores, filtered
        )

        before_scores = self._score_snapshot(filtered)
        filtered = self._apply_seasonal_boost(
            filtered,
            enabled=self._seasonal_recall_enabled,
        )
        self._append_boost_trace_stage(
            trace_by_id, "seasonal_boost", before_scores, filtered
        )

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
                    tracked_task = self._create_tracked_task
                    assert tracked_task is not None
                    tracked_task(coro)
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
        mode: str = "enhanced",
    ) -> list[HybridResult]:
        """兼容旧调用入口并委托给独立的情感增强模块。"""

        return apply_human_like_emotion_boost(
            results,
            emotion_context,
            mode=mode,
        )

    @staticmethod
    def _apply_seasonal_boost(
        results: list[HybridResult],
        enabled: bool = True,
    ) -> list[HybridResult]:
        """兼容旧调用入口并委托给独立的季节性增强模块。"""

        return apply_human_like_seasonal_boost(results, enabled=enabled)

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

    _db: Any
    _search_memories: Any
    _get_memory: Any
    _update_memory: Any
    _create_tracked_task: Callable[[Any], Any] | None
    _testing_effect_async: bool
    _testing_effect_top_k: int
    _emotion_scoring_mode: str
    _seasonal_recall_enabled: bool
    _last_mood_delta: float
    _last_mood_tags: list[str]
    _last_weighted_tags: dict[str, float]
    _last_dominant_emotion: str
