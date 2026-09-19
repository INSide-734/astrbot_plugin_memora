"""Graph 请求 scope、source boundary 校验与 canonical 回填的召回行为测试。"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from core.features.memory.graph.domain.models import GraphBoundary, GraphQueryScope
from core.features.retrieval.graph_keyword_retriever import GraphKeywordResult
from core.features.retrieval.graph_retriever import GraphRetriever
from core.features.retrieval.graph_vector_retriever import GraphVectorResult
from core.features.retrieval.rrf_fusion import BM25Result, RRFFusion, VectorResult

SCOPE = GraphQueryScope("scope-a", "public")
CURRENT = GraphBoundary("scope-a", "public", "rev-1")


def _keyword_hit(
    doc_id: int,
    boundary: GraphBoundary | None,
    *,
    score: float = 0.9,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> GraphKeywordResult:
    return GraphKeywordResult(
        doc_id=doc_id,
        score=score,
        content=content or f"graph keyword body {doc_id}",
        metadata=metadata if metadata is not None else {"importance": 0.7},
        graph_distance=0,
        source_boundary=boundary,
    )


def _vector_hit(
    doc_id: int,
    boundary: GraphBoundary | None,
    *,
    score: float = 0.9,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> GraphVectorResult:
    return GraphVectorResult(
        doc_id=doc_id,
        score=score,
        content=content or f"graph vector body {doc_id}",
        metadata=metadata if metadata is not None else {"importance": 0.7},
        source_boundary=boundary,
    )


def _canonical(
    memory_id: int,
    boundary: GraphBoundary,
    *,
    text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 ``MemoryEngine.get_memory`` 形状的 canonical 记录。"""

    return {
        "id": memory_id,
        "text": text or f"canonical body {memory_id}",
        "updated_at": boundary.revision_token,
        "metadata": {
            "scope_key": boundary.scope_key,
            "privacy_level": boundary.privacy_level,
            "importance": 0.7,
            **(metadata or {}),
        },
    }


def _loader(
    memories: dict[int, dict[str, Any] | None],
    *,
    failing: set[int] | None = None,
) -> tuple[Any, list[int]]:
    """记录调用顺序的 canonical loader；``failing`` 中的来源按加载失败处理。"""

    calls: list[int] = []
    failing_ids = failing or set()

    async def load(memory_id: int) -> dict[str, Any] | None:
        calls.append(memory_id)
        if memory_id in failing_ids:
            raise RuntimeError("source lookup failed")
        return memories.get(memory_id)

    return load, calls


def _retriever(
    keyword_hits: list[GraphKeywordResult] | None = None,
    vector_hits: list[GraphVectorResult] | None = None,
    *,
    memory_loader: Any = None,
    fusion: RRFFusion | None = None,
) -> tuple[GraphRetriever, AsyncMock, AsyncMock]:
    keyword = AsyncMock()
    keyword.search = AsyncMock(return_value=list(keyword_hits or []))
    vector = AsyncMock()
    vector.search = AsyncMock(return_value=list(vector_hits or []))
    retriever = GraphRetriever(
        keyword,
        vector,
        fusion or RRFFusion(k=60),
        memory_loader=memory_loader,
    )
    return retriever, keyword, vector


class _RecordingFusion(RRFFusion):
    """记录进入融合的候选 ID，用于证明过滤发生在 RRF 之前。"""

    def __init__(self) -> None:
        super().__init__(k=60)
        self.seen: list[int] = []

    def fuse(
        self,
        bm25_results: list[BM25Result],
        vector_results: list[VectorResult],
        top_k: int,
    ) -> list[Any]:
        self.seen.extend(item.doc_id for item in (*bm25_results, *vector_results))
        return super().fuse(bm25_results, vector_results, top_k)


@pytest.mark.asyncio
async def test_missing_request_scope_skips_routes_and_loader() -> None:
    """无请求 scope 时不查询图路，也不加载任何 canonical 来源。"""

    loader, calls = _loader({1: _canonical(1, CURRENT)})
    retriever, keyword, vector = _retriever(
        [_keyword_hit(1, CURRENT)], memory_loader=loader
    )
    timing: dict[str, object] = {}

    results = await retriever.search("query", timing_sink=timing)

    assert results == []
    keyword.search.assert_not_awaited()
    vector.search.assert_not_awaited()
    assert calls == []
    assert timing["graph_route_skipped"] is True


@pytest.mark.asyncio
async def test_request_scope_without_canonical_loader_skips_graph_route() -> None:
    """请求 scope 路径缺少 canonical 校验器时安全跳过，而不是信任图结果。"""

    retriever, keyword, vector = _retriever([_keyword_hit(1, CURRENT)])
    timing: dict[str, object] = {}

    results = await retriever.search("query", query_scope=SCOPE, timing_sink=timing)

    assert results == []
    keyword.search.assert_not_awaited()
    vector.search.assert_not_awaited()
    assert timing["graph_route_skipped"] is True


