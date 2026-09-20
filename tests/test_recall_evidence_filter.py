"""证据需求请求在引擎与双路检索上的预截断证据门契约。

普通召回只能保留完整正文可逐事实归属用户来源的候选；``require_user_evidence``
为真的请求必须在重排、k 截断与缓存读写之前完成该判定，否则高分混合/assistant
候选会先占用槽位，再被后置选择门丢弃。

同一平面还覆盖「事实文本单 owner」的读取兜底：正文改写后残留的旧事实不再
属于当前 canonical，图抽取必须按「无事实元数据」回落正文而不是继续派生。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.fact_text_alignment import (
    FactTextAlignment,
    fact_in_content,
    facts_aligned,
    normalize_fact,
)
from core.features.memory.application.memory_engine_crud import MemoryEngineCRUDMixin
from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.recall.processors.graph_extractor import GraphExtractor
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


class _CanonicalRows:
    """缓存命中重校验的最小 canonical 替身：按 ID 返回当前正文与 metadata。"""

    def __init__(self, rows: dict[int, tuple[str, dict]]) -> None:
        self._rows = rows

    async def get_documents(
        self,
        metadata_filters: dict | None = None,
        ids: list[int] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict]:
        """复刻宿主文档存储的按 ID 批量读取形状。"""

        del metadata_filters, offset
        docs = [
            {
                "id": int(doc_id),
                "text": self._rows[int(doc_id)][0],
                "metadata": self._rows[int(doc_id)][1],
            }
            for doc_id in ids or []
            if int(doc_id) in self._rows
        ]
        return docs[: int(limit)] if limit is not None else docs


class _SearchHost(MemoryEngineCRUDMixin):
    """只装配 search_memories 所需协作对象的最小引擎宿主。"""

    def __init__(
        self,
        dual_route: Any,
        canonical_rows: dict[int, tuple[str, dict]] | None = None,
    ) -> None:
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
        if canonical_rows is not None:
            # 缓存命中重校验按可观察状态回读 canonical；只给需要命中缓存的用例装配。
            self.faiss_db = SimpleNamespace(
                document_storage=_CanonicalRows(canonical_rows)
            )


def _host_without_retrieval(
    canonical_rows: dict[int, tuple[str, dict]] | None = None,
) -> _SearchHost:
    """构造证据请求命中缓存后不得再调用检索器的最小宿主。"""

    dual = AsyncMock()
    dual.search = AsyncMock(side_effect=AssertionError("证据请求必须直接使用已有缓存"))
    return _SearchHost(dual, canonical_rows)


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

    canonical_rows = {
        1: (_mixed(1, 0.99).content, _mixed_metadata()),
        2: (_supported(2, 0.05).content, candidate_evidence_metadata(_USER_FACT)),
    }
    full_cache_host = _host_without_retrieval(canonical_rows)
    full_cache_host._retrieval.set_cached(
        full_cache_host._retrieval.cache_key(
            _QUERY, 1, _SESSION, None, require_user_evidence=True
        ),
        [_mixed(1, 0.99), _supported(2, 0.05)],
    )
    session_cache_host = _host_without_retrieval(canonical_rows)
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


_FACT_A = "用户习惯手冲咖啡"
_FACT_B = "用户每周游泳两次"
_GRAPH_BOUNDARY: dict[str, Any] = {
    "scope_key": "scope-fact-text",
    "privacy_level": "public",
    "revision_token": "r1",
}


def _v2_metadata(
    facts: list[str], *, summary: str | None = None, **fields: Any
) -> dict[str, Any]:
    """构造带逐事实用户证据的 v2 事实 metadata。"""

    return {
        **_GRAPH_BOUNDARY,
        "key_facts": facts,
        "fact_source_evidence": [[resolved_reference(fact)] for fact in facts],
        "canonical_summary": summary if summary is not None else "；".join(facts),
        **fields,
    }


def _graph_text(graph: Any) -> str:
    """拼接图快照的可检索文本与 metadata，用于「旧事实不得出现」断言。"""

    return "\n".join(
        [node.value for node in graph.nodes]
        + [entry.content for entry in graph.entries]
        + [str(entry.metadata) for entry in graph.entries]
        + [str(edge.metadata) for edge in graph.edges]
    )


@pytest.mark.parametrize(
    "content, expected",
    [
        ("用户习惯手冲咖啡；用户每周游泳两次", FactTextAlignment.ALIGNED),
        ("用户习惯手冲咖啡", FactTextAlignment.MISALIGNED),
        ("用户已经改喝气泡水", FactTextAlignment.MISALIGNED),
        ("", FactTextAlignment.UNDETERMINABLE),
    ],
)
def test_facts_aligned_compares_normalized_entries_against_body(
    content: str, expected: FactTextAlignment
) -> None:
    """逐条比对正文；缺正文、缺事实或证据未一一对应都按不可判定处理。"""

    facts = [_FACT_A, _FACT_B]
    evidence = [[resolved_reference(fact)] for fact in facts]

    assert facts_aligned(content, facts, evidence) is expected
    assert facts_aligned(content, facts, None) is FactTextAlignment.UNDETERMINABLE
    assert facts_aligned(content, [], evidence) is FactTextAlignment.UNDETERMINABLE
    assert (
        facts_aligned(content, facts, [*evidence, evidence[0]])
        is FactTextAlignment.UNDETERMINABLE
    )


def test_fact_normalization_folds_width_case_and_whitespace() -> None:
    """全角、大小写与空白差异不改变事实归属判定。"""

    assert normalize_fact("  ＳＱＬｉｔｅ　存储  ") == "sqlite 存储"
    assert fact_in_content("正文：SQLite 存储", "ＳＱＬｉｔｅ　存储") is True
    assert fact_in_content("正文：SQLite 存储", "PostgreSQL 存储") is False
    assert fact_in_content("", _FACT_A) is False


def test_graph_extractor_drops_facts_misaligned_with_current_body(caplog) -> None:
    """正文改写后残留的旧事实不得进入图节点、条目或 metadata。"""

    stale = "用户已经搬到上海"
    body = "用户现在住在杭州"
    caplog.set_level(logging.WARNING)

    graph = GraphExtractor().extract(
        1, body, _v2_metadata([stale], summary=stale, topics=["居住地"])
    )

    rendered = _graph_text(graph)
    assert stale not in rendered
    assert body in rendered
    # 回落而非拒绝整条记忆：canonical 正文成为唯一事实文本。
    assert [node.value for node in graph.nodes if node.node_type == "fact"] == [body]
    assert "事实元数据与当前正文不一致" in caplog.text


def test_graph_extractor_keeps_aligned_fact_metadata() -> None:
    """条目仍属于正文时保持既有派生：事实节点与摘要都来自记录的事实。"""

    body = "；".join([_FACT_A, _FACT_B])

    graph = GraphExtractor().extract(1, body, _v2_metadata([_FACT_A, _FACT_B]))

    assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
        _FACT_A,
        _FACT_B,
    }
    assert all(
        entry.metadata.get("canonical_summary") == body for entry in graph.entries
    )


def test_graph_extractor_drops_unpaired_legacy_facts_outside_body(caplog) -> None:
    """无逐事实证据的历史行同样按正文成员性准入：事实已不在正文中即回落正文。

    旧期望（保留 ``key_facts`` 的既有派生）与「事实文本单 owner」不变量冲突：
    ``旧式事实`` 已不在正文 ``旧式叙述正文`` 中，保留派生即等于把旧事实继续
    当作可检索事实。此处改为断言新不变量：无该 fact 节点，正文成为唯一事实文本，
    回落可观察（警告计数）。
    """

    stale = "旧式事实"
    body = "旧式叙述正文"
    caplog.set_level(logging.WARNING)

    graph = GraphExtractor().extract(1, body, {**_GRAPH_BOUNDARY, "key_facts": [stale]})

    assert all(node.value != stale for node in graph.nodes)
    assert [node.value for node in graph.nodes if node.node_type == "fact"] == [body]
    assert stale not in _graph_text(graph)
    assert "事实元数据与当前正文不一致" in caplog.text


def test_graph_extractor_keeps_unpaired_legacy_facts_inside_body() -> None:
    """历史行的事实仍在正文中时保持既有派生，回落只针对已失效的事实表示。"""

    body = "用户提到旧式事实并继续讨论"
    graph = GraphExtractor().extract(
        1, body, {**_GRAPH_BOUNDARY, "key_facts": ["旧式事实"]}
    )

    assert any(node.value == "旧式事实" for node in graph.nodes)


def test_structured_graph_uses_current_body_when_summary_mismatches(caplog) -> None:
    """结构化图载荷的摘要已不在正文中时，图条目改回 canonical 正文。"""

    stale = "用户昨天在加班"
    body = "用户今天完成了十公里慢跑"
    caplog.set_level(logging.DEBUG)

    graph = GraphExtractor().extract(
        1,
        body,
        {
            **_GRAPH_BOUNDARY,
            "canonical_summary": stale,
            "graph_extraction": {
                "entities": [{"name": "慢跑", "type": "event"}],
                "relations": [],
            },
        },
    )

    assert any(node.value == "慢跑" for node in graph.nodes)
    assert all(
        entry.metadata.get("canonical_summary") == body for entry in graph.entries
    )
    assert stale not in _graph_text(graph)
    assert "事实摘要与当前正文不一致" in caplog.text
