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
from .shared_helpers import CANONICAL_PRIVACY_LEVELS

# 列表项 metadata 只投影已经提升到顶层的展示标量：
# 原始 metadata、会话/人格身份、source mapping、scope/privacy/revision、
# gate_disposition 与 update_history 不进入列表响应（与详情 allowlist 一致）。
_LIST_METADATA_SAFE_FIELDS = (
    "memory_type",
    "canonical_summary",
    "importance",
)


def _project_list_metadata(metadata: Any) -> dict[str, Any]:
    """按白名单投影列表项 metadata，避免泄露身份与来源字段。"""

    if not isinstance(metadata, dict):
        return {}
    return {
        field: metadata[field]
        for field in _LIST_METADATA_SAFE_FIELDS
        if field in metadata
    }


_MAX_SCOPE_KEY_CHARS = 256
_ASCII_WHITESPACE_SQL = "char(9) || char(10) || char(11) || char(12) || char(13) || ' '"


# /memories 保留 canonical 直读 SQL（性能），但必须补齐与
# canonical_source_validation / 目录来源校验等价的门：非 summary_source_orphan、
# provenance 完整、scope/privacy 合法。取值统一走 json_extract()（与同文件既有的
# session_id / gate_disposition / memory_type 筛选同一做法），避免逐行 json_each()
# 全键扫描；整个门只做一次 json_valid()，损坏 metadata 的行按 fail-closed 排除而不
# 让整页查询报错。写入路径统一以 json.dumps(dict) 序列化 metadata，不会产生重复
# 顶层键，因此首值读取不与 Python json.loads() 的末值语义冲突。
# 状态筛选仍由请求显式控制，不在本门内限制来源生命周期状态。
_SCOPE_KEY_SQL = f"TRIM(json_extract(metadata, '$.scope_key'), {_ASCII_WHITESPACE_SQL})"
# canonical 契约：privacy_level 缺失或为 null 的历史行按 "shared" 回退
# （与 canonical_source_validation.load_canonical_source_states 同一口径）；
# 键存在但值越界或非文本仍按非法排除，请求端筛选也沿用同一回退值。
_PRIVACY_LEVEL_SQL = "COALESCE(json_extract(metadata, '$.privacy_level'), 'shared')"
_CANONICAL_LIST_GATE_SQL = (
    "CASE WHEN json_valid(metadata) THEN ("
    "json_extract(metadata, '$.summary_source_orphan') IS NOT 1 "
    "AND json_extract(metadata, '$.source_provenance_complete') IS 1 "
    "AND typeof(json_extract(metadata, '$.scope_key')) = 'text' "
    f"AND LENGTH({_SCOPE_KEY_SQL}) BETWEEN 1 AND {_MAX_SCOPE_KEY_CHARS} "
    f"AND {_PRIVACY_LEVEL_SQL} IN ('public', 'shared', 'confidential')"
    ") ELSE 0 END = 1"
)


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

        # 请求可按来源 scope/privacy 收窄；提供的取值必须与 canonical 行当前
        # 门禁值一致，否则该行不进入响应（fail-closed，不放宽到其它来源）。
        request_scope_key = str(query.get("scope_key", "")).strip() or None
        request_privacy_level = (
            str(query.get("privacy_level", "")).strip().lower() or None
        )
        if (
            request_scope_key is not None
            and len(request_scope_key) > _MAX_SCOPE_KEY_CHARS
        ):
            return self._error("scope_key 无效")
        if (
            request_privacy_level is not None
            and request_privacy_level not in CANONICAL_PRIVACY_LEVELS
        ):
            return self._error("privacy_level 无效")

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
            "scope_key": request_scope_key,
            "privacy_level": request_privacy_level,
        }

        # 请求筛选与 canonical 读取门分开拼装：列表查询 = 请求筛选 AND 门，
        # 被剔除计数 = 请求筛选 AND NOT 门，两者共用同一段请求筛选，保证
        # total / items / dropped_source_count 口径一致。
        filters_sql = (
            "("
            "    :session_id IS NULL "
            "    OR CASE WHEN json_valid(metadata) "
            "       THEN json_extract(metadata, '$.session_id') END = :session_id"
            ") "
            "AND ("
            "    :status IS NULL "
            f"    OR ({MEMORY_STATUS_SQL}) = :status"
            ") "
            "AND ("
            "    :include_mark_write = 1 "
            "    OR COALESCE("
            "        CASE WHEN json_valid(metadata) "
            "        THEN json_extract(metadata, '$.gate_disposition') END,"
            "        ''"
            "    ) <> 'mark_write'"
            ") "
            f"AND (:scope_key IS NULL OR CASE WHEN json_valid(metadata) "
            f"    THEN {_SCOPE_KEY_SQL} END = :scope_key) "
            f"AND (:privacy_level IS NULL OR CASE WHEN json_valid(metadata) "
            f"    THEN {_PRIVACY_LEVEL_SQL} END = :privacy_level) "
            "AND ("
            "    :keyword IS NULL "
            "    OR ("
            "        :keyword_is_digit = 1 "
            "        AND ("
            "            CAST(id AS TEXT) = :keyword "
            "            OR text LIKE :keyword_like COLLATE NOCASE"
            "        )"
            "    ) "
            "    OR ("
            "        :keyword_is_digit = 0 "
            "        AND ("
            "            text LIKE :keyword_like COLLATE NOCASE "
            "            OR COALESCE("
            "                CASE WHEN json_valid(metadata) "
            "                THEN json_extract(metadata, '$.memory_type') END,"
            "                ''"
            "            ) LIKE :keyword_like COLLATE NOCASE"
            "        )"
            "    )"
            ")"
        )
        # COUNT 与分页读共用同一段过滤（canonical 读取门 + 请求筛选），
        # 保证 total 与 items 口径一致。
        where_sql = f"WHERE {filters_sql} AND ({_CANONICAL_LIST_GATE_SQL})"
        # 列表门比召回门严（额外要求 provenance 完整与文本 scope_key），仍可召回的
        # active 行因此可能不出现在管理列表。这里按同一组请求筛选单独聚合被该门
        # 剔除的行数，只回传标量计数，不携带 scope/privacy/revision 取值或正文。
        dropped_where_sql = f"WHERE {filters_sql} AND NOT ({_CANONICAL_LIST_GATE_SQL})"

        try:
            async with aiosqlite.connect(db_path) as db:
                await apply_perf_pragmas(db)
                db.row_factory = aiosqlite.Row
                count_cursor = await db.execute(
                    f"SELECT COUNT(*) AS total FROM documents {where_sql}",
                    params,
                )
                count_row = await count_cursor.fetchone()
                total = int(count_row["total"]) if count_row else 0

                dropped_cursor = await db.execute(
                    f"SELECT COUNT(*) AS dropped FROM documents {dropped_where_sql}",
                    params,
                )
                dropped_row = await dropped_cursor.fetchone()
                dropped_source_count = int(dropped_row["dropped"]) if dropped_row else 0

                cursor = await db.execute(
                    f"SELECT id, doc_id, text, metadata, created_at, updated_at "
                    f"FROM documents {where_sql} "
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
                    "metadata": _project_list_metadata(metadata),
                    "created_at": row_created_at,
                    "updated_at": row_updated_at,
                }
            )

        return self._ok(
            {
                "items": items,
                "total": total,
                # 被列表 canonical 来源门剔除、但在同一组请求筛选下的行数（整数）。
                "dropped_source_count": dropped_source_count,
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
