"""图谱 API — 概览 + 查询。"""

import asyncio
import math
import re
import time
from typing import Any

from astrbot.api import logger
from quart import request

from ....features.memory.domain.revision import memory_revision
from ....features.memory.graph.domain.models import GraphBoundary
from ....shared.data_helpers import safe_parse_metadata
from .response_utils import error_response

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
    def _graph_boundary_required_response() -> dict[str, Any]:
        """返回不暴露来源细节的稳定图边界错误。"""
        return error_response("图谱来源边界不可用", code="graph_boundary_required")

    @staticmethod
    def _graph_boundary_from_memory(memory: Any) -> GraphBoundary | None:
        """从 canonical 记录的权威时间快照构造图边界。"""
        if not isinstance(memory, dict):
            return None
        metadata = dict(safe_parse_metadata(memory.get("metadata")))
        revision = memory_revision(memory)
        if not revision:
            raw_revision = metadata.get("revision_token")
            if isinstance(raw_revision, str) and raw_revision.strip():
                revision = raw_revision.strip()
        if not revision:
            return None
        metadata["revision_token"] = revision
        try:
            return GraphBoundary.from_metadata(metadata)
        except ValueError:
            return None

    @staticmethod
    async def _canonical_graph_boundary(
        memory_engine: Any,
        memory_id: int,
    ) -> GraphBoundary | None:
        """只从 canonical memory metadata 构造图查询边界。"""
        try:
            memory = await memory_engine.get_memory(memory_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return GraphApiMixin._graph_boundary_from_memory(memory)

    @staticmethod
    def _coerce_memory_id(raw_id: Any) -> int:
        """将外部传入的 memory ID 转换为整数，同时拒绝 JSON 布尔值。"""

        if isinstance(raw_id, bool):
            raise TypeError("boolean values are not valid memory ids")
        return int(raw_id)

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

    @staticmethod
    def _filter_graph_snapshot_by_query(
        snapshot: dict[str, Any], query: str
    ) -> dict[str, Any]:
        """在已授权的记忆快照内匹配节点或条目，不放宽到全局图谱。"""
        needle = query.strip().casefold()
        if not needle:
            return snapshot
        entries = [
            entry
            for entry in snapshot.get("entries", [])
            if needle in str(entry.get("content") or "").casefold()
        ]
        node_ids = {
            str(node_id) for entry in entries for node_id in entry.get("node_ids", [])
        }
        nodes = snapshot.get("nodes", [])
        node_ids.update(
            str(node["id"])
            for node in nodes
            if any(
                needle in str(node.get(key) or "").casefold()
                for key in ("label", "canonical_value")
            )
        )
        return {
            **snapshot,
            "nodes": [node for node in nodes if str(node["id"]) in node_ids],
            "edges": [
                edge
                for edge in snapshot.get("edges", [])
                if str(edge.get("source")) in node_ids
                and str(edge.get("target")) in node_ids
            ],
            "entries": entries,
            "memories": snapshot.get("memories", []) if node_ids or entries else [],
        }

    @staticmethod
    def _filter_admin_canvas_by_query(
        snapshot: dict[str, Any], query: str
    ) -> dict[str, Any]:
        """在管理员画布内匹配可见标签，并保留命中节点的一跳关联边与对端。"""
        needle = query.strip().casefold()
        if not needle:
            return snapshot
        nodes = [node for node in snapshot.get("nodes", []) if isinstance(node, dict)]
        matched_node_ids = {
            str(node.get("id"))
            for node in nodes
            if any(
                needle in str(node.get(key) or "").casefold()
                for key in ("label", "canonical_value")
            )
        }
        if not matched_node_ids:
            return {**snapshot, "nodes": [], "edges": []}

        visible_node_ids = set(matched_node_ids)
        visible_edges: list[dict[str, Any]] = []
        for edge in snapshot.get("edges", []):
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source"))
            target = str(edge.get("target"))
            # 只沿命中节点展开一跳：对端不会成为新的扩散种子。
            if source in matched_node_ids or target in matched_node_ids:
                visible_edges.append(edge)
                visible_node_ids.update((source, target))
        return {
            **snapshot,
            "nodes": [
                node for node in nodes if str(node.get("id")) in visible_node_ids
            ],
            "edges": visible_edges,
        }

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
            logger.error("[PageAPI] 图谱搜索失败: %s", type(exc).__name__)
            return error_response("图谱搜索失败", code="internal_error")

    async def query_graph(self):
        """处理 POST /graph/query，并从 JSON 请求体读取参数。"""
        payload = await request.get_json(silent=True)
        payload, error = GraphApiMixin._graph_json_object_payload_or_error(payload)
        if error:
            return self._error(error)
        return await self._query_graph_impl(payload)

    async def get_graph_overview(self):
        """处理 GET /graph/overview，并返回与默认搜索相同的管理员总览。"""
        args = request.args
        payload: dict[str, Any] = {}
        for key in ("session_id", "persona_id"):
            value = str(args.get(key, "")).strip()
            if value:
                payload[key] = value
        for key in ("time_start_hours", "time_end_hours"):
            if key in args:
                payload[key] = args.get(key)
        return await self._admin_graph_snapshot_impl(payload)

    # ---- 内部实现 ----

    async def _query_graph_impl(self, payload: dict[str, Any]):
        """按 canonical memory ID 聚焦查询，否则回退管理员只读总览/搜索。"""
        memory_id_raw = payload.get("memory_id")
        if memory_id_raw in (None, ""):
            # 未提供 canonical memory ID 不是错误：返回管理员跨来源画布。
            return await self._admin_graph_snapshot_impl(payload)

        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        try:
            memory_id = GraphApiMixin._coerce_memory_id(memory_id_raw)
        except (TypeError, ValueError):
            return self._error("memory_id 必须是整数")
        boundary = await GraphApiMixin._canonical_graph_boundary(
            memory_engine,
            memory_id,
        )
        if boundary is None:
            return GraphApiMixin._graph_boundary_required_response()

        query_text = str(payload.get("query", "")).strip()
        session_id = str(payload.get("session_id", "")).strip() or None
        persona_id = str(payload.get("persona_id", "")).strip() or None
        filters = {"session_id": session_id, "persona_id": persona_id}
        time_range, time_range_error = GraphApiMixin._parse_graph_time_range(payload)
        if time_range_error:
            return self._error(time_range_error)
        oldest_timestamp, newest_timestamp = time_range

        try:
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

            snapshot = await graph_store.get_subgraph_for_memories(
                [memory_id],
                limit_entries=limit_entries,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
                boundary=boundary,
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
            snapshot = GraphApiMixin._filter_graph_snapshot_by_query(
                snapshot, query_text
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
        except ValueError as exc:
            if str(exc) == "graph_boundary_required":
                return GraphApiMixin._graph_boundary_required_response()
            logger.error("[PageAPI] 图谱查询失败: %s", type(exc).__name__)
            return error_response("图谱查询失败", code="internal_error")
        except Exception as exc:
            logger.error("[PageAPI] 图谱查询失败: %s", type(exc).__name__)
            return error_response("图谱查询失败", code="internal_error")

    async def _admin_graph_snapshot_impl(self, payload: dict[str, Any]):
        """返回管理员总览：合法来源的轻量画布，可按查询词过滤可见标签。"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        query_text = str(payload.get("query", "")).strip()
        session_id = str(payload.get("session_id", "")).strip() or None
        persona_id = str(payload.get("persona_id", "")).strip() or None
        filters = {"session_id": session_id, "persona_id": persona_id}
        time_range, time_range_error = GraphApiMixin._parse_graph_time_range(payload)
        if time_range_error:
            return self._error(time_range_error)
        oldest_timestamp, newest_timestamp = time_range
        mode = "query" if query_text else "overview"

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
                        mode=mode,
                        query=query_text or None,
                        filters=filters,
                    )
                )

            snapshot = await graph_store.get_admin_canvas_snapshot(
                session_id=session_id,
                persona_id=persona_id,
                oldest_timestamp=oldest_timestamp,
                newest_timestamp=newest_timestamp,
            )
            identity_runtime = ready.get("identity_runtime")
            snapshot = await GraphApiMixin._enrich_graph_identity_nodes(
                self, snapshot, identity_runtime
            )
            if query_text:
                # 只匹配总览已授权的可见标签，并保留命中节点的一跳关联边。
                snapshot = GraphApiMixin._filter_admin_canvas_by_query(
                    snapshot, query_text
                )
            return self._ok(
                self._build_graph_view_payload(
                    snapshot,
                    stats,
                    enabled=True,
                    mode=mode,
                    query=query_text or None,
                    filters=filters,
                )
            )
        except Exception as exc:
            logger.error("[PageAPI] 图谱总览失败: %s", type(exc).__name__)
            return error_response("图谱总览失败", code="internal_error")
