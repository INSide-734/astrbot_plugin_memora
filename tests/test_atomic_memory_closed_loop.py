"""原子证据参与普通召回的闭环契约测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.features.recall.processors.memory_processor import MemoryProcessor
from core.features.recall.processors.text_processor import TextProcessor
from core.retrieval.atom_retriever import AtomRetrievalResult, AtomRetriever
from core.retrieval.dual_route_retriever import DualRouteRetriever
from core.retrieval.graph_keyword_retriever import GraphKeywordResult
from core.retrieval.graph_retriever import GraphRetriever
from core.retrieval.rrf_fusion import HybridResult, RRFFusion
from core.storage.atom_store import AtomStore


def _atom_result(
    atom_id: int,
    parent_memory_id: int,
    score: float,
) -> AtomRetrievalResult:
    """构造只在检索器内部使用的原子证据结果。"""

    return AtomRetrievalResult(
        atom_id=atom_id,
        parent_memory_id=parent_memory_id,
        content="用户只喝无糖燕麦拿铁",
        base_score=score,
        temporal_score=1.0,
        final_score=score,
        atom_type="preference",
        importance=0.8,
        confidence=0.9,
        ttl_days=60.0,
        decay_type="exponential",
        metadata={"internal_atom_marker": "不得外泄"},
    )


def _canonical_result(doc_id: int = 7) -> HybridResult:
    """构造文档路的 canonical 基线结果。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=0.8,
        rrf_score=0.02,
        bm25_score=0.8,
        vector_score=None,
        content="canonical 基线正文",
        metadata={"privacy_level": "shared"},
        score_breakdown={},
    )


def _make_dual(
    *,
    atom_results: list[AtomRetrievalResult] | None = None,
    atom_error: BaseException | None = None,
    canonical_metadata: dict | None = None,
) -> tuple[DualRouteRetriever, AsyncMock, AsyncMock, AsyncMock]:
    """构造带 Atom 路的三路召回器及其可断言替身。"""

    document = AsyncMock()
    document.search = AsyncMock(return_value=[])
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    atom = AsyncMock()
    if atom_error is None:
        atom.search = AsyncMock(return_value=atom_results or [])
    else:
        atom.search = AsyncMock(side_effect=atom_error)
    atom.touch_many = AsyncMock()
    loader = AsyncMock(
        return_value={
            "text": "父 canonical 完整正文",
            "metadata": canonical_metadata
            or {"privacy_level": "shared", "source": "canonical"},
        }
    )
    retriever = DualRouteRetriever(
        document_retriever=document,
        graph_retriever=graph,
        memory_loader=loader,
        atom_retriever=atom,
    )
    return retriever, document, atom, loader


@pytest.mark.asyncio
async def test_atom_only_hit_returns_parent_canonical_without_internal_ids() -> None:
    """Atom-only 命中必须去重并返回父 canonical，而不是 Atom 自身。"""

    retriever, _, atom, loader = _make_dual(
        atom_results=[_atom_result(101, 42, 0.9), _atom_result(102, 42, 0.7)]
    )

    results = await retriever.search(
        "那种燕麦饮品",
        k=5,
        session_id="private:user-a",
        persona_id="persona-a",
    )

    assert len(results) == 1
    assert results[0].doc_id == 42
    assert results[0].content == "父 canonical 完整正文"
    assert results[0].metadata == {
        "privacy_level": "shared",
        "source": "canonical",
    }
    assert "atom" not in repr(results[0].metadata).lower()
    assert "atom" not in repr(results[0].score_breakdown).lower()
    loader.assert_awaited_once_with(42)
    atom.touch_many.assert_awaited_once_with([101, 102])


