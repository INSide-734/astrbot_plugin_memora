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
from ...injection.application.selection import metadata_has_user_evidence
from ...quality.application.near_duplicate_detector import (
    DedupScope,
    same_dedup_scope,
    stored_scope,
)
from ...retrieval.rrf_fusion import HybridResult
from ..domain.revision import memory_revision
from ..graph.domain.models import GraphBoundary, GraphQueryScope


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
        *,
        query_scope: GraphQueryScope | None = None,
        require_user_evidence: bool = False,
    ) -> list[HybridResult]:
        """用关联记忆扩展顶部结果（单跳，兼容旧行为）。"""
        return await self.chain_expand_multi_hop(
            direct_results,
            k,
            session_id,
            persona_id,
            max_hops=1,
            query_scope=query_scope,
            require_user_evidence=require_user_evidence,
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
        query_scope: GraphQueryScope | None = None,
        require_user_evidence: bool = False,
    ) -> list[HybridResult]:
        """R2: 多跳检索 — 沿图边 + 话题关联做多层扩展。

        每跳衰减 hop_decay 的平方（hop 1: ×0.65, hop 2: ×0.42, hop 3: ×0.27）。
        """
        if query_scope is not None:
            query_scope = GraphQueryScope.require(query_scope)
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
                if graph_expansion_enabled and query_scope is not None:
                    source_metadata = await self._load_graph_candidate_metadata(
                        seed.doc_id
                    )
                    try:
                        boundary = GraphBoundary.from_metadata(source_metadata)
                    except ValueError:
                        linked_via_graph = []
                    else:
                        linked_via_graph = (
                            await self._expand_via_graph_edges(
                                seed,
                                seen_ids,
                                session_id,
                                persona_id,
                                boundary=boundary,
                            )
                            if GraphQueryScope.from_boundary(boundary) == query_scope
                            else []
                        )
                    for lr in linked_via_graph:
                        canonical_metadata = lr.metadata
                        if (
                            lr.doc_id not in seen_ids
                            and canonical_metadata is not None
                            and is_memory_recallable(canonical_metadata)
                            and canonical_visible_at(canonical_metadata, reference_time)
                            and (
                                not require_user_evidence
                                or metadata_has_user_evidence(canonical_metadata)
                            )
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
                    query_scope=query_scope,
                    require_user_evidence=require_user_evidence,
                )
                for lr in linked:
                    if (
                        lr.doc_id not in seen_ids
                        and is_memory_recallable(lr.metadata or {})
                        and canonical_visible_at(lr.metadata or {}, reference_time)
                        and (
                            not require_user_evidence
                            or metadata_has_user_evidence(lr.metadata or {})
                        )
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
        metadata = dict(_safe_json(memory.get("metadata")))
        metadata["revision_token"] = memory_revision(dict(memory))
        return metadata

    async def _expand_via_graph_edges(
        self,
        seed: HybridResult,
        seen_ids: set[int],
        session_id: str | None,
        persona_id: str | None,
        *,
        boundary: GraphBoundary,
    ) -> list[HybridResult]:
        """按种子当前来源找同 scope 的节点，并独立核对每条目标 revision。"""
        GraphBoundary.require(boundary)
        results: list[HybridResult] = []
        if self._db is None:
            return results
        try:
            cursor = await self._db.execute(
                """
                SELECT DISTINCT ge2.source_memory_id, ge2.content,
                                ge2.revision_token
                FROM graph_entries ge1
                JOIN graph_entry_nodes gen1 ON gen1.entry_id = ge1.id
                JOIN graph_nodes gn1 ON gn1.id = gen1.node_id
                JOIN graph_nodes gn2 ON gn2.node_key = gn1.node_key
                  AND gn2.scope_key = gn1.scope_key
                  AND gn2.privacy_level = gn1.privacy_level
                JOIN graph_entry_nodes gen2 ON gen2.node_id = gn2.id
                JOIN graph_entries ge2 ON ge2.id = gen2.entry_id
                WHERE ge1.source_memory_id = :source_memory_id
                  AND ge2.source_memory_id != :source_memory_id
                  AND ge1.scope_key = :scope_key
                  AND ge1.privacy_level = :privacy_level
                  AND ge1.revision_token = :revision_token
                  AND gn1.scope_key = ge1.scope_key
                  AND gn1.privacy_level = ge1.privacy_level
                  AND gn1.revision_token = ge1.revision_token
                  AND ge2.scope_key = :scope_key
                  AND ge2.privacy_level = :privacy_level
                  AND ge2.revision_token = gn2.revision_token
                  AND (:session_id IS NULL OR ge1.session_id = :session_id)
                  AND (:session_id IS NULL OR ge2.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge1.persona_id = :persona_id)
                  AND (:persona_id IS NULL OR ge2.persona_id = :persona_id)
                LIMIT 5
                """,
                {
                    **boundary.as_params(),
                    "source_memory_id": seed.doc_id,
                    "session_id": session_id,
                    "persona_id": persona_id,
                },
            )
            rows = await cursor.fetchall()
            added_ids = set(seen_ids)
            for row in rows:
                doc_id = int(row["source_memory_id"])
                if doc_id in added_ids:
                    continue
                meta = await self._load_graph_candidate_metadata(doc_id)
                try:
                    current = GraphBoundary.from_metadata(meta)
                except ValueError:
                    continue
                if (
                    current.scope_key != boundary.scope_key
                    or current.privacy_level != boundary.privacy_level
                    or current.revision_token != row["revision_token"]
                ):
                    continue
                results.append(
                    HybridResult(
                        doc_id=doc_id,
                        final_score=0.5,
                        rrf_score=0.5,
                        bm25_score=None,
                        vector_score=None,
                        content=row["content"] or "",
                        metadata=meta or {},
                    )
                )
                added_ids.add(doc_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("[MultiHop] graph_expansion_unavailable")
        return results

    # ---- 梦境整合 ----

    async def consolidate(self) -> dict[str, int]:
        """夜间整合：基于共享话题关联同一来源边界内的高重要度记忆。"""
        if self._db is None or self._update_memory is None:
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

            high_imp: list[tuple[int, dict, DedupScope]] = []
            for row in rows:
                metadata = _safe_json(row["metadata"])
                if not is_memory_recallable(metadata):
                    continue
                last_access = safe_float(metadata.get("last_access_time"), 0.0)
                if last_access < recent_cutoff:
                    continue
                scope = stored_scope(metadata)
                if scope is None:
                    continue
                high_imp.append((int(row["id"]), metadata, scope))

            paired = 0
            for i in range(len(high_imp)):
                for j in range(i + 1, min(i + 4, len(high_imp))):
                    if not same_dedup_scope(high_imp[i][2], high_imp[j][2]):
                        continue
                    topics_i = set(high_imp[i][1].get("topics", []) or [])
                    topics_j = set(high_imp[j][1].get("topics", []) or [])
                    if not topics_i & topics_j:
                        continue
                    if await self._apply_consolidation_pair(
                        high_imp[i][0], high_imp[j][0], scope=high_imp[i][2]
                    ):
                        paired += 1
            if paired:
                self.invalidate_cache()
                logger.info(f"[梦境整合] {paired} 对记忆已关联巩固")
            return {"paired": paired}
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[梦境整合] 失败", exc_info=True)
            return {"paired": 0}

    async def _apply_consolidation_pair(
        self, owner_id: int, target_id: int, *, scope: DedupScope
    ) -> bool:
        """经 canonical 写入口以 source revision CAS 提交单条整合关联。

        扫描阶段的选择不是授权：提交前必须回读两端最新 canonical，重新确认两端
        仍活跃、仍落在同一来源边界，且 owner 边界与配对时选择一致；任一项失效
        即跳过本对，不把新 revision 当成旧选择的延续。
        """

        update_memory = self._update_memory
        get_memory = self._get_memory
        if update_memory is None or get_memory is None:
            return False
        fresh_owner = await get_memory(owner_id)
        fresh_target = await get_memory(target_id)
        if not isinstance(fresh_owner, Mapping) or not isinstance(
            fresh_target, Mapping
        ):
            return False
        owner_metadata = _safe_json(fresh_owner.get("metadata"))
        target_metadata = _safe_json(fresh_target.get("metadata"))
        owner_scope = stored_scope(owner_metadata)
        target_scope = stored_scope(target_metadata)
        if owner_scope is None or target_scope is None:
            return False
        if not is_memory_recallable(owner_metadata) or not is_memory_recallable(
            target_metadata
        ):
            return False
        if not same_dedup_scope(owner_scope, target_scope) or not same_dedup_scope(
            scope, owner_scope
        ):
            return False
        if not set(owner_metadata.get("topics", []) or []) & set(
            target_metadata.get("topics", []) or []
        ):
            return False
        expected_revision = memory_revision(dict(fresh_owner))
        if not expected_revision:
            return False
        pairs = list(owner_metadata.get("consolidated_pairs", []) or [])
        if target_id in pairs or len(pairs) >= 5:
            return False
        pairs.append(target_id)
        applied = await update_memory(
            owner_id,
            {
                "metadata": {
                    "consolidated_pairs": pairs,
                    "importance": min(
                        0.95, safe_float(owner_metadata.get("importance"), 0.5) + 0.02
                    ),
                }
            },
            expected_revision=expected_revision,
        )
        return bool(applied)

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
