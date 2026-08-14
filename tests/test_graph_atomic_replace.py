"""图记忆 SQLite 原子替换测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import pytest

from core.features.memory.graph.domain.models import GraphEdge, GraphEntry, GraphNode
from core.features.memory.graph.infrastructure.graph_store import GraphStore


def _graph_payload(
    source_memory_id: int,
    prefix: str,
) -> tuple[list[GraphNode], list[GraphEdge], list[GraphEntry]]:
    """构造一组节点、边和条目，供原子替换场景复用。"""
    source_key = f"entity:{prefix}-source"
    target_key = f"entity:{prefix}-target"
    nodes = [
        GraphNode(
            node_type="entity",
            value=f"{prefix}源节点",
            canonical_value=f"{prefix}-source",
        ),
        GraphNode(
            node_type="entity",
            value=f"{prefix}目标节点",
            canonical_value=f"{prefix}-target",
        ),
    ]
    edge = GraphEdge(
        source_key=source_key,
        target_key=target_key,
        relation_type="关联",
        source_memory_id=source_memory_id,
    )
    entry = GraphEntry(
        entry_key=f"memory:{source_memory_id}:{prefix}",
        source_memory_id=source_memory_id,
        session_id="session-test",
        persona_id="persona-test",
        entry_type="relation",
        content=f"{prefix}图条目",
        node_keys=[source_key, target_key],
        relation_type="关联",
        metadata={"source_memory_id": source_memory_id},
    )
    return nodes, [edge], [entry]


async def _seed_graph(
    store: GraphStore,
    source_memory_id: int,
    prefix: str,
    *,
    vector_doc_id: int | None = None,
) -> int:
    """通过现有公开 CRUD 写入一组旧图，并可选绑定向量标识。"""
    nodes, edges, entries = _graph_payload(source_memory_id, prefix)
    node_map = await store.upsert_nodes(nodes)
    edge_map = await store.add_edges(edges, node_map)
    entry_ids = await store.add_entries(entries, node_map, edge_map)
    entry_id = entry_ids[0]
    if vector_doc_id is not None:
        await store.update_entry_vector_doc_id(entry_id, vector_doc_id)
    return entry_id


async def _fetch_one(store: GraphStore, sql: str, parameters: tuple = ()):
    """执行只读查询并返回首行。"""
    async with store._connect() as db:
        cursor = await db.execute(sql, parameters)
        return await cursor.fetchone()


@pytest.mark.asyncio
async def test_replace_memory_graph_replaces_all_rows_atomically(tmp_db_path) -> None:
    """原子替换提交后只保留新图，且外键保持完整。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _seed_graph(store, 36, "旧", vector_doc_id=9001)
    nodes, edges, entries = _graph_payload(36, "新")

    result = await store.replace_memory_graph(36, nodes, edges, entries)

    assert len(result.entry_ids) == 1
    old_row = await _fetch_one(
        store,
        "SELECT id FROM graph_entries WHERE entry_key = ?",
        ("memory:36:旧",),
    )
    new_row = await _fetch_one(
        store,
        "SELECT id, vector_doc_id FROM graph_entries WHERE entry_key = ?",
        ("memory:36:新",),
    )
    assert old_row is None
    assert new_row == (result.entry_ids[0], None)
    async with store._connect() as db:
        cursor = await db.execute("PRAGMA foreign_key_check")
        assert await cursor.fetchall() == []


