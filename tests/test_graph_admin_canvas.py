"""管理员图谱画布：跨来源校验、边界隔离与时间/查询过滤测试。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

import aiosqlite
import pytest

from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.memory.infrastructure.base import ConnectionPool, apply_perf_pragmas
from core.platform.transport.page_api.graph_api import GraphApiMixin
from tests.fact_evidence_helpers import fact_evidence, source_evidence

_ANONYMOUS_FACT = "匿名事实"


async def _seed_legacy_node(db_path: str) -> None:
    """写入边界列出现之前的 legacy 节点行，供迁移后验证总览丢弃。"""
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute(
            """
            CREATE TABLE graph_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_key TEXT NOT NULL UNIQUE,
                node_type TEXT NOT NULL,
                node_value TEXT NOT NULL,
                canonical_value TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT INTO graph_nodes(node_key, node_type, node_value, canonical_value,"
            " metadata, created_at, updated_at)"
            " VALUES ('fact:legacy', 'fact', '遗留', 'legacy', '{}', 'legacy', 'legacy')"
        )
        await db.commit()
    finally:
        await db.close()


async def _write_canonical(
    store: GraphStore,
    memory_id: int,
    *,
    revision: str = "rev-1",
    scope: str = "scope-a",
    privacy: str = "public",
    metadata: dict[str, Any] | None = None,
    raw_metadata: str | bytes | None = None,
    drop_scope: bool = False,
) -> None:
    """写入匿名 canonical 文档行；``raw_metadata``/``drop_scope`` 用于单故障负测。"""
    payload: dict[str, Any] = {
        "scope_key": scope,
        "privacy_level": privacy,
        "key_facts": [_ANONYMOUS_FACT],
        "fact_source_evidence": fact_evidence([_ANONYMOUS_FACT]),
        "source_evidence": source_evidence(_ANONYMOUS_FACT),
    }
    payload.update(metadata or {})
    if drop_scope:
        payload.pop("scope_key", None)
    async with store._connect() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "id INTEGER PRIMARY KEY, text TEXT, metadata TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
            (
                memory_id,
                "匿名正文",
                (
                    raw_metadata
                    if raw_metadata is not None
                    else json.dumps(payload, ensure_ascii=False)
                ),
                revision,
                revision,
            ),
        )
        await db.commit()


