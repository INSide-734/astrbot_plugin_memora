"""回退门禁使用的完整 Schema 与真实派生重建证据。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast


def _ensure_repo_root_importable() -> None:
    """让直接执行 ``scripts/*.py`` 时可导入当前候选的 ``core``。"""

    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _canonical_json(value: Any) -> bytes:
    """编码不含运行机差异的稳定 JSON。"""

    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    """返回结构化证据的 SHA-256。"""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _name_set(connection: sqlite3.Connection, object_type: str) -> set[str]:
    """读取 SQLite schema 中指定对象类型的名称集合。"""

    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type=?",
            (object_type,),
        ).fetchall()
    }


def _probe_idempotency_triggers(database: Path) -> str:
    """在回滚事务内验证 insert/update/delete trigger 的映射行为。"""

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    probe_id = 2_000_000_001
    initial_key = "rollback-probe-initial"
    updated_key = "rollback-probe-updated"
    try:
        connection.execute("SAVEPOINT idempotency_probe")
        connection.execute(
            "INSERT INTO documents "
            "(id,doc_id,text,metadata,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (
                probe_id,
                "rollback-probe",
                "probe",
                json.dumps({"idempotency_key": initial_key}),
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        inserted = connection.execute(
            "SELECT canonical_memory_id FROM canonical_idempotency_keys "
            "WHERE idempotency_key=?",
            (initial_key,),
        ).fetchone()
        if inserted != (probe_id,):
            return "remaining"

        connection.execute(
            "UPDATE documents SET metadata=? WHERE id=?",
            (json.dumps({"idempotency_key": updated_key}), probe_id),
        )
        old_mapping = connection.execute(
            "SELECT 1 FROM canonical_idempotency_keys WHERE idempotency_key=?",
            (initial_key,),
        ).fetchone()
        updated = connection.execute(
            "SELECT canonical_memory_id FROM canonical_idempotency_keys "
            "WHERE idempotency_key=?",
            (updated_key,),
        ).fetchone()
        if old_mapping is not None or updated != (probe_id,):
            return "remaining"

        connection.execute("DELETE FROM documents WHERE id=?", (probe_id,))
        deleted = connection.execute(
            "SELECT 1 FROM canonical_idempotency_keys WHERE idempotency_key=?",
            (updated_key,),
        ).fetchone()
        return "closed" if deleted is None else "remaining"
    except sqlite3.Error:
        return "remaining"
    finally:
        try:
            connection.execute("ROLLBACK TO idempotency_probe")
            connection.execute("RELEASE idempotency_probe")
        except sqlite3.Error:
            connection.rollback()
        connection.close()


def inspect_schema_contract(
    database: Path,
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    """核验完整 v9 对象、映射内容与三类维护 trigger。"""

    contract = fixture.get("schema_contract")
    expected_mapping = fixture.get("idempotency_mapping")
    if not isinstance(contract, Mapping) or not isinstance(expected_mapping, Mapping):
        return {
            "status": "remaining",
            "reason_code": "fixture_schema_contract_missing",
        }
    required: dict[str, set[str]] = {}
    for key in ("tables", "indexes", "triggers"):
        values = contract.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return {
                "status": "remaining",
                "reason_code": "fixture_schema_contract_invalid",
            }
        if any(not isinstance(value, str) or not value for value in values):
            return {
                "status": "remaining",
                "reason_code": "fixture_schema_contract_invalid",
            }
        required[key] = set(values)

    try:
        uri = f"file:{database.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            actual = {
                "tables": _name_set(connection, "table"),
                "indexes": _name_set(connection, "index"),
                "triggers": _name_set(connection, "trigger"),
            }
            mapping_rows = connection.execute(
                "SELECT idempotency_key,canonical_memory_id "
                "FROM canonical_idempotency_keys ORDER BY idempotency_key"
            ).fetchall()
    except sqlite3.Error:
        return {
            "status": "remaining",
            "reason_code": "fixture_schema_contract_query_failed",
        }

    missing = {key: sorted(required[key] - actual[key]) for key in required}
    expected_rows = sorted(
        (str(key), int(value)) for key, value in expected_mapping.items()
    )
    mapping_valid = mapping_rows == expected_rows
    probe = _probe_idempotency_triggers(database)
    closed = not any(missing.values()) and mapping_valid and probe == "closed"
    return {
        "status": "closed" if closed else "remaining",
        "reason_code": "schema_contract_verified" if closed else "schema_contract_gap",
        "required_table_count": len(required["tables"]),
        "required_index_count": len(required["indexes"]),
        "required_trigger_count": len(required["triggers"]),
        "missing_table_count": len(missing["tables"]),
        "missing_index_count": len(missing["indexes"]),
        "missing_trigger_count": len(missing["triggers"]),
        "missing_object_hash": _hash(missing),
        "idempotency_mapping_count": len(mapping_rows),
        "idempotency_mapping_hash": _hash(mapping_rows),
        "idempotency_mapping_valid": mapping_valid,
        "idempotency_trigger_probe": probe,
        "contract_hash": _hash({key: sorted(value) for key, value in required.items()}),
    }


def _canonical_rows(database: Path) -> list[tuple[Any, ...]]:
    """读取派生重建所需的 canonical 行。"""

    uri = f"file:{database.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return connection.execute(
            "SELECT id,text,metadata,created_at,updated_at FROM documents ORDER BY id"
        ).fetchall()


def _stage_remaining(reason_code: str) -> dict[str, Any]:
    """构造不会被误判为通过的派生阶段结果。"""

    return {"status": "remaining", "reason_code": reason_code}


class _LiteralTextProcessor:
    """为生产 BM25 组件提供确定性、无外部资源的分词器。"""

    async def tokenize_async(
        self,
        text: str,
        *,
        remove_stopwords: bool = True,
    ) -> list[str]:
        """按空白分割匿名英文 fixture。"""

        del remove_stopwords
        return [token.casefold() for token in text.split() if token]


async def _rebuild_fts5(
    database: Path,
    rows: Sequence[tuple[Any, ...]],
    query: str,
) -> dict[str, Any]:
    """通过生产 BM25Retriever 创建、写入并查询真实 FTS5。"""

    try:
        _ensure_repo_root_importable()
        from core.features.recall.processors.text_processor import TextProcessor
        from core.features.retrieval.bm25_retriever import BM25Retriever

        text_processor = cast(TextProcessor, _LiteralTextProcessor())
        retriever = BM25Retriever(str(database), text_processor)
        await retriever.initialize()
        for row in rows:
            await retriever.add_document(int(row[0]), str(row[1]))
        results = await retriever.search(query, limit=max(1, len(rows)))
        with sqlite3.connect(database) as connection:
            indexed = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT doc_id) FROM memora_memories_fts"
                ).fetchone()[0]
            )
        if indexed != len(rows) or not results:
            return _stage_remaining("fts5_rebuild_incomplete")
        return {
            "status": "closed",
            "reason_code": "fts5_rebuild_verified",
            "implementation": "core.features.retrieval.BM25Retriever/sqlite_fts5",
            "indexed_count": indexed,
            "query_match_count": len(results),
            "matched_id_hash": _hash(sorted(result.doc_id for result in results)),
        }
    except (ImportError, OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        return {
            **_stage_remaining("fts5_rebuild_unavailable"),
            "error_type": type(exc).__name__,
        }


def _vector_for_text(text: str, dimension: int) -> Any:
    """从 canonical 正文哈希生成确定性归一化向量。"""

    import numpy as np

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = np.frombuffer(digest[:dimension], dtype=np.uint8).astype("float32")
    values -= 127.5
    norm = float(np.linalg.norm(values))
    return values / max(norm, 1.0)


def _rebuild_faiss(
    rows: Sequence[tuple[Any, ...]],
    index_path: Path,
) -> dict[str, Any]:
    """创建、持久化、重载并查询真实 FAISS IDMap 索引。"""

    try:
        import faiss
        import numpy as np

        dimension = 16
        vectors = np.stack(
            [_vector_for_text(str(row[1]), dimension) for row in rows]
        ).astype("float32")
        ids = np.asarray([int(row[0]) for row in rows], dtype="int64")
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
        index.add_with_ids(vectors, ids)
        faiss.write_index(index, str(index_path))
        restored = faiss.read_index(str(index_path))
        _scores, matched = restored.search(vectors[:1], max(1, len(rows)))
        matched_ids = [int(value) for value in matched[0] if int(value) >= 0]
        if int(restored.ntotal) != len(rows) or int(ids[0]) not in matched_ids:
            return _stage_remaining("faiss_rebuild_incomplete")
        return {
            "status": "closed",
            "reason_code": "faiss_rebuild_verified",
            "implementation": "faiss.IndexIDMap2/IndexFlatIP",
            "indexed_count": int(restored.ntotal),
            "query_match_count": len(matched_ids),
            "matched_id_hash": _hash(matched_ids),
            "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        }
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return {
            **_stage_remaining("faiss_rebuild_unavailable"),
            "error_type": type(exc).__name__,
        }


async def _rebuild_graph(
    database: Path,
    rows: Sequence[tuple[Any, ...]],
) -> dict[str, Any]:
    """使用生产 GraphStore 原子重建图节点、边、条目与图 FTS。"""

    try:
        _ensure_repo_root_importable()
        from core.features.memory.graph.domain.models import (
            GraphEdge,
            GraphEntry,
            GraphNode,
        )
        from core.features.memory.graph.infrastructure.graph_store import GraphStore

        source_id = int(rows[0][0])
        nodes = [
            GraphNode("fixture", "alpha", "alpha"),
            GraphNode("fixture", "beta", "beta"),
        ]
        edge = GraphEdge(
            nodes[0].node_key,
            nodes[1].node_key,
            "related_to",
            source_id,
        )
        entry = GraphEntry(
            "rollback-fixture-entry",
            source_id,
            "fixture-shared",
            None,
            "relation",
            "anonymous graph evidence",
            node_keys=[node.node_key for node in nodes],
            relation_type="related_to",
        )
        store = GraphStore(str(database))
        await store.initialize()
        result = await store.replace_memory_graph(source_id, nodes, [edge], [entry])
        stats = await store.get_memory_entry_stats()
        with sqlite3.connect(database) as connection:
            fts_matches = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memora_graph_entries_fts "
                    "WHERE memora_graph_entries_fts MATCH 'anonymous'"
                ).fetchone()[0]
            )
        if not result.entry_ids or fts_matches < 1:
            return _stage_remaining("graph_rebuild_incomplete")
        return {
            "status": "closed",
            "reason_code": "graph_rebuild_verified",
            "implementation": "core.features.memory.graph.GraphStore",
            "node_count": stats["graph_nodes"],
            "edge_count": stats["graph_edges"],
            "entry_count": stats["graph_entries"],
            "fts_query_match_count": fts_matches,
        }
    except (ImportError, OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        return {
            **_stage_remaining("graph_rebuild_unavailable"),
            "error_type": type(exc).__name__,
        }


async def _rebuild_evolution(
    database: Path,
    rows: Sequence[tuple[Any, ...]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """用生产 Store 写入并读取 relation 与 source-backed projection。"""

    store = None
    try:
        _ensure_repo_root_importable()
        from core.features.evolution.domain import (
            DerivedApplyPlan,
            ProjectionSourceView,
            ProjectionType,
            ProjectionView,
            RelationType,
            RelationView,
        )
        from core.features.evolution.infrastructure import MemoryEvolutionStore

        first, second = rows[0], rows[1]
        first_metadata = json.loads(str(first[2]))
        scope = str(first_metadata["scope_key"])
        privacy = str(first_metadata["privacy_level"])
        first_revision = str(first[4] or first[3])
        second_revision = str(second[4] or second[3])
        relation_id = "rollback-fixture-relation"
        projection_id = "rollback-fixture-projection"
        plan = DerivedApplyPlan(
            relations=(
                RelationView(
                    relation_id,
                    int(first[0]),
                    int(second[0]),
                    RelationType.SAME_EPISODE,
                    0.9,
                    scope,
                    privacy,
                    source_revision=first_revision,
                    target_revision=second_revision,
                ),
            ),
            projections=(
                ProjectionView(
                    projection_id,
                    ProjectionType.EPISODE_SUMMARY,
                    "anonymous derived projection",
                    (int(first[0]), int(second[0])),
                    scope,
                    privacy,
                    0.8,
                ),
            ),
            projection_sources=(
                ProjectionSourceView(
                    projection_id,
                    int(first[0]),
                    first_revision,
                    "primary",
                    0,
                ),
                ProjectionSourceView(
                    projection_id,
                    int(second[0]),
                    second_revision,
                    "supporting",
                    1,
                ),
            ),
        )
        store = MemoryEvolutionStore(str(database))
        await store.initialize()
        await store.apply_derived_plan(plan)
        relations = await store.active_relations_for_seeds([int(first[0])], scope)
        bundles = await store.active_projection_bundles_for_seeds(
            [int(second[0])],
            scope_key=scope,
        )
        relation = {
            "status": "closed" if len(relations) == 1 else "remaining",
            "reason_code": (
                "relation_rebuild_verified"
                if len(relations) == 1
                else "relation_rebuild_incomplete"
            ),
            "implementation": "core.features.evolution.MemoryEvolutionStore",
            "active_count": len(relations),
        }
        projection = {
            "status": "closed" if len(bundles) == 1 else "remaining",
            "reason_code": (
                "projection_rebuild_verified"
                if len(bundles) == 1
                else "projection_rebuild_incomplete"
            ),
            "implementation": "core.features.evolution.MemoryEvolutionStore",
            "active_count": len(bundles),
            "source_mapping_count": len(bundles[0].sources) if bundles else 0,
        }
        return relation, projection
    except (
        ImportError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        return (
            {
                **_stage_remaining("relation_rebuild_unavailable"),
                "error_type": type(exc).__name__,
            },
            {
                **_stage_remaining("projection_rebuild_unavailable"),
                "error_type": type(exc).__name__,
            },
        )
    finally:
        if store is not None:
            await store.close()


async def _build_derived_rebuild_evidence(
    source_database: Path,
    scratch_root: Path,
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    """在 canonical 快照副本上按依赖顺序重建全部派生存储。"""

    scratch_root.mkdir(parents=True, exist_ok=False)
    derived_database = scratch_root / "derived-evidence.db"
    with (
        sqlite3.connect(source_database) as source,
        sqlite3.connect(derived_database) as target,
    ):
        source.backup(target)
    rows = _canonical_rows(derived_database)
    if len(rows) < 2:
        return {
            "status": "remaining",
            "reason_code": "canonical_fixture_insufficient",
            "stages": {
                name: _stage_remaining("canonical_fixture_insufficient")
                for name in ("fts5", "faiss", "graph", "relation", "projection")
            },
        }
    canonical_before = _hash(rows)
    query = str((fixture.get("derived_queries") or {}).get("fts5", "anonymous")).strip()
    stages = {
        "fts5": await _rebuild_fts5(derived_database, rows, query),
        "faiss": _rebuild_faiss(rows, scratch_root / "derived.faiss"),
        "graph": await _rebuild_graph(derived_database, rows),
    }
    relation, projection = await _rebuild_evolution(derived_database, rows)
    stages["relation"] = relation
    stages["projection"] = projection
    canonical_preserved = _hash(_canonical_rows(derived_database)) == canonical_before
    closed = canonical_preserved and all(
        stage.get("status") == "closed" for stage in stages.values()
    )
    summary = {
        "status": "closed" if closed else "remaining",
        "reason_code": (
            "derived_rebuild_verified" if closed else "derived_rebuild_remaining"
        ),
        "canonical_preserved": canonical_preserved,
        "canonical_count": len(rows),
        "stages": stages,
    }
    summary["evidence_hash"] = _hash(summary)
    return summary


def build_derived_rebuild_evidence(
    source_database: Path,
    scratch_root: Path,
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    """同步入口；命令行 harness 不在事件循环内运行。"""

    return asyncio.run(
        _build_derived_rebuild_evidence(source_database, scratch_root, fixture)
    )


__all__ = ["build_derived_rebuild_evidence", "inspect_schema_contract"]
