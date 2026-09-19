"""图记忆路由的关键词检索。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...shared.adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
)
from ..memory.graph.domain.models import (
    GraphBoundary,
    GraphQueryScope,
    resolve_graph_query_scope,
)
from ..memory.graph.infrastructure.graph_store import GraphStore
from ..memory.infrastructure.hierarchy_store import EntityHierarchyStore
from ..recall.processors.text_processor import TextProcessor


@dataclass(slots=True)
class GraphKeywordResult:
    """聚合到单条源记忆的关键词匹配结果。"""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]
    graph_distance: int | None = None
    source_boundary: GraphBoundary | None = None


class GraphKeywordRetriever:
    """通过全文检索、邻居扩展和 G3 层级检索图记忆候选项。"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.GRAPH_RETRIEVER,
        native=frozenset({AdapterCapability.FILTERING}),
        caller_enforced=frozenset(
            {AdapterCapability.SCORING, AdapterCapability.CANCELLATION}
        ),
        score=ScoreSemantics(
            direction=ScoreDirection.HIGHER_IS_BETTER,
            minimum=0.0,
            maximum=1.0,
            normalization=NormalizationScope.CALLER,
        ),
    )

    def __init__(
        self,
        graph_store: GraphStore,
        text_processor: TextProcessor,
        hierarchy_store: EntityHierarchyStore | None = None,
        config: dict[str, Any] | None = None,
    ):
        """装配图 Store、分词器、可选层级 Store 与邻居深度配置。"""

        self.graph_store = graph_store
        self.text_processor = text_processor
        self.hierarchy_store = hierarchy_store
        self.config = config or {}
        self.expansion_limit = int(self.config.get("graph_expansion_limit", 24))
        self.expansion_hops = max(
            0,
            min(2, int(self.config.get("graph_expansion_hops", 1))),
        )
        self.second_hop_weight = float(self.config.get("graph_second_hop_weight", 0.4))

    async def search(
        self,
        query: str,
        limit: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
    ) -> list[GraphKeywordResult]:
        """通过关键词匹配搜索图路由。"""
        resolve_graph_query_scope(boundary=boundary, query_scope=query_scope)
        if not query or not query.strip():
            return []

        tokens = await self.text_processor.tokenize_async(query, remove_stopwords=True)
        if not tokens:
            return []

        escaped_tokens = ['"' + token.replace('"', '""') + '"' for token in tokens]
        fts_query = " OR ".join(escaped_tokens)
        direct_hits = await self.graph_store.search_entries_by_bm25(
            fts_query=fts_query,
            limit=max(limit * 3, 12),
            session_id=session_id,
            persona_id=persona_id,
            boundary=boundary,
            query_scope=query_scope,
        )
        matched_nodes = await self.graph_store.search_nodes_by_tokens(
            tokens=tokens,
            limit=max(limit * 3, 12),
            boundary=boundary,
            query_scope=query_scope,
        )
        matched_node_ids = [item["id"] for item in matched_nodes]

        hierarchy_hits: list[dict[str, Any]] = []
        if self.hierarchy_store is not None and matched_node_ids:
            ancestor_ids: set[int] = set()
            for item in matched_nodes:
                node_val = item.get("canonical_value") or item.get("node_value", "")
                if not node_val:
                    continue
                ancestors = await self.hierarchy_store.get_ancestors(
                    str(node_val), max_depth=3
                )
                for ancestor in ancestors:
                    anc_nodes = await self.graph_store.search_nodes_by_tokens(
                        tokens=[ancestor],
                        limit=3,
                        boundary=boundary,
                        query_scope=query_scope,
                    )
                    ancestor_ids.update(an["id"] for an in anc_nodes)
            if ancestor_ids:
                hierarchy_hits = await self.graph_store.get_entries_for_node_ids(
                    node_ids=list(ancestor_ids),
                    limit=max(self.expansion_limit, limit * 3),
                    session_id=session_id,
                    persona_id=persona_id,
                    boundary=boundary,
                    query_scope=query_scope,
                )

        expansion_hits = await self.graph_store.get_entries_for_node_ids(
            node_ids=matched_node_ids,
            limit=max(self.expansion_limit, limit * 3),
            session_id=session_id,
            persona_id=persona_id,
            boundary=boundary,
            query_scope=query_scope,
        )
        edge_neighbor_hits: list[dict[str, Any]] = []
        second_hop_hits: list[dict[str, Any]] = []
        if matched_node_ids and self.expansion_hops >= 1:
            first_hop_node_ids = await self.graph_store.get_neighbor_node_ids(
                node_ids=matched_node_ids,
                limit=max(self.expansion_limit, limit * 3),
                boundary=boundary,
                query_scope=query_scope,
            )
            matched_node_set = set(matched_node_ids)
            first_hop_node_ids = [
                node_id
                for node_id in first_hop_node_ids
                if node_id not in matched_node_set
            ]
            edge_neighbor_hits = await self.graph_store.get_entries_for_node_ids(
                node_ids=first_hop_node_ids,
                limit=max(self.expansion_limit, limit * 3),
                session_id=session_id,
                persona_id=persona_id,
                boundary=boundary,
                query_scope=query_scope,
            )
            if self.expansion_hops >= 2 and first_hop_node_ids:
                second_hop_node_ids = await self.graph_store.get_neighbor_node_ids(
                    node_ids=first_hop_node_ids,
                    limit=max(self.expansion_limit, limit * 3),
                    boundary=boundary,
                    query_scope=query_scope,
                )
                excluded_node_ids = matched_node_set | set(first_hop_node_ids)
                second_hop_node_ids = [
                    node_id
                    for node_id in second_hop_node_ids
                    if node_id not in excluded_node_ids
                ]
                second_hop_hits = await self.graph_store.get_entries_for_node_ids(
                    node_ids=second_hop_node_ids,
                    limit=max(self.expansion_limit, limit * 3),
                    session_id=session_id,
                    persona_id=persona_id,
                    boundary=boundary,
                    query_scope=query_scope,
                )

        aggregated: dict[int, GraphKeywordResult] = {}
        conflicted_doc_ids: set[int] = set()

        def merge_hit(
            hit: dict[str, Any],
            weight: float,
            match_source: str,
            graph_distance: int | None,
        ) -> None:
            """按 canonical ID 合并命中，并保留最小已知图距离。"""
            doc_id = int(hit["source_memory_id"])
            if doc_id in conflicted_doc_ids:
                return
            source_boundary = hit.get("source_boundary")
            if not isinstance(source_boundary, GraphBoundary):
                source_boundary = boundary
            if source_boundary is None:
                return
            current = aggregated.get(doc_id)
            if current is not None and current.source_boundary != source_boundary:
                conflicted_doc_ids.add(doc_id)
                aggregated.pop(doc_id, None)
                return
            weighted_score = max(0.0, min(1.0, float(hit["score"]) * weight))
            hit_metadata = dict(hit.get("metadata") or {})
            hit_metadata["graph_match_source"] = match_source
            hit_metadata["graph_entry_type"] = hit.get("entry_type")
            hit_metadata["graph_relation_type"] = hit.get("relation_type")
            minimum_distance = graph_distance
            if current is not None and current.graph_distance is not None:
                minimum_distance = (
                    current.graph_distance
                    if graph_distance is None
                    else min(current.graph_distance, graph_distance)
                )
            if current is None or weighted_score > current.score:
                aggregated[doc_id] = GraphKeywordResult(
                    doc_id=doc_id,
                    score=weighted_score,
                    content=str(hit.get("content") or ""),
                    metadata=hit_metadata,
                    graph_distance=minimum_distance,
                    source_boundary=source_boundary,
                )
                return
            current.graph_distance = minimum_distance
            current.score = min(1.0, current.score + weighted_score * 0.35)
            if "graph_match_source" in current.metadata:
                current.metadata["graph_match_source"] = (
                    f"{current.metadata['graph_match_source']}+{match_source}"
                )

        for hit in direct_hits:
            merge_hit(hit, 1.0, "graph_keyword", 0)
        for hit in expansion_hits:
            merge_hit(hit, 0.7, "graph_neighbor", 0)
        for hit in hierarchy_hits:
            merge_hit(hit, 0.5, "graph_hierarchy", None)
        for hit in edge_neighbor_hits:
            merge_hit(hit, 0.7, "graph_edge_neighbor", 1)
        for hit in second_hop_hits:
            merge_hit(
                hit, max(0.0, min(1.0, self.second_hop_weight)), "graph_second_hop", 2
            )

        results = sorted(aggregated.values(), key=lambda item: item.score, reverse=True)
        return results[:limit]


__all__ = ["GraphKeywordRetriever", "GraphKeywordResult"]