async def _write_graph_rows(
    store: GraphStore,
    memory_id: int,
    *,
    nodes: Sequence[tuple[str, str]],
    edges: Sequence[tuple[str, str, str, dict[str, Any]]] = (),
    scope: str = "scope-a",
    privacy: str = "public",
    revision: str = "rev-1",
    node_boundary: tuple[str, str, str] | None = None,
    edge_boundary: tuple[str, str, str] | None = None,
) -> dict[str, int]:
    """写入一个来源的匿名节点、边与条目，返回节点 key 到 ID 的映射。"""
    node_boundary = node_boundary or (scope, privacy, revision)
    edge_boundary = edge_boundary or (scope, privacy, revision)
    timestamp = "2026-09-17T00:00:00+00:00"
    async with store._connect() as db:
        node_ids: dict[str, int] = {}
        for node_key, label in nodes:
            cursor = await db.execute(
                """
                INSERT INTO graph_nodes(
                    node_key, node_type, node_value, canonical_value, metadata,
                    created_at, updated_at, scope_key, privacy_level, revision_token
                ) VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, ?)
                """,
                (
                    node_key,
                    node_key.split(":", 1)[0],
                    label,
                    label.casefold(),
                    timestamp,
                    timestamp,
                    *node_boundary,
                ),
            )
            node_ids[node_key] = int(cursor.lastrowid or 0)
        for source_key, target_key, relation, edge_metadata in edges:
            await db.execute(
                """
                INSERT INTO graph_edges(
                    edge_key, source_node_id, target_node_id, relation_type,
                    source_memory_id, weight, confidence, status, metadata,
                    created_at, updated_at, scope_key, privacy_level, revision_token
                ) VALUES (?, ?, ?, ?, ?, 1.0, 0.8, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"edge-{memory_id}-{source_key}-{target_key}",
                    node_ids[source_key],
                    node_ids[target_key],
                    relation,
                    memory_id,
                    json.dumps(edge_metadata, ensure_ascii=False),
                    timestamp,
                    timestamp,
                    *edge_boundary,
                ),
            )
        entry_cursor = await db.execute(
            """
            INSERT INTO graph_entries(
                entry_key, source_memory_id, session_id, persona_id, entry_type,
                relation_type, content, metadata, edge_id, created_at, updated_at,
                scope_key, privacy_level, revision_token
            ) VALUES (?, ?, NULL, NULL, 'fact', NULL, '匿名条目', '{}', NULL, ?, ?,
                      ?, ?, ?)
            """,
            (
                f"entry-{memory_id}-{revision}",
                memory_id,
                timestamp,
                timestamp,
                scope,
                privacy,
                revision,
            ),
        )
        entry_id = int(entry_cursor.lastrowid or 0)
        for node_id in node_ids.values():
            await db.execute(
                "INSERT INTO graph_entry_nodes(entry_id, node_id) VALUES (?, ?)",
                (entry_id, node_id),
            )
        await db.commit()
    return node_ids


async def _seed_invalid_source(
    store: GraphStore,
    memory_id: int,
    variant: str,
    *,
    scope: str,
) -> None:
    """按负测变体写入一条 canonical 来源；除目标故障外不制造第二个故障。"""
    if variant == "missing_canonical":
        return
    if variant == "stale_revision":
        await _write_canonical(store, memory_id, scope=scope, revision="rev-2")
        return
    if variant == "deleted":
        await _write_canonical(
            store, memory_id, scope=scope, metadata={"status": "deleted"}
        )
        return
    if variant == "orphan":
        await _write_canonical(
            store, memory_id, scope=scope, metadata={"summary_source_orphan": True}
        )
        return
    if variant == "mark_write":
        await _write_canonical(
            store, memory_id, scope=scope, metadata={"gate_disposition": "mark_write"}
        )
        return
    if variant == "assistant_evidence":
        await _write_canonical(
            store,
            memory_id,
            scope=scope,
            metadata={
                "fact_source_evidence": [
                    source_evidence(_ANONYMOUS_FACT, role="assistant")
                ]
            },
        )
        return
    if variant == "missing_evidence":
        blank_evidence = {"key_facts": None, "fact_source_evidence": None}
        await _write_canonical(store, memory_id, scope=scope, metadata=blank_evidence)
        return
    if variant == "malformed_canonical":
        await _write_canonical(store, memory_id, raw_metadata="{not-json")
        return
    if variant == "blob_metadata":
        # 非法 UTF-8 BLOB：decode 前就必须拒绝，只让该来源失效。
        await _write_canonical(store, memory_id, raw_metadata=b"\xff\xfe")
        return
    if variant == "deep_json":
        # 超深 JSON：解析抛 RecursionError，只跳过该来源。
        await _write_canonical(store, memory_id, raw_metadata="[" * 5000 + "]" * 5000)
        return
    if variant == "int_limit":
        # 超出 int 位数限制的 JSON 数字抛普通 ValueError，只跳过该来源。
        await _write_canonical(store, memory_id, raw_metadata="1" * 5000)
        return
    if variant == "scope_missing":
        # 只去掉 scope_key：事实与证据保持正常，避免与缺证据混为一谈。
        await _write_canonical(store, memory_id, scope=scope, drop_scope=True)
        return
    raise AssertionError(variant)


@pytest.mark.asyncio
async def test_admin_canvas_keeps_valid_sources_across_scopes(tmp_db_path) -> None:
    """合法的多个 scope 来源都进入总览，节点与边按来源边界归属。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="session:alpha", revision="rev-1")
    await _write_canonical(store, 2, scope="group:beta", revision="rev-2")
    await _write_graph_rows(
        store,
        1,
        scope="session:alpha",
        revision="rev-1",
        nodes=[("fact:alpha", "甲事实")],
    )
    await _write_graph_rows(
        store,
        2,
        scope="group:beta",
        revision="rev-2",
        nodes=[("topic:beta", "乙主题")],
    )

    snapshot = await store.get_admin_canvas_snapshot()

    assert {node["label"] for node in snapshot["nodes"]} == {"甲事实", "乙主题"}
    assert {node["entry_count"] for node in snapshot["nodes"]} == {1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "missing_canonical",
        "stale_revision",
        "deleted",
        "orphan",
        "mark_write",
        "assistant_evidence",
        "missing_evidence",
        "malformed_canonical",
        "blob_metadata",
        "deep_json",
        "int_limit",
        "scope_missing",
    ],
)
async def test_admin_canvas_drops_invalid_sources(tmp_db_path, variant: str) -> None:
    """缺失/陈旧/已删/孤儿/mark_write/证据不足/损坏的来源都不进入总览。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 7, scope="scope-valid", revision="rev-1")
    await _write_graph_rows(
        store,
        7,
        scope="scope-valid",
        revision="rev-1",
        nodes=[("fact:valid", "合法事实")],
    )
    await _seed_invalid_source(store, 8, variant, scope="scope-invalid")
    await _write_graph_rows(
        store,
        8,
        scope="scope-invalid",
        revision="rev-1",
        nodes=[("fact:invalid", "失效事实")],
    )

    snapshot = await store.get_admin_canvas_snapshot()

    assert [node["label"] for node in snapshot["nodes"]] == ["合法事实"]
    assert snapshot["edges"] == []


@pytest.mark.asyncio
async def test_admin_canvas_drops_cross_boundary_nodes_and_edges(tmp_db_path) -> None:
    """来源自身的边界不匹配与无合法条目支撑的边都必须丢弃。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_canonical(store, 2, scope="scope-b", revision="rev-1")
    # 来源 1 的派生行落在 scope-b：与自身 canonical 边界不符，整体丢弃。
    foreign_ids = await _write_graph_rows(
        store,
        1,
        scope="scope-b",
        revision="rev-1",
        nodes=[("fact:foreign", "异界")],
    )
    valid_ids = await _write_graph_rows(
        store,
        2,
        scope="scope-b",
        revision="rev-1",
        nodes=[("fact:kept", "保留")],
        edges=[("fact:kept", "fact:kept", "related", {})],
    )
    # 合法来源的边指向仅有失效来源条目支撑的节点时也必须被丢弃。
    async with store._connect() as db:
        await db.execute(
            """
            INSERT INTO graph_edges(
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, status, metadata,
                created_at, updated_at, scope_key, privacy_level, revision_token
            ) VALUES ('cross-entry-edge', ?, ?, 'related', 2, 1.0, 0.8, 'active',
                      '{}', '2026-09-17T00:00:00+00:00',
                      '2026-09-17T00:00:00+00:00', 'scope-b', 'public', 'rev-1')
            """,
            (valid_ids["fact:kept"], foreign_ids["fact:foreign"]),
        )
        await db.commit()

    snapshot = await store.get_admin_canvas_snapshot()

    assert {node["label"] for node in snapshot["nodes"]} == {"保留"}
    assert [(edge["source"], edge["target"]) for edge in snapshot["edges"]] == [
        (valid_ids["fact:kept"], valid_ids["fact:kept"])
    ]


