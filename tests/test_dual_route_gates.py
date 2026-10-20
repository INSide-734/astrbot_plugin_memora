"""图检索与 Atom 检索的运行门契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.features.retrieval.dual_route_retriever import DualRouteRetriever


def _retriever(
    *,
    config: dict[str, object],
    atom: AsyncMock | None = None,
) -> tuple[DualRouteRetriever, AsyncMock, AsyncMock, AsyncMock]:
    document = AsyncMock()
    document.search = AsyncMock(return_value=[])
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    atom = atom or AsyncMock()
    atom.search = AsyncMock(return_value=[])
    retriever = DualRouteRetriever(
        document_retriever=document,
        graph_retriever=graph,
        memory_loader=AsyncMock(return_value=None),
        config=config,
        atom_retriever=atom,
    )
    return retriever, document, graph, atom


@pytest.mark.asyncio
async def test_atom_zero_weight_skips_atom_task() -> None:
    retriever, _document, _graph, atom = _retriever(config={"atom_route_weight": 0.0})

    await retriever.search("q", k=3)

    atom.search.assert_not_awaited()
    assert retriever.last_search_timing["atom_route_skipped"] is True


@pytest.mark.asyncio
async def test_atom_positive_weight_runs_atom_task() -> None:
    retriever, _document, _graph, atom = _retriever(config={"atom_route_weight": 0.25})

    await retriever.search("q", k=3)

    atom.search.assert_awaited_once()
    assert "atom_route_skipped" not in retriever.last_search_timing


@pytest.mark.asyncio
async def test_graph_retrieval_disabled_skips_graph_task() -> None:
    retriever, _document, graph, _atom = _retriever(
        config={
            "graph_retrieval_enabled": False,
            "graph_route_weight": 0.35,
        }
    )

    await retriever.search("q", k=3)

    graph.search.assert_not_awaited()
    assert retriever.last_search_timing["graph_route_skipped"] is True


@pytest.mark.asyncio
async def test_graph_retrieval_enabled_runs_graph_task() -> None:
    retriever, _document, graph, _atom = _retriever(
        config={
            "graph_retrieval_enabled": True,
            "graph_route_weight": 0.35,
        }
    )

    await retriever.search("q", k=3)

    graph.search.assert_awaited_once()
    assert "graph_route_skipped" not in retriever.last_search_timing
