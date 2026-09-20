"""Graph manager canonical-source gates and derived mutation behavior."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.application.maintenance_operations import (
    MaintenanceOperations,
)
from core.features.memory.graph.domain.models import GraphBoundary
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.recall.processors.graph_extractor import GraphExtractor
from tests.fact_evidence_helpers import fact_evidence, source_evidence


class _Vectors:
    """In-memory vector records expose source cleanup and interrupted writes."""

    def __init__(self) -> None:
        self.records: dict[int, tuple[str, dict[str, Any]]] = {}
        self.next_id = 1
        self.fail_at: int | None = None
        self.started: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def add_entry(
        self,
        content: str,
        metadata: dict[str, Any],
        *,
        boundary: GraphBoundary,
    ) -> int:
        boundary.validate_metadata(metadata)
        if self.started is not None and self.release is not None:
            self.started.set()
            await self.release.wait()
        vector_id = self.next_id
        self.next_id += 1
        if vector_id == self.fail_at:
            raise RuntimeError("vector_write_failed")
        self.records[vector_id] = (
            content,
            {**metadata, **boundary.as_params()},
        )
        return vector_id

    async def reap_entries_for_memory(self, memory_id: int) -> int:
        deleted = [
            key
            for key, (_, metadata) in self.records.items()
            if metadata.get("source_memory_id") == memory_id
        ]
        for key in deleted:
            del self.records[key]
        return len(deleted)


async def _source(
    store: GraphStore,
    memory_id: int = 1,
    *,
    scope: str = "scope-a",
    revision: str = "r1",
    overrides: dict[str, Any] | None = None,
) -> GraphBoundary:
    facts = ["Alice likes coffee", "Alice likes tea"]
    metadata = {
        "scope_key": scope,
        "privacy_level": "public",
        "key_facts": facts,
        "fact_source_evidence": fact_evidence(facts),
        "source_evidence": source_evidence("; ".join(facts)),
        "canonical_summary": "; ".join(facts),
        "topics": ["drink"],
        "participants": ["Alice"],
        **(overrides or {}),
    }
    async with store._connect() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, text TEXT, "
            "metadata TEXT, created_at TEXT, updated_at TEXT)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
            (
                memory_id,
                metadata["canonical_summary"],
                json.dumps(metadata),
                revision,
                revision,
            ),
        )
        await db.commit()
    return GraphBoundary.from_metadata({**metadata, "revision_token": revision})


async def _manager(tmp_db_path):
    store = GraphStore(tmp_db_path)
    await store.initialize()
    vectors = _Vectors()
    return (
        GraphMemoryManager(store, cast(Any, vectors), GraphExtractor()),
        store,
        vectors,
    )


@pytest.mark.asyncio
async def test_index_uses_current_canonical_content_not_caller_copy(tmp_db_path):
    manager, store, vectors = await _manager(tmp_db_path)
    boundary = await _source(store)

    await manager.index_memory(
        1, "Untrusted caller claim", {"key_facts": ["Untrusted caller claim"]}
    )

    snapshot = await store.get_graph_snapshot(full=True, boundary=boundary)
    assert {node["label"] for node in snapshot["nodes"] if node["type"] == "fact"} == {
        "Alice likes coffee",
        "Alice likes tea",
    }
    assert all(
        "Untrusted caller claim" not in content
        for content, _ in vectors.records.values()
    )
    assert await store.get_recent_memory_ids(boundary=boundary) == [1]


@pytest.mark.asyncio
async def test_stale_boundary_cannot_replace_current_graph(tmp_db_path):
    manager, store, vectors = await _manager(tmp_db_path)
    boundary = await _source(store)
    await manager.index_memory(1, "", {})
    previous = dict(vectors.records)
    await _source(store, revision="r2")

    with pytest.raises(ValueError, match="graph_boundary_mismatch"):
        await manager.index_memory(1, "", boundary.as_params())

    assert vectors.records == previous
    assert await store.get_recent_memory_ids(boundary=boundary) == [1]
    assert (
        await store.get_recent_memory_ids(
            boundary=GraphBoundary("scope-a", "public", "r2")
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"memory_status": "archived"}, "graph_source_not_recallable"),
        ({"gate_disposition": "mark_write"}, "graph_source_not_recallable"),
        ({"fact_source_evidence": []}, "graph_source_evidence_required"),
        (
            {
                "fact_source_evidence": [
                    source_evidence(role="assistant"),
                    source_evidence(),
                ]
            },
            "graph_source_evidence_required",
        ),
    ],
)
async def test_untrusted_canonical_sources_never_reach_graph(
    tmp_db_path, overrides, reason
):
    manager, store, vectors = await _manager(tmp_db_path)
    boundary = await _source(store, overrides=overrides)

    with pytest.raises(ValueError, match=reason):
        await manager.index_memory(1, "", {})

    assert await store.get_graph_snapshot(full=True, boundary=boundary) == {
        "nodes": [],
        "edges": [],
        "entries": [],
        "memories": [],
    }
    assert vectors.records == {}
    async with store._connect() as db:
        row = await (await db.execute("SELECT id FROM documents")).fetchone()
    assert row == (1,)


@pytest.mark.asyncio
async def test_index_retry_removes_partial_vectors_and_rebuilds_same_source(
    tmp_db_path,
):
    manager, store, vectors = await _manager(tmp_db_path)
    boundary = await _source(store)
    vectors.fail_at = 2
    with pytest.raises(RuntimeError, match="vector_write_failed"):
        await manager.index_memory(1, "", {})
    assert set(vectors.records) == {1}

    await manager.index_memory(1, "", {})

    assert 1 not in vectors.records
    snapshot = await store.get_graph_snapshot(full=True, boundary=boundary)
    assert {content for content, _ in vectors.records.values()} == {
        entry["content"] for entry in snapshot["entries"]
    }
    assert await store.get_recent_memory_ids(boundary=boundary) == [1]


@pytest.mark.asyncio
async def test_delete_waits_for_inflight_vector_sync(tmp_db_path):
    manager, store, vectors = await _manager(tmp_db_path)
    boundary = await _source(store)
    vectors.started = asyncio.Event()
    vectors.release = asyncio.Event()
    index_task = asyncio.create_task(manager.index_memory(1, "", {}))
    try:
        await asyncio.wait_for(vectors.started.wait(), timeout=5)
        delete_task = asyncio.create_task(manager.delete_memory(1))
        await asyncio.sleep(0)
        assert not delete_task.done()
        vectors.release.set()
        await asyncio.gather(index_task, delete_task)
    finally:
        vectors.release.set()
        if not index_task.done():
            index_task.cancel()
            await asyncio.gather(index_task, return_exceptions=True)

    assert vectors.records == {}
    assert await store.get_recent_memory_ids(boundary=boundary) == []


@pytest.mark.asyncio
async def test_batch_delete_reaps_every_source_variant(tmp_db_path):
    manager, store, vectors = await _manager(tmp_db_path)
    old = await _source(store)
    await manager.index_memory(1, "", {})
    newer = await _source(store, revision="r2")
    await manager.index_memory(1, "", {})
    other = await _source(store, 2, scope="scope-b")
    await manager.index_memory(2, "", {})
    other_source_record = ("other", {"source_memory_id": 2})
    vectors.records[99] = other_source_record

    await manager.batch_delete_memories([1, 1])

    assert await store.get_recent_memory_ids(boundary=old) == []
    assert await store.get_recent_memory_ids(boundary=newer) == []
    assert await store.get_recent_memory_ids(boundary=other) == [2]
    assert vectors.records[99] == other_source_record
    assert {
        metadata["source_memory_id"] for _, metadata in vectors.records.values()
    } == {2}


@pytest.mark.asyncio
async def test_delete_reaps_legacy_vectors_without_boundary_proof(tmp_db_path):
    manager, _, vectors = await _manager(tmp_db_path)
    vectors.records[4] = ("legacy", {"source_memory_id": 1})
    vectors.records[5] = ("retained", {"source_memory_id": 2})

    await manager.delete_memory(1)

    assert vectors.records == {5: ("retained", {"source_memory_id": 2})}


def _atom_for(fact: str, entity: str, *, revision: str = "r1") -> SimpleNamespace:
    """构造与 canonical 边界一致、带用户来源证据的测试原子。"""

    return SimpleNamespace(
        content=fact,
        confidence=0.7,
        session_id="session-a",
        persona_id="persona-a",
        entities=[entity],
        atom_type="preference",
        importance=0.6,
        ttl_days=12.0,
        created_at=1_000.0,
        event_time=1_100.0,
        expires_at=9_000_000_000.0,
        decay_type=SimpleNamespace(value="exponential"),
        parent_memory_id=1,
        parent_revision=revision,
        parent_scope_key="scope-a",
        parent_privacy_level="public",
        source_evidence=source_evidence(fact),
    )


async def _manager_with_atoms(tmp_db_path, loader):
    store = GraphStore(tmp_db_path)
    await store.initialize()
    vectors = _Vectors()
    manager = GraphMemoryManager(
        store,
        cast(Any, vectors),
        GraphExtractor(),
        atom_loader=loader,
    )
    return manager, store, vectors


@pytest.mark.asyncio
async def test_missing_atoms_are_loaded_so_representation_stays_atom_based(
    tmp_db_path,
):
    """未显式传原子的重建必须经注入加载器保持原子表示（R4-1 回归）。"""

    async def loader(memory_id: int):
        assert memory_id == 1
        return [
            _atom_for("Alice likes coffee", "Alice"),
            _atom_for("Alice likes tea", "Alice"),
        ]

    manager, store, _ = await _manager_with_atoms(tmp_db_path, loader)
    boundary = await _source(store)

    await manager.index_memory(1, "", {})

    snapshot = await store.get_graph_snapshot(full=True, boundary=boundary)
    contents = [entry["content"] for entry in snapshot["entries"]]
    assert any(content.startswith("记忆原子：") for content in contents)
    assert not any(content.startswith("事实：") for content in contents)
    fact_entries = [
        entry for entry in snapshot["entries"] if entry["entry_type"] == "fact"
    ]
    assert fact_entries
    assert all(
        entry["metadata"].get("atom_type") == "preference" for entry in fact_entries
    )
    assert all("expires_at" in entry["metadata"] for entry in fact_entries)


@pytest.mark.asyncio
async def test_empty_atom_set_keeps_legacy_fallback(tmp_db_path):
    """无原子可用时按既有 legacy 路径派生，不伪造原子表示。"""

    async def loader(_memory_id: int):
        return []

    manager, store, _ = await _manager_with_atoms(tmp_db_path, loader)
    boundary = await _source(store)

    await manager.index_memory(1, "", {})

    snapshot = await store.get_graph_snapshot(full=True, boundary=boundary)
    contents = [entry["content"] for entry in snapshot["entries"]]
    assert any(content.startswith("事实：") for content in contents)
    assert not any(content.startswith("记忆原子：") for content in contents)


@pytest.mark.asyncio
async def test_atom_loader_failure_is_not_silently_downgraded(tmp_db_path):
    """原子读取失败必须保持可见，不能静默改用另一种表示。"""

    async def loader(_memory_id: int):
        raise RuntimeError("atom_read_failed")

    manager, store, vectors = await _manager_with_atoms(tmp_db_path, loader)
    boundary = await _source(store)
    previous_records = dict(vectors.records)

    with pytest.raises(RuntimeError, match="atom_read_failed"):
        await manager.index_memory(1, "", {})

    assert vectors.records == previous_records
    assert await store.get_graph_snapshot(full=True, boundary=boundary) == {
        "nodes": [],
        "edges": [],
        "entries": [],
        "memories": [],
    }


@pytest.mark.asyncio
async def test_explicit_atoms_still_win_over_loader(tmp_db_path):
    """显式传入原子时不得再触发加载器读取。"""

    calls: list[int] = []

    async def loader(memory_id: int):
        calls.append(memory_id)
        return []

    manager, store, _ = await _manager_with_atoms(tmp_db_path, loader)
    boundary = await _source(store)

    await manager.index_memory(
        1,
        "",
        {},
        [
            _atom_for("Alice likes coffee", "Alice"),
            _atom_for("Alice likes tea", "Alice"),
        ],
    )

    assert calls == []
    snapshot = await store.get_graph_snapshot(full=True, boundary=boundary)
    assert any(
        entry["content"].startswith("记忆原子：") for entry in snapshot["entries"]
    )


@pytest.mark.asyncio
async def test_rebuild_path_keeps_atom_representation(tmp_db_path):
    """批量重建（stats 路径）必须与增量写入产生同一表示。"""

    async def loader(_memory_id: int):
        return [
            _atom_for("Alice likes coffee", "Alice"),
            _atom_for("Alice likes tea", "Alice"),
        ]

    manager, store, vectors = await _manager_with_atoms(tmp_db_path, loader)
    boundary = await _source(store)
    await manager.index_memory(1, "", {})
    increments = {
        entry["content"]
        for entry in (await store.get_graph_snapshot(full=True, boundary=boundary))[
            "entries"
        ]
    }

    faiss_db = MagicMock()
    faiss_db.document_storage = MagicMock()
    faiss_db.document_storage.count_documents = AsyncMock(return_value=1)
    faiss_db.document_storage.get_documents = AsyncMock(
        return_value=[
            {
                "id": 1,
                "text": "Alice likes coffee; Alice likes tea",
                "created_at": "r1",
                "updated_at": "r1",
                "metadata": {
                    "scope_key": "scope-a",
                    "privacy_level": "public",
                    "key_facts": ["Alice likes coffee", "Alice likes tea"],
                    "fact_source_evidence": fact_evidence(
                        ["Alice likes coffee", "Alice likes tea"]
                    ),
                },
            }
        ]
    )
    rebuild = MaintenanceOperations(
        config={},
        faiss_db=faiss_db,
        graph_memory_manager=manager,
        invalidate_cache_cb=MagicMock(),
    )

    result = await rebuild.rebuild_graph_index()

    assert result["rebuilt"] == 1
    rebuilt = {
        entry["content"]
        for entry in (await store.get_graph_snapshot(full=True, boundary=boundary))[
            "entries"
        ]
    }
    assert rebuilt == increments
    assert vectors.records