@pytest.mark.asyncio
async def test_admin_canvas_edge_cannot_borrow_same_boundary_endpoint(
    tmp_db_path,
) -> None:
    """A 的边不得借用同一边界下只属于 B 的端点节点。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="scope-shared", revision="rev-1")
    await _write_canonical(store, 2, scope="scope-shared", revision="rev-1")
    first_ids = await _write_graph_rows(
        store,
        1,
        scope="scope-shared",
        revision="rev-1",
        nodes=[("fact:a1", "甲一"), ("fact:a2", "甲二")],
        edges=[("fact:a1", "fact:a2", "related", {})],
    )
    second_ids = await _write_graph_rows(
        store,
        2,
        scope="scope-shared",
        revision="rev-1",
        nodes=[("fact:b1", "乙一")],
    )
    # A 的边指向只有 B 条目支撑的节点：同 scope/privacy/revision 也不得借用。
    async with store._connect() as db:
        await db.execute(
            """
            INSERT INTO graph_edges(
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, status, metadata,
                created_at, updated_at, scope_key, privacy_level, revision_token
            ) VALUES ('borrowed-endpoint-edge', ?, ?, 'related', 1, 1.0, 0.8,
                      'active', '{}', '2026-09-17T00:00:00+00:00',
                      '2026-09-17T00:00:00+00:00',
                      'scope-shared', 'public', 'rev-1')
            """,
            (first_ids["fact:a1"], second_ids["fact:b1"]),
        )
        await db.commit()

    snapshot = await store.get_admin_canvas_snapshot()

    assert {node["label"] for node in snapshot["nodes"]} == {"甲一", "甲二", "乙一"}
    assert [(edge["source"], edge["target"]) for edge in snapshot["edges"]] == [
        (first_ids["fact:a1"], first_ids["fact:a2"])
    ]


@pytest.mark.asyncio
async def test_admin_canvas_drops_migrated_legacy_rows(tmp_db_path) -> None:
    """迁移得到的 legacy 空边界行不进入管理员总览。"""
    await _seed_legacy_node(tmp_db_path)
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_graph_rows(
        store,
        1,
        scope="scope-a",
        revision="rev-1",
        nodes=[("fact:kept", "保留")],
    )
    # 空边界 legacy 节点必须被合法来源条目真实引用，否则用例会因不可达而假绿。
    async with store._connect() as db:
        await db.execute(
            "INSERT INTO graph_entry_nodes(entry_id, node_id)"
            " SELECT ge.id, gn.id FROM graph_entries ge JOIN graph_nodes gn"
            " ON gn.node_key = 'fact:legacy' WHERE ge.source_memory_id = 1"
        )
        cursor = await db.execute(
            "SELECT COUNT(*) FROM graph_entry_nodes gen JOIN graph_nodes gn"
            " ON gn.id = gen.node_id"
            " WHERE gn.node_key = 'fact:legacy' AND gn.scope_key IS NULL"
        )
        witness = await cursor.fetchone()
        assert witness is not None and int(witness[0]) == 1
        await db.commit()

    snapshot = await store.get_admin_canvas_snapshot()

    assert [node["label"] for node in snapshot["nodes"]] == ["保留"]


@pytest.mark.asyncio
async def test_admin_canvas_keeps_same_label_nodes_separate(tmp_db_path) -> None:
    """同名节点在不同 scope 下保持独立，不合并计数也不产生跨来源边。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_canonical(store, 2, scope="scope-b", revision="rev-1")
    first_ids = await _write_graph_rows(
        store,
        1,
        scope="scope-a",
        revision="rev-1",
        nodes=[("person:同名", "同名")],
    )
    second_ids = await _write_graph_rows(
        store,
        2,
        scope="scope-b",
        revision="rev-1",
        nodes=[("person:同名", "同名")],
    )

    snapshot = await store.get_admin_canvas_snapshot()

    assert {node["id"] for node in snapshot["nodes"]} == {
        first_ids["person:同名"],
        second_ids["person:同名"],
    }
    assert {node["entry_count"] for node in snapshot["nodes"]} == {1}
    assert {node["memory_count"] for node in snapshot["nodes"]} == {1}
    assert snapshot["edges"] == []


