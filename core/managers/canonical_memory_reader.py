"""读取 canonical memory，并保留 SQLite 中的原始 revision 字符串。"""

from __future__ import annotations

import asyncio
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
    try:
        cursor = await db_connection.execute(
            "SELECT created_at, updated_at FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    except asyncio.CancelledError:
        raise
    except Exception:
        return {}
    if row is None:
        return {}
    return {"created_at": row[0], "updated_at": row[1]}


__all__ = ["load_canonical_memory"]
