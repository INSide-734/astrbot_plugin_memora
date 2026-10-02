"""Issue 88：图重建、运行门与历史 evidence 回归。"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.application.maintenance_operations import (
    MaintenanceOperations,
)
from core.features.memory.domain.graph_memory_config import (
    GraphMemoryConfig,
    graph_runtime_enabled,
)
from core.features.memory.graph.domain.models import (
    GraphBoundary,
    GraphEdge,
    GraphEntry,
    GraphNode,
    GraphQueryScope,
)
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.recall.processors.graph_extractor import GraphExtractor
from core.features.retrieval.retrieval_execution import RouteExecutionCoordinator
from tests.test_graph_multi_source_evidence import (
    FACT,
    SCOPE,
    TOPIC,
    _delete_source,
    _evidence_rows,
    _semantic_rows,
    _setup,
    _Vectors,
    _write_source,
)

# ---------------------------------------------------------------------------
# 在线 / 重建一致
# ---------------------------------------------------------------------------


async def _graph_plan(store: GraphStore) -> dict[str, Any]:
    async with store._connect() as db:
        nodes = await (
            await db.execute("SELECT node_key FROM graph_nodes ORDER BY node_key")
        ).fetchall()
        semantic = await (
            await db.execute(
                "SELECT semantic_key FROM graph_semantic_edges ORDER BY semantic_key"
            )
        ).fetchall()
        evidence = await (
            await db.execute(
                "SELECT source_memory_id, relation_type, evidence_kind, binding_key "
                "FROM graph_edges ORDER BY source_memory_id, binding_key"
            )
        ).fetchall()
    return {
        "nodes": [str(row[0]) for row in nodes],
        "semantic": [str(row[0]) for row in semantic],
        "evidence": [tuple(row) for row in evidence],
    }


def _rebuild_ops(store: GraphStore, manager: GraphMemoryManager) -> Any:
    async def get_documents(*, metadata_filters, limit, offset):  # noqa: ARG001
        async with store._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, text, metadata, created_at, updated_at FROM documents "
                "ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def count_documents(*, metadata_filters):  # noqa: ARG001
        async with store._connect() as db:
            row = await (await db.execute("SELECT COUNT(*) FROM documents")).fetchone()
            return int(row[0]) if row else 0

    faiss_db = MagicMock()
    faiss_db.document_storage = MagicMock()
    faiss_db.document_storage.count_documents = count_documents
    faiss_db.document_storage.get_documents = get_documents
    ops = MaintenanceOperations(
        config={},
        faiss_db=faiss_db,
        graph_memory_manager=manager,
        invalidate_cache_cb=MagicMock(),
    )
    ops._graph_store = store
    return ops


@pytest.mark.asyncio
async def test_online_and_rebuild_produce_equivalent_graph(tmp_db_path: str) -> None:
    calls: list[int] = []

    async def atom_loader(memory_id: int) -> list:
        calls.append(memory_id)
        return []

    store = GraphStore(tmp_db_path)
    await store.initialize()
    vectors = _Vectors()
    manager = GraphMemoryManager(
        store, cast(Any, vectors), GraphExtractor(), atom_loader=atom_loader
    )
    await _write_source(store, 1)
    await _write_source(store, 2)
    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})
    online = await _graph_plan(store)

    # 派生平面失效后全量重建。
    await manager.batch_delete_memories([1, 2])
    assert (await _graph_plan(store))["semantic"] == []

    result = await _rebuild_ops(store, manager).rebuild_graph_index()

    assert result["rebuilt"] == 2 and result["failed"] == 0
    assert await _graph_plan(store) == online
    # 重建复用 manager 的同一 Atom 加载器，而不是另走 legacy 语义。
    assert calls.count(1) == 2 and calls.count(2) == 2


@pytest.mark.asyncio
async def test_rebuild_reaps_deleted_and_ineligible_sources(tmp_db_path: str) -> None:
    manager, store, vectors = await _setup(tmp_db_path)
    for memory_id in (1, 2, 3):
        await _write_source(store, memory_id)
        await manager.index_memory(memory_id, "", {})
    semantic_id = (await _semantic_rows(store))[0][0]
    await _write_source(store, 2, revision="r2", memory_status="archived")
    await _delete_source(store, 3)

    connection = await aiosqlite.connect(tmp_db_path)
    try:
        ops = _rebuild_ops(store, manager)
        ops._db = connection
        result = await ops.rebuild_graph_index()
    finally:
        await connection.close()

    assert result["rebuilt"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert await _evidence_rows(store) == [(semantic_id, 1, "r1")]
    assert vectors.sources() == {1}


@pytest.mark.asyncio
async def test_rebuild_without_manager_reports_stable_skip() -> None:
    ops = MaintenanceOperations(config={})

    result = await ops.rebuild_graph_index()

    assert result["status"] == "skipped"
    assert result["reason_code"] == "graph_rebuild_unavailable"
    assert result["rebuilt"] == 0


# ---------------------------------------------------------------------------
# 图关闭 / 零权运行门
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "weight", "expected"),
    [
        (True, 0.35, True),
        (True, 0.0, False),
        (False, 0.35, False),
        (True, -0.1, False),
        (True, float("nan"), False),
        (True, "oops", False),
        (True, True, False),
    ],
)
def test_graph_runtime_gate(enabled: object, weight: object, expected: bool) -> None:
    assert graph_runtime_enabled(enabled, weight) is expected


def test_zero_graph_weight_keeps_existing_validator_contract() -> None:
    """配置模型不改变既有归一化契约：单独零权仍按用户值保留。"""
    config = GraphMemoryConfig(document_route_weight=1.0, graph_route_weight=0.0)

    assert config.graph_route_weight == 0.0
    assert graph_runtime_enabled(config.enabled, config.graph_route_weight) is False


def test_engine_zero_weight_keeps_atom_baseline_and_skips_graph(
    tmp_db_path: str,
) -> None:
    from core.features.memory.application.memory_engine import MemoryEngine

    enabled = MemoryEngine(
        db_path=tmp_db_path,
        faiss_db=MagicMock(),
        graph_vector_db=MagicMock(),
        config={"graph_memory_enabled": True, "graph_route_weight": 0.35},
    )
    zero = MemoryEngine(
        db_path=tmp_db_path,
        faiss_db=MagicMock(),
        graph_vector_db=MagicMock(),
        config={"graph_memory_enabled": True, "graph_route_weight": 0.0},
    )

    assert enabled.graph_enabled is True
    assert enabled.graph_runtime_enabled is True
    assert zero.graph_enabled is True
    assert zero.graph_runtime_enabled is False


@pytest.mark.asyncio
async def test_zero_weight_dual_route_never_calls_graph_retriever() -> None:
    from core.features.retrieval.dual_route_retriever import DualRouteRetriever
    from core.features.retrieval.rrf_fusion import HybridResult

    doc = HybridResult(
        doc_id=1,
        content="doc",
        metadata={},
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=0.9,
        vector_score=0.9,
    )
    document_retriever = MagicMock()
    document_retriever.search = AsyncMock(return_value=[doc])
    graph_retriever = MagicMock()
    graph_retriever.search = AsyncMock(return_value=[])
    retriever = DualRouteRetriever(
        document_retriever,
        graph_retriever,
        AsyncMock(return_value=None),
        config={"graph_route_weight": 0.0, "document_route_weight": 1.0},
    )

    results = await retriever.search(
        "query", k=3, query_scope=GraphQueryScope(SCOPE, "public")
    )

    graph_retriever.search.assert_not_awaited()
    assert [item.doc_id for item in results] == [1]


@pytest.mark.asyncio
async def test_route_coordinator_creates_no_graph_task_without_retriever() -> None:
    document_retriever = MagicMock()
    document_retriever.search = AsyncMock(return_value=[])
    coordinator = RouteExecutionCoordinator(document_retriever, None)

    outcome = await coordinator.execute("query", 3, use_graph_route=True)

    assert outcome.graph_results == []
    assert outcome.timing.get("graph_route_skipped") is True
    assert "graph" not in outcome.degraded_routes


@pytest.mark.asyncio
async def test_cancellation_during_index_propagates(tmp_db_path: str) -> None:
    store = GraphStore(tmp_db_path)
    await store.initialize()

    async def cancelled_loader(_memory_id: int) -> list:
        raise asyncio.CancelledError

    manager = GraphMemoryManager(
        store, cast(Any, _Vectors()), GraphExtractor(), atom_loader=cancelled_loader
    )
    await _write_source(store, 1)

    with pytest.raises(asyncio.CancelledError):
        await manager.index_memory(1, "", {})


@pytest.mark.asyncio
async def test_legacy_edge_entries_not_searchable_after_migration(
    tmp_db_path: str,
) -> None:
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundary = GraphBoundary(SCOPE, "public", "r1")
    nodes = await store.upsert_nodes(
        [
            GraphNode("topic", TOPIC, TOPIC),
            GraphNode("fact", FACT, FACT),
        ],
        boundary=boundary,
    )
    topic_id = nodes[f"topic:{TOPIC}"]
    await store.add_edge(
        GraphEdge(
            source_key=f"topic:{TOPIC}",
            target_key=f"fact:{FACT}",
            relation_type="describes",
            source_memory_id=9,
        ),
        nodes,
        boundary=boundary,
    )
    entries = await store.add_entries(
        [
            GraphEntry(
                entry_key="legacy-edge-entry",
                source_memory_id=9,
                session_id=None,
                persona_id=None,
                entry_type="edge",
                content=f"主题 {TOPIC} 描述了事实 {FACT}。",
                node_keys=[f"topic:{TOPIC}", f"fact:{FACT}"],
                relation_type="describes",
            )
        ],
        nodes,
        {},
        boundary=boundary,
    )
    assert entries
    async with store._connect() as db:
        await db.execute(
            "UPDATE graph_edges SET semantic_edge_id = NULL, evidence_kind = NULL, "
            "binding_key = NULL"
        )
        await db.commit()

    scope = GraphQueryScope(SCOPE, "public")
    assert await store.search_entries_by_bm25(FACT, 10, query_scope=scope) == []
    assert await store.get_entries_for_node_ids([topic_id], 10, query_scope=scope) == []
    assert await store.get_recent_memory_ids(10, query_scope=scope) == []
    snapshot = await store.get_canvas_snapshot(boundary=boundary)
    assert snapshot["edges"] == []
    subgraph = await store.get_subgraph_for_memories([9], boundary=boundary)
    assert subgraph["edges"] == []
