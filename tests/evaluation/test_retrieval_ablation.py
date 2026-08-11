"""隔离检索消融能力契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _engine(
    *, hop: int = 1, reranker: object | None = None, vector_access: bool = True
):
    """构造覆盖 config、retriever、cache 和向量能力的轻量引擎。"""

    config = {
        "recall_engine.chain_graph_expansion_enabled": True,
        "recall_engine.chain_topic_expansion_enabled": True,
        "memory_evolution": {"enabled": True, "mode": "disabled"},
        "reranker.mmr_lambda": 0.7,
        "reranker.embedding_similarity_lambda": 0.7,
    }
    hybrid = SimpleNamespace(mmr_lambda=0.7, config=config)
    keyword = SimpleNamespace(expansion_hops=hop)
    graph = SimpleNamespace(keyword_retriever=keyword, config=config)
    dual = SimpleNamespace(
        reranker=reranker if reranker is not None else SimpleNamespace(kind="custom"),
        document_retriever=hybrid,
        graph_retriever=graph,
        config=config,
    )
    from core.shared.adapter_capabilities import (
        ASTRBOT_FAISS_CAPABILITIES,
        AdapterCapability,
        AdapterCapabilityContract,
        AdapterKind,
    )

    vector_contract = ASTRBOT_FAISS_CAPABILITIES
    if not vector_access:
        vector_contract = AdapterCapabilityContract(
            kind=AdapterKind.VECTOR_BACKEND,
            native=frozenset({AdapterCapability.EMBEDDING}),
        )
    faiss_db = SimpleNamespace(
        encode_query=lambda _query: [1.0],
        adapter_capabilities=vector_contract,
    )
    if vector_access:
        faiss_db.get_vector = lambda _doc_id: [1.0]
    retrieval = SimpleNamespace(
        _config=config,
        _cache={"baseline": [1]},
        _session_cache={"session": [1]},
        _dual_route_retriever=dual,
    )

    class Engine:
        async def search_memories(self, **_kwargs):
            """返回空检索结果以验证快照绑定。"""

            return []

    engine = Engine()
    engine.config = config
    engine.hybrid_retriever = hybrid
    engine.graph_keyword_retriever = keyword
    engine.graph_retriever = graph
    engine.dual_route_retriever = dual
    engine.faiss_db = faiss_db
    engine._retrieval = retrieval
    return engine


def test_descriptors_report_available_and_equivalent_variants() -> None:
    """能力描述应区分可运行与等价变体。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    descriptors = {
        item["name"]: item
        for item in RetrievalAblationController(_engine(hop=1)).descriptors()
    }

    assert descriptors["baseline"]["available"] is True
    assert descriptors["final_reranker_off"]["available"] is True
    assert descriptors["graph_neighbors_1_hop"] == {
        "name": "graph_neighbors_1_hop",
        "available": False,
        "reason_code": "equivalent_to_baseline",
        "default_selected": False,
    }
    assert descriptors["graph_neighbors_2_hops"]["available"] is True


def test_descriptors_require_engine_and_tolerate_malformed_live_state() -> None:
    """缺失引擎应 fail closed，畸形组件属性不得破坏能力列表。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RETRIEVAL_VARIANT_NAMES,
        RetrievalAblationController,
    )

    missing = RetrievalAblationController(None).descriptors()
    assert all(item["reason_code"] == "missing_engine" for item in missing)

    engine = _engine()
    engine.graph_keyword_retriever.expansion_hops = object()
    malformed_mmr = type("MMRReranker", (), {})()
    malformed_mmr._lambda = object()
    engine.dual_route_retriever.reranker = malformed_mmr
    descriptors = RetrievalAblationController(engine).descriptors()
    assert len(descriptors) == len(RETRIEVAL_VARIANT_NAMES)


def test_descriptors_reject_config_and_reranker_no_ops() -> None:
    """已经处于目标状态的配置与 MMR 变体必须标记为 baseline 等价。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )
    from core.retrieval.reranker_factory import MMRReranker

    engine = _engine(reranker=MMRReranker(0.7))
    engine.config["recall_engine.chain_graph_expansion_enabled"] = False
    descriptors = {
        item["name"]: item for item in RetrievalAblationController(engine).descriptors()
    }

    assert descriptors["graph_expansion_off"]["reason_code"] == (
        "equivalent_to_baseline"
    )
    assert descriptors["final_reranker_mmr"]["reason_code"] == (
        "equivalent_to_baseline"
    )


