"""canonical 读取状态门测试的共享装配。

``tests/test_canonical_read_gates_cache.py``（缓存命中重校验）与
``tests/test_canonical_read_gates_delete_cache.py``（删除路径缓存失效）共用同一
引擎 host 形状与 canonical 行改写工具，避免两处各自维护一份引擎替身。
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite

from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.retrieval.rrf_fusion import HybridResult

_ACTIVE: dict[str, object] = {"memory_status": "active"}
# 与 tests/test_managers_memory_crud.py 相同的 host 形状：SQLite 原始 revision。
_RAW_REVISION = "2026-07-24 02:21:07.123456"


def _close_background(coro):
    """关闭测试中不需要实际调度的后台协程。"""

    if inspect.iscoroutine(coro):
        coro.close()


def _result(doc_id: int, content: str, metadata: dict | None = None) -> HybridResult:
    """构造与检索缓存同形的候选。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata=dict(metadata or {}),
    )


def _cache_engine(storage) -> MemoryEngine:
    """装配真实检索缓存、真实 canonical 行与受控检索结果的引擎边界。"""

    engine = MemoryEngine(
        db_path=":memory:",
        faiss_db=SimpleNamespace(document_storage=storage),
    )
    engine.dual_route_retriever = None
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.search = AsyncMock(return_value=[])
    engine.hybrid_retriever.delete_memory = AsyncMock(return_value=True)
    engine._retrieval = RetrievalOptimizer(
        config={
            "search_cache_enabled": True,
            "search_cache_ttl_seconds": 60.0,
            "session_cache_enabled": True,
            "session_cache_ttl_seconds": 60.0,
        }
    )
    engine._maintenance = MagicMock()
    engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)
    engine._maintenance.migrate_session_if_needed = AsyncMock()
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._create_tracked_task = MagicMock(side_effect=_close_background)
    return engine


async def _rewrite_row(
    db_path: str,
    memory_id: int,
    *,
    text: str | None = None,
    metadata: dict | None = None,
) -> None:
    """改写 canonical 行，模拟归档与正文更新后的当前状态。"""

    assignments: list[str] = []
    params: list[object] = []
    if text is not None:
        assignments.append("text = ?")
        params.append(text)
    if metadata is not None:
        assignments.append("metadata = ?")
        params.append(json.dumps(metadata, ensure_ascii=False))
    if not assignments:
        return
    params.append(memory_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"UPDATE documents SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        await db.commit()


async def _delete_row(db_path: str, memory_id: int) -> None:
    """删除 canonical 行，模拟已提交的存储层删除。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM documents WHERE id = ?", (memory_id,))
        await db.commit()
