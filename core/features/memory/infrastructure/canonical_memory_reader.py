"""读取 canonical memory，并保留 SQLite 中的原始 revision 字符串。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from contextlib import suppress
from typing import Any


async def load_canonical_memory(
    faiss_db: Any,
    db_connection: Any,
    memory_id: int,
) -> dict[str, Any] | None:
    """按整数 ID 读取 canonical memory，并恢复数据库原始时间字段。"""

    docs = await faiss_db.document_storage.get_documents(
        metadata_filters={},
        ids=[memory_id],
        limit=1,
    )
    if not docs:
        return None

    doc = docs[0]
    result = {
        "id": doc["id"],
        "text": doc["text"],
        "metadata": doc["metadata"],
    }
    raw_timestamps = await _load_raw_timestamps(db_connection, memory_id)
    for field in ("created_at", "updated_at"):
        if field in raw_timestamps:
            result[field] = raw_timestamps[field]
        elif field in doc:
            result[field] = doc[field]
    return result


async def _load_raw_timestamps(
    db_connection: Any,
    memory_id: int,
) -> dict[str, Any]:
    """尽力读取 SQLite 原始时间值，旧后端不可用时返回空映射。"""

    if db_connection is None:
        return {}
    cursor = None
    try:
        cursor = await db_connection.execute(
            "SELECT created_at, updated_at FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
    except asyncio.CancelledError:
        raise
    except Exception:
        return {}
    finally:
        if cursor is not None:
            with suppress(Exception):
                await cursor.close()
    if row is None:
        return {}
    return {"created_at": row[0], "updated_at": row[1]}


def _normalize_metadata(raw: Any) -> dict[str, Any]:
    """把 metadata 规范化为字典；JSON 文本损坏时按空字典处理。"""

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


async def _load_raw_timestamps_batch(
    db_connection: Any,
    memory_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """按 ID 批量读取 SQLite 原始时间值，旧后端不可用时返回空映射。

    与 ``_load_raw_timestamps`` 同一口径（单次主键 ``IN`` 查询）：批量读取也
    必须保留 raw revision 表示，格式转换只发生在展示层。
    """

    if db_connection is None or not memory_ids:
        return {}
    placeholders = ",".join("?" for _ in memory_ids)
    cursor = None
    try:
        cursor = await db_connection.execute(
            "SELECT id, created_at, updated_at FROM documents "
            f"WHERE id IN ({placeholders})",
            memory_ids,
        )
        rows = await cursor.fetchall()
    except asyncio.CancelledError:
        raise
    except Exception:
        return {}
    finally:
        if cursor is not None:
            with suppress(Exception):
                await cursor.close()
    return {int(row[0]): {"created_at": row[1], "updated_at": row[2]} for row in rows}


async def load_canonical_memories(
    faiss_db: Any,
    memory_ids: Iterable[int],
    db_connection: Any = None,
) -> dict[int, dict[str, Any]]:
    """按整数 ID 批量读取 canonical memory，只做本地主键查询。

    返回 ``{id: {"id": int, "text": str, "metadata": dict, "created_at": Any,
    "updated_at": Any}}``；提供 ``db_connection`` 时时间字段优先取 SQLite 原始值
    （与 ``load_canonical_memory`` 同一口径，批量一次主键 ``IN`` 查询），
    存储后端未提供时回退文档自身字段。不存在或非法的 ID 不进入结果映射，由
    调用方按各自平面决定剔除或回落。读取异常不在此吞掉：无法确认 canonical
    当前状态时，调用方必须自己决定降级语义。
    """

    wanted = sorted(
        {
            item
            for item in memory_ids
            if isinstance(item, int) and not isinstance(item, bool)
        }
    )
    if not wanted:
        return {}
    docs = await faiss_db.document_storage.get_documents(
        metadata_filters={},
        ids=wanted,
        limit=len(wanted),
    )
    raw_timestamps = await _load_raw_timestamps_batch(db_connection, wanted)
    records: dict[int, dict[str, Any]] = {}
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        doc_id = doc.get("id")
        if not isinstance(doc_id, int) or isinstance(doc_id, bool):
            continue
        raw = raw_timestamps.get(int(doc_id), {})
        # 保留原始 revision 表示：调用方按 memory_revision() 与候选快照比对。
        records[int(doc_id)] = {
            "id": int(doc_id),
            "text": doc.get("text"),
            "metadata": _normalize_metadata(doc.get("metadata")),
            "created_at": raw.get("created_at", doc.get("created_at")),
            "updated_at": raw.get("updated_at", doc.get("updated_at")),
        }
    return records


__all__ = ["load_canonical_memory", "load_canonical_memories"]