@pytest.mark.asyncio
async def test_atom_failure_keeps_document_baseline() -> None:
    """Atom 普通异常只能降级，不能丢失已有 canonical 文档结果。"""

    retriever, document, atom, _ = _make_dual(atom_error=RuntimeError("atom down"))
    document.search.return_value = [_canonical_result()]

    results = await retriever.search("baseline", k=5)

    assert [item.doc_id for item in results] == [7]
    atom.touch_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_atom_cancellation_propagates() -> None:
    """Atom 路取消必须向上传播，不能伪装成普通降级。"""

    retriever, _, _, _ = _make_dual(atom_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await retriever.search("cancel", k=5)


@pytest.mark.asyncio
async def test_filtered_private_parent_is_not_touched_in_group_chat() -> None:
    """群聊隐私过滤淘汰的父记忆不得产生 Atom 访问反馈。"""

    retriever, _, atom, _ = _make_dual(
        atom_results=[_atom_result(201, 55, 0.9)],
        canonical_metadata={"privacy_level": "confidential"},
    )

    results = await retriever.search("secret", k=5, chat_type="group")

    assert results == []
    atom.touch_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_atom_pipeline_recalls_parent_canonical(tmp_db_path) -> None:
    """真实处理、存储与检索链应以 Atom 证据召回父 canonical。"""

    processor = MemoryProcessor(
        config={
            "atom_enabled": True,
            "atom_quality_filter_enabled": True,
            "atom_min_confidence": 0.65,
            "atom_min_importance": 0.3,
            "atom_min_content_length": 5,
            "atom_info_check_enabled": True,
        }
    )
    atoms = processor.classify_atoms_from_metadata(
        {
            "key_facts": ["用户只喝无糖燕麦拿铁"],
            "topics": ["饮品偏好"],
            "participants": ["用户"],
            "emotional_intensity": 0.7,
        },
        parent_importance=0.8,
        session_id="private:user-a",
        persona_id="persona-a",
    )
    assert len(atoms) == 1
    atoms[0].parent_memory_id = 42

    store = AtomStore(str(tmp_db_path))
    await store.initialize()
    atom_id = await store.insert(atoms[0])
    assert atom_id > 0

    document = AsyncMock()
    document.search = AsyncMock(return_value=[])
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    loader = AsyncMock(
        return_value={
            "text": "用户曾明确说自己只喝无糖燕麦拿铁。",
            "metadata": {"privacy_level": "shared", "source": "canonical"},
        }
    )
    retriever = DualRouteRetriever(
        document_retriever=document,
        graph_retriever=graph,
        memory_loader=loader,
        atom_retriever=AtomRetriever(store, text_processor=TextProcessor()),
    )

    results = await retriever.search(
        "他喜欢什么拿铁",
        k=5,
        session_id="private:user-a",
        persona_id="persona-a",
    )

    assert [item.doc_id for item in results] == [42]
    assert results[0].content == "用户曾明确说自己只喝无糖燕麦拿铁。"
    assert results[0].metadata == {
        "privacy_level": "shared",
        "source": "canonical",
    }
    assert "atom" not in repr(results[0].metadata).lower()
    assert "atom" not in repr(results[0].score_breakdown).lower()
    loader.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_graph_retriever_uses_request_reference_time_and_expiry() -> None:
    """图路应按请求时钟计算非零时间因子，并过滤已过期 Atom 条目。"""

    reference_epoch = 1_735_689_600.0
    reference_time = datetime.fromtimestamp(reference_epoch, tz=timezone.utc)
    metadata = {
        "importance": 0.8,
        "graph_confidence": 0.9,
        "create_time": reference_epoch - 86400.0,
        "expires_at": reference_epoch + 86400.0,
        "ttl_days": 10.0,
        "decay_type": "exponential",
    }
    keyword = AsyncMock()
    keyword.search = AsyncMock(
        return_value=[
            GraphKeywordResult(
                doc_id=8,
                score=0.9,
                content="图原子命中",
                metadata=metadata,
            )
        ]
    )
    vector = AsyncMock()
    vector.search = AsyncMock(return_value=[])
    retriever = GraphRetriever(keyword, vector, RRFFusion())

    active = await retriever.search("图原子", reference_time=reference_time)
    expired = await retriever.search(
        "图原子",
        reference_time=datetime.fromtimestamp(
            reference_epoch + 2 * 86400.0,
            tz=timezone.utc,
        ),
    )

    assert active
    assert active[0].score_breakdown["graph_temporal_factor"] > 0.5
    assert expired == []


def test_atom_graph_entry_contains_complete_time_snapshot() -> None:
    """Atom 图条目必须携带可重建的创建、过期与衰减快照。"""

    from core.features.recall.processors.atom_graph_extractor import (
        extract_graph_from_atoms,
    )

    atom = SimpleNamespace(
        content="用户下周参加读书会",
        confidence=0.9,
        session_id="s1",
        persona_id="p1",
        entities=["读书会"],
        atom_type="planned",
        importance=0.8,
        ttl_days=7.0,
        created_at=1000.0,
        event_time=2000.0,
        expires_at=3000.0,
        decay_type=SimpleNamespace(value="step"),
    )

    graph = extract_graph_from_atoms(
        12,
        [atom],
        {"participants": []},
        temporal_edges_enabled=False,
        causal_edges_enabled=False,
    )

    assert graph.entries
    for entry in graph.entries:
        assert entry.metadata["create_time"] == 1000.0
        assert entry.metadata["expires_at"] == 3000.0
        assert entry.metadata["decay_type"] == "step"
