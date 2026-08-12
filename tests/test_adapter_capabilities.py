"""Provider、Store 与 Retriever 能力契约回归。"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from math import inf, nan
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from core.shared.adapter_capabilities import AdapterCapabilityContract


def test_reranker_factory_uses_shared_mmr_implementation() -> None:
    """重排工厂必须直接使用 shared 中的唯一 MMR 实现。"""

    from core.retrieval import reranker_factory
    from core.shared import mmr as shared_mmr

    assert reranker_factory.MMRReranker is shared_mmr.MMRReranker


def _contract(
    *,
    kind: Any = "vector_backend",
    native: tuple[Any, ...] = (),
    caller_enforced: tuple[Any, ...] = (),
    score: Any = None,
) -> AdapterCapabilityContract:
    """使用待规范化的原始值构建最小能力契约。"""

    from core.shared.adapter_capabilities import AdapterCapabilityContract

    return AdapterCapabilityContract(
        kind=kind,
        native=frozenset(native),
        caller_enforced=frozenset(caller_enforced),
        score=score,
    )


def _hybrid_result(doc_id: int, score: float = 0.9):
    """构造 DualRoute 测试需要的 canonical 候选。"""

    from core.retrieval.rrf_fusion import HybridResult

    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=f"canonical-{doc_id}",
        metadata={},
    )


def test_capability_contract_is_immutable_and_uses_three_levels() -> None:
    """能力契约必须不可变，并区分 native/caller-enforced/unsupported。"""

    from core.shared.adapter_capabilities import AdapterCapability, SupportLevel

    contract = _contract(
        native=(AdapterCapability.SCORING,),
        caller_enforced=(AdapterCapability.FILTERING,),
        score={
            "direction": "higher_is_better",
            "minimum": 0.0,
            "maximum": 1.0,
            "normalization": "per_query",
        },
    )

    assert contract.level(AdapterCapability.SCORING) is SupportLevel.NATIVE
    assert contract.level(AdapterCapability.FILTERING) is SupportLevel.CALLER_ENFORCED
    assert contract.level(AdapterCapability.DELETE) is SupportLevel.UNSUPPORTED
    with pytest.raises(FrozenInstanceError):
        setattr(contract, "kind", "other")


def test_contract_rejects_overlap_and_invalid_score_range() -> None:
    """同一能力不能同时属于两种等级，score range 必须有限且有序。"""

    from core.shared.adapter_capabilities import (
        AdapterCapability,
        NormalizationScope,
        ScoreDirection,
        ScoreSemantics,
    )

    with pytest.raises(ValueError, match="capability_level_overlap"):
        _contract(
            native=(AdapterCapability.FILTERING,),
            caller_enforced=(AdapterCapability.FILTERING,),
        )
    with pytest.raises(ValueError, match="score_range_invalid"):
        ScoreSemantics(
            direction=ScoreDirection.HIGHER_IS_BETTER,
            minimum=1.0,
            maximum=0.0,
            normalization=NormalizationScope.BACKEND,
        )
    for invalid in (nan, inf):
        with pytest.raises(ValueError, match="score_range_invalid"):
            ScoreSemantics(
                direction=ScoreDirection.HIGHER_IS_BETTER,
                minimum=0.0,
                maximum=invalid,
                normalization=NormalizationScope.BACKEND,
            )


def test_unknown_adapter_is_conservative_and_error_is_safe() -> None:
    """未知 adapter 默认关闭能力，错误只包含固定枚举。"""

    from core.shared.adapter_capabilities import (
        AdapterCapability,
        UnsupportedAdapterCapability,
        adapter_contract,
        require_capability,
    )

    contract = adapter_contract(object())
    assert not contract.supports(AdapterCapability.FILTERING)
    with pytest.raises(UnsupportedAdapterCapability) as captured:
        require_capability(object(), AdapterCapability.DELETE)
    error = captured.value
    assert error.reason_code == "adapter_capability_unsupported"
    assert "query" not in error.safe_details
    assert "provider_id" not in error.safe_details


@pytest.mark.asyncio
async def test_llm_adapter_requires_text_chat_and_propagates_cancellation() -> None:
    """LLM adapter 构建时冻结 text_chat，取消信号必须传播。"""

    from core.platform.provider.adapters import LLMProviderAdapter

    with pytest.raises(RuntimeError, match="adapter_capability_unsupported"):
        LLMProviderAdapter.from_provider(MagicMock(spec=[]))

    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=asyncio.CancelledError())
    adapter = LLMProviderAdapter.from_provider(provider)
    with pytest.raises(asyncio.CancelledError):
        await adapter.generate("提示", "系统约束")


@pytest.mark.asyncio
async def test_component_factory_rejects_missing_provider_capability_before_io(
    tmp_path,
) -> None:
    """启动期能力校验失败时不得进入索引检查或数据库初始化。"""

    from astrbot.core.provider.provider import Provider

    from core.platform.composition.component_factory import ComponentFactory
    from core.shared.errors import ProviderNotReadyError

    config = MagicMock()
    config.get.return_value = False
    config.get_section.return_value = {}
    embedding_provider = MagicMock(spec=[])
    embedding_provider.get_embeddings = AsyncMock()
    llm_provider = MagicMock(spec=Provider)
    llm_provider.text_chat = None
    faiss_checker = MagicMock()
    faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
    faiss_db_cls = MagicMock()

    factory = ComponentFactory(MagicMock(), config, str(tmp_path))
    with pytest.raises(ProviderNotReadyError, match="Provider 缺少 Memora 必需能力"):
        await factory.build_all(
            embedding_provider,
            llm_provider,
            faiss_db_cls,
            faiss_checker,
            MagicMock(),
        )

    faiss_checker.check_and_fix_dimension_mismatch.assert_not_awaited()
    faiss_db_cls.assert_not_called()


@pytest.mark.asyncio
async def test_embedding_adapter_supports_native_batch_and_single_emulation() -> None:
    """Embedding adapter 必须保持 native batch 和逐项模拟的输入顺序。"""

    from core.platform.provider.adapters import EmbeddingProviderAdapter
    from core.shared.adapter_capabilities import AdapterCapability, SupportLevel

    native = MagicMock(spec=[])
    native.get_embeddings = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
    native_adapter = EmbeddingProviderAdapter.from_provider(native)
    assert await native_adapter.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert (
        native_adapter.adapter_capabilities.level(AdapterCapability.BATCH_READ)
        is SupportLevel.NATIVE
    )

    single = MagicMock(spec=[])
    calls: list[str] = []

    async def get_embedding(text: str):
        calls.append(text)
        return [float(len(text)), 1.0]

    single.get_embedding = get_embedding
    single_adapter = EmbeddingProviderAdapter.from_provider(single)
    assert await single_adapter.embed(["a", "bb"]) == [[1.0, 1.0], [2.0, 1.0]]
    assert calls == ["a", "bb"]
    assert (
        single_adapter.adapter_capabilities.level(AdapterCapability.BATCH_READ)
        is SupportLevel.CALLER_ENFORCED
    )


@pytest.mark.asyncio
async def test_embedding_batch_internal_type_error_is_not_retried_as_signature() -> (
    None
):
    """Provider 内部 TypeError 不得被误判为另一种 batch 签名。"""

    from core.platform.provider.adapters import EmbeddingProviderAdapter

    provider = MagicMock(spec=[])
    calls = 0

    async def get_embeddings_batch(contents: list[str]):
        nonlocal calls
        calls += 1
        raise TypeError("provider internal failure")

    provider.get_embeddings_batch = get_embeddings_batch
    adapter = EmbeddingProviderAdapter.from_provider(provider)
    with pytest.raises(TypeError, match="provider internal failure"):
        await adapter.embed(["a", "b"])
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vectors, reason",
    [
        ([[0.1]], "embedding_count_mismatch"),
        ([[0.1], [0.1, 0.2]], "embedding_dimension_mismatch"),
        ([[0.1], [inf]], "embedding_non_finite"),
    ],
)
async def test_embedding_adapter_rejects_invalid_result(vectors, reason) -> None:
    """Embedding 数量、统一维度和有限性必须在 adapter 边界验证。"""

    from core.platform.provider.adapters import EmbeddingProviderAdapter

    provider = MagicMock(spec=[])
    provider.get_embeddings = AsyncMock(return_value=vectors)
    adapter = EmbeddingProviderAdapter.from_provider(provider)
    with pytest.raises(RuntimeError, match=reason):
        await adapter.embed(["a", "b"])


@pytest.mark.asyncio
async def test_embedding_retry_wraps_invalid_count_without_leaking_provider_error() -> (
    None
):
    """重试边界必须保留安全原因链，并隐藏 Provider 原始错误正文。"""

    from core.platform.provider.adapters import AdapterResponseError
    from core.validators.embedding_retry import EmbeddingRetryMixin

    provider = MagicMock(spec=[])
    provider.get_embeddings = AsyncMock(return_value=[[0.1]])

    with pytest.raises(RuntimeError, match="Embedding 批次重试失败") as captured:
        await EmbeddingRetryMixin()._embed_request_with_retry(
            provider,
            ["a", "b", "c"],
            max_retries=1,
            retry_base_delay=0.001,
        )

    cause = captured.value.__cause__
    assert isinstance(cause, AdapterResponseError)
    assert cause.reason_code == "embedding_count_mismatch"
    assert "provider" not in str(captured.value).casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contents", "raw_vectors"),
    [
        (["a", "b"], "12"),
        (["a"], ["12"]),
    ],
)
async def test_embedding_adapter_rejects_string_vector_shapes(
    contents,
    raw_vectors,
) -> None:
    """字符串不得被按字符误解释为二维数值向量。"""

    from core.platform.provider.adapters import EmbeddingProviderAdapter

    provider = MagicMock(spec=[])
    provider.get_embeddings = AsyncMock(return_value=raw_vectors)

    with pytest.raises(RuntimeError, match="embedding_result_invalid"):
        await EmbeddingProviderAdapter.from_provider(provider).embed(contents)


@pytest.mark.asyncio
async def test_vector_filter_unsupported_fails_closed() -> None:
    """显式不支持 filter 的向量后端不得收到去掉 filter 的检索。"""

    from core.retrieval.vector_retriever import VectorRetriever

    backend = MagicMock()
    backend.retrieve = AsyncMock(return_value=[])
    backend.adapter_capabilities = _contract(kind="vector_backend")
    retriever = VectorRetriever(backend)

    assert await retriever.search("查询", k=5, session_id="scope-a") == []
    backend.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_filter_rechecks_backend_results_locally() -> None:
    """固定后端忽略 metadata filter 时也不得返回跨 scope 结果。"""

    from core.retrieval.vector_retriever import VectorRetriever

    wrong_scope = MagicMock()
    wrong_scope.similarity = 0.9
    wrong_scope.data = {
        "id": 7,
        "text": "不应可见",
        "metadata": {"session_id": "scope-b"},
    }
    backend = MagicMock()
    backend.retrieve = AsyncMock(return_value=[wrong_scope])

    results = await VectorRetriever(backend).search(
        "查询",
        k=5,
        session_id="scope-a",
    )

    assert results == []
    backend.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_graph_vector_filter_unsupported_fails_closed() -> None:
    """图向量后端显式不支持 filter 时不得执行底层查询。"""

    from core.retrieval.graph_vector_retriever import GraphVectorRetriever

    backend = MagicMock()
    backend.adapter_capabilities = _contract(kind="vector_backend")
    backend.retrieve = AsyncMock(return_value=[])

    results = await GraphVectorRetriever(backend).search(
        "查询",
        k=5,
        session_id="scope-a",
    )

    assert results == []
    backend.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_vector_rechecks_scope_and_rejects_non_finite_scores() -> None:
    """图向量返回侧必须复核 scope，并丢弃 NaN/Infinity 分数。"""

    from core.retrieval.graph_vector_retriever import GraphVectorRetriever

    wrong_scope = MagicMock()
    wrong_scope.similarity = 0.9
    wrong_scope.data = {
        "text": "跨 scope",
        "metadata": {"source_memory_id": 7, "session_id": "scope-b"},
    }
    non_finite = MagicMock()
    non_finite.similarity = nan
    non_finite.data = {
        "text": "坏分数",
        "metadata": {"source_memory_id": 8, "session_id": "scope-a"},
    }
    backend = MagicMock()
    backend.retrieve = AsyncMock(return_value=[wrong_scope, non_finite])

    results = await GraphVectorRetriever(backend).search(
        "查询",
        k=5,
        session_id="scope-a",
    )

    assert results == []


@pytest.mark.asyncio
async def test_vector_mutations_unsupported_do_not_touch_backend() -> None:
    """主/图向量显式不支持 update/delete 时不得读取或写入底层 Store。"""

    from core.retrieval.graph_vector_retriever import GraphVectorRetriever
    from core.retrieval.vector_retriever import VectorRetriever

    backend = MagicMock()
    backend.adapter_capabilities = _contract(kind="vector_backend")
    backend.document_storage.get_documents = AsyncMock()
    backend.delete = AsyncMock()
    vector = VectorRetriever(backend)
    graph_vector = GraphVectorRetriever(backend)

    assert await vector.update_metadata(1, {"importance": 0.5}) is False
    assert await vector.delete_document(1) is False
    assert await graph_vector.update_metadata(2, {"scope": "a"}) is False
    assert await graph_vector.delete_entry(2) is False
    backend.document_storage.get_documents.assert_not_awaited()
    backend.delete.assert_not_awaited()


def test_current_adapter_snapshots_state_real_filter_and_score_semantics() -> None:
    """当前 BM25/Vector/Derived/Store 必须公开实际而非理想化的快照。"""

    from core.features.evolution.application import (
        DerivedRelationExpander,
        ProjectionReader,
    )
    from core.features.evolution.infrastructure import MemoryEvolutionStore
    from core.retrieval.bm25_retriever import BM25Retriever
    from core.retrieval.vector_retriever import VectorRetriever
    from core.shared.adapter_capabilities import (
        AdapterCapability,
        ScoreSemantics,
        SupportLevel,
    )

    assert (
        BM25Retriever.adapter_capabilities.level(AdapterCapability.FILTERING)
        is SupportLevel.CALLER_ENFORCED
    )
    bm25_score = BM25Retriever.adapter_capabilities.score
    vector_score = VectorRetriever.adapter_capabilities.score
    assert isinstance(bm25_score, ScoreSemantics)
    assert isinstance(vector_score, ScoreSemantics)
    assert bm25_score.maximum == 1.0
    assert vector_score.maximum is None
    assert DerivedRelationExpander.adapter_capabilities.supports(
        AdapterCapability.REFERENCE_TIME
    )
    assert ProjectionReader.adapter_capabilities.supports(
        AdapterCapability.REFERENCE_TIME
    )
    assert MemoryEvolutionStore.adapter_capabilities.supports(
        AdapterCapability.BATCH_WRITE
    )


def test_current_faiss_backend_exposes_vector_access_after_fixed_adapter_setup() -> (
    None
):
    """固定 AstrBot FAISS 装配后必须向重排工厂公开向量访问能力。"""

    from core.retrieval.vector_retriever import VectorRetriever
    from core.shared.adapter_capabilities import AdapterCapability, adapter_contract

    backend = MagicMock()
    VectorRetriever(backend)

    assert adapter_contract(backend).supports(AdapterCapability.VECTOR_ACCESS)


@pytest.mark.asyncio
async def test_dual_route_skips_explicitly_unsupported_derived_reference_time() -> None:
    """Derived adapter 显式不支持 as-of 时应保留 canonical baseline。"""

    from core.retrieval.dual_route_retriever import DualRouteRetriever

    direct = _hybrid_result(1)
    document = MagicMock()
    document.search = AsyncMock(return_value=[direct])
    graph = MagicMock()
    graph.search = AsyncMock(return_value=[])
    expander = MagicMock()
    expander.adapter_capabilities = _contract(kind="derived_reader")
    expander.expand = AsyncMock(side_effect=AssertionError("不应调用"))
    retriever = DualRouteRetriever(
        document,
        graph,
        AsyncMock(),
        config={"memory_evolution": {"enabled": True, "mode": "readonly"}},
        derived_expander=expander,
    )

    results = await retriever.search("查询", k=5)
    assert [item.doc_id for item in results] == [1]
    expander.expand.assert_not_awaited()


def test_injection_provider_contract_preserves_legacy_tuple_and_safe_summary() -> None:
    """Provider 新 contract 不得破坏旧 tuple 调用方，也不得输出实例身份。"""

    from core.shared.adapter_capabilities import AdapterCapability
    from core.utils.injection_adapter import InjectionAdapter

    provider = MagicMock()
    provider.provider_config = {"type": "openai_chat_completion", "id": "secret-id"}
    provider.get_model.return_value = "private-model"
    adapter = InjectionAdapter()
    snapshot = adapter.describe_capabilities(provider)

    assert snapshot.contract.supports(AdapterCapability.TOOL_DELIVERY)
    assert adapter.capabilities(provider) == (
        "openai_chat_completion",
        "private-model",
        True,
    )
    assert "secret-id" not in str(snapshot.contract.safe_summary())
    assert "private-model" not in str(snapshot.contract.safe_summary())


def test_injection_unknown_provider_does_not_infer_runtime_capabilities() -> None:
    """未知 Provider 不得因 MagicMock 属性或方法存在而获得能力。"""

    from core.shared.adapter_capabilities import AdapterCapability
    from core.utils.injection_adapter import InjectionAdapter

    snapshot = InjectionAdapter().describe_capabilities(MagicMock())

    assert not snapshot.contract.supports(AdapterCapability.TEXT_GENERATION)
    assert not snapshot.contract.supports(AdapterCapability.CANCELLATION)
    assert not snapshot.contract.supports(AdapterCapability.TOOL_DELIVERY)


@pytest.mark.asyncio
async def test_reranker_factory_explicitly_degrades_unsupported_dependencies() -> None:
    """缺少 vector-access/同步 LLM 能力时应在工厂阶段降级 MMR。"""

    from core.retrieval.reranker_factory import MMRReranker, create_reranker

    backend = MagicMock()
    backend.adapter_capabilities = _contract(kind="vector_backend")
    reranker = await create_reranker("embedding_similarity", {}, faiss_db=backend)

    assert isinstance(reranker, MMRReranker)
    assert reranker.degradation_reason_code == "adapter_capability_unsupported"