@pytest.mark.asyncio
async def test_each_source_revision_is_validated_against_its_own_canonical() -> None:
    """同一 scope 下不同 revision 的来源各自校验，不套用请求级 revision。"""

    older = GraphBoundary("scope-a", "public", "rev-1")
    newer = GraphBoundary("scope-a", "public", "rev-2")
    loader, calls = _loader({1: _canonical(1, older), 2: _canonical(2, newer)})
    retriever, _, _ = _retriever(
        [_keyword_hit(1, older)],
        [_vector_hit(2, newer)],
        memory_loader=loader,
    )

    results = await retriever.search("query", k=5, query_scope=SCOPE)

    assert {item.doc_id for item in results} == {1, 2}
    assert calls == [1, 2]
    assert {item.content for item in results} == {
        "canonical body 1",
        "canonical body 2",
    }


@pytest.mark.asyncio
async def test_foreign_scope_proof_is_rejected_despite_its_canonical() -> None:
    """命中证据必须落在请求 scope 内；与自身 canonical 相等不足以放行。"""

    foreign = GraphBoundary("scope-b", "public", "rev-1")
    foreign_privacy = GraphBoundary("scope-a", "confidential", "rev-1")
    loader, _ = _loader(
        {
            1: _canonical(1, foreign),
            2: _canonical(2, foreign_privacy),
            3: _canonical(3, CURRENT),
        }
    )
    retriever, _, _ = _retriever(
        [
            _keyword_hit(1, foreign, score=0.99),
            _keyword_hit(2, foreign_privacy, score=0.98),
            _keyword_hit(3, CURRENT, score=0.5),
        ],
        memory_loader=loader,
    )

    results = await retriever.search("query", k=5, query_scope=SCOPE)

    assert [item.doc_id for item in results] == [3]


@pytest.mark.asyncio
async def test_canonical_scope_is_never_inferred_or_coerced() -> None:
    """canonical 缺少显式 scope_key 或类型非法时 fail-closed，不从其它字段推断。"""

    loader, _ = _loader(
        {
            1: {
                "id": 1,
                "text": "canonical body 1",
                "updated_at": "rev-1",
                "metadata": {
                    "session_id": "scope-a",
                    "persona_id": "persona-a",
                    "privacy_level": "public",
                },
            },
            2: {
                "id": 2,
                "text": "canonical body 2",
                "updated_at": "rev-1",
                "metadata": {"scope_key": 123, "privacy_level": "public"},
            },
        }
    )
    retriever, _, _ = _retriever(
        [_keyword_hit(1, CURRENT), _keyword_hit(2, CURRENT)],
        memory_loader=loader,
    )

    results = await retriever.search("query", query_scope=SCOPE)

    assert results == []


@pytest.mark.asyncio
async def test_invalid_hits_are_excluded_before_fusion_and_truncation() -> None:
    """跨 scope、越权隐私、过期 revision、缺失来源与损坏证据都不得进入 RRF。"""

    fusion = _RecordingFusion()
    cross_scope = GraphBoundary("scope-b", "public", "rev-1")
    private = GraphBoundary("scope-a", "confidential", "rev-1")
    stale = GraphBoundary("scope-a", "public", "rev-old")
    keyword_hits = [
        _keyword_hit(2, cross_scope, score=0.99),
        _keyword_hit(3, private, score=0.98),
        _keyword_hit(4, stale, score=0.97),
        _keyword_hit(5, CURRENT, score=0.96),
        _keyword_hit(6, None, score=0.95),
        _keyword_hit(7, cast(Any, {"scope_key": "scope-a"}), score=0.94),
        _keyword_hit(9, CURRENT, score=0.93),
        _keyword_hit(10, CURRENT, score=0.92),
        _keyword_hit(11, CURRENT, score=0.91),
        _keyword_hit(12, CURRENT, score=0.9),
        _keyword_hit(1, CURRENT, score=0.5),
    ]
    vector_hits = [
        _vector_hit(8, stale, score=0.99),
        _vector_hit(20, CURRENT, score=0.4),
    ]
    loader, calls = _loader(
        {
            1: _canonical(1, CURRENT),
            2: _canonical(2, CURRENT),
            3: _canonical(3, CURRENT),
            4: _canonical(4, CURRENT),
            6: _canonical(6, CURRENT),
            7: _canonical(7, CURRENT),
            8: _canonical(8, CURRENT),
            9: _canonical(9, CURRENT, metadata={"privacy_level": "top-secret"}),
            10: _canonical(10, CURRENT, metadata={"gate_disposition": "mark_write"}),
            11: _canonical(11, CURRENT, metadata={"status": "archived"}),
            20: _canonical(20, CURRENT),
        },
        failing={12},
    )
    retriever, _, _ = _retriever(
        keyword_hits, vector_hits, memory_loader=loader, fusion=fusion
    )

    results = await retriever.search("query", k=2, query_scope=SCOPE)

    assert fusion.seen == [1, 20]
    assert {item.doc_id for item in results} == {1, 20}
    assert calls == sorted({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20})


