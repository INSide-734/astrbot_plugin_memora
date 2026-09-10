PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT,
    text TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE entity_hierarchy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child TEXT NOT NULL,
    parent TEXT NOT NULL,
    UNIQUE(child, parent)
);

CREATE INDEX idx_doc_metadata
ON documents(json_extract(metadata, '$.session_id'));
CREATE INDEX idx_doc_persona_metadata
ON documents(json_extract(metadata, '$.persona_id'));
CREATE INDEX idx_doc_importance_metadata
ON documents(json_extract(metadata, '$.importance'));
CREATE INDEX idx_doc_last_access_metadata
ON documents(json_extract(metadata, '$.last_access_time'));
CREATE INDEX idx_documents_doc_id ON documents(doc_id);
CREATE INDEX idx_hierarchy_child ON entity_hierarchy(child);
CREATE INDEX idx_hierarchy_parent ON entity_hierarchy(parent);

CREATE TABLE db_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    description TEXT,
    migrated_at TEXT NOT NULL,
    migration_duration_seconds REAL
);

CREATE TABLE migration_status (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE TABLE canonical_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    canonical_memory_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    CHECK (LENGTH(idempotency_key) > 0),
    CHECK (idempotency_key = TRIM(idempotency_key)),
    FOREIGN KEY (canonical_memory_id)
        REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE canonical_idempotency_conflicts (
    idempotency_key TEXT NOT NULL,
    owner_memory_id INTEGER NOT NULL,
    duplicate_memory_id INTEGER NOT NULL,
    resolution TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (idempotency_key, duplicate_memory_id),
    CHECK (LENGTH(idempotency_key) > 0),
    CHECK (owner_memory_id <> duplicate_memory_id),
    CHECK (resolution = 'preserved_non_owner')
);

CREATE TABLE memory_write_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type TEXT NOT NULL,
    memory_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    step TEXT NOT NULL DEFAULT 'started',
    payload TEXT DEFAULT '{}',
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX idx_memory_write_ops_status
ON memory_write_ops(status, updated_at);
CREATE INDEX idx_memory_write_ops_memory
ON memory_write_ops(memory_id, op_type);

CREATE TRIGGER documents_idempotency_insert
AFTER INSERT ON documents
WHEN json_valid(NEW.metadata)
 AND json_type(NEW.metadata, '$.idempotency_key') = 'text'
 AND LENGTH(TRIM(CAST(json_extract(
     NEW.metadata, '$.idempotency_key'
 ) AS TEXT))) > 0
BEGIN
    INSERT INTO canonical_idempotency_keys (
        idempotency_key,
        canonical_memory_id,
        created_at
    ) VALUES (
        TRIM(CAST(json_extract(
            NEW.metadata, '$.idempotency_key'
        ) AS TEXT)),
        NEW.id,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
END;

CREATE TRIGGER documents_idempotency_update
AFTER UPDATE OF metadata ON documents
WHEN COALESCE(
    CASE
        WHEN json_valid(OLD.metadata)
         AND json_type(OLD.metadata, '$.idempotency_key') = 'text'
        THEN TRIM(CAST(json_extract(
            OLD.metadata, '$.idempotency_key'
        ) AS TEXT))
        ELSE NULL
    END,
    ''
) <> COALESCE(
    CASE
        WHEN json_valid(NEW.metadata)
         AND json_type(NEW.metadata, '$.idempotency_key') = 'text'
        THEN TRIM(CAST(json_extract(
            NEW.metadata, '$.idempotency_key'
        ) AS TEXT))
        ELSE NULL
    END,
    ''
)
BEGIN
    DELETE FROM canonical_idempotency_keys
    WHERE canonical_memory_id = OLD.id;

    INSERT OR IGNORE INTO canonical_idempotency_keys (
        idempotency_key,
        canonical_memory_id,
        created_at
    )
    SELECT
        TRIM(CAST(json_extract(
            OLD.metadata, '$.idempotency_key'
        ) AS TEXT)),
        d.id,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    FROM documents AS d
    WHERE json_valid(OLD.metadata)
      AND json_type(OLD.metadata, '$.idempotency_key') = 'text'
      AND json_valid(d.metadata)
      AND json_type(d.metadata, '$.idempotency_key') = 'text'
      AND TRIM(CAST(json_extract(
          d.metadata, '$.idempotency_key'
      ) AS TEXT)) = TRIM(CAST(json_extract(
          OLD.metadata, '$.idempotency_key'
      ) AS TEXT))
    ORDER BY d.id ASC
    LIMIT 1;

    INSERT INTO canonical_idempotency_keys (
        idempotency_key,
        canonical_memory_id,
        created_at
    )
    SELECT
        TRIM(CAST(json_extract(
            NEW.metadata, '$.idempotency_key'
        ) AS TEXT)),
        NEW.id,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE json_valid(NEW.metadata)
      AND json_type(NEW.metadata, '$.idempotency_key') = 'text'
      AND LENGTH(TRIM(CAST(json_extract(
          NEW.metadata, '$.idempotency_key'
      ) AS TEXT))) > 0;
END;

CREATE TRIGGER documents_idempotency_delete
AFTER DELETE ON documents
BEGIN
    DELETE FROM canonical_idempotency_keys
    WHERE canonical_memory_id = OLD.id;

    INSERT OR IGNORE INTO canonical_idempotency_keys (
        idempotency_key,
        canonical_memory_id,
        created_at
    )
    SELECT
        TRIM(CAST(json_extract(
            OLD.metadata, '$.idempotency_key'
        ) AS TEXT)),
        d.id,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    FROM documents AS d
    WHERE json_valid(OLD.metadata)
      AND json_type(OLD.metadata, '$.idempotency_key') = 'text'
      AND json_valid(d.metadata)
      AND json_type(d.metadata, '$.idempotency_key') = 'text'
      AND TRIM(CAST(json_extract(
          d.metadata, '$.idempotency_key'
      ) AS TEXT)) = TRIM(CAST(json_extract(
          OLD.metadata, '$.idempotency_key'
      ) AS TEXT))
    ORDER BY d.id ASC
    LIMIT 1;
END;

INSERT INTO db_version (
    version,
    description,
    migrated_at,
    migration_duration_seconds
) VALUES (
    9,
    'refactor-rollback-v1',
    '2026-01-01T00:00:00+00:00',
    0.0
);

INSERT INTO documents (
    id,
    doc_id,
    text,
    metadata,
    created_at,
    updated_at
) VALUES
    (
        101,
        'fixture-memory-alpha',
        'anonymous canonical fixture alpha',
        '{"revision_token":"revision-alpha","scope_key":"fixture-shared","privacy_level":"shared","idempotency_key":"fixture-op-alpha"}',
        '2026-01-01T00:00:00+00:00',
        '2026-01-02T00:00:00+00:00'
    ),
    (
        202,
        'fixture-memory-beta',
        'anonymous canonical fixture beta',
        '{"revision_token":"revision-beta","scope_key":"fixture-shared","privacy_level":"shared","idempotency_key":"fixture-op-beta"}',
        '2026-01-03T00:00:00+00:00',
        '2026-01-04T00:00:00+00:00'
    );

COMMIT;