def test_embedding_variant_requires_document_vector_access() -> None:
    """embedding-similarity 缺少稳定文档向量访问时必须禁用。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    descriptors = {
        item["name"]: item
        for item in RetrievalAblationController(
            _engine(vector_access=False)
        ).descriptors()
    }

    descriptor = descriptors["final_reranker_embedding_similarity"]
    assert descriptor["available"] is False
    assert descriptor["reason_code"] == "missing_document_vector_access"


def test_embedding_variant_rejects_unknown_adapter_with_matching_methods() -> None:
    """未知 adapter 即使方法同名也不能被推断为支持向量访问。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    engine = _engine()
    engine.faiss_db = SimpleNamespace(
        encode_query=lambda _query: [1.0],
        get_vector=lambda _doc_id: [1.0],
    )
    descriptor = {
        item["name"]: item for item in RetrievalAblationController(engine).descriptors()
    }["final_reranker_embedding_similarity"]

    assert descriptor["available"] is False
    assert descriptor["reason_code"] == "missing_document_vector_access"


def test_prepare_copies_components_config_and_caches() -> None:
    """快照应复制可变组件和缓存，而不污染 live engine。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    live = _engine(hop=1)
    prepared = RetrievalAblationController(live).prepare("graph_neighbors_2_hops")

    assert prepared.available is True
    assert prepared.engine is not live
    assert prepared.engine.graph_keyword_retriever.expansion_hops == 2
    assert live.graph_keyword_retriever.expansion_hops == 1
    assert prepared.engine.config is not live.config
    assert prepared.engine._retrieval._cache == {}
    assert prepared.engine._retrieval._session_cache == {}
    assert live._retrieval._cache == {"baseline": [1]}


def test_prepare_config_variant_does_not_mutate_live_engine() -> None:
    """配置类变体只能修改 snapshot config。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    live = _engine()
    prepared = RetrievalAblationController(live).prepare("graph_expansion_off")

    assert (
        prepared.engine.config["recall_engine.chain_graph_expansion_enabled"] is False
    )
    assert live.config["recall_engine.chain_graph_expansion_enabled"] is True
    assert prepared.effective_settings == {
        "chain_graph_expansion_enabled": False,
    }


def test_prepare_unknown_or_unavailable_variant_is_skipped() -> None:
    """未知或缺少能力的变体应返回稳定不可用结果。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    controller = RetrievalAblationController(_engine(vector_access=False))

    unknown = controller.prepare("unknown")
    missing = controller.prepare("final_reranker_embedding_similarity")

    assert unknown.available is False
    assert unknown.reason_code == "unknown_variant"
    assert missing.available is False
    assert missing.engine is None


@pytest.mark.asyncio
async def test_snapshot_search_uses_snapshot_method_binding() -> None:
    """快照的 search_memories 必须绑定到 snapshot 本身。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    live = _engine()
    prepared = RetrievalAblationController(live).prepare("baseline")

    assert await prepared.engine.search_memories(query="synthetic") == []


def test_snapshot_disables_retrieval_optimizer_canonical_writes() -> None:
    """评测快照必须切断 RetrievalOptimizer 的 canonical 写回。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    live = _engine()
    live_update = object()
    live_create_task = object()
    live._retrieval._update_memory = live_update
    live._retrieval._create_tracked_task = live_create_task

    prepared = RetrievalAblationController(live).prepare("baseline")

    assert prepared.engine._retrieval._update_memory is None
    assert callable(prepared.engine._retrieval._create_tracked_task)
    assert live._retrieval._update_memory is live_update
    assert live._retrieval._create_tracked_task is live_create_task


def test_prepare_failure_uses_stable_reason_without_exception_text() -> None:
    """快照构造失败只能暴露稳定 reason code。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    class BrokenEngine:
        config = {}

        def __copy__(self):
            """模拟不能复制的 live engine。"""

            raise RuntimeError("sensitive-provider-path")

    prepared = RetrievalAblationController(BrokenEngine()).prepare("baseline")

    assert prepared.available is False
    assert prepared.reason_code == "variant_prepare_failed"
    assert "sensitive-provider-path" not in prepared.reason_code


