"""链式扩展、整合与触发词增强。"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

from ....shared.memory_status import is_memory_recallable
from ....shared.number_utils import safe_float
from ....shared.temporal import canonical_visible_at
from ...retrieval.rrf_fusion import HybridResult


def _safe_json(value: Any) -> dict[str, Any]:
    """把字典或 JSON 文本规范化为字典。"""
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


class RetrievalExpansionMixin:
    """为 RetrievalOptimizer 提供链式扩展、整合和触发词增强。"""

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
        reference_time: Any | None = None,
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
                        canonical_metadata = await self._load_graph_candidate_metadata(
                            lr.doc_id
                        )
                        if (
                            lr.doc_id not in seen_ids
                            and canonical_metadata is not None
                            and is_memory_recallable(canonical_metadata)
                            and canonical_visible_at(canonical_metadata, reference_time)
                        ):
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
                    reference_time=reference_time,
                )
                for lr in linked:
                    if (
                        lr.doc_id not in seen_ids
                        and is_memory_recallable(lr.metadata or {})
                        and canonical_visible_at(lr.metadata or {}, reference_time)
                    ):
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

    async def _load_graph_candidate_metadata(
        self, memory_id: int
    ) -> dict[str, Any] | None:
        """从 canonical 存储读取图扩展候选的生命周期 metadata。

        图条目是可重建派生产物，不携带权威生命周期状态；无法重读 canonical
        记录时 fail-closed，避免休眠、归档或已删除记忆被图路径重新引入。
        """
        if self._get_memory is None:
            return None
        try:
            memory = await self._get_memory(memory_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if not isinstance(memory, Mapping):
            return None
        return _safe_json(memory.get("metadata"))

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

                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                results.append(
                    HybridResult(
                        doc_id=doc_id,
                        final_score=0.5,  # 基础分，会再由 hop_decay 继续衰减
                        rrf_score=0.5,
                        bm25_score=None,
                        vector_score=None,
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

    _config: dict[str, Any]
    _db: Any
    _search_memories: Any
    _get_memory: Any
    _trigger_registry: dict[str, int]