@pytest.mark.asyncio
async def test_replace_memory_graph_rolls_back_on_edge_failure(
    tmp_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边写入失败时保留完整旧图，并回滚全部新节点。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    old_entry_id = await _seed_graph(store, 36, "旧", vector_doc_id=9001)
    nodes, edges, entries = _graph_payload(36, "新")

    async def fail_add_edges(*_args, **_kwargs):
        """在节点写入后模拟边阶段失败。"""
        raise RuntimeError("模拟边写入失败")

    monkeypatch.setattr(store, "_add_edges", fail_add_edges)

    with pytest.raises(RuntimeError, match="模拟边写入失败"):
        await store.replace_memory_graph(36, nodes, edges, entries)

    old_row = await _fetch_one(
        store,
        "SELECT id, vector_doc_id FROM graph_entries WHERE entry_key = ?",
        ("memory:36:旧",),
    )
    new_node = await _fetch_one(
        store,
        "SELECT id FROM graph_nodes WHERE node_key = ?",
        ("entity:新-source",),
    )
    assert old_row == (old_entry_id, 9001)
    assert new_node is None


@pytest.mark.asyncio
async def test_replace_memory_graph_rolls_back_before_propagating_cancel(
    tmp_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事务取消时先回滚，再传播取消并允许后续写入。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    old_entry_id = await _seed_graph(store, 36, "旧", vector_doc_id=9001)
    nodes, edges, entries = _graph_payload(36, "新")

    async def cancel_add_edges(*_args, **_kwargs):
        """在节点写入后模拟任务取消。"""
        raise asyncio.CancelledError

    monkeypatch.setattr(store, "_add_edges", cancel_add_edges)

    with pytest.raises(asyncio.CancelledError):
        await store.replace_memory_graph(36, nodes, edges, entries)

    old_row = await _fetch_one(
        store,
        "SELECT id, vector_doc_id FROM graph_entries WHERE entry_key = ?",
        ("memory:36:旧",),
    )
    assert old_row == (old_entry_id, 9001)

    follow_up = GraphNode(
        node_type="entity",
        value="后续节点",
        canonical_value="follow-up",
    )
    assert await store.upsert_node(follow_up) > 0


@pytest.mark.asyncio
async def test_concurrent_delete_waits_for_atomic_replace(
    tmp_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发删除必须等待替换事务提交，不能清理中间节点。"""
    replacing_store = GraphStore(tmp_db_path)
    deleting_store = GraphStore(tmp_db_path)
    await replacing_store.initialize()
    await _seed_graph(replacing_store, 1, "待替换")
    await _seed_graph(replacing_store, 2, "待删除")
    nodes, edges, entries = _graph_payload(1, "替换后")
    entered_edge_stage = asyncio.Event()
    release_edge_stage = asyncio.Event()
    delete_begin_attempted = asyncio.Event()
    delete_rows_started = asyncio.Event()
    original_add_edges: Callable[..., Awaitable[dict[str, int]]] = (
        replacing_store._add_edges
    )
    original_delete_connect = deleting_store._connect
    original_delete_memory_rows = deleting_store._delete_memory_rows

    async def pause_add_edges(*args, **kwargs) -> dict[str, int]:
        """在同一事务的节点与边阶段之间建立确定性并发窗口。"""
        entered_edge_stage.set()
        await release_edge_stage.wait()
        return await original_add_edges(*args, **kwargs)

    @asynccontextmanager
    async def track_delete_begin():
        """在删除连接实际尝试获取 SQLite 写锁时发出信号。"""
        async with original_delete_connect() as db:
            original_execute = db.execute

            async def tracked_execute(sql, *args, **kwargs):
                """标记删除事务的 BEGIN IMMEDIATE，并转发数据库调用。"""
                if sql == "BEGIN IMMEDIATE":
                    delete_begin_attempted.set()
                return await original_execute(sql, *args, **kwargs)

            with monkeypatch.context() as connection_patch:
                connection_patch.setattr(db, "execute", tracked_execute)
                yield db

    async def track_delete_memory_rows(*args, **kwargs):
        """标记删除事务已经越过写锁并进入数据删除阶段。"""
        delete_rows_started.set()
        return await original_delete_memory_rows(*args, **kwargs)

    monkeypatch.setattr(replacing_store, "_add_edges", pause_add_edges)
    monkeypatch.setattr(deleting_store, "_connect", track_delete_begin)
    monkeypatch.setattr(
        deleting_store,
        "_delete_memory_rows",
        track_delete_memory_rows,
    )
    replace_task = asyncio.create_task(
        replacing_store.replace_memory_graph(1, nodes, edges, entries)
    )
    await entered_edge_stage.wait()
    delete_task = asyncio.create_task(deleting_store.delete_memory(2))
    await delete_begin_attempted.wait()
    assert not delete_task.done()
    assert not delete_rows_started.is_set()

    release_edge_stage.set()
    await replace_task
    await delete_task
    assert delete_rows_started.is_set()

    async with replacing_store._connect() as db:
        cursor = await db.execute("PRAGMA foreign_key_check")
        assert await cursor.fetchall() == []
