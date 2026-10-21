"""效果门固定 baseline 变体的设置与执行探针契约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.features.evaluation.application.retrieval_ablation import (
    RetrievalAblationController,
)


class _Atom:
    def __init__(self) -> None:
        self.search_calls = 0

    async def search(self, *_args: Any, **_kwargs: Any) -> list[object]:
        self.search_calls += 1
        return []


def _engine(*, graph: bool = True, atom: bool = True) -> tuple[Any, _Atom]:
    config = {
        "document_route_weight": 0.65,
        "graph_route_weight": 0.35,
        "dynamic_route_weighting": True,
        "graph_retrieval_enabled": True,
        "atom_route_weight": 0.25,
        "reranker.mmr_lambda": 0.7,
        "memory_evolution": {"enabled": True, "mode": "readonly"},
    }
    atom_retriever = _Atom()
    dual = SimpleNamespace(
        config=config,
        document_retriever=SimpleNamespace(config=config),
        graph_retriever=SimpleNamespace(config=config) if graph else None,
        atom_retriever=atom_retriever if atom else None,
        reranker=SimpleNamespace(rerank=lambda *_args, **_kwargs: []),
        _route_coordinator=object(),
    )

    class Engine:
        async def search_memories(self, **_kwargs: Any) -> list[object]:
            return []

    engine: Any = Engine()
    engine.config = config
    engine.hybrid_retriever = dual.document_retriever
    engine.graph_keyword_retriever = SimpleNamespace(expansion_hops=1)
    engine.graph_retriever = dual.graph_retriever
    engine.atom_retriever = dual.atom_retriever
    engine.dual_route_retriever = dual
    return engine, atom_retriever


def _prepare(engine: Any, name: str) -> Any:
    return RetrievalAblationController(engine).prepare(name, gate=True)


def test_fixed_and_document_baselines_set_fixed_route() -> None:
    engine, _atom = _engine()
    fixed = _prepare(engine, "fixed_route_baseline")
    document = _prepare(engine, "document_only_baseline")

    assert fixed.available is True
    assert fixed.engine.config["document_route_weight"] == 0.65
    assert fixed.engine.config["graph_route_weight"] == 0.35
    assert fixed.engine.config["graph_retrieval_enabled"] is True
    assert fixed.engine.config["dynamic_route_weighting"] is False
    assert fixed.engine.config["atom_route_weight"] == 0.0
    assert fixed.engine.config["memory_evolution"]["mode"] == "disabled"
    assert fixed.engine.dual_route_retriever.reranker is None
    assert fixed.engine.dual_route_retriever.document_route_weight == 0.65

    assert document.available is True
    assert document.engine.config["document_route_weight"] == 1.0
    assert document.engine.config["graph_route_weight"] == 0.0
    assert document.engine.config["graph_retrieval_enabled"] is False
    assert document.engine.config["atom_route_weight"] == 0.0


def test_atom_and_dynamic_variants_apply_and_probe() -> None:
    engine, atom = _engine()
    atom_variant = _prepare(engine, "atom_signal_on")
    dynamic_variant = _prepare(engine, "dynamic_route_weighting_on")

    assert atom_variant.available is True
    assert atom_variant.engine.config["atom_route_weight"] == 0.25
    assert atom_variant.execution_reason_code() == "variant_not_exercised"
    import asyncio

    asyncio.run(atom_variant.engine.atom_retriever.search("q", k=1))
    assert atom.search_calls == 1
    assert atom_variant.execution_reason_code() == "available"

    assert dynamic_variant.available is True
    assert dynamic_variant.engine.config["dynamic_route_weighting"] is True
    assert dynamic_variant.engine.dual_route_retriever.dynamic_route_weighting is True


def test_graph_off_keeps_weight_but_disables_retrieval() -> None:
    engine, _atom = _engine()
    prepared = _prepare(engine, "graph_route_off")

    assert prepared.available is True
    assert prepared.engine.config["graph_retrieval_enabled"] is False
    assert prepared.engine.config["graph_route_weight"] == 0.35


def test_mmr_variant_is_counted() -> None:
    engine, _atom = _engine()
    prepared = _prepare(engine, "final_reranker_mmr")

    assert prepared.available is True
    reranker = prepared.engine.dual_route_retriever.reranker
    assert prepared.execution_reason_code() == "variant_not_exercised"
    reranker.rerank([], 5, query="q")
    assert prepared.execution_reason_code() == "available"


def test_missing_components_return_stable_reason_codes() -> None:
    graphless, _atom = _engine(graph=False)
    atomless, _atomless = _engine(atom=False)

    assert _prepare(graphless, "fixed_route_baseline").reason_code == (
        "missing_graph_retriever"
    )
    assert _prepare(graphless, "graph_route_off").reason_code == (
        "equivalent_to_baseline"
    )
    assert _prepare(graphless, "dynamic_route_weighting_on").reason_code == (
        "missing_graph_retriever"
    )
    assert _prepare(graphless, "document_only_baseline").available is True
    assert _prepare(atomless, "atom_signal_on").reason_code == (
        "missing_atom_retriever"
    )
