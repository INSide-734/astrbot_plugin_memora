"""验证 canonical 语义更新会刷新领域 proposal，运行态更新不会制造任务。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.memory_engine import MemoryEngine


def _engine_with_memory(metadata: dict[str, object]) -> MemoryEngine:
    """构造只覆盖 canonical 原地更新边界的最小引擎替身。"""

    faiss_db = MagicMock()
    faiss_db.document_storage = MagicMock()
    faiss_db.document_storage.get_documents = AsyncMock(
        return_value=[
            {
                "id": 42,
                "text": "原始正文",
                "metadata": metadata,
                "updated_at": "r-current",
            }
        ]
    )
    engine = MemoryEngine(db_path=":memory:", faiss_db=faiss_db)
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
    engine.hybrid_retriever.update_content_if_revision = AsyncMock(return_value=True)
    engine.graph_memory_manager = None
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._invalidate_evolution_after_revision = AsyncMock()
    engine._schedule_evolution_after_write = AsyncMock()
    engine._schedule_domain_proposals_after_write = MagicMock()
    return engine


@pytest.mark.asyncio
async def test_cas_content_update_refreshes_domain_proposals() -> None:
    """正文 CAS 成功后必须按新 source revision 调度全部领域 proposal。"""

    engine = _engine_with_memory({"importance": 0.8})

    assert (
        await engine.update_memory(
            42,
            {"content": "更新后的正文"},
            expected_revision="r-current",
        )
        is True
    )

    engine._invalidate_evolution_after_revision.assert_awaited_once_with(42)
    engine._schedule_evolution_after_write.assert_awaited_once_with(42)
    engine._schedule_domain_proposals_after_write.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_semantic_metadata_update_refreshes_domain_proposals() -> None:
    """影响领域 eligibility 的 metadata 变化必须刷新派生 proposal。"""

    engine = _engine_with_memory({"importance": 0.4, "status": "draft"})

    assert await engine.update_memory(42, {"importance": 0.8}) is True

    engine._invalidate_evolution_after_revision.assert_awaited_once_with(42)
    engine._schedule_evolution_after_write.assert_awaited_once_with(42)
    engine._schedule_domain_proposals_after_write.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_operational_metadata_update_does_not_schedule_derived_work() -> None:
    """访问计数和访问时间更新不得触发 Evolution 或领域 proposal。"""

    engine = _engine_with_memory(
        {"importance": 0.8, "access_count": 4, "last_access_time": 100.0}
    )

    assert (
        await engine.update_memory(
            42,
            {"metadata": {"access_count": 5, "last_access_time": 101.0}},
        )
        is True
    )

    engine._invalidate_evolution_after_revision.assert_not_awaited()
    engine._schedule_evolution_after_write.assert_not_awaited()
    engine._schedule_domain_proposals_after_write.assert_not_called()
