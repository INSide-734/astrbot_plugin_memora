SELECT
    id,
    doc_id,
    json_extract(metadata, '$.revision_token') AS revision_token
FROM documents
ORDER BY id;
