"""证据需求请求在引擎与双路检索上的预截断证据门契约。

普通召回只能保留完整正文可逐事实归属用户来源的候选；``require_user_evidence``
为真的请求必须在重排、k 截断与缓存读写之前完成该判定，否则高分混合/assistant
候选会先占用槽位，再被后置选择门丢弃。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.memory_engine_crud import MemoryEngineCRUDMixin
from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.retrieval.dual_route_retriever import DualRouteRetriever
from core.features.retrieval.rrf_fusion import HybridResult
from tests.fact_evidence_helpers import candidate_evidence_metadata
from tests.injection_executor_support import resolved_reference

_USER_FACT = "用户明确喜欢低糖饮料"
_ASSISTANT_FACT = "ASSISTANT_UNCONFIRMED_PLAN"
_QUERY = "用户喜欢什么饮料"
_SESSION = "private:user-a"


def _candidate(doc_id: int, content: str, score: float, metadata: Any) -> HybridResult:
    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata=metadata,
    )


def _mixed_metadata() -> dict[str, Any]:
    """一半用户事实、一半助手事实：聚合分数由两部分共同产生。"""

    return {
        "key_facts": [_USER_FACT, _ASSISTANT_FACT],
        "fact_source_evidence": [
            [resolved_reference(_USER_FACT)],
            [resolved_reference(_ASSISTANT_FACT, role="assistant")],
        ],
        "source_evidence": [resolved_reference(_USER_FACT)],
    }


def _mixed(doc_id: int, score: float) -> HybridResult:
    return _candidate(
        doc_id, f"{_USER_FACT}；{_ASSISTANT_FACT}", score, _mixed_metadata()
    )


def _supported(doc_id: int, score: float, content: str = _USER_FACT) -> HybridResult:
    return _candidate(doc_id, content, score, candidate_evidence_metadata(content))


def _discard_coroutine(coro: Any) -> None:
    """关闭测试宿主不调度的后台协程。"""

    coro.close()


class _SearchHost(MemoryEngineCRUDMixin):
    """只装配 search_memories 所需协作对象的最小引擎宿主。"""

    def __init__(self, dual_route: Any) -> None:
        self.config: dict[str, Any] = {}
        self.dual_route_retriever = dual_route
        self.hybrid_retriever = None
        self._retrieval = RetrievalOptimizer(config={})
        self._maintenance = MagicMock()
        self._maintenance.update_access_times_batch = AsyncMock()
        self._maintenance.migrate_session_if_needed = AsyncMock()
        self._create_tracked_task = _discard_coroutine
        self._last_search_timing: dict[str, Any] = {}
        self._last_debug_trace: list[dict[str, Any]] = []


def _host_without_retrieval() -> _SearchHost:
    """构造证据请求命中缓存后不得再调用检索器的最小宿主。"""

    dual = AsyncMock()
    dual.search = AsyncMock(side_effect=AssertionError("证据请求必须直接使用已有缓存"))
    return _SearchHost(dual)


@pytest.mark.asyncio
async def test_engine_evidence_request_denies_mixed_candidate_top_slot() -> None:
    """k=1 时高分混合候选不得占用槽位，低分完整用户证据候选补位。"""

    dual = AsyncMock()
    dual.search = AsyncMock(return_value=[_mixed(1, 0.99), _supported(2, 0.05)])
    host = _SearchHost(dual)

    plain = await host.search_memories(_QUERY, k=1, session_id=_SESSION)
    gated = await host.search_memories(
        _QUERY, k=1, session_id=_SESSION, require_user_evidence=True
    )

    assert [item.doc_id for item in plain] == [1]
    assert [item.doc_id for item in gated] == [2]
    plain_again = await host.search_memories(_QUERY, k=1, session_id=_SESSION)
    assert [item.doc_id for item in plain_again] == [1]


@pytest.mark.asyncio
async def test_dual_route_evidence_gate_excludes_mixed_before_rerank() -> None:
    """证据门先于外部重排与最终截断：重排器只能看到合格候选。"""

    document = AsyncMock()
    document.search = AsyncMock(
        return_value=[
            _mixed(1, 0.99),
            _supported(2, 0.05),
            _supported(3, 0.04, "用户更喜欢气泡水"),
        ]
    )
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    seen: list[list[int]] = []

    async def rerank(values: list[Any], k: int, *, query: str) -> list[Any]:
        seen.append([item.doc_id for item in values])
        return values

    reranker = MagicMock()
    reranker.rerank = rerank
    retriever = DualRouteRetriever(
        document_retriever=document,
        graph_retriever=graph,
        memory_loader=AsyncMock(return_value=None),
        reranker=reranker,
    )

    plain = await retriever.search(_QUERY, k=1, session_id=_SESSION)
    gated = await retriever.search(
        _QUERY, k=1, session_id=_SESSION, require_user_evidence=True
    )

    assert [item.doc_id for item in plain] == [1]
    assert [item.doc_id for item in gated] == [2]
    assert seen == [[1, 2, 3], [2, 3]]


@pytest.mark.asyncio
async def test_engine_chain_expansion_cannot_readmit_mixed_candidate() -> None:
    """链式扩展新增的混合候选必须在返回与写入缓存前被丢弃。"""

    seed = _supported(7, 0.05, "用户喜欢低糖饮料")
    seed.metadata["topics"] = ["饮料偏好"]
    dual = AsyncMock()
    dual.search = AsyncMock(return_value=[seed])
    host = _SearchHost(dual)
    host._retrieval._search_memories = AsyncMock(return_value=[_mixed(8, 0.9)])

    gated = await host.search_memories(
        _QUERY, k=2, session_id=_SESSION, chain_depth=2, require_user_evidence=True
    )
    plain = await host.search_memories(_QUERY, k=2, session_id=_SESSION, chain_depth=2)

    assert [item.doc_id for item in gated] == [7]
    assert sorted(item.doc_id for item in plain) == [7, 8]


def test_evidence_requirement_separates_cache_keys() -> None:
    """证据需求开关必须进入两级缓存键，普通条目不得被证据需求请求命中。"""

    optimizer = RetrievalOptimizer(config={})
    mixed = _mixed(1, 0.99)

    assert optimizer.cache_key(_QUERY, 1, _SESSION, None) != optimizer.cache_key(
        _QUERY, 1, _SESSION, None, require_user_evidence=True
    )
    optimizer.set_cached(optimizer.cache_key(_QUERY, 1, _SESSION, None), [mixed])
    optimizer.set_session_cached(_QUERY, 1, _SESSION, None, [mixed])

    assert (
        optimizer.get_cached(
            optimizer.cache_key(_QUERY, 1, _SESSION, None, require_user_evidence=True)
        )
        is None
    )
    assert (
        optimizer.get_session_cached(
            _QUERY, 1, _SESSION, None, require_user_evidence=True
        )
        is None
    )


@pytest.mark.asyncio
async def test_engine_filters_cache_served_candidates_for_evidence_request() -> None:
    """两级缓存命中的返回路径同样先过滤证据，再原样返回或按 k 截断。"""

    full_cache_host = _host_without_retrieval()
    full_cache_host._retrieval.set_cached(
        full_cache_host._retrieval.cache_key(
            _QUERY, 1, _SESSION, None, require_user_evidence=True
        ),
        [_mixed(1, 0.99), _supported(2, 0.05)],
    )
    session_cache_host = _host_without_retrieval()
    session_cache_host._retrieval.set_session_cached(
        _QUERY,
        1,
        _SESSION,
        None,
        [_mixed(1, 0.99), _supported(2, 0.05)],
        require_user_evidence=True,
    )

    for host in (full_cache_host, session_cache_host):
        results = await host.search_memories(
            _QUERY, k=1, session_id=_SESSION, require_user_evidence=True
        )
        assert [item.doc_id for item in results] == [2]
