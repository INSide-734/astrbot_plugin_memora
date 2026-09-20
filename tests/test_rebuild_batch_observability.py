"""Focused F5 rebuild measurement and graph batch behavior tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.graph.domain.models import GraphBoundary
from core.features.memory.graph.infrastructure.graph_store import GraphReplaceResult
from core.features.memory.infrastructure.validators.embedding_retry import (
    EmbeddingRetryMixin,
)
from core.features.memory.rebuild_observability import (
    RebuildMeasurement,
    classify_rebuild_trigger,
    finalize_rebuild_observability,
    normalize_rebuild_trigger,
    rebuild_measurement_scope,
)
from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever
from core.platform.composition import DerivedRebuildCoordinator
from tests.fact_evidence_helpers import fact_evidence

BOUNDARY = GraphBoundary.from_metadata(
    {"scope_key": "graph-test", "privacy_level": "public", "revision_token": "r1"}
)


class _RetryHarness(EmbeddingRetryMixin):
    """Provide the retry policy hooks used by the embedding mixin."""

    RATE_LIMIT_RETRY_MIN_DELAY = 0.0

    @staticmethod
    def _is_rate_limit_error(_error: Exception) -> bool:
        return False


class _SingleProvider:
    """A deterministic single-item provider."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_embedding(self, content: str) -> list[float]:
        self.calls.append(content)
        return [float(len(content))]


@pytest.mark.asyncio
async def test_embedding_measurement_counts_invoked_single_requests() -> None:
    provider = _SingleProvider()
    options = {
        "max_retries": 1,
        "retry_base_delay": 0.0,
        "embedding_batch_size": 2,
        "request_delay": 0.0,
    }

    with rebuild_measurement_scope("indexes_inconsistent") as measurement:
        vectors = await _RetryHarness()._embed_batch_with_retry(
            provider, ["a", "bb", "ccc"], options
        )

    assert vectors == [[1.0], [2.0], [3.0]]
    assert provider.calls == ["a", "bb", "ccc"]
    assert measurement.embedding_batches == 2
    assert measurement.embedding_requests == 3


@pytest.mark.asyncio
async def test_cancelled_embedding_does_not_count_uninvoked_items() -> None:
    calls: list[str] = []

    class CancelProvider:
        async def get_embedding(self, content: str) -> list[float]:
            calls.append(content)
            raise asyncio.CancelledError

    options = {
        "max_retries": 3,
        "retry_base_delay": 0.0,
        "embedding_batch_size": 8,
        "request_delay": 0.0,
    }
    with rebuild_measurement_scope("indexes_inconsistent") as measurement:
        with pytest.raises(asyncio.CancelledError):
            await _RetryHarness()._embed_batch_with_retry(
                CancelProvider(), ["first", "never"], options
            )

    assert calls == ["first"]
    assert measurement.embedding_requests == 1


def test_rebuild_measurement_keeps_unknown_and_does_not_infer_total() -> None:
    assert normalize_rebuild_trigger("made-up") == "unknown"
    assert classify_rebuild_trigger(True, True) == "indexes_and_catalog"
    assert classify_rebuild_trigger(False, True) == "catalog_dirty"

    measurement = RebuildMeasurement("made-up")
    measurement.record_stage("graph", 0.25, {"processed": 4})
    snapshot = measurement.snapshot(duration_seconds=0.25)

    assert snapshot["trigger_reason"] == "unknown"
    assert snapshot["stages"]["graph"]["processed"] == 4
    assert snapshot["stages"]["graph"]["total"] is None


def test_finalize_observability_falls_back_when_indexes_stage_has_no_counts() -> None:
    """indexes 阶段无计数时不得用它覆盖 canonical 已记录的文档数。"""
    measurement = RebuildMeasurement("indexes_consistent")
    measurement.record_stage("canonical", 0.1, {"success": True, "documents": 7})
    measurement.record_stage(
        "indexes",
        0.0,
        {"status": "skipped", "success": True, "reason_code": "indexes_consistent"},
        status="skipped",
    )

    output = finalize_rebuild_observability({}, measurement, duration_seconds=0.1)

    snapshot = output["observability"]
    assert snapshot["processed"] == 7
    assert snapshot["total"] == 7
    assert snapshot["stages"]["indexes"]["processed"] is None


def test_finalize_observability_prefers_indexes_counts_when_present() -> None:
    """indexes 阶段给出计数时仍以它为准。"""
    measurement = RebuildMeasurement("indexes_inconsistent")
    measurement.record_stage("canonical", 0.1, {"success": True, "documents": 7})
    measurement.record_stage("indexes", 0.4, {"processed": 3, "errors": 1, "total": 4})

    output = finalize_rebuild_observability({}, measurement, duration_seconds=0.5)

    snapshot = output["observability"]
    assert snapshot["processed"] == 3
    assert snapshot["failed"] == 1
    assert snapshot["total"] == 4


