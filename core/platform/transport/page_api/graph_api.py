"""图谱 API — 概览 + 查询。"""

import asyncio
import math
import re
import time
from typing import Any

from astrbot.api import logger
from quart import request

_ONEBOT11_PERSON_LABEL = re.compile(r"QQ:([1-9][0-9]{0,18})", re.ASCII)
_POSITIVE_INT64_MAX = 9_223_372_036_854_775_807
_GRAPH_TIME_RANGE_MAX_HOURS = 720


class GraphApiMixin:
    """混入类：图谱概览 / 图谱查询 / 图谱搜索 (GET) / 图谱视图构建"""

    @staticmethod
    def _graph_json_object_payload_or_error(payload: Any):
        """校验请求体为 JSON 对象并返回稳定错误文本。"""

        if isinstance(payload, dict):
            return payload, None
        return None, "request body must be a JSON object"

    @staticmethod
    def _safe_score_breakdown(value: Any) -> dict[str, float]:
        """把分数明细收敛为有限的数字映射。"""

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
        """把外部分数安全转换为六位小数，非法值返回 ``None``。"""

        if value is None or isinstance(value, bool):
            return None
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_graph_time_range(
        payload: dict[str, Any],
    ) -> tuple[tuple[float | None, float | None], str | None]:
        """校验相对小时范围，并转换为绝对 Unix 秒边界。"""

        start_key = "time_start_hours"
        end_key = "time_end_hours"
        has_start = start_key in payload
        has_end = end_key in payload
        if not has_start and not has_end:
            return (None, None), None
        if not has_end:
            return (None, None), "图谱时间范围必须提供较旧边界"

        values: dict[str, int] = {}
        for key in (start_key, end_key):
            raw_value = payload.get(key, 0)
            if isinstance(raw_value, bool):
                return (None, None), "图谱时间范围必须是整数小时"
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                return (None, None), "图谱时间范围必须是整数小时"
            if not math.isfinite(numeric_value) or not numeric_value.is_integer():
                return (None, None), "图谱时间范围必须是整数小时"
            values[key] = int(numeric_value)

        start_hours = values[start_key]
        end_hours = values[end_key]
        if (
            start_hours < 0
            or start_hours > _GRAPH_TIME_RANGE_MAX_HOURS
            or end_hours <= 0
            or end_hours > _GRAPH_TIME_RANGE_MAX_HOURS
            or start_hours > end_hours
        ):
            return (None, None), "图谱时间范围无效"

        now = time.time()
        oldest_timestamp = now - end_hours * 3600
        newest_timestamp = now - start_hours * 3600 if start_hours > 0 else None
        return (oldest_timestamp, newest_timestamp), None

    @staticmethod
    def _canvas_response_snapshot(
        snapshot: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """移除画布渲染不需要的内部字段、条目与记忆详情。"""
        node_fields = (
            "id",
            "label",
            "display_name",
            "type",
            "identity_namespace",
            "stable_user_id",
            "weight",
            "memory_count",
            "degree",
            "entry_count",
        )
        edge_fields = ("id", "source", "target", "type", "weight", "timestamp")
        nodes = [
            {field: item[field] for field in node_fields if field in item}
            for item in snapshot.get("nodes", [])
            if isinstance(item, dict)
        ]
        edges = [
            {field: item[field] for field in edge_fields if field in item}
            for item in snapshot.get("edges", [])
            if isinstance(item, dict)
        ]
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _filter_graph_snapshot_by_time(
        snapshot: dict[str, Any],
        *,
        oldest_timestamp: float | None,
        newest_timestamp: float | None,
    ) -> dict[str, Any]:
        """按已规范化的边时间裁剪有限快照，并移除孤立节点。"""

        if oldest_timestamp is None and newest_timestamp is None:
            return snapshot

        visible_edges: list[dict[str, Any]] = []
        visible_node_ids: set[str] = set()
        for item in snapshot.get("edges", []):
            if not isinstance(item, dict):
                continue
            raw_timestamp = item.get("timestamp")
            timestamp: float | None = None
            if not isinstance(raw_timestamp, bool) and raw_timestamp is not None:
                try:
                    candidate = float(raw_timestamp)
                except (TypeError, ValueError):
                    candidate = 0.0
                if math.isfinite(candidate) and candidate > 0:
                    while candidate > 100_000_000_000:
                        candidate /= 1000.0
                    timestamp = candidate
            if timestamp is not None:
                if oldest_timestamp is not None and timestamp < oldest_timestamp:
                    continue
                if newest_timestamp is not None and timestamp > newest_timestamp:
                    continue
            visible_edges.append(item)
            visible_node_ids.update((str(item.get("source")), str(item.get("target"))))

        visible_nodes = [
            item
            for item in snapshot.get("nodes", [])
            if isinstance(item, dict) and str(item.get("id")) in visible_node_ids
        ]
        return {**snapshot, "nodes": visible_nodes, "edges": visible_edges}

    @staticmethod
    def _stable_person_identity(
        node: dict[str, Any],
    ) -> tuple[str, str] | None:
        """从字段完全一致的人物节点提取 OneBot 11 稳定 QQ 身份。"""

        if node.get("type") != "person":
            return None
        label = node.get("label")
        if not isinstance(label, str):
            return None
        matched = _ONEBOT11_PERSON_LABEL.fullmatch(label)
        if matched is None:
            return None
        stable_user_id = matched.group(1)
        if int(stable_user_id) > _POSITIVE_INT64_MAX:
            return None
        if node.get("canonical_value") != f"qq:{stable_user_id}":
            return None
        if node.get("key") != f"person:qq:{stable_user_id}":
            return None
        return "qq", stable_user_id

    async def _enrich_graph_identity_nodes(
        self,
        snapshot: dict[str, Any],
        identity_runtime: Any,
    ) -> dict[str, Any]:
        """在图快照副本中投影当前身份名称，不修改持久化节点。"""

        nodes = snapshot.get("nodes")
        get_identity = getattr(identity_runtime, "get_identity", None)
        if not isinstance(nodes, list) or not callable(get_identity):
            return snapshot

        cached_identities: dict[tuple[str, str], Any | None] = {}
        projected_nodes: list[Any] = []
        for item in nodes:
            if not isinstance(item, dict):
                projected_nodes.append(item)
                continue
            node = dict(item)
            identity_key = GraphApiMixin._stable_person_identity(node)
            if identity_key is None:
                projected_nodes.append(node)
                continue

            if identity_key not in cached_identities:
                try:
                    cached_identities[identity_key] = await get_identity(*identity_key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("图谱身份目录读取失败，已保留稳定人物标签")
                    return snapshot

            identity = cached_identities[identity_key]
            identity_namespace, stable_user_id = identity_key
            display_name = getattr(identity, "display_name", None)
            if (
                getattr(identity, "identity_namespace", None) != identity_namespace
                or getattr(identity, "stable_user_id", None) != stable_user_id
                or getattr(identity, "canonical_user_id", None) != stable_user_id
                or not isinstance(display_name, str)
                or not display_name
            ):
                projected_nodes.append(node)
                continue

            node.update(
                {
                    "label": display_name,
                    "identity_namespace": identity_namespace,
                    "stable_user_id": stable_user_id,
                    "display_name": display_name,
                }
            )
            projected_nodes.append(node)

        return {**snapshot, "nodes": projected_nodes}

    # ---- 公开端点 ----

    async def search_graph(self):
        """处理 GET /graph/search?query=X&memory_id=Y，并从查询参数构建请求载荷。"""
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
            if str(args.get("canvas", "")).strip().lower() in {"1", "true"}:
                payload["canvas"] = True
            for key in ("time_start_hours", "time_end_hours"):
                if key in args:
                    payload[key] = args.get(key)
            return await self._query_graph_impl(payload)
        except Exception as exc:
            logger.error(f"[PageAPI] 图谱搜索失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def query_graph(self):
        """处理 POST /graph/query，并从 JSON 请求体读取参数。"""
        payload = await request.get_json(silent=True) or {}
        payload, error = GraphApiMixin._graph_json_object_payload_or_error(payload)
        if error:
            return self._error(error)
        return await self._query_graph_impl(payload)

    async def get_graph_overview(self):
        """返回全量图概览；显式限制参数继续使用有限快照。"""

        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        args = request.args
        session_id = str(args.get("session_id", "")).strip() or None
        persona_id = str(args.get("persona_id", "")).strip() or None
        limit_keys = (
            "limit_memories",
            "limit_entries",
            "limit_nodes",
            "limit_edges",
        )
        has_explicit_limits = any(key in args for key in limit_keys)

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

            if has_explicit_limits:
                snapshot = await graph_store.get_graph_snapshot(
                    session_id=session_id,
                    persona_id=persona_id,
                    limit_memories=limit_memories,
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
            else:
                snapshot = await graph_store.get_graph_snapshot(
                    session_id=session_id,
                    persona_id=persona_id,
                    full=True,
                )
            identity_runtime = ready.get("identity_runtime")
            snapshot = await GraphApiMixin._enrich_graph_identity_nodes(
                self, snapshot, identity_runtime
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
        canvas_view = payload.get("canvas") is True
        filters = {"session_id": session_id, "persona_id": persona_id}
        time_range, time_range_error = GraphApiMixin._parse_graph_time_range(payload)
        if time_range_error:
            return self._error(time_range_error)
        oldest_timestamp, newest_timestamp = time_range
        limit_keys = (
            "limit_memories",
            "limit_entries",
            "limit_nodes",
            "limit_edges",
        )
        has_explicit_limits = any(key in payload for key in limit_keys)

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
                snapshot = GraphApiMixin._filter_graph_snapshot_by_time(
                    snapshot,
                    oldest_timestamp=oldest_timestamp,
                    newest_timestamp=newest_timestamp,
                )
                identity_runtime = ready.get("identity_runtime")
                snapshot = await GraphApiMixin._enrich_graph_identity_nodes(
                    self, snapshot, identity_runtime
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
                if canvas_view and not has_explicit_limits:
                    canvas_kwargs: dict[str, Any] = {
                        "session_id": session_id,
                        "persona_id": persona_id,
                    }
                    if oldest_timestamp is not None or newest_timestamp is not None:
                        canvas_kwargs.update(
                            {
                                "oldest_timestamp": oldest_timestamp,
                                "newest_timestamp": newest_timestamp,
                            }
                        )
                    snapshot = await graph_store.get_canvas_snapshot(
                        **canvas_kwargs,
                    )
                elif has_explicit_limits:
                    snapshot = await graph_store.get_graph_snapshot(
                        session_id=session_id,
                        persona_id=persona_id,
                        limit_memories=limit_memories,
                        limit_entries=limit_entries,
                        limit_nodes=limit_nodes,
                        limit_edges=limit_edges,
                    )
                else:
                    snapshot = await graph_store.get_graph_snapshot(
                        session_id=session_id,
                        persona_id=persona_id,
                        full=True,
                    )
                identity_runtime = ready.get("identity_runtime")
                snapshot = await GraphApiMixin._enrich_graph_identity_nodes(
                    self, snapshot, identity_runtime
                )
                if canvas_view and not has_explicit_limits:
                    snapshot = GraphApiMixin._canvas_response_snapshot(snapshot)
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
            snapshot = GraphApiMixin._filter_graph_snapshot_by_time(
                snapshot,
                oldest_timestamp=oldest_timestamp,
                newest_timestamp=newest_timestamp,
            )
            identity_runtime = ready.get("identity_runtime")
            snapshot = await GraphApiMixin._enrich_graph_identity_nodes(
                self, snapshot, identity_runtime
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
