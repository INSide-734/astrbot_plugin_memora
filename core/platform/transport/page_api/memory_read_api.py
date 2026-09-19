"""记忆读取 API"""

import asyncio
from typing import Any

import aiosqlite
from astrbot.api import logger
from quart import request

from ....features.memory.application.source_replayability import (
    SourceReplayabilityAssessor,
)
from ....features.memory.infrastructure.base import apply_perf_pragmas
from ....shared.memory_status import effective_memory_status
from ....shared.number_utils import clamp_float
from ....shared.sql import MEMORY_STATUS_SQL
from .graph_api import GraphApiMixin
from .response_utils import error_response


class MemoryReadApiMixin:
    """混入类：记忆列表 / 详情"""

    async def list_memories(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        query = request.args
        session_id = str(query.get("session_id", "")).strip() or None
        keyword = str(query.get("keyword", "")).strip()
        status_filter = str(query.get("status", "all")).strip().lower() or "all"
        include_mark_write_raw = (
            str(query.get("include_mark_write", "false")).strip().lower()
        )
        include_mark_write = include_mark_write_raw in {"1", "true", "yes", "on"}

        try:
            page = max(1, int(query.get("page", 1)))
            page_size = min(500, max(1, int(query.get("page_size", 20))))
        except (TypeError, ValueError):
            return self._error("分页参数无效")

        db_path = getattr(memory_engine, "db_path", None)
        if not db_path:
            return self._error("记忆引擎数据库路径不可用")

        offset = (page - 1) * page_size
        keyword_value = keyword or None
        params: dict[str, Any] = {
            "session_id": session_id,
            "status": None if status_filter == "all" else status_filter,
            "keyword": keyword_value,
            "keyword_is_digit": int(bool(keyword_value and keyword.isdigit())),
            "keyword_like": f"%{keyword}%" if keyword_value else None,
            "include_mark_write": int(include_mark_write),
        }

        try:
            async with aiosqlite.connect(db_path) as db:
                await apply_perf_pragmas(db)
                db.row_factory = aiosqlite.Row
                count_cursor = await db.execute(
                    f"SELECT COUNT(*) AS total "
                    f"FROM documents "
                    f"WHERE ("
                    f"    :session_id IS NULL "
                    f"    OR CASE WHEN json_valid(metadata) "
                    f"       THEN json_extract(metadata, '$.session_id') END = :session_id"
                    f") "
                    f"AND ("
                    f"    :status IS NULL "
                    f"    OR ({MEMORY_STATUS_SQL}) = :status"
                    f") "
                    f"AND ("
                    f"    :include_mark_write = 1 "
                    f"    OR COALESCE("
                    f"        CASE WHEN json_valid(metadata) "
                    f"        THEN json_extract(metadata, '$.gate_disposition') END,"
                    f"        ''"
                    f"    ) <> 'mark_write'"
                    f") "
                    f"AND ("
                    f"    :keyword IS NULL "
                    f"    OR ("
                    f"        :keyword_is_digit = 1 "
                    f"        AND ("
                    f"            CAST(id AS TEXT) = :keyword "
                    f"            OR text LIKE :keyword_like COLLATE NOCASE"
                    f"        )"
                    f"    ) "
                    f"    OR ("
                    f"        :keyword_is_digit = 0 "
                    f"        AND ("
                    f"            text LIKE :keyword_like COLLATE NOCASE "
                    f"            OR COALESCE("
                    f"                CASE WHEN json_valid(metadata) "
                    f"                THEN json_extract(metadata, '$.memory_type') END,"
                    f"                ''"
                    f"            ) LIKE :keyword_like COLLATE NOCASE"
                    f"        )"
                    f"    )"
                    f")",
                    params,
                )
                count_row = await count_cursor.fetchone()
                total = int(count_row["total"]) if count_row else 0

                cursor = await db.execute(
                    f"SELECT id, doc_id, text, metadata, created_at, updated_at "
                    f"FROM documents "
                    f"WHERE ("
                    f"    :session_id IS NULL "
                    f"    OR CASE WHEN json_valid(metadata) "
                    f"       THEN json_extract(metadata, '$.session_id') END = :session_id"
                    f") "
                    f"AND ("
                    f"    :status IS NULL "
                    f"    OR ({MEMORY_STATUS_SQL}) = :status"
                    f") "
                    f"AND ("
                    f"    :include_mark_write = 1 "
                    f"    OR COALESCE("
                    f"        CASE WHEN json_valid(metadata) "
                    f"        THEN json_extract(metadata, '$.gate_disposition') END,"
                    f"        ''"
                    f"    ) <> 'mark_write'"
                    f") "
                    f"AND ("
                    f"    :keyword IS NULL "
                    f"    OR ("
                    f"        :keyword_is_digit = 1 "
                    f"        AND ("
                    f"            CAST(id AS TEXT) = :keyword "
                    f"            OR text LIKE :keyword_like COLLATE NOCASE"
                    f"        )"
                    f"    ) "
                    f"    OR ("
                    f"        :keyword_is_digit = 0 "
                    f"        AND ("
                    f"            text LIKE :keyword_like COLLATE NOCASE "
                    f"            OR COALESCE("
                    f"                CASE WHEN json_valid(metadata) "
                    f"                THEN json_extract(metadata, '$.memory_type') END,"
                    f"                ''"
                    f"            ) LIKE :keyword_like COLLATE NOCASE"
                    f"        )"
                    f"    )"
                    f") "
                    f"ORDER BY COALESCE("
                    f"    CASE WHEN json_valid(metadata) "
                    f"    THEN CAST(json_extract(metadata, '$.create_time') AS REAL) END,"
                    f"    0"
                    f") DESC, id DESC "
                    f"LIMIT :limit OFFSET :offset",
                    {**params, "limit": page_size, "offset": offset},
                )
                rows = await cursor.fetchall()
        except Exception as exc:
            logger.error(f"[PageAPI] 获取记忆列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

        items = []
        for row in rows:
            try:
                row_id = row["id"]
                row_doc_id = row["doc_id"]
                row_text = row["text"]
                row_metadata = row["metadata"]
                row_created_at = row["created_at"]
                row_updated_at = row["updated_at"]
            except (KeyError, TypeError, IndexError) as exc:
                logger.debug("Skipping malformed memory list row: %r (%s)", row, exc)
                continue

            metadata = self._normalize_metadata(row_metadata)
            if not isinstance(metadata, dict):
                metadata = {}
            items.append(
                {
                    "id": row_id,
                    "doc_id": row_doc_id,
                    "text": row_text,
                    "content": row_text,
                    "summary": metadata.get("canonical_summary") or row_text,
                    "type": metadata.get("memory_type", "GENERAL"),
                    "status": effective_memory_status(metadata),
                    "importance": clamp_float(metadata.get("importance"), default=0.5),
                    "metadata": metadata,
                    "created_at": row_created_at,
                    "updated_at": row_updated_at,
                }
            )

        return self._ok(
            {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_more": (offset + page_size) < total,
            }
        )

    async def get_memory_detail(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error

        query = request.args
        # 前端发送 ?id=，同时兼容后端的 ?memory_id=
        raw_id = query.get("memory_id") or query.get("id") or ""
        try:
            memory_id = int(raw_id)
        except (TypeError, ValueError):
            return self._error("memory_id 必须是整数")

        try:
            memory = await self._get_memory_record(memory_id)
        except Exception as exc:
            logger.error(
                "[PageAPI] operation=get_memory_detail memory_id=%s error_class=%s",
                memory_id,
                type(exc).__name__,
            )
            return error_response("读取记忆失败", code="internal_error")
        if not isinstance(memory, dict) or not memory:
            return self._error("记忆不存在")

        metadata = self._normalize_metadata(memory.get("metadata"))
        if not isinstance(metadata, dict):
            metadata = {}
        memory_text = memory.get("text", "")
        key_facts = metadata.get("key_facts", [])
        topics = metadata.get("topics", [])
        update_history = metadata.get("update_history", [])
        detail = {
            # 前端兼容字段
            "id": memory.get("id"),
            "content": memory_text,
            "type": metadata.get("memory_type", "GENERAL"),
            # 后端字段：只投影管理员详情 allowlist；原始 metadata、会话/人格身份、
            # source mapping、scope/privacy/revision 不进入响应。
            "memory_id": memory.get("id"),
            "doc_id": memory.get("doc_id"),
            "text": memory_text,
            "summary": metadata.get("canonical_summary") or memory.get("text", ""),
            "created_at": memory.get("created_at"),
            "updated_at": memory.get("updated_at"),
            "memory_type": metadata.get("memory_type", "GENERAL"),
            "importance": clamp_float(metadata.get("importance"), default=0.5),
            "status": effective_memory_status(metadata),
            "key_facts": key_facts if isinstance(key_facts, list) else [],
            "topics": topics if isinstance(topics, list) else [],
            "create_time": metadata.get("create_time"),
            "last_access_time": metadata.get("last_access_time"),
            "update_history": update_history
            if isinstance(update_history, list)
            else [],
        }

        assessor = SourceReplayabilityAssessor(_source_store(ready))
        detail["source_replayability"] = (await assessor.assess(metadata)).to_payload()

        graph_store = self._get_graph_store(ready["memory_engine"])
        boundary = GraphApiMixin._graph_boundary_from_memory(
            {**memory, "metadata": metadata}
        )

        if graph_store is not None and boundary is not None:
            try:
                subgraph = await graph_store.get_subgraph_for_memories(
                    [memory_id],
                    limit_entries=20,
                    limit_nodes=20,
                    limit_edges=30,
                    boundary=boundary,
                )
                if not isinstance(subgraph, dict):
                    raise TypeError("subgraph payload must be a mapping")
                detail["graph_context"] = _project_graph_context(subgraph)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    "获取子图上下文失败 operation=get_memory_detail error_class=%s",
                    type(exc).__name__,
                )
                detail["graph_context"] = None
        else:
            detail["graph_context"] = None

        return self._ok(detail)


def _source_store(ready: dict[str, Any]) -> Any | None:
    """解析会话消息存储；缺失时返回 None，由评估器判为 unknown。"""

    conversation_manager = ready.get("conversation_manager")
    if conversation_manager is None:
        return None
    return getattr(conversation_manager, "store", None)


_GRAPH_NODE_SAFE_FIELDS = ("type", "entry_count", "memory_count", "degree", "weight")
_GRAPH_EDGE_SAFE_FIELDS = (
    "relation_type",
    "type",
    "weight",
    "confidence",
    "status",
    "timestamp",
)
_GRAPH_ENTRY_SAFE_FIELDS = ("entry_type", "relation_type")


def _project_graph_context(payload: Any) -> dict[str, list[dict[str, Any]]]:
    """只返回图统计字段，拒绝内部正文、身份和来源映射。"""

    if not isinstance(payload, dict):
        return {"nodes": [], "edges": [], "entries": []}
    return {
        "nodes": _project_graph_items(payload.get("nodes"), _GRAPH_NODE_SAFE_FIELDS),
        "edges": _project_graph_items(payload.get("edges"), _GRAPH_EDGE_SAFE_FIELDS),
        "entries": _project_graph_items(
            payload.get("entries"), _GRAPH_ENTRY_SAFE_FIELDS
        ),
    }


def _project_graph_items(items: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """投影有限标量字段，不透传任意 Store 字典或嵌套对象。"""

    if not isinstance(items, list):
        return []
    projected: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        safe = {
            field: item[field]
            for field in fields
            if field in item and isinstance(item[field], (str, int, float, bool))
        }
        if safe:
            projected.append(safe)
    return projected