class _BatchRetriever(GraphVectorRetriever):
    """A graph owner test double with observable cleanup and batch calls."""

    def __init__(self, result: list[int] | BaseException) -> None:
        self.result = result
        self.add_calls: list[list[tuple[str, dict]]] = []
        self.delete_calls: list[int] = []

    async def add_entries(
        self,
        entries: list[tuple[str, dict]],
        *,
        boundary: GraphBoundary,
        batch_size: int | None = None,
    ) -> list[int]:
        assert boundary == BOUNDARY
        self.add_calls.append(entries)
        if isinstance(self.result, BaseException):
            raise self.result
        return list(self.result)

    async def reap_entries_for_memory(self, source_memory_id: int) -> int:
        self.delete_calls.append(source_memory_id)
        return 0


class _GraphStore:
    """Capture graph replacement and vector ID mapping writes."""

    def __init__(self, entry_ids: list[int]) -> None:
        self.entry_ids = entry_ids
        self.mappings: list[dict[int, int]] = []

    async def load_source_memory(
        self, source_memory_id: int
    ) -> tuple[str, dict[str, object]]:
        assert source_memory_id > 0
        fact = "source"
        return fact, {
            **BOUNDARY.as_params(),
            "key_facts": [fact],
            "fact_source_evidence": fact_evidence([fact]),
        }

    async def replace_memory_graph(
        self, *_args, boundary: GraphBoundary
    ) -> GraphReplaceResult:
        assert boundary == BOUNDARY
        return GraphReplaceResult(entry_ids=list(self.entry_ids))

    async def update_entry_vector_doc_ids(
        self, mapping: dict[int, int], *, boundary: GraphBoundary
    ) -> None:
        assert boundary == BOUNDARY
        self.mappings.append(mapping)


class _Extractor:
    """Return deterministic graph entries without invoking extraction."""

    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self.entries = entries

    def extract(self, *_args) -> SimpleNamespace:
        return SimpleNamespace(nodes=[], edges=[], entries=self.entries)


@pytest.mark.asyncio
async def test_graph_manager_persists_ordered_batch_mapping() -> None:
    entries = [
        SimpleNamespace(content="one", metadata={"rank": 1}),
        SimpleNamespace(content="two", metadata={"rank": 2}),
    ]
    store = _GraphStore([31, 32])
    retriever = _BatchRetriever([701, 702])
    manager = GraphMemoryManager(store, retriever, _Extractor(entries))

    await manager.index_memory(9, "source", {})

    assert [content for content, _metadata in retriever.add_calls[0]] == [
        "one",
        "two",
    ]
    assert retriever.add_calls[0][0][1]["source_memory_id"] == 9
    assert store.mappings == [{31: 701, 32: 702}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("batch failed"), asyncio.CancelledError()]
)
async def test_graph_manager_cleans_batch_failure_without_mapping(
    failure: BaseException,
) -> None:
    entries = [SimpleNamespace(content="one", metadata={})]
    store = _GraphStore([31])
    retriever = _BatchRetriever(failure)
    manager = GraphMemoryManager(store, retriever, _Extractor(entries))

    expected = (
        asyncio.CancelledError
        if isinstance(failure, asyncio.CancelledError)
        else RuntimeError
    )
    with pytest.raises(expected):
        await manager.index_memory(9, "source", {})

    assert retriever.delete_calls == [9, 9]
    assert store.mappings == [{}]


class _BatchDb:
    """Return ordered IDs while exposing bounded insert calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[dict], int]] = []
        self.next_id = 100

    async def insert_batch(
        self,
        *,
        contents: list[str],
        metadatas: list[dict],
        batch_size: int,
    ) -> list[int]:
        self.calls.append((contents, metadatas, batch_size))
        ids = list(range(self.next_id, self.next_id + len(contents)))
        self.next_id += len(contents)
        return ids


@pytest.mark.asyncio
async def test_graph_vector_retriever_uses_bounded_ordered_batches() -> None:
    backend = _BatchDb()
    retriever = GraphVectorRetriever(backend, {"graph_embedding_batch_size": 2})

    result = await retriever.add_entries(
        [
            ("a", {"n": 1, **BOUNDARY.as_params()}),
            ("b", {"n": 2, **BOUNDARY.as_params()}),
            ("c", {"n": 3, **BOUNDARY.as_params()}),
        ],
        boundary=BOUNDARY,
    )

    assert result == [100, 101, 102]
    assert [len(contents) for contents, _metadata, _size in backend.calls] == [2, 1]
    assert [size for _contents, _metadata, size in backend.calls] == [2, 1]


@pytest.mark.asyncio
async def test_coordinator_publishes_cancelled_stage_before_propagating() -> None:
    validator = SimpleNamespace(
        _get_document_count=AsyncMock(return_value=2),
        rebuild_indexes=AsyncMock(side_effect=asyncio.CancelledError()),
    )
    engine = SimpleNamespace(rebuild_graph_index=AsyncMock())
    coordinator = DerivedRebuildCoordinator(validator, engine)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.rebuild_all(trigger_reason="indexes_inconsistent")

    snapshot = validator._observability
    assert snapshot["trigger_reason"] == "indexes_inconsistent"
    assert snapshot["stages"]["indexes"]["status"] == "cancelled"
    assert snapshot["stages"]["rebuild"]["status"] == "cancelled"
    engine.rebuild_graph_index.assert_not_awaited()
