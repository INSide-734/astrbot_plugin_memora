"""跨检索、管理与校验组件共享的静态 SQLite 契约。"""

from __future__ import annotations

from typing import Final

DOCUMENTS_TABLE: Final = "documents"
MEMORY_FTS_TABLE: Final = "memora_memories_fts"
SOCIAL_RELATIONS_TABLE: Final = "social_relations"

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
    "SOCIAL_RELATIONS_TABLE",
]
