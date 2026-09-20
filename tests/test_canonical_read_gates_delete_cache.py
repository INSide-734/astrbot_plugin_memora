"""canonical 读取状态门：删除路径先失效检索缓存。

覆盖 09-20-canonical-read-gates 的 C2/R2.2：``delete_memory`` 的成功、提前返回
失败与取消传播三条路径都必须在返回控制权前失效检索缓存，随后按实时检索取结果。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.canonical_read_gate_helpers import (
    _ACTIVE,
    _cache_engine,
    _delete_row,
    _result,
)
from tests.representation_migration_support import (
    _create_schema,
    _DocumentStorage,
    _seed,
)


@pytest.mark.asyncio
async def test_delete_memory_clears_same_cache_key_on_success(tmp_path) -> None:
    """删除成功后同缓存键不再返回旧正文，随后按实时检索取结果。"""

    db_path = str(tmp_path / "delete-success.db")
    await _create_schema(db_path)
    await _seed(db_path, [(1, "旧正文", dict(_ACTIVE), "r1-created", "r1")])
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)

    async def _delete_canonical_row(memory_id: int) -> bool:
        """按存储层语义删除 canonical 行并返回成功。"""

        await _delete_row(db_path, memory_id)
        return True

    engine.hybrid_retriever.delete_memory = AsyncMock(side_effect=_delete_canonical_row)
    key = engine._retrieval.cache_key("删除查询", 5, None, None)
    engine._retrieval.set_cached(key, [_result(1, "旧正文", {"revision_token": "r1"})])

    assert [item.doc_id for item in await engine.search_memories("删除查询", k=5)] == [
        1
    ]
    assert engine._last_search_timing["cache_hit"] is True

    assert await engine.delete_memory(1) is True

    assert engine._retrieval.get_cached(key) is None
    assert await engine.search_memories("删除查询", k=5) == []
    assert engine._last_search_timing["cache_hit"] is False
    await storage.close()


@pytest.mark.asyncio
async def test_delete_memory_invalidates_cache_on_early_failures(tmp_path) -> None:
    """缺少检索器与向量删除失败两条提前返回路径都必须先失效缓存。"""

    for scenario in ("retriever_missing", "vector_delete_failed"):
        db_path = str(tmp_path / f"delete-{scenario}.db")
        await _create_schema(db_path)
        await _seed(db_path, [(1, "旧正文", dict(_ACTIVE), "r1-created", "r1")])
        storage = _DocumentStorage(db_path)
        engine = _cache_engine(storage)
        key = engine._retrieval.cache_key("删除失败查询", 5, None, None)
        engine._retrieval.set_cached(
            key, [_result(1, "旧正文", {"revision_token": "r1"})]
        )
        if scenario == "retriever_missing":
            engine.hybrid_retriever = None
        else:
            engine.hybrid_retriever.delete_memory = AsyncMock(return_value=False)

        assert await engine.delete_memory(1) is False

        assert engine._retrieval.get_cached(key) is None
        await storage.close()


@pytest.mark.asyncio
async def test_delete_memory_invalidates_cache_before_propagating_cancel(
    tmp_path,
) -> None:
    """取消必须继续传播，但返回控制权前缓存已失效。"""

    db_path = str(tmp_path / "delete-cancel.db")
    await _create_schema(db_path)
    await _seed(db_path, [(1, "旧正文", dict(_ACTIVE), "r1-created", "r1")])
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.hybrid_retriever.delete_memory = AsyncMock(
        side_effect=asyncio.CancelledError()
    )
    key = engine._retrieval.cache_key("取消查询", 5, None, None)
    engine._retrieval.set_cached(key, [_result(1, "旧正文", {"revision_token": "r1"})])

    with pytest.raises(asyncio.CancelledError):
        await engine.delete_memory(1)

    assert engine._retrieval.get_cached(key) is None
    await storage.close()
