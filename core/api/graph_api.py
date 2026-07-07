"""图谱 API — 概览 + 查询"""

from typing import Any

from quart import request

from astrbot.api import logger


class GraphApiMixin:
    """混入类：图谱概览 / 图谱查询 / 图谱搜索 (GET) / 图谱视图构建"""

    @staticmethod
    def _json_object_payload_or_error(payload: Any):
        if isinstance(payload, dict):
            return payload, None
        return None, "request body must be a JSON object"

    @staticmethod
    def _safe_score_breakdown(value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, float] = {}
        for key, item in value.items():
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                normalized[str(key)] = round(float(item), 6)
        return normalized

    @staticmethod
    def _coerce_memory_id(raw_id: Any) -> int:
        """将外部传入的 memory ID 转换为整数，同时拒绝 JSON 布尔值。"""
        if isinstance(raw_id, bool):
            raise TypeError("boolean values are not valid memory ids")
        return int(raw_id)

    @staticmethod
    def _safe_round_score(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            return None

    # ---- 公开端点 ----

    async def search_graph(self):
        """GET /graph/search?query=X&memory_id=Y — 前端兼容端点，从 query string 构建 payload"""
        try:
            args = request.args
            query_text = str(args.get("query", "")).strip()
            memory_id_raw = args.get("memory_id", "").strip()
            payload: dict[str, Any] = {}
            if query_text:
                payload["query"] = query_text
            if memory_id_raw:
                try:
                    payload["memory_id"] = GraphApiMixin._coerce_memory_id(
                        memory_id_raw
                    )
                except (TypeError, ValueError):
                    return self._error("memory_id 必须是整数")
            return await self._query_graph_impl(payload)
        except Exception as exc:
            logger.error(f"[PageAPI] 图谱搜索失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def query_graph(self):
        """POST /graph/query — 从 JSON body 读取参数"""
        payload = await request.get_json(silent=True) or {}
        payload, error = GraphApiMixin._json_object_payload_or_error(payload)
        if error:
            return self._error(error)
        return await self._query_graph_impl(payload)

    async def get_graph_overview(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        args = request.args
        session_id = str(args.get("session_id", "")).strip() or None
        persona_id = str(args.get("persona_id", "")).strip() or None

        try:
            limit_memories = max(1, min(int(args.get("limit_memories", 12)), 24))
            limit_entries = max(12, min(int(args.get("limit_entries", 36)), 80))
            limit_nodes = max(12, min(int(args.get("limit_nodes", 48)), 80))
            limit_edges = max(12, min(int(args.get("limit_edges", 72)), 120))
        except (TypeError, ValueError):
            return self._error("图谱分页参数无效")

        try:
            stats = await memory_engine.get_statistics()
            graph_store = self._get_graph_store(memory_engine)
            empty = {"nodes": [], "edges": [], "entries": [], "memories": []}
            filters = {"session_id": session_id, "persona_id": persona_id}

            if graph_store is None:
                return self._ok(
                    self._build_graph_view_payload(
                        empty, stats, enabled=False, mode="overview", filters=filters
                    )
                )

            snapshot = await graph_store.get_graph_snapshot(
                session_id=session_id,
                persona_id=persona_id,
                limit_memories=limit_memories,
                limit_entries=limit_entries,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
            )
            return self._ok(
                self._build_graph_view_payload(
                    snapshot, stats, enabled=True, mode="overview", filters=filters
                )
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 获取图谱概览失败: {exc}", exc_info=True)
            return self._error(str(exc))

    # ---- 内部实现 ----

    async def _query_graph_impl(self, payload: dict[str, Any]):
        """图谱查询核心逻辑，由 search_graph (GET) 和 query_graph (POST) 共用"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        query_text = str(payload.get("query", "")).strip()
        session_id = str(payload.get("session_id", "")).strip() or None
        persona_id = str(payload.get("persona_id", "")).strip() or None
        memory_id_raw = payload.get("memory_id")
        filters = {"session_id": session_id, "persona_id": persona_id}

        try:
            limit_memories = max(1, min(int(payload.get("limit_memories", 10)), 24))
            limit_entries = max(12, min(int(payload.get("limit_entries", 40)), 80))
            limit_nodes = max(12, min(int(payload.get("limit_nodes", 56)), 80))
            limit_edges = max(12, min(int(payload.get("limit_edges", 96)), 120))
        except (TypeError, ValueError):
            return self._error("图谱检索参数无效")

        try:
            stats = await memory_engine.get_statistics()
            graph_store = self._get_graph_store(memory_engine)
            empty = {"nodes": [], "edges": [], "entries": [], "memories": []}

            if graph_store is None:
                return self._ok(
                    self._build_graph_view_payload(
                        empty,
                        stats,
                        enabled=False,
                        mode="query",
                        query=query_text,
                        filters=filters,
                    )
                )

            if memory_id_raw not in (None, ""):
                try:
                    memory_id = GraphApiMixin._coerce_memory_id(memory_id_raw)
                except (TypeError, ValueError):
                    return self._error("memory_id 必须是整数")
                snapshot = await graph_store.get_subgraph_for_memories(
                    [memory_id],
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
                return self._ok(
                    self._build_graph_view_payload(
                        snapshot,
                        stats,
                        enabled=True,
                        mode="memory_focus",
                        memory_id=memory_id,
                        filters=filters,
                    )
                )

            if not query_text:
                snapshot = await graph_store.get_graph_snapshot(
                    session_id=session_id,
                    persona_id=persona_id,
                    limit_memories=limit_memories,
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
                return self._ok(
                    self._build_graph_view_payload(
                        snapshot, stats, enabled=True, mode="overview", filters=filters
                    )
                )

            search_results = await memory_engine.search_memories(
                query=query_text,
                k=limit_memories,
                session_id=session_id,
                persona_id=persona_id,
            )
            retrieval_items = []
            matched_memory_ids: list[int] = []
            seen: set[int] = set()
            for result in search_results:
                try:
                    mid = int(result.doc_id)
                    final_score = GraphApiMixin._safe_round_score(result.final_score)
                    rrf_score = GraphApiMixin._safe_round_score(result.rrf_score)
                    bm25_score = GraphApiMixin._safe_round_score(result.bm25_score)
                    vector_score = GraphApiMixin._safe_round_score(result.vector_score)
                except (TypeError, ValueError, AttributeError):
                    continue
                if final_score is None or rrf_score is None:
                    continue
                if mid not in seen:
                    seen.add(mid)
                    matched_memory_ids.append(mid)
                retrieval_items.append(
                    {
                        "memory_id": mid,
                        "content": getattr(result, "content", ""),
                        "metadata": getattr(result, "metadata", {}) or {},
                        "final_score": final_score,
                        "rrf_score": rrf_score,
                        "bm25_score": bm25_score,
                        "vector_score": vector_score,
                        "score_breakdown": GraphApiMixin._safe_score_breakdown(
                            getattr(result, "score_breakdown", None)
                        ),
                    }
                )

            tokens = self._tokenize_graph_query(query_text)
            matched_node_ids: list[int] = []
            if tokens:
                node_hits = await graph_store.search_nodes_by_tokens(
                    tokens, limit=max(8, min(limit_nodes, 24))
                )
                matched_node_ids = []
                for item in node_hits:
                    if not isinstance(item, dict):
                        continue
                    try:
                        matched_node_ids.append(int(item["id"]))
                    except (KeyError, TypeError, ValueError):
                        continue
                node_entry_hits = await graph_store.get_entries_for_node_ids(
                    matched_node_ids,
                    limit=max(8, min(limit_entries, 24)),
                    session_id=session_id,
                    persona_id=persona_id,
                )
                for hit in node_entry_hits:
                    if not isinstance(hit, dict):
                        continue
                    try:
                        mid = int(hit["source_memory_id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if mid not in seen:
                        seen.add(mid)
                        matched_memory_ids.append(mid)

            snapshot = await graph_store.get_subgraph_for_memories(
                matched_memory_ids[:limit_memories],
                limit_entries=limit_entries,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
            )
            return self._ok(
                self._build_graph_view_payload(
                    snapshot,
                    stats,
                    enabled=True,
                    mode="query",
                    query=query_text,
                    retrieval_items=retrieval_items,
                    matched_node_ids=matched_node_ids,
                    filters=filters,
                )
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 图谱查询失败: {exc}", exc_info=True)
            return self._error(str(exc))