@pytest.mark.asyncio
async def test_admin_canvas_filters_edges_and_orphans_by_time(tmp_db_path) -> None:
    """时间范围只保留命中边及其端点，语义与固定边界画布一致。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    now = 1_700_000_000.0
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_graph_rows(
        store,
        1,
        scope="scope-a",
        revision="rev-1",
        nodes=[
            ("fact:recent-a", "近期甲"),
            ("fact:recent-b", "近期乙"),
            ("fact:old-a", "旧甲"),
            ("fact:old-b", "旧乙"),
        ],
        edges=[
            ("fact:recent-a", "fact:recent-b", "related", {"event_time": now - 3600}),
            ("fact:old-a", "fact:old-b", "related", {"event_time": now - 864_000}),
        ],
    )

    snapshot = await store.get_admin_canvas_snapshot(oldest_timestamp=now - 604_800)

    assert {node["label"] for node in snapshot["nodes"]} == {"近期甲", "近期乙"}
    assert [edge["timestamp"] for edge in snapshot["edges"]] == [now - 3600]


@pytest.mark.asyncio
async def test_admin_canvas_query_keeps_one_hop_neighborhood(tmp_db_path) -> None:
    """查询命中可见标签后保留关联边与对端，但不递归扩散到第二跳。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_canonical(store, 2, scope="scope-b", revision="rev-1")
    await _write_graph_rows(
        store,
        1,
        scope="scope-a",
        revision="rev-1",
        nodes=[
            ("person:alice", "Alice"),
            ("topic:club", "读书俱乐部"),
            ("topic:coffee", "Coffee"),
            ("topic:sugar", "Sugar"),
        ],
        edges=[
            ("person:alice", "topic:club", "related", {}),
            ("person:alice", "topic:coffee", "likes", {}),
            ("topic:coffee", "topic:sugar", "related", {}),
        ],
    )
    await _write_graph_rows(
        store,
        2,
        scope="scope-b",
        revision="rev-1",
        nodes=[("person:bob", "Bob")],
    )

    snapshot = await store.get_admin_canvas_snapshot()
    node_id_by_label = {node["label"]: node["id"] for node in snapshot["nodes"]}
    matched = GraphApiMixin._filter_admin_canvas_by_query(snapshot, "alice")

    assert {node["label"] for node in matched["nodes"]} == {
        "Alice",
        "读书俱乐部",
        "Coffee",
    }
    assert sorted(
        (edge["source"], edge["target"]) for edge in matched["edges"]
    ) == sorted(
        [
            (node_id_by_label["Alice"], node_id_by_label["读书俱乐部"]),
            (node_id_by_label["Alice"], node_id_by_label["Coffee"]),
        ]
    )


