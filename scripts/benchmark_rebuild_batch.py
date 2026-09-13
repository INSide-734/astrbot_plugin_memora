"""Deterministic, throwaway F5 graph batch benchmark.

This deliberately reports request counts and mapping/cleanup outcomes only.  It
is not a latency benchmark and must not be used to claim production P95.
Run manually from the repository root when reviewing the batch contract.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.graph.infrastructure.graph_store import GraphReplaceResult
from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever


class _Backend:
    """Deterministic backend that returns IDs in input order."""

    def __init__(self) -> None:
        self.calls = 0
        self.next_id = 1000

    async def insert_batch(self, *, contents, metadatas, batch_size):
        self.calls += 1
        result = list(range(self.next_id, self.next_id + len(contents)))
        self.next_id += len(contents)
        return result


class _Store:
    def __init__(self, entry_count: int) -> None:
        self.entry_ids = list(range(1, entry_count + 1))
        self.mappings: list[dict[int, int]] = []

    async def replace_memory_graph(self, *_args):
        return GraphReplaceResult(entry_ids=list(self.entry_ids))

    async def update_entry_vector_doc_ids(self, mapping):
        self.mappings.append(mapping)


class _BatchFailureRetriever:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.delete_calls: list[int] = []

    async def add_entries(self, _entries):
        raise self.failure

    async def delete_entries_for_memory(self, source_memory_id: int) -> int:
        self.delete_calls.append(source_memory_id)
        return 0


class _Extractor:
    def __init__(self, entry_count: int) -> None:
        self.entries = [
            SimpleNamespace(content=f"entry-{index}", metadata={})
            for index in range(entry_count)
        ]

    def extract(self, *_args):
        return SimpleNamespace(nodes=[], edges=[], entries=self.entries)


async def _mapping_demo(entry_count: int = 9) -> dict[str, object]:
    backend = _Backend()
    retriever = GraphVectorRetriever(backend, {"graph_embedding_batch_size": 3})
    entries = [
        (f"entry-{index}", {"source_memory_id": 7}) for index in range(entry_count)
    ]
    vector_ids = await retriever.add_entries(entries)
    return {
        "entry_count": entry_count,
        "legacy_physical_requests": entry_count,
        "batched_physical_requests": backend.calls,
        "ordered_mapping": vector_ids,
        "mapping_preserved": vector_ids == list(range(1000, 1000 + entry_count)),
    }


async def _failure_demo() -> dict[str, object]:
    outcomes: dict[str, object] = {}
    for name, failure in (
        ("failure", RuntimeError("deterministic failure")),
        ("cancelled", asyncio.CancelledError()),
    ):
        store = _Store(2)
        retriever = _BatchFailureRetriever(failure)
        manager = GraphMemoryManager(store, retriever, _Extractor(2))
        try:
            await manager.index_memory(7, "source", {})
        except asyncio.CancelledError:
            outcomes[name] = "cancelled"
        except RuntimeError:
            outcomes[name] = "failed"
        outcomes[f"{name}_cleanup_calls"] = retriever.delete_calls
        outcomes[f"{name}_mapping"] = store.mappings
    return outcomes


async def main() -> None:
    print(
        json.dumps({"mapping": await _mapping_demo(), "failure": await _failure_demo()})
    )


if __name__ == "__main__":
    asyncio.run(main())
