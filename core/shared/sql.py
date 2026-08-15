"""跨 feature 共享的静态 SQLite 契约与性能 PRAGMA 原语。

本模块只存放无状态、不依赖具体 SQLite 驱动的表名、静态语句与 PRAGMA 设置；
连接由调用方传入并鸭子类型使用，禁止在本模块导入 ``aiosqlite``/``sqlite3``
或 AstrBot。
"""

from __future__ import annotations

from typing import Any, Final

# ---------------------------------------------------------------------------
# 表名与静态 FTS 契约
# ---------------------------------------------------------------------------

DOCUMENTS_TABLE: Final = "documents"
MEMORY_FTS_TABLE: Final = "memora_memories_fts"
SOCIAL_RELATIONS_TABLE: Final = "social_relations"

# canonical memory 生命周期状态按 memory_status → status → active 的兼容优先级解析。
# Python json.loads() 对重复对象键保留最后一个值；SQLite json_extract() 会返回第一个。
# 因此顶层字段通过 json_each() 的 JSON 顺序 ID 读取最后一个同名键，以避免
# 页面 SQL 筛选与 Python 领域判断产生不同状态。仅接受文本字段，并与
# effective_memory_status() 一样剔除 ASCII 空白、折叠为小写；旧版
# current/stable 统一归一为 active。状态字段存在但均不合法时返回 unknown，
# 因此读取方可以 fail-closed。所有 JSON 函数仅在 json_valid() 为真时执行。
MEMORY_STATUS_SQL: Final = """
    CASE WHEN json_valid(metadata) THEN
        COALESCE(
            CASE WHEN (
                SELECT type FROM json_each(metadata)
                WHERE key = 'memory_status' ORDER BY id DESC LIMIT 1
            ) = 'text' THEN
                CASE LOWER(TRIM(
                    (
                        SELECT value FROM json_each(metadata)
                        WHERE key = 'memory_status' ORDER BY id DESC LIMIT 1
                    ),
                    char(9) || char(10) || char(11) || char(12) || char(13) || ' '
                ))
                    WHEN 'current' THEN 'active'
                    WHEN 'stable' THEN 'active'
                    WHEN 'active' THEN 'active'
                    WHEN 'dormant' THEN 'dormant'
                    WHEN 'archived' THEN 'archived'
                    WHEN 'deleted' THEN 'deleted'
                END
            END,
            CASE WHEN (
                SELECT type FROM json_each(metadata)
                WHERE key = 'status' ORDER BY id DESC LIMIT 1
            ) = 'text' THEN
                CASE LOWER(TRIM(
                    (
                        SELECT value FROM json_each(metadata)
                        WHERE key = 'status' ORDER BY id DESC LIMIT 1
                    ),
                    char(9) || char(10) || char(11) || char(12) || char(13) || ' '
                ))
                    WHEN 'current' THEN 'active'
                    WHEN 'stable' THEN 'active'
                    WHEN 'active' THEN 'active'
                    WHEN 'dormant' THEN 'dormant'
                    WHEN 'archived' THEN 'archived'
                    WHEN 'deleted' THEN 'deleted'
                END
            END,
            CASE
                WHEN EXISTS(
                    SELECT 1 FROM json_each(metadata)
                    WHERE key IN ('memory_status', 'status')
                ) THEN 'unknown'
                ELSE 'active'
            END
        )
    ELSE 'active' END
"""

MEMORY_FTS_CREATE_SQL: Final = """
    CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts
    USING fts5(
        content,
        doc_id UNINDEXED,
        tokenize='unicode61'
    )
"""
MEMORY_FTS_SEARCH_SQL: Final = """
    SELECT doc_id, bm25(memora_memories_fts) AS score
    FROM memora_memories_fts
    WHERE memora_memories_fts MATCH :fts_query
    ORDER BY score ASC
    LIMIT :fetch_limit
"""
MEMORY_FTS_INSERT_SQL: Final = """
    INSERT INTO memora_memories_fts(doc_id, content)
    VALUES (:doc_id, :content)
"""
MEMORY_FTS_DELETE_BY_DOC_ID_SQL: Final = """
    DELETE FROM memora_memories_fts WHERE doc_id = :doc_id
"""
MEMORY_FTS_DELETE_BY_JSON_IDS_SQL: Final = """
    DELETE FROM memora_memories_fts
    WHERE doc_id IN (SELECT value FROM json_each(:memory_ids_json))
"""
MEMORY_FTS_CLEAR_SQL: Final = "DELETE FROM memora_memories_fts"
MEMORY_FTS_SELECT_DISTINCT_DOC_IDS_SQL: Final = (
    "SELECT DISTINCT doc_id FROM memora_memories_fts"
)
MEMORY_FTS_COUNT_DISTINCT_DOC_IDS_SQL: Final = (
    "SELECT COUNT(DISTINCT doc_id) FROM memora_memories_fts"
)
MEMORY_FTS_OPTIMIZE_SQL: Final = (
    "INSERT INTO memora_memories_fts(memora_memories_fts) VALUES ('optimize')"
)

# ---------------------------------------------------------------------------
# 共享 SQLite 性能 PRAGMA —— 单一事实来源
# ---------------------------------------------------------------------------

_PERF_PRAGMAS: Final = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 30000",
    "PRAGMA cache_size = -65536",  # 64 MB page cache
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 268435456",  # 256 MB memory-mapped I/O
)


async def apply_perf_pragmas(conn: Any) -> None:
    """把共享性能 PRAGMA 应用到传入的连接。

    参数:
        conn: 任意具备 ``execute(str)`` 异步方法的连接对象，不绑定具体驱动。
    """

    for statement in _PERF_PRAGMAS:
        await conn.execute(statement)


__all__ = [
    "DOCUMENTS_TABLE",
    "MEMORY_FTS_CLEAR_SQL",
    "MEMORY_FTS_COUNT_DISTINCT_DOC_IDS_SQL",
    "MEMORY_FTS_CREATE_SQL",
    "MEMORY_FTS_DELETE_BY_DOC_ID_SQL",
    "MEMORY_FTS_DELETE_BY_JSON_IDS_SQL",
    "MEMORY_FTS_INSERT_SQL",
    "MEMORY_FTS_OPTIMIZE_SQL",
    "MEMORY_FTS_SEARCH_SQL",
    "MEMORY_FTS_SELECT_DISTINCT_DOC_IDS_SQL",
    "MEMORY_FTS_TABLE",
    "MEMORY_STATUS_SQL",
    "SOCIAL_RELATIONS_TABLE",
    "apply_perf_pragmas",
]
