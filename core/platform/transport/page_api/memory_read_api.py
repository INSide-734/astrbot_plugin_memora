"""记忆读取 API"""

from typing import Any

import aiosqlite
from astrbot.api import logger
from quart import request

from ....features.memory.infrastructure.base import apply_perf_pragmas
from ....shared.number_utils import clamp_float
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
        }

        try:
            async with aiosqlite.connect(db_path) as db:
                await apply_perf_pragmas(db)
                db.row_factory = aiosqlite.Row
                count_cursor = await db.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM documents
                    WHERE (
                        :session_id IS NULL
                        OR CASE WHEN json_valid(metadata)
                           THEN json_extract(metadata, '$.session_id') END = :session_id
                    )
                      AND (
                        :status IS NULL
                        OR COALESCE(
                            CASE WHEN json_valid(metadata)
                            THEN json_extract(metadata, '$.status') END,
                            'active'
                        ) = :status
                      )
                      AND (
                        :keyword IS NULL
                        OR (
                            :keyword_is_digit = 1
                            AND (
                                CAST(id AS TEXT) = :keyword
                                OR text LIKE :keyword_like COLLATE NOCASE
                            )
                        )
                        OR (
                            :keyword_is_digit = 0
                            AND (
                                text LIKE :keyword_like COLLATE NOCASE
                                OR COALESCE(
                                    CASE WHEN json_valid(metadata)
                                    THEN json_extract(metadata, '$.memory_type') END,
                                    ''
                                ) LIKE :keyword_like COLLATE NOCASE
                            )
                        )
                      )
                    """,
                    params,
                )
                count_row = await count_cursor.fetchone()
                total = int(count_row["total"]) if count_row else 0

                cursor = await db.execute(
                    """
                    SELECT id, doc_id, text, metadata, created_at, updated_at
                    FROM documents
                    WHERE (
                        :session_id IS NULL
                        OR CASE WHEN json_valid(metadata)
                           THEN json_extract(metadata, '$.session_id') END = :session_id
                    )
                      AND (
                        :status IS NULL
                        OR COALESCE(
                            CASE WHEN json_valid(metadata)
                            THEN json_extract(metadata, '$.status') END,
                            'active'
                        ) = :status
                      )
                      AND (
                        :keyword IS NULL
                        OR (
                            :keyword_is_digit = 1
                            AND (
                                CAST(id AS TEXT) = :keyword
                                OR text LIKE :keyword_like COLLATE NOCASE
                            )
                        )
                        OR (
                            :keyword_is_digit = 0
                            AND (
                                text LIKE :keyword_like COLLATE NOCASE
                                OR COALESCE(
                                    CASE WHEN json_valid(metadata)
                                    THEN json_extract(metadata, '$.memory_type') END,
                                    ''
                                ) LIKE :keyword_like COLLATE NOCASE
                            )
                        )
                      )
                    ORDER BY COALESCE(
                        CASE WHEN json_valid(metadata)
                        THEN CAST(json_extract(metadata, '$.create_time') AS REAL) END,
                        0
                    ) DESC, id DESC
                    LIMIT :limit OFFSET :offset
                    """,
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
                    "status": metadata.get("status", "active"),
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
            # 后端完整字段
            "memory_id": memory.get("id"),
            "doc_id": memory.get("doc_id"),
            "text": memory_text,
            "summary": metadata.get("canonical_summary") or memory.get("text", ""),
            "created_at": memory.get("created_at"),
            "updated_at": memory.get("updated_at"),
            "metadata": metadata,
            "memory_type": metadata.get("memory_type", "GENERAL"),
            "importance": clamp_float(metadata.get("importance"), default=0.5),
            "status": metadata.get("status", "active"),
            "session_id": metadata.get("session_id"),
            "persona_id": metadata.get("persona_id"),
            "key_facts": key_facts if isinstance(key_facts, list) else [],
            "topics": topics if isinstance(topics, list) else [],
            "create_time": metadata.get("create_time"),
            "last_access_time": metadata.get("last_access_time"),
            "update_history": update_history
            if isinstance(update_history, list)
            else [],
        }

        graph_store = self._get_graph_store(ready["memory_engine"])
        if graph_store is not None:
            try:
                subgraph = await graph_store.get_subgraph_for_memories(
                    [memory_id],
                    limit_entries=20,
                    limit_nodes=20,
                    limit_edges=30,
                )
                if not isinstance(subgraph, dict):
                    raise TypeError("subgraph payload must be a mapping")
                nodes = subgraph.get("nodes", [])
                edges = subgraph.get("edges", [])
                entries = subgraph.get("entries", [])
                detail["graph_context"] = {
                    "nodes": nodes if isinstance(nodes, list) else [],
                    "edges": edges if isinstance(edges, list) else [],
                    "entries": entries if isinstance(entries, list) else [],
                }
            except Exception as e:
                logger.debug(f"获取子图上下文失败 (memory_id={memory_id}): {e}")
                detail["graph_context"] = None
        else:
            detail["graph_context"] = None

        return self._ok(detail)
