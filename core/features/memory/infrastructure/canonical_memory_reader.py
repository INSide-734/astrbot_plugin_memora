"""读取 canonical memory，并保留 SQLite 中的原始 revision 字符串。

读取端口（``faiss_db.document_storage``）解析失败时按主键回落原生 SQL 读取：
历史遗留的非 ISO 时间值不得让整批 canonical 读取失败，也不得把读取故障当成
「无行」。
"""

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
    """按整数 ID 读取 canonical memory，并恢复数据库原始时间字段。

    读取端口解析失败（例如行内是非 ISO 的历史 ``updated_at``）时按主键回落到
    原生 SQL 读取；两条路径都不可用时抛出，不把读取故障当成「无行」。
    """

    docs = await _load_documents(faiss_db, [memory_id], db_connection)
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


async def _load_documents(
    faiss_db: Any,
    memory_ids: list[int],
    db_connection: Any,
) -> list[dict[str, Any]]:
    """按主键读取 canonical 行；存储后端解析失败时回落原生 SQL 读取。

    存储后端（``faiss_db.document_storage``）按 ``datetime`` 解析
    ``created_at``/``updated_at``，历史遗留的非 ISO 值（旧写点写入的 Unix 秒）
    会让同一查询的**整批**读取抛错。此时按同一批主键用原生 SQL 读出
    ``id``/``text``/``metadata`` 与两个原始时间列，让调用方仍能看到 canonical
    行；原生路径也不可用时继续抛出（原始异常），由调用方按 fail-closed 处理——
    读取故障不得伪装成「无行」。
    """

    try:
        docs = await faiss_db.document_storage.get_documents(
            metadata_filters={},
            ids=memory_ids,
            limit=len(memory_ids),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        raw_docs = await _load_raw_documents(db_connection, memory_ids)
        if raw_docs is None:
            raise
        return raw_docs
    return list(docs or [])


async def _load_raw_documents(
    db_connection: Any,
    memory_ids: list[int],
) -> list[dict[str, Any]] | None:
    """按主键用原生 SQL 读取 canonical 行；连接缺失或读取失败时返回 ``None``。"""

    if db_connection is None or not memory_ids:
        return None
    placeholders = ",".join("?" for _ in memory_ids)
    cursor = None
    try:
        cursor = await db_connection.execute(
            "SELECT id, text, metadata, created_at, updated_at FROM documents "
            f"WHERE id IN ({placeholders})",
            memory_ids,
        )
        rows = await cursor.fetchall()
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    finally:
        if cursor is not None:
            with suppress(Exception):
                await cursor.close()
    return [
        {
            "id": int(row[0]),
            "text": row[1],
            "metadata": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }
        for row in rows
    ]


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
    存储后端未提供时回退文档自身字段。存储后端整批解析失败（行内是非 ISO 的
    历史时间值）时按同一批主键回落原生 SQL 读取，不因单行脏数据丢掉整批。
    不存在或非法的 ID 不进入结果映射，由调用方按各自平面决定剔除或回落。
    读取异常不在此吞掉：无法确认 canonical 当前状态时（含两条读取路径都不可用），
    调用方必须自己决定降级语义。
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
    docs = await _load_documents(faiss_db, wanted, db_connection)
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
