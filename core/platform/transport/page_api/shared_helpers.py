"""PluginPageApi 的共享辅助 mixin（供所有 page_api 混入类复用）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ....features.observability.infrastructure.debug_reporter import (
    report_debug_exception,
)
from ....shared.number_utils import safe_float
from .response_utils import error_response, ok_response


def _page_api_logger():
    """返回聚合器当前绑定的 logger，兼容模块级替换与方法 patch。"""
    from . import page_api

    return page_api.logger


class SharedPageApiHelpersMixin:
    """页面接口共享辅助方法。组合类必须提供已初始化的插件实例。"""

    plugin: Any

    async def _ensure_plugin_ready(self) -> tuple[dict[str, Any] | None, dict | None]:
        """确保插件就绪，且永不抛出异常（所有异常都会转为错误响应）。

        返回值：``(ready_dict, error_dict)``，二者互斥，必有一个为 ``None``。
        """
        try:
            # 页面请求不应在 Provider 缺失时阻塞 30 秒；状态提示由 Dashboard
            # 通过 metrics/summary 展示，具体数据接口立即返回可识别的未就绪错误。
            ready, message = await self.plugin._ensure_plugin_ready(wait=False)
        except Exception as exc:
            _page_api_logger().error("[页面接口] 插件就绪检查异常")
            report_debug_exception(
                "maintenance_task",
                exc,
                component="page_api",
                stage="maintenance",
                status="failed",
                reason_code="plugin_readiness_error",
                task_type="maintenance",
            )
            return None, error_response(
                "插件就绪检查失败", code="plugin_readiness_error"
            )
        if not ready:
            return None, error_response(
                message or "插件尚未就绪",
                code="plugin_not_ready",
            )
        try:
            memory_engine = self.plugin.initializer.memory_engine
            if memory_engine is None:
                return None, self._error("记忆引擎未初始化")
            return {
                "memory_engine": memory_engine,
                "conversation_manager": self.plugin.initializer.conversation_manager,
                "identity_runtime": getattr(
                    self.plugin.initializer, "identity_runtime", None
                ),
                "index_validator": self.plugin.initializer.index_validator,
            }, None
        except Exception as exc:
            _page_api_logger().error("[页面接口] 获取引擎组件失败")
            report_debug_exception(
                "maintenance_task",
                exc,
                component="page_api",
                stage="maintenance",
                status="failed",
                reason_code="component_lookup_error",
                task_type="maintenance",
            )
            return None, error_response(
                "获取引擎组件失败", code="component_lookup_error"
            )

    async def _get_memory_record(self, memory_id: int) -> dict[str, Any] | None:
        """读取 canonical 记忆记录，必要时回退到权威数据库。"""
        memory_engine = self.plugin.initializer.memory_engine
        if memory_engine is None:
            return None
        memory = await memory_engine.get_memory(memory_id)
        if memory:
            return memory
        if memory_engine.db_connection is None:
            return None
        cursor = await memory_engine.db_connection.execute(
            "SELECT id, doc_id, text, metadata, created_at, updated_at "
            "FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "doc_id": row[1],
            "text": row[2],
            "metadata": self._normalize_metadata(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
        }

    @staticmethod
    def _normalize_metadata(metadata: Any) -> dict[str, Any]:
        """把字典或 JSON 文本规范化为 metadata 字典。"""
        if isinstance(metadata, dict):
            return metadata
        if not metadata:
            return {}
        try:
            parsed = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _importance_to_display(value: Any) -> float:
        """把内部重要性规范化为控制台使用的零到十分值。"""
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = 0.5
        if parsed <= 1.0:
            parsed *= 10.0
        return round(max(0.0, min(10.0, parsed)), 2)

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        """构造成功响应 envelope。"""
        return ok_response(data)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        """构造错误响应 envelope。"""
        return error_response(message)

    def _maintenance_write_guard(self) -> dict[str, Any] | None:
        """在待恢复备份存在时阻止页面写操作。"""
        backup_manager = getattr(self.plugin, "_backup_manager", None)
        if backup_manager is None:
            return None
        try:
            get_state = getattr(backup_manager, "get_maintenance_state", None)
            if callable(get_state):
                state = get_state()
                has_pending = (
                    bool(state.get("blocked", False))
                    if isinstance(state, Mapping)
                    else bool(backup_manager.has_pending_restores())
                )
            else:
                has_pending = bool(backup_manager.has_pending_restores())
        except AttributeError:
            has_pending = False
        except Exception as exc:
            _page_api_logger().error(
                "[页面接口] operation=%s error_class=%s",
                "maintenance_write_guard",
                type(exc).__name__,
            )
            return error_response(
                "维护状态检查失败，请稍后重试。",
                code="maintenance_guard_failed",
            )
        if not has_pending:
            return None
        try:
            backup_manager.list_pending_restores()
        except Exception as exc:
            _page_api_logger().debug(
                "[页面接口] operation=%s error_class=%s",
                "maintenance_write_guard_list_pending",
                type(exc).__name__,
            )
        return error_response(
            "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。",
            code="maintenance_blocked",
        )

    @staticmethod
    def _get_graph_store(memory_engine):
        """返回记忆引擎上的可选图存储。"""
        return getattr(memory_engine, "graph_store", None)

    @staticmethod
    def _tokenize_graph_query(query: str) -> list[str]:
        """生成最多十二个稳定图谱查询词元。"""
        query_text = str(query or "").strip().lower()
        if not query_text:
            return []
        normalized = "".join(c if c.isalnum() else " " for c in query_text)
        raw_tokens = [t for t in normalized.split() if t]
        tokens: list[str] = []
        seen: set[str] = set()

        def add_token(value: str):
            """按首次出现顺序加入有效且未重复的词元。"""
            token = value.strip()
            if len(token) < 2 or token in seen:
                return
            seen.add(token)
            tokens.append(token)

        for token in raw_tokens:
            add_token(token)

        compact = "".join(c for c in query_text if c.isalnum())
        if compact and any(ord(c) > 127 for c in compact):
            add_token(compact)
            for size in (2, 3):
                if len(tokens) >= 12:
                    break
                max_index = max(0, len(compact) - size + 1)
                for idx in range(max_index):
                    add_token(compact[idx : idx + size])
                    if len(tokens) >= 12:
                        break
        return tokens[:12]

    @staticmethod
    def _build_graph_view_payload(
        snapshot: dict[str, Any],
        stats: dict[str, Any],
        *,
        enabled: bool,
        mode: str,
        query: str | None = None,
        memory_id: int | None = None,
        retrieval_items: list[dict[str, Any]] | None = None,
        matched_node_ids: list[int] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建 Dashboard 图谱视图使用的安全聚合载荷。"""

        def _dict_items(values: Any) -> list[dict[str, Any]]:
            """仅复制输入序列中的字典项。"""
            return [dict(item) for item in values if isinstance(item, dict)]

        def _coerce_int(value: Any, default: int | None = 0) -> int | None:
            """把图谱字段转换为整数，非法值返回调用方指定的默认值。"""
            if isinstance(value, bool):
                return default
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        nodes = _dict_items(snapshot.get("nodes", []))
        edges_raw = _dict_items(snapshot.get("edges", []))
        entries = _dict_items(snapshot.get("entries", []))
        memories = [
            item
            for item in _dict_items(snapshot.get("memories", []))
            if _coerce_int(item.get("memory_id"), None) is not None
        ]

        # 安全保护：过滤引用不存在节点的孤立边，防止前端 G6 抛出“节点不存在”错误
        node_ids = {item.get("id") for item in nodes}
        edges = [
            edge
            for edge in edges_raw
            if edge.get("source") in node_ids and edge.get("target") in node_ids
        ]
        if len(edges) < len(edges_raw):
            _page_api_logger().warning(
                f"[页面接口] 已过滤 {len(edges_raw) - len(edges)} 条孤立边 "
                f"(节点数={len(nodes)}, 原始边数={len(edges_raw)})"
            )
        raw_retrieval_items = retrieval_items or []
        retrieval_items = []
        for item in raw_retrieval_items:
            if not isinstance(item, dict):
                continue
            memory_id = item.get("memory_id")
            if memory_id is None:
                continue
            if _coerce_int(memory_id, None) is None:
                continue
            retrieval_items.append(dict(item))

        raw_matched_node_ids = matched_node_ids or []
        matched_node_ids = []
        for item in raw_matched_node_ids:
            coerced = _coerce_int(item, None)
            if coerced is None:
                continue
            matched_node_ids.append(coerced)
        matched_node_id_set = set(matched_node_ids)
        retrieval_lookup = {
            _coerce_int(item["memory_id"]): item
            for item in retrieval_items
            if item.get("memory_id") is not None
        }

        node_type_breakdown: dict[str, int] = {}
        relation_breakdown: dict[str, int] = {}

        for node in nodes:
            node["highlighted"] = (
                _coerce_int(node.get("id", 0), 0) in matched_node_id_set
            )
            nt = str(node.get("type", "unknown") or "unknown")
            node_type_breakdown[nt] = node_type_breakdown.get(nt, 0) + 1

        for edge in edges:
            rt = str(edge.get("relation_type", "related") or "related")
            relation_breakdown[rt] = relation_breakdown.get(rt, 0) + 1

        for memory in memories:
            mk = memory.get("memory_id")
            if mk is None:
                continue
            retrieval = retrieval_lookup.get(_coerce_int(mk, -1))
            if retrieval is not None:
                memory["retrieval"] = retrieval
        top_nodes = sorted(
            nodes,
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -(_coerce_int(item.get("degree", 0), 0) or 0),
                str(item.get("label", "")),
            ),
        )[:8]
        top_memories = sorted(
            memories,
            key=lambda item: (
                -safe_float((item.get("retrieval") or {}).get("final_score"), -1.0),
                -(_coerce_int(item.get("entry_count", 0), 0) or 0),
                -(_coerce_int(item.get("node_count", 0), 0) or 0),
                -(_coerce_int(item.get("edge_count", 0), 0) or 0),
                -safe_float(item.get("importance"), 0.0),
            ),
        )[:8]

        return {
            # 前端兼容：GraphPage 会直接从数据根层读取 nodes/edges
            "nodes": nodes,
            "edges": edges,
            "enabled": enabled,
            "mode": mode,
            "query": query or None,
            "memory_id": memory_id,
            "filters": filters or {},
            "summary": {
                "visible_node_count": len(nodes),
                "visible_edge_count": len(edges),
                "visible_entry_count": len(entries),
                "visible_memory_count": len(memories),
                "graph_node_count": _coerce_int(stats.get("graph_nodes", 0), 0),
                "graph_edge_count": _coerce_int(stats.get("graph_edges", 0), 0),
                "graph_entry_count": _coerce_int(stats.get("graph_entries", 0), 0),
                "graph_memory_enabled": bool(enabled),
                "node_type_breakdown": node_type_breakdown,
                "relation_breakdown": relation_breakdown,
            },
            "matched_node_ids": matched_node_ids,
            "matched_memory_ids": [item["memory_id"] for item in retrieval_items],
            "top_nodes": top_nodes,
            "top_memories": top_memories,
            "retrieval": {"total": len(retrieval_items), "items": retrieval_items},
            "snapshot": {
                "nodes": nodes,
                "edges": edges,
                "entries": entries,
                "memories": memories,
            },
        }


__all__ = ["SharedPageApiHelpersMixin"]