def test_descriptors_reject_noop_config_and_reranker_variants() -> None:
    """已经生效的配置或同权重 reranker 不得再次标记为可消融。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )
    from core.retrieval.embedding_similarity_reranker import (
        EmbeddingSimilarityReranker,
    )
    from core.retrieval.reranker_factory import MMRReranker

    graph_disabled = _engine()
    graph_disabled.config["recall_engine.chain_graph_expansion_enabled"] = False
    graph_descriptors = {
        item["name"]: item
        for item in RetrievalAblationController(graph_disabled).descriptors()
    }
    assert graph_descriptors["graph_expansion_off"]["reason_code"] == (
        "equivalent_to_baseline"
    )

    mmr_engine = _engine(reranker=MMRReranker(0.7))
    mmr_descriptors = {
        item["name"]: item
        for item in RetrievalAblationController(mmr_engine).descriptors()
    }
    assert mmr_descriptors["final_reranker_mmr"]["reason_code"] == (
        "equivalent_to_baseline"
    )

    embedding_engine = _engine()
    embedding_engine.dual_route_retriever.reranker = EmbeddingSimilarityReranker(
        embedding_engine.faiss_db,
        0.7,
    )
    embedding_descriptors = {
        item["name"]: item
        for item in RetrievalAblationController(embedding_engine).descriptors()
    }
    assert (
        embedding_descriptors["final_reranker_embedding_similarity"]["reason_code"]
        == "equivalent_to_baseline"
    )


def test_embedding_variant_does_not_silently_fallback_on_vector_failure() -> None:
    """向量执行失败必须成为稳定不可用错误，不能静默回退 MMR。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )
    from core.retrieval.rrf_fusion import HybridResult

    live = _engine()

    def fail_encode(_query: str):
        """模拟运行期 query embedding 失败。"""

        raise RuntimeError("sensitive-vector-provider")

    live.faiss_db.encode_query = fail_encode
    prepared = RetrievalAblationController(live).prepare(
        "final_reranker_embedding_similarity"
    )
    reranker = prepared.engine.dual_route_retriever.reranker
    candidates = [
        HybridResult(1, 0.9, 0.9, None, None, "候选一", {}),
        HybridResult(2, 0.8, 0.8, None, None, "候选二", {}),
    ]

    reranker.rerank(candidates, 1, query="查询")

    assert prepared.execution_reason_code() == "embedding_query_failed"
    assert "sensitive-vector-provider" not in prepared.execution_reason_code()


def test_embedding_probe_failure_is_sticky_across_later_success() -> None:
    """前一个用例的 embedding 失败不能被后续成功覆盖。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )
    from core.retrieval.rrf_fusion import HybridResult

    live = _engine()
    prepared = RetrievalAblationController(live).prepare(
        "final_reranker_embedding_similarity"
    )
    reranker = prepared.engine.dual_route_retriever.reranker
    candidates = [
        HybridResult(1, 0.9, 0.9, None, None, "候选一", {}),
        HybridResult(2, 0.8, 0.8, None, None, "候选二", {}),
    ]
    live.faiss_db.encode_query = lambda _query: (_ for _ in ()).throw(
        RuntimeError("query-failed")
    )
    reranker.rerank(candidates, 1, query="查询")
    prepared.engine.faiss_db.encode_query = lambda _query: [1.0]
    reranker.rerank(candidates, 1, query="查询")

    assert prepared.execution_reason_code() == "embedding_query_failed"
    assert reranker.success_count == 1


def test_embedding_probe_success_is_not_reset_by_unexercised_case() -> None:
    """成功执行后，未触发目标策略的用例不能重置 available 状态。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )
    from core.retrieval.rrf_fusion import HybridResult

    prepared = RetrievalAblationController(_engine()).prepare(
        "final_reranker_embedding_similarity"
    )
    reranker = prepared.engine.dual_route_retriever.reranker
    candidates = [
        HybridResult(1, 0.9, 0.9, None, None, "候选一", {}),
        HybridResult(2, 0.8, 0.8, None, None, "候选二", {}),
    ]
    reranker.rerank(candidates, 1, query="查询")
    reranker.rerank(candidates[:1], 1, query="查询")

    assert prepared.execution_reason_code() == "available"


@pytest.mark.asyncio
async def test_snapshot_shallow_copies_real_memory_evolution_store_config(
    tmp_path,
) -> None:
    """真实 MemoryEvolutionStore 配置也必须能创建只读快照。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )
    from core.storage.memory_evolution_store import MemoryEvolutionStore

    store = MemoryEvolutionStore(str(tmp_path / "evolution.db"))
    await store.initialize()
    live = _engine()
    live.config["memory_evolution"]["store"] = store
    live.dual_route_retriever.derived_expander = object()

    prepared = RetrievalAblationController(live).prepare("B")

    try:
        assert prepared.available is True
        assert prepared.engine.config is not live.config
        assert (
            prepared.engine.config["memory_evolution"]
            is not live.config["memory_evolution"]
        )
        assert prepared.engine.config["memory_evolution"]["store"] is store
    finally:
        await store.close()


def test_prepare_failure_is_stable_and_cancellation_propagates() -> None:
    """普通快照失败只返回 reason code，取消信号必须继续传播。"""

    from core.features.evaluation.application.retrieval_ablation import (
        RetrievalAblationController,
    )

    class BrokenEngine:
        config = {}

        def __copy__(self):
            """模拟不含敏感输出的普通复制失败。"""

            raise RuntimeError("sensitive implementation detail")

    failed = RetrievalAblationController(BrokenEngine()).prepare("baseline")
    assert failed.available is False
    assert failed.reason_code == "variant_prepare_failed"

    class CancelledEngine:
        config = {}

        def __copy__(self):
            """模拟任务在创建快照时被取消。"""

            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        RetrievalAblationController(CancelledEngine()).prepare("baseline")