@pytest.mark.asyncio
async def test_top_k_budget_is_filled_by_valid_sources_only() -> None:
    """过期来源即使排在首位也不能占用 top-k 名额。"""

    stale = GraphBoundary("scope-a", "public", "rev-old")
    loader, _ = _loader(
        {
            1: _canonical(1, CURRENT),
            2: _canonical(2, CURRENT),
            3: _canonical(3, CURRENT),
        }
    )
    retriever, _, _ = _retriever(
        [
            _keyword_hit(2, stale, score=0.99),
            _keyword_hit(1, CURRENT, score=0.8),
            _keyword_hit(3, CURRENT, score=0.7),
        ],
        memory_loader=loader,
    )

    results = await retriever.search("query", k=2, query_scope=SCOPE)

    assert [item.doc_id for item in results] == [1, 3]


@pytest.mark.asyncio
async def test_exact_boundary_path_stays_supported_without_loader() -> None:
    """显式精确 boundary 路径保持既有内部/受信消费者行为。"""

    retriever, _, _ = _retriever([_keyword_hit(1, CURRENT)])

    results = await retriever.search("query", k=5, boundary=CURRENT)

    assert [item.doc_id for item in results] == [1]
    assert results[0].content == "graph keyword body 1"


@pytest.mark.asyncio
async def test_exact_boundary_revalidates_current_canonical_with_loader() -> None:
    """精确 boundary 路径在有 loader 时同样复核来源是否已前进。"""

    stale = GraphBoundary("scope-a", "public", "rev-1")
    advanced = GraphBoundary("scope-a", "public", "rev-2")
    loader, calls = _loader({1: _canonical(1, advanced), 2: _canonical(2, stale)})
    retriever, _, _ = _retriever(
        [_keyword_hit(1, stale, score=0.9), _keyword_hit(2, stale, score=0.8)],
        memory_loader=loader,
    )

    results = await retriever.search("query", k=5, boundary=stale)

    assert [item.doc_id for item in results] == [2]
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_exact_boundary_requires_proof_to_carry_the_requested_revision() -> None:
    """请求精确 boundary 时，证据必须携带同一 revision 而非自身 canonical 的。"""

    other = GraphBoundary("scope-a", "public", "rev-9")
    loader, _ = _loader({1: _canonical(1, other)})
    retriever, _, _ = _retriever([_keyword_hit(1, other)], memory_loader=loader)

    results = await retriever.search("query", k=5, boundary=CURRENT)

    assert results == []


@pytest.mark.asyncio
async def test_canonical_body_and_metadata_replace_graph_derived_payload() -> None:
    """校验通过后正文与用户 metadata 以 canonical 为准，只保留图路注解。"""

    loader, _ = _loader(
        {
            1: _canonical(
                1,
                CURRENT,
                text="canonical body 1",
                metadata={"key_facts": ["user fact"]},
            )
        }
    )
    hit_metadata = {
        "scope_key": "scope-a",
        "privacy_level": "public",
        "revision_token": "rev-1",
        "source_memory_id": 1,
        "canonical_summary": "derived summary",
        "source_window": "window",
        "importance": 0.1,
        "graph_confidence": 0.91,
        "graph_match_source": "graph_keyword",
        "ttl_days": 12.0,
        "decay_type": "linear",
    }
    retriever, _, _ = _retriever(
        [
            _keyword_hit(
                1,
                CURRENT,
                content="stale graph body",
                metadata=hit_metadata,
            )
        ],
        memory_loader=loader,
    )

    results = await retriever.search("query", k=5, query_scope=SCOPE)

    assert [item.doc_id for item in results] == [1]
    result = results[0]
    assert result.content == "canonical body 1"
    assert result.metadata["key_facts"] == ["user fact"]
    assert result.metadata["importance"] == 0.7
    assert result.metadata["graph_confidence"] == 0.91
    assert result.metadata["graph_match_source"] == "graph_keyword"
    assert result.metadata["ttl_days"] == 12.0
    assert result.metadata["decay_type"] == "linear"
    assert "canonical_summary" not in result.metadata
    assert "source_window" not in result.metadata
    assert "revision_token" not in result.metadata
    assert "source_memory_id" not in result.metadata


@pytest.mark.asyncio
async def test_loader_cancellation_propagates() -> None:
    """loader 抛出 CancelledError 时不得被当作普通失败吞掉。"""

    async def cancelled(memory_id: int) -> dict[str, Any] | None:
        raise asyncio.CancelledError

    retriever, _, _ = _retriever([_keyword_hit(1, CURRENT)], memory_loader=cancelled)

    with pytest.raises(asyncio.CancelledError):
        await retriever.search("query", query_scope=SCOPE)


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_through_source_loading() -> None:
    """调用方取消时，正在等待 canonical 加载的检索必须立即取消。"""

    started = asyncio.Event()

    async def blocked(memory_id: int) -> dict[str, Any] | None:
        started.set()
        await asyncio.Event().wait()
        return None

    retriever, _, _ = _retriever([_keyword_hit(1, CURRENT)], memory_loader=blocked)
    task = asyncio.create_task(retriever.search("query", query_scope=SCOPE))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
