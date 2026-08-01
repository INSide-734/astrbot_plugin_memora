"""LIFE-04 再巩固闭环：候选不直接写 canonical，应用走 CAS，可回滚。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from core.managers.memory_engine import MemoryEngine
from core.managers.reconsolidation import ReconsolidationManager
from core.retrieval.rrf_fusion import HybridResult
from core.storage.reconsolidation_store import ReconsolidationStore


def _memory_dict(content: str, revision: str = "r-7") -> dict[str, object]:
    """构造带稳定 revision 的 canonical 视图。"""

    return {
        "text": content,
        "updated_at": revision,
        "metadata": {
            "memory_id": 7,
            "doc_id": "d7",
            "access_count": 8,
            "importance": 0.7,
        },
    }


@pytest.mark.asyncio
async def test_propose_stages_candidate_without_writing_canonical(
    tmp_path: Path,
) -> None:
    """召回入口只生成候选；LLM 修订不直接写入 canonical。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
        min_recall_count=5,
    )

    result = await manager.maybe_propose(7, context="近期上下文")

    assert result is not None
    assert result["status"] == "pending"
    candidate = await store.get_candidate(result["candidate_id"])
    assert candidate["memory_id"] == 7
    assert candidate["source_revision"] == "r-7"
    assert candidate["evidence_type"] == "llm_revision"
    assert "近期上下文" not in json.dumps(candidate, ensure_ascii=False)


@pytest.mark.asyncio
async def test_apply_candidate_rejects_stale_revision(tmp_path: Path) -> None:
    """source revision 已变化时应用必须被拒绝，不触碰 canonical。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    update_cb = AsyncMock(return_value=False)
    update_cb._last_write_reason_code = "source_revision_mismatch"

    result = await manager.apply_candidate(proposed["candidate_id"], update_cb)

    assert result["applied"] is False
    assert result["reason_code"] == "source_revision_mismatch"
    update_cb.assert_awaited_once_with(
        7,
        {"content": "修正后的记忆正文内容", "metadata": ANY},
        expected_revision="r-7",
    )
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "rejected"


@pytest.mark.asyncio
async def test_rollback_restores_old_content_with_cas(tmp_path: Path) -> None:
    """批准后的候选可按当前 revision CAS 回滚到旧正文。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await manager.apply_candidate(
        proposed["candidate_id"],
        AsyncMock(return_value=True),
    )
    get_cb = AsyncMock(
        return_value=_memory_dict("修正后的记忆正文内容", revision="r-8")
    )
    update_cb = AsyncMock(return_value=True)

    result = await manager.rollback_candidate(
        proposed["candidate_id"],
        get_memory_cb=get_cb,
        update_memory_cb=update_cb,
    )

    assert result["restored"] is True
    update_cb.assert_awaited_once()
    assert update_cb.await_args.args[1]["content"] == "原始记忆正文"
    assert update_cb.await_args.kwargs["expected_revision"] == "r-8"
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_real_engine_wires_reconsolidation_when_enabled(tmp_path: Path) -> None:
    """启用时引擎应装配 Store 与 Manager，关闭时保持 None。"""

    config = {
        "graph_memory_enabled": False,
        "recall_engine.stopwords_path": "",
        "write_reliability.repair_enabled": False,
        "user_profile.enabled": False,
        "auto_learning.enabled": False,
        "knowledge_base.enabled": False,
        "notes.enabled": False,
        "reranker.enabled": False,
        "export.enabled": False,
        "continuity_tracking.enabled": False,
        "reconsolidation.enabled": True,
        "reconsolidation.min_recall_count": 4,
        "data_dir": str(tmp_path),
    }
    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
        config=config,
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert isinstance(engine.reconsolidation, ReconsolidationManager)
        assert engine.reconsolidation._enabled is True
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_recall_proposes_candidate_but_never_writes_canonical() -> None:
    """召回钩子只调用候选生成，不直接调用 update_memory。"""

    manager = AsyncMock(spec=ReconsolidationManager)
    manager.maybe_propose.return_value = {
        "candidate_id": "c1",
        "memory_id": 7,
        "status": "pending",
    }
    engine = SimpleNamespace(reconsolidation=manager)
    handler = object.__new__(type("RecallHandlerStub", (), {}))
    handler._memory_engine = engine

    from core.handlers.recall_handler import RecallHandler

    await RecallHandler._maybe_propose_reconsolidation(
        handler,
        [
            HybridResult(
                doc_id=7,
                final_score=1.0,
                rrf_score=1.0,
                bm25_score=None,
                vector_score=None,
                content="记忆",
                metadata={},
            )
        ],
        "查询文本",
    )

    manager.maybe_propose.assert_awaited_once_with(7, context="查询文本")


@pytest.mark.asyncio
async def test_unchanged_llm_output_creates_no_candidate(tmp_path: Path) -> None:
    """未使用 LLM 或输出未变化时不创建候选。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原样返回的正文内容")),
        llm_caller=AsyncMock(return_value="原样返回的正文内容"),
        enabled=True,
    )

    assert await manager.maybe_propose(7, context="上下文") is None
    assert await store.list_candidates() == []
