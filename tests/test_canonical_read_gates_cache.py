"""canonical 读取状态门：缓存命中重校验与删除路径缓存失效。

覆盖 09-20-canonical-read-gates 的 C2/R2.1（缓存命中不盲信缓存）与 C2/R2.2
（``delete_memory`` 的任意返回路径先失效检索缓存）。断言的都是可观察行为：
同缓存键是否还能取回旧正文、响应计数与走的是命中还是实时检索。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.memory.application.retrieval_timing import RetrievalTimingSink
from core.features.memory.graph.domain.models import GraphQueryScope
from core.features.retrieval.rrf_fusion import HybridResult
from tests.fact_evidence_helpers import source_evidence
from tests.representation_migration_support import (
    _create_schema,
    _DocumentStorage,
    _seed,
)

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


@pytest.mark.asyncio
async def test_cache_hit_drops_archived_canonical_body(tmp_path) -> None:
    """归档后的行不得再从缓存命中返回旧正文，失效项计入响应计数。"""

    db_path = str(tmp_path / "cache-archived.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (1, "记忆一", dict(_ACTIVE), "r1-created", "r1"),
            (2, "记忆二", dict(_ACTIVE), "r2-created", "r2"),
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    timing_sink = RetrievalTimingSink()
    key = engine._retrieval.cache_key("归档查询", 5, None, None)
    engine._retrieval.set_cached(
        key,
        [
            _result(1, "记忆一", {"revision_token": "r1"}),
            _result(2, "记忆二", {"revision_token": "r2"}),
        ],
    )
    await _rewrite_row(
        db_path,
        1,
        metadata={"memory_status": "archived", "status": "archived"},
    )

    visible = await engine.search_memories("归档查询", k=5, timing_sink=timing_sink)

    assert [item.doc_id for item in visible] == [2]
    assert engine._last_search_timing["cache_hit"] is True
    assert engine._last_search_timing["dropped_stale_count"] == 1
    # 计数必须落在真实响应面上：召回样本 sink 只保留 allowlist 标量。
    assert timing_sink.snapshot()["dropped_stale_count"] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_cache_hit_drops_rewritten_and_deleted_bodies(tmp_path) -> None:
    """正文已改写或行已删除的缓存项都不得返回旧正文。"""

    db_path = str(tmp_path / "cache-stale.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (1, "旧正文一", dict(_ACTIVE), "r1-created", "r1"),
            (2, "记忆二", dict(_ACTIVE), "r2-created", "r2"),
            (3, "记忆三", dict(_ACTIVE), "r3-created", "r3"),
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    key = engine._retrieval.cache_key("改写查询", 5, None, None)
    engine._retrieval.set_cached(
        key,
        [
            _result(1, "旧正文一", {"revision_token": "r1"}),
            _result(2, "记忆二", {"revision_token": "r2"}),
            _result(3, "记忆三", {"revision_token": "r3"}),
        ],
    )
    await _rewrite_row(db_path, 1, text="新正文一")
    await _delete_row(db_path, 3)

    visible = await engine.search_memories("改写查询", k=5)

    assert [item.doc_id for item in visible] == [2]
    assert engine._last_search_timing["dropped_stale_count"] == 2
    await storage.close()


@pytest.mark.asyncio
async def test_cache_hit_rechecks_mark_write_on_canonical(tmp_path) -> None:
    """缓存里的候选若在 canonical 侧已标记 mark_write，则按当前状态过滤。"""

    db_path = str(tmp_path / "cache-mark-write.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {"memory_status": "active", "gate_disposition": "mark_write"},
                "r1-created",
                "r1",
            ),
            (2, "记忆二", dict(_ACTIVE), "r2-created", "r2"),
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    cached = [
        _result(1, "记忆一", {"revision_token": "r1"}),
        _result(2, "记忆二", {"revision_token": "r2"}),
    ]
    default_key = engine._retrieval.cache_key("标记查询", 5, None, None)
    included_key = engine._retrieval.cache_key(
        "标记查询", 5, None, None, include_mark_write=True
    )
    engine._retrieval.set_cached(default_key, cached)
    engine._retrieval.set_cached(included_key, cached)

    default_visible = await engine.search_memories("标记查询", k=5)
    included_visible = await engine.search_memories(
        "标记查询", k=5, include_mark_write=True
    )

    assert [item.doc_id for item in default_visible] == [2]
    assert engine._last_search_timing["dropped_stale_count"] == 0
    assert [item.doc_id for item in included_visible] == [1, 2]
    await storage.close()


@pytest.mark.asyncio
async def test_cache_hit_rechecks_user_evidence_on_canonical(tmp_path) -> None:
    """用户证据在 canonical 侧失效的候选不得借旧缓存进入注入。"""

    db_path = str(tmp_path / "cache-user-evidence.db")
    await _create_schema(db_path)
    assistant_reference = source_evidence("记忆二", role="assistant")[0]
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "key_facts": ["记忆一"],
                    "fact_source_evidence": [source_evidence("记忆一")],
                    "source_evidence": source_evidence("记忆一"),
                },
                "r1-created",
                "r1",
            ),
            (
                2,
                "记忆二",
                {
                    "memory_status": "active",
                    "key_facts": ["记忆二"],
                    "fact_source_evidence": [[assistant_reference]],
                    "source_evidence": [assistant_reference],
                },
                "r2-created",
                "r2",
            ),
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    cached = [
        _result(
            1,
            "记忆一",
            {
                "revision_token": "r1",
                "key_facts": ["记忆一"],
                "fact_source_evidence": [source_evidence("记忆一")],
                "source_evidence": source_evidence("记忆一"),
            },
        ),
        _result(
            2,
            "记忆二",
            {
                "revision_token": "r2",
                "key_facts": ["记忆二"],
                "fact_source_evidence": [source_evidence("记忆二")],
                "source_evidence": source_evidence("记忆二"),
            },
        ),
    ]
    key = engine._retrieval.cache_key(
        "证据查询", 5, None, None, require_user_evidence=True
    )
    engine._retrieval.set_cached(key, cached)

    visible = await engine.search_memories("证据查询", k=5, require_user_evidence=True)

    assert [item.doc_id for item in visible] == [1]
    assert engine._last_search_timing["dropped_stale_count"] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_session_cache_hit_revalidates_canonical(tmp_path) -> None:
    """请求级会话缓存命中同样按 canonical 当前状态剔除失效项。"""

    db_path = str(tmp_path / "session-cache.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (1, "记忆一", dict(_ACTIVE), "r1-created", "r1"),
            (
                2,
                "记忆二",
                {"memory_status": "archived", "status": "archived"},
                "r2-created",
                "r2",
            ),
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine._retrieval.set_session_cached(
        "会话查询",
        5,
        "s1",
        None,
        [
            _result(1, "记忆一", {"revision_token": "r1"}),
            _result(2, "记忆二", {"revision_token": "r2"}),
        ],
    )

    visible = await engine.search_memories("会话查询", k=5, session_id="s1")

    assert [item.doc_id for item in visible] == [1]
    assert engine._last_search_timing["cache_hit"] is True
    assert engine._last_search_timing["dropped_stale_count"] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_cache_hit_falls_back_to_search_when_canonical_unreadable(
    tmp_path,
) -> None:
    """无法回读 canonical 时不得把未经验证的缓存正文当结果返回。"""

    db_path = str(tmp_path / "cache-unreadable.db")
    await _create_schema(db_path)
    await _seed(db_path, [(1, "记忆一", dict(_ACTIVE), "r1-created", "r1")])
    storage = _DocumentStorage(db_path)
    storage.get_documents = AsyncMock(side_effect=RuntimeError("canonical down"))
    engine = _cache_engine(storage)
    key = engine._retrieval.cache_key("回读失败查询", 5, None, None)
    engine._retrieval.set_cached(key, [_result(1, "记忆一")])

    visible = await engine.search_memories("回读失败查询", k=5)

    assert visible == []
    assert engine._last_search_timing["cache_hit"] is False
    engine.hybrid_retriever.search.assert_awaited()
    await storage.close()


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created_at", "updated_at", "expected_ids"),
    [
        ("c1", _RAW_REVISION, []),
        (None, None, [1]),
    ],
    ids=[
        "canonical_snapshot_drops_unsnapshotted_entry",
        "both_sides_without_snapshot_retained",
    ],
)
async def test_cache_hit_fails_closed_when_entry_has_no_revision_snapshot(
    tmp_path, created_at, updated_at, expected_ids
) -> None:
    """条目无快照而 canonical 行有快照时视为失效；两侧都无快照时不误剔。"""

    db_path = str(tmp_path / "cache-no-snapshot.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "privacy_level": "public",
                    "scope_key": "session-a",
                },
                created_at,
                updated_at,
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    key = engine._retrieval.cache_key("无快照查询", 5, "session-a", None)
    engine._retrieval.set_cached(
        key,
        [
            _result(
                1,
                "记忆一",
                {
                    "derived_projections": [
                        {"type": "episode_summary", "summary": "旧派生结论"}
                    ]
                },
            )
        ],
    )

    visible = await engine.search_memories("无快照查询", k=5, session_id="session-a")

    assert [item.doc_id for item in visible] == expected_ids
    assert engine._last_search_timing["cache_hit"] is True
    assert engine._last_search_timing["dropped_stale_count"] == (
        0 if expected_ids else 1
    )
    if not expected_ids:
        # 无法证明与当前 canonical 相等的旧条目连同旧派生投影一并剔除。
        assert "旧派生结论" not in repr(visible)
    await storage.close()


@pytest.mark.asyncio
async def test_live_search_stamps_revision_snapshot_on_cached_entries(tmp_path) -> None:
    """实时检索写入缓存前固化 canonical revision 快照，命中时可同源比对。"""

    db_path = str(tmp_path / "cache-stamp.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "privacy_level": "public",
                    "scope_key": "session-a",
                },
                "c1",
                "r2",
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.hybrid_retriever.search = AsyncMock(
        return_value=[_result(1, "记忆一", {"memory_status": "active"})]
    )

    visible = await engine.search_memories("首次写入查询", k=5, session_id="session-a")

    assert [item.doc_id for item in visible] == [1]
    # 固化只作用于缓存副本：调用方载荷不得因此多出内部字段。
    assert "revision_token" not in visible[0].metadata
    cached = engine._retrieval.get_cached(
        engine._retrieval.cache_key("首次写入查询", 5, "session-a", None)
    )
    assert cached is not None
    assert cached[0].metadata["revision_token"] == "r2"
    session_cached = engine._retrieval.get_session_cached(
        "首次写入查询", 5, "session-a", None
    )
    assert session_cached is not None
    assert session_cached[0].metadata["revision_token"] == "r2"
    await storage.close()


@pytest.mark.asyncio
async def test_live_search_does_not_publish_across_invalidated_window(
    tmp_path,
) -> None:
    """读取窗口内发生 revision 推进时不得把旧候选发布到缓存（含旧派生投影）。"""

    db_path = str(tmp_path / "cache-race.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "privacy_level": "public",
                    "scope_key": "session-a",
                },
                "c1",
                "r1",
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.hybrid_retriever.search = AsyncMock(
        side_effect=lambda *args, **kwargs: [
            _result(
                1,
                "记忆一",
                {
                    "derived_projections": [
                        {"type": "episode_summary", "summary": "旧派生结论"}
                    ]
                },
            )
        ]
    )
    original_boosts = engine._retrieval.apply_boosts
    fired = False

    async def _interleaved_boosts(results, emotion_context):
        """在读取窗口内推进 canonical revision 并失效缓存。"""

        nonlocal fired
        if not fired:
            fired = True
            await _rewrite_row(
                db_path,
                1,
                metadata={"memory_status": "active", "updated_at": 1700000100},
            )
            async with aiosqlite.connect(db_path) as writer:
                await writer.execute("UPDATE documents SET updated_at='r2' WHERE id=1")
                await writer.commit()
            engine._retrieval.invalidate_cache()
        return await original_boosts(results, emotion_context)

    engine._retrieval.apply_boosts = _interleaved_boosts

    first = await engine.search_memories("竞争查询", k=5, session_id="session-a")

    assert [item.doc_id for item in first] == [1]
    # 旧候选不得被补成新 revision，也不得携带无法证明的派生注解。
    assert "revision_token" not in first[0].metadata
    assert "derived_projections" not in first[0].metadata
    key = engine._retrieval.cache_key("竞争查询", 5, "session-a", None)
    assert engine._retrieval.get_cached(key) is None

    await engine.search_memories("竞争查询", k=5, session_id="session-a")

    assert engine._last_search_timing["cache_hit"] is False
    await storage.close()


@pytest.mark.asyncio
async def test_cache_hit_rechecks_request_visibility_on_canonical(tmp_path) -> None:
    """canonical 行收紧为机密后，群会话的缓存命中不得再返回该行。"""

    db_path = str(tmp_path / "cache-visibility.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "privacy_level": "public",
                    "scope_key": "group-a",
                },
                "r1-created",
                "r1",
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    key = engine._retrieval.cache_key("可见性查询", 5, None, None, chat_type="group")
    engine._retrieval.set_cached(
        key,
        [
            _result(
                1,
                "记忆一",
                {
                    "revision_token": "r1",
                    "privacy_level": "public",
                    "scope_key": "group-a",
                },
            )
        ],
    )
    await _rewrite_row(
        db_path,
        1,
        metadata={
            "memory_status": "active",
            "privacy_level": "confidential",
            "scope_key": "private-b",
        },
    )

    visible = await engine.search_memories("可见性查询", k=5, chat_type="group")

    assert visible == []
    assert engine._last_search_timing["cache_hit"] is True
    assert engine._last_search_timing["dropped_stale_count"] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_cache_hit_rechecks_query_scope_on_canonical(tmp_path) -> None:
    """canonical scope 变更后，带来源范围的请求不得从缓存返回该行。"""

    db_path = str(tmp_path / "cache-scope.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "privacy_level": "public",
                    "scope_key": "group-a",
                },
                "r1-created",
                "r1",
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    scope = GraphQueryScope("group-a", "public")
    key = engine._retrieval.cache_key("范围查询", 5, None, None, query_scope=scope)
    engine._retrieval.set_cached(
        key,
        [
            _result(
                1,
                "记忆一",
                {
                    "revision_token": "r1",
                    "privacy_level": "public",
                    "scope_key": "group-a",
                },
            )
        ],
    )
    await _rewrite_row(
        db_path,
        1,
        metadata={
            "memory_status": "active",
            "privacy_level": "shared",
            "scope_key": "private-b",
        },
    )

    visible = await engine.search_memories("范围查询", k=5, query_scope=scope)

    assert visible == []
    assert engine._last_search_timing["cache_hit"] is True
    assert engine._last_search_timing["dropped_stale_count"] == 1
    await storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cached_metadata", "expected_ids"),
    [
        ({"revision_token": "stale-revision"}, []),
        ({"revision_token": _RAW_REVISION}, [1]),
        ({"updated_at": "stale-updated-at"}, []),
        ({"updated_at": _RAW_REVISION}, [1]),
    ],
    ids=[
        "stale_revision_token_dropped",
        "matching_revision_token_retained",
        "stale_updated_at_snapshot_dropped",
        "matching_updated_at_snapshot_retained",
    ],
)
async def test_cache_hit_compares_canonical_revision(
    tmp_path, cached_metadata, expected_ids
) -> None:
    """仅 revision 前进（正文与可见性不变）时，携带旧 revision 快照的缓存条目失效。"""

    db_path = str(tmp_path / "cache-revision.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "记忆一",
                {
                    "memory_status": "active",
                    "privacy_level": "public",
                    "scope_key": "session-a",
                    "revision_token": _RAW_REVISION,
                    "updated_at": _RAW_REVISION,
                    "derived_projections": [
                        {"type": "episode_summary", "summary": "旧派生结论"}
                    ],
                },
                "c1",
                _RAW_REVISION,
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    # 批量回读保留 SQLite 原始 revision（与候选携带的快照同源）。
    engine.db_connection = await aiosqlite.connect(db_path)
    try:
        cached = _result(
            1,
            "记忆一",
            {
                **cached_metadata,
                "derived_projections": [
                    {"type": "episode_summary", "summary": "旧派生结论"}
                ],
            },
        )
        key = engine._retrieval.cache_key("revision 查询", 5, "session-a", None)
        engine._retrieval.set_cached(key, [cached])

        visible = await engine.search_memories(
            "revision 查询", k=5, session_id="session-a"
        )

        assert [item.doc_id for item in visible] == expected_ids
        assert engine._last_search_timing["cache_hit"] is True
        assert engine._last_search_timing["dropped_stale_count"] == (
            0 if expected_ids else 1
        )
        if not expected_ids:
            # 失效条目连同其携带的旧派生投影一起被剔除，不进入后续 selection。
            assert "旧派生结论" not in repr(visible)
    finally:
        await engine.db_connection.close()
        await storage.close()
