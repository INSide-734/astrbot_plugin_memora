"""Canonical memory 幂等键的 SQLite 唯一映射与迁移辅助。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite


CANONICAL_IDEMPOTENCY_TABLE = "canonical_idempotency_keys"
CANONICAL_IDEMPOTENCY_CONFLICT_TABLE = "canonical_idempotency_conflicts"

DOCUMENTS_IDEMPOTENCY_INSERT_TRIGGER = "documents_idempotency_insert"
DOCUMENTS_IDEMPOTENCY_UPDATE_TRIGGER = "documents_idempotency_update"
DOCUMENTS_IDEMPOTENCY_DELETE_TRIGGER = "documents_idempotency_delete"

REQUIRED_CANONICAL_IDEMPOTENCY_TABLES = frozenset(
    {
        CANONICAL_IDEMPOTENCY_TABLE,
        CANONICAL_IDEMPOTENCY_CONFLICT_TABLE,
    }
)
REQUIRED_CANONICAL_IDEMPOTENCY_TRIGGERS = frozenset(
    {
        DOCUMENTS_IDEMPOTENCY_INSERT_TRIGGER,
        DOCUMENTS_IDEMPOTENCY_UPDATE_TRIGGER,
        DOCUMENTS_IDEMPOTENCY_DELETE_TRIGGER,
    }
)

_PYTHON_STRIP_WHITESPACE_CODEPOINTS = (
    9,
    10,
    11,
    12,
    13,
    28,
    29,
    30,
    31,
    32,
    133,
    160,
    5760,
    8192,
    8193,
    8194,
    8195,
    8196,
    8197,
    8198,
    8199,
    8200,
    8201,
    8202,
    8232,
    8233,
    8239,
    8287,
    12288,
)
_PYTHON_STRIP_WHITESPACE_SQL = (
    "char(" + ", ".join(map(str, _PYTHON_STRIP_WHITESPACE_CODEPOINTS)) + ")"
)
_ALLOWED_METADATA_EXPRESSIONS = frozenset(
    {"metadata", "NEW.metadata", "OLD.metadata", "d.metadata"}
)
_PRESERVED_NON_OWNER = "preserved_non_owner"
_SQLITE_TIMESTAMP_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def normalize_canonical_idempotency_key(value: Any) -> str:
    """复用 MemoryEngine 的非空字符串规范化语义。"""

    return str(value or "").strip()


async def find_canonical_memory_id_by_idempotency_key(
    connection: aiosqlite.Connection,
    key: Any,
) -> int | None:
    """从唯一映射读取 canonical ID，并拒绝孤立映射。"""

    normalized_key = normalize_canonical_idempotency_key(key)
    if not normalized_key:
        return None
    cursor = await connection.execute(
        f"""
        SELECT mapping.canonical_memory_id, documents.id
        FROM {CANONICAL_IDEMPOTENCY_TABLE} AS mapping
        LEFT JOIN documents
          ON documents.id = mapping.canonical_memory_id
        WHERE mapping.idempotency_key = ?
        """,
        (normalized_key,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    if row[1] is None:
        raise RuntimeError("canonical_idempotency_mapping_invalid")
    return int(row[0])


async def create_canonical_idempotency_schema(
    connection: aiosqlite.Connection,
) -> None:
    """创建映射、迁移冲突审计与 documents 维护 trigger。"""

    await connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CANONICAL_IDEMPOTENCY_TABLE} (
            idempotency_key TEXT PRIMARY KEY,
            canonical_memory_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            CHECK (LENGTH(idempotency_key) > 0),
            CHECK (
                idempotency_key = TRIM(
                    idempotency_key,
                    {_PYTHON_STRIP_WHITESPACE_SQL}
                )
            ),
            FOREIGN KEY (canonical_memory_id)
                REFERENCES documents(id) ON DELETE CASCADE
        )
        """
    )
    await connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CANONICAL_IDEMPOTENCY_CONFLICT_TABLE} (
            idempotency_key TEXT NOT NULL,
            owner_memory_id INTEGER NOT NULL,
            duplicate_memory_id INTEGER NOT NULL,
            resolution TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (idempotency_key, duplicate_memory_id),
            CHECK (LENGTH(idempotency_key) > 0),
            CHECK (owner_memory_id <> duplicate_memory_id),
            CHECK (resolution = '{_PRESERVED_NON_OWNER}')
        )
        """
    )
    await _replace_idempotency_triggers(connection)


async def rebuild_canonical_idempotency_mapping(
    connection: aiosqlite.Connection,
) -> None:
    """以最小 canonical ID 为 owner 重建映射并记录保留的重复行。"""

    metadata_key = _normalized_key_sql("metadata")
    await connection.execute(f"DELETE FROM {CANONICAL_IDEMPOTENCY_TABLE}")
    await connection.execute(
        f"""
        WITH keyed AS (
            SELECT id AS memory_id, {metadata_key} AS idempotency_key
            FROM documents
        ), usable AS (
            SELECT memory_id, idempotency_key
            FROM keyed
            WHERE idempotency_key IS NOT NULL
              AND LENGTH(idempotency_key) > 0
        )
        INSERT INTO {CANONICAL_IDEMPOTENCY_TABLE} (
            idempotency_key,
            canonical_memory_id,
            created_at
        )
        SELECT
            idempotency_key,
            MIN(memory_id),
            {_SQLITE_TIMESTAMP_SQL}
        FROM usable
        GROUP BY idempotency_key
        """
    )
    await connection.execute(
        f"""
        WITH keyed AS (
            SELECT id AS memory_id, {metadata_key} AS idempotency_key
            FROM documents
        ), usable AS (
            SELECT memory_id, idempotency_key
            FROM keyed
            WHERE idempotency_key IS NOT NULL
              AND LENGTH(idempotency_key) > 0
        ), owners AS (
            SELECT idempotency_key, MIN(memory_id) AS owner_memory_id
            FROM usable
            GROUP BY idempotency_key
        )
        INSERT OR REPLACE INTO {CANONICAL_IDEMPOTENCY_CONFLICT_TABLE} (
            idempotency_key,
            owner_memory_id,
            duplicate_memory_id,
            resolution,
            recorded_at
        )
        SELECT
            usable.idempotency_key,
            owners.owner_memory_id,
            usable.memory_id,
            '{_PRESERVED_NON_OWNER}',
            {_SQLITE_TIMESTAMP_SQL}
        FROM usable
        INNER JOIN owners
          ON owners.idempotency_key = usable.idempotency_key
        WHERE usable.memory_id <> owners.owner_memory_id
        """
    )


async def validate_canonical_idempotency_mapping(
    connection: aiosqlite.Connection,
) -> bool:
    """验证每个规范化 key 唯一映射到当前最小 canonical ID。"""

    document_key = _normalized_key_sql("d.metadata")
    cursor = await connection.execute(
        f"""
        SELECT 1
        FROM {CANONICAL_IDEMPOTENCY_TABLE} AS mapping
        LEFT JOIN documents AS d
          ON d.id = mapping.canonical_memory_id
        WHERE d.id IS NULL
           OR mapping.idempotency_key <> {document_key}
        LIMIT 1
        """
    )
    invalid_mapping = await cursor.fetchone()
    await cursor.close()
    if invalid_mapping is not None:
        return False

    metadata_key = _normalized_key_sql("metadata")
    cursor = await connection.execute(
        f"""
        WITH keyed AS (
            SELECT id AS memory_id, {metadata_key} AS idempotency_key
            FROM documents
        ), owners AS (
            SELECT idempotency_key, MIN(memory_id) AS owner_memory_id
            FROM keyed
            WHERE idempotency_key IS NOT NULL
              AND LENGTH(idempotency_key) > 0
            GROUP BY idempotency_key
        )
        SELECT 1
        FROM owners
        LEFT JOIN {CANONICAL_IDEMPOTENCY_TABLE} AS mapping
          ON mapping.idempotency_key = owners.idempotency_key
        WHERE mapping.canonical_memory_id IS NULL
           OR mapping.canonical_memory_id <> owners.owner_memory_id
        LIMIT 1
        """
    )
    missing_or_wrong_owner = await cursor.fetchone()
    await cursor.close()
    return missing_or_wrong_owner is None


async def count_current_canonical_idempotency_conflicts(
    connection: aiosqlite.Connection,
) -> int:
    """统计当前仍保留的非 owner 重复 canonical 行数。"""

    metadata_key = _normalized_key_sql("metadata")
    cursor = await connection.execute(
        f"""
        WITH keyed AS (
            SELECT id AS memory_id, {metadata_key} AS idempotency_key
            FROM documents
        ), usable AS (
            SELECT memory_id, idempotency_key
            FROM keyed
            WHERE idempotency_key IS NOT NULL
              AND LENGTH(idempotency_key) > 0
        ), owners AS (
            SELECT idempotency_key, MIN(memory_id) AS owner_memory_id
            FROM usable
            GROUP BY idempotency_key
        )
        SELECT COUNT(*)
        FROM usable
        INNER JOIN owners
          ON owners.idempotency_key = usable.idempotency_key
        WHERE usable.memory_id <> owners.owner_memory_id
        """
    )
    row = await cursor.fetchone()
    await cursor.close()
    return int(row[0]) if row is not None else 0


async def _replace_idempotency_triggers(
    connection: aiosqlite.Connection,
) -> None:
    """只重建固定 allowlist 内的 canonical idempotency trigger。"""

    for trigger_name in sorted(REQUIRED_CANONICAL_IDEMPOTENCY_TRIGGERS):
        await connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')

    new_key = _normalized_key_sql("NEW.metadata")
    old_key = _normalized_key_sql("OLD.metadata")
    document_key = _normalized_key_sql("d.metadata")
    await connection.execute(
        f"""
        CREATE TRIGGER {DOCUMENTS_IDEMPOTENCY_INSERT_TRIGGER}
        AFTER INSERT ON documents
        WHEN {new_key} IS NOT NULL AND LENGTH({new_key}) > 0
        BEGIN
            INSERT INTO {CANONICAL_IDEMPOTENCY_TABLE} (
                idempotency_key,
                canonical_memory_id,
                created_at
            ) VALUES (
                {new_key},
                NEW.id,
                {_SQLITE_TIMESTAMP_SQL}
            );
        END
        """
    )
    await connection.execute(
        f"""
        CREATE TRIGGER {DOCUMENTS_IDEMPOTENCY_UPDATE_TRIGGER}
        AFTER UPDATE OF metadata ON documents
        WHEN COALESCE({old_key}, '') <> COALESCE({new_key}, '')
        BEGIN
            DELETE FROM {CANONICAL_IDEMPOTENCY_TABLE}
            WHERE canonical_memory_id = OLD.id;

            INSERT OR IGNORE INTO {CANONICAL_IDEMPOTENCY_TABLE} (
                idempotency_key,
                canonical_memory_id,
                created_at
            )
            SELECT
                {old_key},
                d.id,
                {_SQLITE_TIMESTAMP_SQL}
            FROM documents AS d
            WHERE {old_key} IS NOT NULL
              AND LENGTH({old_key}) > 0
              AND {document_key} = {old_key}
            ORDER BY d.id ASC
            LIMIT 1;

            INSERT INTO {CANONICAL_IDEMPOTENCY_TABLE} (
                idempotency_key,
                canonical_memory_id,
                created_at
            )
            SELECT
                {new_key},
                NEW.id,
                {_SQLITE_TIMESTAMP_SQL}
            WHERE {new_key} IS NOT NULL
              AND LENGTH({new_key}) > 0;
        END
        """
    )
    await connection.execute(
        f"""
        CREATE TRIGGER {DOCUMENTS_IDEMPOTENCY_DELETE_TRIGGER}
        AFTER DELETE ON documents
        BEGIN
            DELETE FROM {CANONICAL_IDEMPOTENCY_TABLE}
            WHERE canonical_memory_id = OLD.id;

            INSERT OR IGNORE INTO {CANONICAL_IDEMPOTENCY_TABLE} (
                idempotency_key,
                canonical_memory_id,
                created_at
            )
            SELECT
                {old_key},
                d.id,
                {_SQLITE_TIMESTAMP_SQL}
            FROM documents AS d
            WHERE {old_key} IS NOT NULL
              AND LENGTH({old_key}) > 0
              AND {document_key} = {old_key}
            ORDER BY d.id ASC
            LIMIT 1;
        END
        """
    )


def _normalized_key_sql(metadata_expression: str) -> str:
    """为固定 metadata 表达式构造 fail-closed 规范化 SQL。"""

    if metadata_expression not in _ALLOWED_METADATA_EXPRESSIONS:
        raise ValueError("unsupported canonical metadata expression")
    return f"""
        CASE
            WHEN json_valid({metadata_expression}) THEN
                CASE
                    WHEN json_type(
                        {metadata_expression},
                        '$.idempotency_key'
                    ) = 'text' THEN
                        TRIM(
                            CAST(
                                json_extract(
                                    {metadata_expression},
                                    '$.idempotency_key'
                                ) AS TEXT
                            ),
                            {_PYTHON_STRIP_WHITESPACE_SQL}
                        )
                    ELSE NULL
                END
            ELSE NULL
        END
    """.strip()


__all__ = [
    "CANONICAL_IDEMPOTENCY_CONFLICT_TABLE",
    "CANONICAL_IDEMPOTENCY_TABLE",
    "DOCUMENTS_IDEMPOTENCY_DELETE_TRIGGER",
    "DOCUMENTS_IDEMPOTENCY_INSERT_TRIGGER",
    "DOCUMENTS_IDEMPOTENCY_UPDATE_TRIGGER",
    "REQUIRED_CANONICAL_IDEMPOTENCY_TABLES",
    "REQUIRED_CANONICAL_IDEMPOTENCY_TRIGGERS",
    "count_current_canonical_idempotency_conflicts",
    "create_canonical_idempotency_schema",
    "find_canonical_memory_id_by_idempotency_key",
    "normalize_canonical_idempotency_key",
    "rebuild_canonical_idempotency_mapping",
    "validate_canonical_idempotency_mapping",
]