class _BlockingBeginCursor:
    """透明 cursor 代理：语句真正执行后暂停一次。"""

    def __init__(self, cursor: Any, pause: Any) -> None:
        self._cursor = cursor
        self._pause = pause

    def __await__(self):
        async def _run():
            result = await self._cursor
            await self._pause()
            return result

        return _run().__await__()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _BlockingBeginPool(ConnectionPool):
    """单连接池替身：首个 BEGIN 生效后暂停，供事务内取消测试使用。"""

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, pool_size=1)
        self.begin_started = asyncio.Event()
        self.begin_release = asyncio.Event()
        self.connection: aiosqlite.Connection | None = None
        self._paused = False
        self._patch: Any = None

    async def initialize(self) -> None:
        """打开一条真实连接，并把 BEGIN 执行后的暂停注入其 execute。"""
        conn = await aiosqlite.connect(self.db_path)
        await apply_perf_pragmas(conn)
        self.connection = conn
        raw_execute = conn.execute

        def blocking_execute(sql: str, parameters: Any = None):
            cursor = raw_execute(sql, parameters)
            if sql.strip().upper().startswith("BEGIN"):
                return _BlockingBeginCursor(cursor, self._pause_once)
            return cursor

        self._patch = patch.object(conn, "execute", blocking_execute)
        self._patch.start()
        await self._pool.put(conn)

    async def close(self) -> None:
        """先恢复真实 execute，再关闭池内连接。"""
        if self._patch is not None:
            self._patch.stop()
            self._patch = None
        await super().close()

    async def _pause_once(self) -> None:
        """只在首个 BEGIN 执行完成后暂停，后续语句不受影响。"""
        if self._paused:
            return
        self._paused = True
        self.begin_started.set()
        await self.begin_release.wait()


@pytest.mark.asyncio
async def test_admin_canvas_begin_cancellation_keeps_connection_clean(
    tmp_db_path,
) -> None:
    """BEGIN 已生效时被取消必须回滚，共享池连接随后仍可正常读取。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_graph_rows(
        store,
        1,
        scope="scope-a",
        revision="rev-1",
        nodes=[("fact:kept", "保留")],
    )
    pool = _BlockingBeginPool(tmp_db_path)
    await pool.initialize()
    store._pool = pool
    try:
        task = asyncio.create_task(store.get_admin_canvas_snapshot())
        await asyncio.wait_for(pool.begin_started.wait(), timeout=5)
        assert pool.connection is not None
        assert pool.connection.in_transaction is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.connection.in_transaction is False

        snapshot = await store.get_admin_canvas_snapshot()
        assert [node["label"] for node in snapshot["nodes"]] == ["保留"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_admin_canvas_ignores_foreign_edge_entry_time(tmp_db_path) -> None:
    """其它来源条目误指本边 edge_id 时不得改变该边的展示与过滤时间。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    now = 1_700_000_000.0
    await _write_canonical(store, 1, scope="scope-a", revision="rev-1")
    await _write_canonical(store, 2, scope="scope-a", revision="rev-1")
    await _write_graph_rows(
        store,
        1,
        scope="scope-a",
        revision="rev-1",
        nodes=[("fact:a1", "甲一"), ("fact:a2", "甲二")],
        edges=[("fact:a1", "fact:a2", "related", {})],
    )
    await _write_graph_rows(
        store,
        2,
        scope="scope-a",
        revision="rev-1",
        nodes=[("fact:b1", "乙一")],
    )
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT id FROM graph_edges WHERE edge_key = 'edge-1-fact:a1-fact:a2'"
        )
        edge_row = await cursor.fetchone()
        assert edge_row is not None
        edge_id = int(edge_row[0])
        # 来源 1 自己的条目绑定该边并携带近期业务时间。
        await db.execute(
            "UPDATE graph_entries SET edge_id = ?, metadata = ?"
            " WHERE source_memory_id = 1",
            (edge_id, json.dumps({"event_time": now - 3600})),
        )
        # 来源 2 的条目（id 更大）误指同一边且携带陈旧时间：必须被忽略。
        await db.execute(
            "UPDATE graph_entries SET edge_id = ?, metadata = ?"
            " WHERE source_memory_id = 2",
            (edge_id, json.dumps({"event_time": now - 864_000})),
        )
        await db.commit()

    snapshot = await store.get_admin_canvas_snapshot(oldest_timestamp=now - 604_800)

    assert [edge["timestamp"] for edge in snapshot["edges"]] == [now - 3600]
    assert {node["label"] for node in snapshot["nodes"]} == {"甲一", "甲二"}
