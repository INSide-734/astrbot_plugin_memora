"""Graph manager canonical-source gates and derived mutation behavior."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
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
