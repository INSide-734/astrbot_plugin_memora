"""评测快照的隔离、协调器重建、Atom 只读与成本计数契约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.features.evaluation.application.evaluation_snapshot import (
    apply_route_settings,
    build_evaluation_snapshot,
)


class _FakeVectorStore:
    def __init__(self) -> None:
        self.retrieve_calls = 0

    async def retrieve(self, **_kwargs: Any) -> list[object]:
        self.retrieve_calls += 1
        return []


class _FakeAtomRetriever:
    def __init__(self) -> None:
        self.search_calls = 0
        self.touch_calls = 0

    async def search(self, *_args: Any, **_kwargs: Any) -> list[object]:
        self.search_calls += 1
        return []

    async def touch_many(self, *_args: Any, **_kwargs: Any) -> None:
        self.touch_calls += 1


class _FakeEngine:
    async def search_memories(self, **_kwargs: Any) -> list[object]:
        return []


def _engine() -> tuple[Any, Any, Any, Any, Any]:
    config = {
        "document_route_weight": 0.65,
        "graph_route_weight": 0.35,
        "dynamic_route_weighting": False,
        "graph_retrieval_enabled": True,
        "atom_route_weight": 0.25,
    }
    faiss_db = _FakeVectorStore()
    graph_faiss = _FakeVectorStore()
    vector_retriever = SimpleNamespace(faiss_db=faiss_db)
    graph_vector_retriever = SimpleNamespace(faiss_db=graph_faiss)
    hybrid = SimpleNamespace(config=config, vector_retriever=vector_retriever)
    keyword = SimpleNamespace(expansion_hops=1)
    graph = SimpleNamespace(
        config=config,
        keyword_retriever=keyword,
        vector_retriever=graph_vector_retriever,
    )
    atom = _FakeAtomRetriever()
    sentinel_coordinator = object()
    dual = SimpleNamespace(
        config=config,
        document_retriever=hybrid,
        graph_retriever=graph,
        atom_retriever=atom,
        document_route_weight=0.65,
        graph_route_weight=0.35,
        dynamic_route_weighting=False,
        graph_retrieval_enabled=True,
        _create_tracked_task=object(),
        _route_coordinator=sentinel_coordinator,
    )
    retrieval = SimpleNamespace(
        _config=config,
        _cache={"live": [1]},
        _session_cache={"live": [1]},
        _dual_route_retriever=dual,
    )
    engine: Any = _FakeEngine()
    engine.config = config
    engine.faiss_db = faiss_db
    engine.graph_vector_db = graph_faiss
    engine.hybrid_retriever = hybrid
    engine.graph_keyword_retriever = keyword
    engine.graph_retriever = graph
    engine.graph_vector_retriever = graph_vector_retriever
    engine.atom_retriever = atom
    engine.dual_route_retriever = dual
    engine._retrieval = retrieval
    return engine, sentinel_coordinator, faiss_db, graph_faiss, atom


@pytest.mark.asyncio
async def test_snapshot_rebuilds_coordinator_and_keeps_live_untouched() -> None:
    engine, sentinel, faiss_db, graph_faiss, atom = _engine()
    snapshot = build_evaluation_snapshot(engine)

    assert snapshot is not engine
    assert snapshot.config is not engine.config
    assert snapshot.config == engine.config
    assert snapshot.dual_route_retriever is not engine.dual_route_retriever
    assert (
        snapshot.dual_route_retriever._route_coordinator
        is not engine.dual_route_retriever._route_coordinator
    )
    assert engine.dual_route_retriever._route_coordinator is sentinel
    coordinator = snapshot.dual_route_retriever._route_coordinator
    assert coordinator._document_retriever is snapshot.hybrid_retriever
    assert coordinator._graph_retriever is snapshot.graph_retriever
    assert coordinator._atom_retriever is snapshot.atom_retriever

    assert snapshot.hybrid_retriever.vector_retriever is not (
        engine.hybrid_retriever.vector_retriever
    )
    assert (
        snapshot.hybrid_retriever.vector_retriever.faiss_db
        is not engine.hybrid_retriever.vector_retriever.faiss_db
    )
    assert engine.hybrid_retriever.vector_retriever.faiss_db is faiss_db
    assert engine.graph_vector_retriever.faiss_db is graph_faiss
    assert engine.atom_retriever is atom
    assert snapshot.atom_retriever is not atom


@pytest.mark.asyncio
async def test_snapshot_counts_provider_calls_without_writing_atom_state() -> None:
    engine, _sentinel, _faiss_db, _graph_faiss, atom = _engine()
    snapshot = build_evaluation_snapshot(engine)
    counter = snapshot._evaluation_cost_counter

    await snapshot.faiss_db.retrieve(query="q")
    await snapshot.graph_vector_db.retrieve(query="q")
    await snapshot.hybrid_retriever.vector_retriever.faiss_db.retrieve(query="q")
    assert counter.value() == 3.0

    await snapshot.atom_retriever.search("q", k=1)
    await snapshot.atom_retriever.touch_many([1, 2])
    assert atom.search_calls == 1
    assert atom.touch_calls == 0

    # 代理只读委托原始 store，live 属性不应被替换；读取本身允许发生。
    assert engine.faiss_db is _faiss_db
    assert engine.graph_vector_db is _graph_faiss


def test_apply_route_settings_updates_config_and_dual_only() -> None:
    engine, _sentinel, _faiss_db, _graph_faiss, _atom = _engine()
    snapshot = build_evaluation_snapshot(engine)

    settings = apply_route_settings(
        snapshot,
        document_route_weight=1.0,
        graph_route_weight=0.0,
        graph_retrieval_enabled=False,
        dynamic_route_weighting=True,
        atom_route_weight=0.0,
    )

    assert settings["graph_retrieval_enabled"] is False
    assert snapshot.config["document_route_weight"] == 1.0
    assert snapshot.dual_route_retriever.graph_retrieval_enabled is False
    assert snapshot.dual_route_retriever.dynamic_route_weighting is True
    assert snapshot.config["atom_route_weight"] == 0.0
    assert engine.config["document_route_weight"] == 0.65
    assert engine.config["graph_retrieval_enabled"] is True
    assert engine.dual_route_retriever.dynamic_route_weighting is False
