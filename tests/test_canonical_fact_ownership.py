"""canonical 事实真源的「写入面 × 读取面」集成矩阵（issue #77 收口验收）。

每个用例只固定一条可观察不变量，装配真实 SQLite（``tmp_path``）与真实组件：
``MemoryEngine`` / ``_DocumentStorage`` / ``AtomStore`` / ``GraphMemoryManager`` /
Page API 混入类 / ``MemoryExporter`` / ``CanonicalMergeCoordinator``。相邻平面
只在必要时用最小端口替身（检索结果、图向量后端、分类端口），断言对象是返回值、
响应 envelope、canonical 行与派生行，不断言日志措辞或私有调用次数。

单平面细节由既有文件负责（``test_canonical_write_recovery.py`` 的故障注入、
``test_canonical_read_gates_cache.py`` 的缓存门、``test_fact_text_alignment_crud.py``
的事实三态、``test_derived_rebuild_coordinator.py`` 的固定顺序），这里只补跨平面
组合。本文件不访问宿主运行数据。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from core.features.memory.application.canonical_merge import (
    DEDUP_REASON_MERGE_CONFLICT,
    CanonicalMergeCoordinator,
    MergeCandidate,
    MergeStatus,
)
from core.features.memory.application.memory_exporter import MemoryExporter
from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.memory.domain.memory_dedup_config import MemoryDedupConfig
from core.features.memory.graph.domain.models import GraphBoundary
from core.features.memory.infrastructure.atom_store import AtomStore
from core.features.quality.application.near_duplicate_detector import (
    DedupDocument,
    DedupQuery,
)
from core.features.recall.application.auxiliary_recall import AuxiliaryRecall
from core.features.recall.application.recall_routing import RecallRoutingMixin
from core.platform.transport.page_api.graph_api import GraphApiMixin
from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin
from core.platform.transport.page_api.memory_stats_recall_api import (
    MemoryStatsRecallApiMixin,
)
from core.platform.transport.page_api.response_utils import (
    error_response,
    ok_response,
)
from core.platform.transport.page_api.shared_helpers import SharedPageApiHelpersMixin
from core.shared.memory_status import set_memory_status
from core.shared.summary_source_fence import SummarySourceFence
from tests.fact_evidence_helpers import fact_evidence, source_evidence
from tests.representation_migration_support import (
    _create_schema,
    _DocumentStorage,
    _engine_with_real_cas,
    _read_row,
    _seed,
)
from tests.test_canonical_read_gates_cache import _cache_engine, _result
from tests.test_canonical_write_recovery import (
    _cas_content_updater,
    _staged_engine,
)
from tests.test_graph_source_cleanup import _manager as _graph_manager
from tests.test_managers_memory_crud import (
    _active_canonical_ids,
    _engine_with_canonical_db,
    _latest_operation,
    _ReplaceLifecycleStub,
)

_SCOPE = "session:s1"
_OLD_BODY = "用户喜欢喝手冲咖啡，周末常去那家店坐一上午"
_NEW_BODY = "用户改喝拿铁咖啡，周末常去那家店坐一上午"
_SURVIVOR_BODY = "用户每周三晚上都会去游泳馆练习自由泳"
_REVISION = "r1"


def _active_metadata(**extra: Any) -> dict[str, Any]:
    """构造能通过 canonical 读取门的最小运行态 metadata。"""

    return {
        "memory_status": "active",
        "scope_key": _SCOPE,
        "privacy_level": "shared",
        "session_id": "s1",
        "source_provenance_complete": True,
        **extra,
    }


async def _seed_row(
    db_path: str,
    memory_id: int,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """写入一条可召回的 canonical 行，并返回它的 revision token。"""

    await _seed(
        db_path,
        [
            (
                memory_id,
                text,
                _active_metadata(**(metadata or {})),
                f"{_REVISION}-created",
                _REVISION,
            )
        ],
    )
    return _REVISION


async def _delete_row(db_path: str, memory_id: int) -> None:
    """按 canonical 语义删除一行。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM documents WHERE id = ?", (memory_id,))
        await db.commit()


class _PageApiHost(
    MemoryStatsRecallApiMixin, MemoryReadApiMixin, SharedPageApiHelpersMixin
):
    """继承真实列表与召回测试端点，只替换宿主响应与就绪装配。"""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @staticmethod
    def _ok(data: dict[str, Any]) -> dict[str, Any]:
        return ok_response(data)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return error_response(message)

    async def _ensure_plugin_ready(self):
        return {"memory_engine": self._engine}, None


async def _recall_test(host: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """经由真实召回测试端点执行一次管理员召回。"""

    request_mock = SimpleNamespace(get_json=AsyncMock(return_value=payload))
    with patch(
        "core.platform.transport.page_api.memory_stats_recall_api.request",
        request_mock,
    ):
        return await host.test_recall()


async def _list_memories(host: Any, args: dict[str, str]) -> dict[str, Any]:
    """经由真实列表端点读取一页 canonical 行。"""

    request_mock = SimpleNamespace(args=args)
    with patch(
        "core.platform.transport.page_api.memory_read_api.request",
        request_mock,
    ):
        return await host.list_memories()


def _graph_texts(snapshot: dict[str, Any]) -> str:
    """把图快照的节点标签与条目正文拼成单串，供事实存在性断言。"""

    parts = [str(node.get("label") or "") for node in snapshot.get("nodes", [])]
    parts.extend(str(entry.get("content") or "") for entry in snapshot["entries"])
    return "\n".join(parts)


def _export_rows(db_path: str):
    """返回读取 canonical 全集的导出回调（导出不按状态过滤）。"""

    async def callback(_session_id: str | None = None) -> list[dict[str, Any]]:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT id, text, metadata FROM documents ORDER BY id"
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": int(row[0]),
                "text": row[1],
                "metadata": json.loads(row[2] or "{}"),
            }
            for row in rows
        ]

    return callback


# --- 写入面：CAS 正文更新 × 读取面：主召回 / 召回测试 ---


@pytest.mark.asyncio
async def test_cas_rewrite_hides_superseded_body_from_cached_recall(tmp_path) -> None:
    """CAS 改写正文后，带旧快照的缓存条目不得再从主召回返回旧正文。"""

    db_path = str(tmp_path / "cas-cache.db")
    await _create_schema(db_path)
    revision = await _seed_row(db_path, 1, _OLD_BODY)
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    key = engine._retrieval.cache_key("咖啡查询", 5, None, None)
    engine._retrieval.set_cached(
        key, [_result(1, _OLD_BODY, {"revision_token": revision})]
    )

    # 另一实例（页面编辑/维护路径）写回 canonical：本引擎的缓存不会随之失效，
    # 命中时必须按 canonical 当前状态重校验。
    write_engine = _engine_with_real_cas(_DocumentStorage(db_path))
    assert (
        await write_engine.update_memory(
            1, {"content": _NEW_BODY}, expected_revision=revision
        )
        is True
    )
    assert (await _read_row(db_path, 1))["text"] == _NEW_BODY

    visible = await engine.search_memories("咖啡查询", k=5)

    assert [item.content for item in visible] == []
    assert engine._last_search_timing["cache_hit"] is True
    assert engine._last_search_timing["dropped_stale_count"] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_recall_test_drops_superseded_candidate_and_counts_it(tmp_path) -> None:
    """检索面仍返回旧正文时，召回测试必须按 canonical 剔除并计数。"""

    db_path = str(tmp_path / "recall-test-superseded.db")
    await _create_schema(db_path)
    await _seed_row(db_path, 7, _NEW_BODY)
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result(7, _OLD_BODY)])

    result = await _recall_test(_PageApiHost(engine), {"query": "咖啡", "k": 5})

    assert result["data"]["results"] == []
    assert result["data"]["dropped_stale_count"] == 1
    assert _OLD_BODY not in json.dumps(result, ensure_ascii=False)
    await storage.close()


@pytest.mark.asyncio
async def test_recall_test_projects_canonical_summary_and_integer_id(tmp_path) -> None:
    """存活候选只投影 canonical 行自身的摘要，身份取整数 memory_id。"""

    db_path = str(tmp_path / "recall-projection.db")
    await _create_schema(db_path)
    await _seed_row(db_path, 9, _NEW_BODY, {"canonical_summary": "咖啡偏好摘要"})
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result(9, _NEW_BODY)])

    result = await _recall_test(_PageApiHost(engine), {"query": "咖啡", "k": 5})
    item = result["data"]["results"][0]

    assert item["content"] == _NEW_BODY
    assert item["summary"] == "咖啡偏好摘要"
    assert item["memory_id"] == 9
    assert isinstance(item["memory_id"], int)
    await storage.close()


# --- 写入面：两阶段替换取消 × 读取面：DB 召回门 / 召回测试 ---


@pytest.mark.asyncio
async def test_cancelled_replace_never_exposes_staged_row(tmp_db_path: str) -> None:
    """替换中途取消后最多一个可召回 owner，暂存新行不得进入任何读取面。"""

    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("doc-old", "旧正文", _active_metadata())
        stub = _ReplaceLifecycleStub(store)
        stub.cancel_after_insert = True
        engine.hybrid_retriever = stub

        with pytest.raises(asyncio.CancelledError):
            await engine.update_memory(1, {"content": "新正文"})

        assert await _active_canonical_ids(db) == [1]
        cursor = await db.execute("SELECT metadata FROM documents WHERE id = 2")
        staged = json.loads((await cursor.fetchone())[0])
        assert staged["replacement_pending"] is True
        assert staged["status"] == "deleted"
        assert (await _latest_operation(db, "replace_content"))["status"] == "pending"
        assert await engine.find_replacement_memory_id(1) is None

        reader = _cache_engine(_DocumentStorage(tmp_db_path))
        reader.hybrid_retriever.search = AsyncMock(return_value=[_result(2, "新正文")])
        result = await _recall_test(_PageApiHost(reader), {"query": "正文"})

        assert result["data"]["results"] == []
        assert result["data"]["dropped_stale_count"] == 1
    finally:
        await db.close()


# --- 写入面：总结来源 fence 暂存后被拒 × 读取面：召回门 / 列表 / 召回测试 ---


@pytest.mark.asyncio
async def test_rejected_summary_fence_row_is_invisible_to_read_surfaces(
    tmp_db_path: str,
) -> None:
    """来源 fence 暂存后被拒的行不得进入召回门、列表或召回测试。"""

    fenced_body = "用户计划下个月搬去杭州，并且已经看过三处房源"
    engine, db, _host, _bm25 = await _staged_engine(tmp_db_path)
    try:
        # 第一次校验让暂存行落地，第二次已失效：该行必须留在不可召回的 orphan 态。
        engine.set_summary_source_validator(AsyncMock(side_effect=[True, False]))
        fence = SummarySourceFence(
            job_id="job-1",
            session_id="s1",
            session_epoch=1,
            start_seq=0,
            end_seq=1,
            expected_count=1,
            source_digest="digest",
            worker_generation=1,
            claim_token="claim",
            scope_key=_SCOPE,
            privacy_level="shared",
            resolver_revision=_REVISION,
        )

        with pytest.raises(RuntimeError, match="summary_source_fenced"):
            await engine.add_memory(fenced_body, session_id="s1", source_fence=fence)

        cursor = await db.execute("SELECT id, metadata FROM documents")
        memory_id, raw_metadata = await cursor.fetchone()
        stored = json.loads(raw_metadata)

        assert stored["summary_source_orphan"] is True
        assert stored["source_provenance_complete"] is True
        assert await _active_canonical_ids(db) == []

        reader = _cache_engine(_DocumentStorage(tmp_db_path))
        reader.db_path = tmp_db_path
        reader.hybrid_retriever.search = AsyncMock(
            return_value=[_result(memory_id, fenced_body)]
        )
        listed = await _list_memories(_PageApiHost(reader), {"keyword": "用户"})
        recalled = await _recall_test(_PageApiHost(reader), {"query": "用户"})

        assert listed["data"]["items"] == []
        assert recalled["data"]["results"] == []
        assert recalled["data"]["dropped_stale_count"] == 1
        assert fenced_body not in json.dumps(recalled, ensure_ascii=False)
    finally:
        await db.close()


# --- 写入面：删除 × 读取面：缓存命中 / JSONL 导出 / 列表 / 召回测试 ---


@pytest.mark.asyncio
async def test_deleted_row_is_absent_from_cache_hit_and_export(tmp_path) -> None:
    """删除提交后，缓存命中与 JSONL 导出都不得再返回该行。"""

    db_path = str(tmp_path / "delete-cache-export.db")
    await _create_schema(db_path)
    await _seed_row(db_path, 1, _OLD_BODY)
    await _seed_row(db_path, 2, _SURVIVOR_BODY)
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)

    async def _delete_canonical_row(memory_id: int) -> bool:
        """按存储层语义删除 canonical 行并返回成功。"""

        await _delete_row(db_path, memory_id)
        return True

    engine.hybrid_retriever.delete_memory = AsyncMock(side_effect=_delete_canonical_row)

    assert await engine.delete_memory(1) is True
    # 删除提交前在途请求写入的缓存条目仍可能落在缓存里：命中必须再按 canonical 剔除。
    key = engine._retrieval.cache_key("删除后查询", 5, None, None)
    engine._retrieval.set_cached(
        key, [_result(1, _OLD_BODY, {"revision_token": _REVISION})]
    )

    visible = await engine.search_memories("删除后查询", k=5)
    exporter = MemoryExporter(get_all_memories_cb=_export_rows(db_path))
    output = str(tmp_path / "export.jsonl")
    assert await exporter.export_jsonl(output) == 1
    records = [
        json.loads(line)
        for line in Path(output).read_text(encoding="utf-8").splitlines()
    ]

    assert await _read_row(db_path, 1) is None
    assert visible == []
    assert engine._last_search_timing["dropped_stale_count"] == 1
    assert records[0]["memory_id"] == 2
    assert records[0]["status"] == "active"
    assert _OLD_BODY not in "\n".join(str(record["content"]) for record in records)
    await storage.close()


@pytest.mark.asyncio
async def test_deleted_row_is_absent_from_list_and_recall_test(tmp_path) -> None:
    """删除后列表与召回测试都不返回该行，候选按失效计数。"""

    db_path = str(tmp_path / "delete-list-recall.db")
    await _create_schema(db_path)
    await _seed_row(db_path, 1, _OLD_BODY)
    await _seed_row(db_path, 2, _SURVIVOR_BODY)
    await _delete_row(db_path, 1)
    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.db_path = db_path
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result(1, _OLD_BODY)])

    listed = await _list_memories(_PageApiHost(engine), {"keyword": "用户"})
    recalled = await _recall_test(_PageApiHost(engine), {"query": "用户", "k": 5})

    assert listed["data"]["total"] == 1
    assert [item["id"] for item in listed["data"]["items"]] == [2]
    assert recalled["data"]["results"] == []
    assert recalled["data"]["dropped_stale_count"] == 1
    assert _OLD_BODY not in json.dumps(recalled, ensure_ascii=False)
    await storage.close()


# --- 写入面：归档（status 变更）× 读取面：列表 / 缓存命中 ---


@pytest.mark.asyncio
async def test_archived_row_visible_only_under_explicit_status_filter(tmp_path) -> None:
    """归档行只在显式 status 筛选下可见，active 视图与缓存命中召回都不返回它。"""

    db_path = str(tmp_path / "archive-surfaces.db")
    await _create_schema(db_path)
    revision = await _seed_row(db_path, 1, _OLD_BODY)
    write_engine = _engine_with_real_cas(_DocumentStorage(db_path))
    status_metadata: dict[str, Any] = {}
    set_memory_status(status_metadata, "archived", status_changed_at=1.0)

    assert await write_engine.update_memory(1, {"metadata": status_metadata}) is True

    storage = _DocumentStorage(db_path)
    reader = _cache_engine(storage)
    reader.db_path = db_path
    key = reader._retrieval.cache_key("归档查询", 5, None, None)
    reader._retrieval.set_cached(
        key, [_result(1, _OLD_BODY, {"revision_token": revision})]
    )

    active = await _list_memories(
        _PageApiHost(reader), {"status": "active", "keyword": "用户"}
    )
    archived = await _list_memories(
        _PageApiHost(reader), {"status": "archived", "keyword": "用户"}
    )
    visible = await reader.search_memories("归档查询", k=5)

    assert active["data"]["items"] == []
    assert [item["id"] for item in archived["data"]["items"]] == [1]
    assert visible == []
    assert reader._last_search_timing["dropped_stale_count"] == 1
    await storage.close()


# --- 写入面：CAS 正文改写 × 派生面：图 fact 节点 / 前瞻注入 ---


@pytest.mark.asyncio
async def test_add_then_cas_rewrite_removes_old_fact_from_derived_graph(
    tmp_db_path: str,
) -> None:
    """正常 add 建图后 CAS 换成新事实，图派生不得再保留旧事实节点或条目。"""

    old_fact, new_fact = "用户喜欢喝手冲咖啡", "用户改喝拿铁咖啡"
    old_body = f"{old_fact}，周末常去那家店坐一上午"
    new_body = f"{new_fact}，周末常去那家店坐一上午"
    engine, db, _host, _bm25 = await _staged_engine(tmp_db_path)
    try:
        manager, graph_store, _vectors = await _graph_manager(tmp_db_path)
        engine.graph_memory_manager = manager
        memory_id = await engine.add_memory(
            old_body,
            session_id="s1",
            metadata=_active_metadata(
                key_facts=[old_fact],
                fact_source_evidence=fact_evidence([old_fact]),
            ),
        )

        before = await graph_store.get_subgraph_for_memories(
            [memory_id], boundary=GraphBoundary(_SCOPE, "shared", _REVISION)
        )
        assert old_fact in _graph_texts(before)

        engine.hybrid_retriever = _cas_content_updater(db)
        assert (
            await engine.update_memory(
                memory_id,
                {
                    "content": new_body,
                    "metadata": {
                        "key_facts": [new_fact],
                        "fact_source_evidence": fact_evidence([new_fact]),
                    },
                },
                expected_revision=_REVISION,
            )
            is True
        )

        after = await graph_store.get_subgraph_for_memories(
            [memory_id], boundary=GraphBoundary(_SCOPE, "shared", "r2")
        )
        assert old_fact not in _graph_texts(after)
        assert new_fact in _graph_texts(after)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cas_rewrite_drops_stale_planned_atom_from_injection(
    tmp_db_path: str,
) -> None:
    """父 canonical 改写后，旧计划 Atom 不得再进入前瞻注入。"""

    plan_fact = "用户计划明天上午去牙科诊所复诊"
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        memory_id = await store.insert_document(
            "doc-1",
            plan_fact,
            _active_metadata(
                key_facts=[plan_fact],
                fact_source_evidence=fact_evidence([plan_fact]),
            ),
        )
        atom_store = AtomStore(tmp_db_path)
        await atom_store.initialize()
        engine.atom_store = atom_store
        await atom_store.insert(
            MemoryAtom(
                parent_memory_id=memory_id,
                atom_type=AtomType.PLANNED,
                content=plan_fact,
                event_time=time.time() + 3600.0,
                session_id="s1",
                metadata={"source_evidence": source_evidence(plan_fact)},
                source_evidence=source_evidence(plan_fact),
                parent_revision=_REVISION,
                parent_scope_key=_SCOPE,
                parent_privacy_level="shared",
            )
        )
        # 配置端口只回落默认值：前瞻召回开启、24h 窗口、3 条上限。
        recall = AuxiliaryRecall(SimpleNamespace(get=lambda _k, d=None: d), engine)
        call = {
            "session_id": "s1",
            "persona_id": None,
            "chat_type": "private",
            "deadline_monotonic": None,
        }

        planned = await recall.maybe_prospective_recall(**call)
        assert plan_fact in RecallRoutingMixin._prospective_context(planned)

        engine.hybrid_retriever = _cas_content_updater(db)
        assert (
            await engine.update_memory(
                memory_id, {"content": _NEW_BODY}, expected_revision=_REVISION
            )
            is True
        )

        after = await recall.maybe_prospective_recall(**call)
        assert plan_fact not in RecallRoutingMixin._prospective_context(after)
    finally:
        await db.close()


# --- 写入面：合并强化 / 事实元数据一侧 × 读取面：canonical 行 ---


def _dedup_document(body: str, facts: list[str]) -> DedupDocument:
    """构造检测器返回的既有 canonical 投影。"""

    return DedupDocument(
        1,
        body,
        {
            "scope_key": _SCOPE,
            "privacy_level": "shared",
            "chat_type": "group",
            "session_id": "s1",
            "memory_status": "active",
            "key_facts": list(facts),
            "fact_source_evidence": fact_evidence(facts),
        },
    )


def _near_duplicate_candidate(body: str, facts: list[str]) -> MergeCandidate:
    """构造与既有 canonical 近重复的反思候选。"""

    return MergeCandidate(
        content=f"{body}。",
        metadata={
            "scope_key": _SCOPE,
            "privacy_level": "shared",
            "chat_type": "group",
            "key_facts": list(facts),
            "fact_source_evidence": fact_evidence(facts),
        },
        importance=0.9,
        session_id="s1",
        persona_id=None,
        idempotency_key="matrix-merge-key",
    )


@pytest.mark.asyncio
async def test_merge_conflict_when_owner_facts_are_not_in_its_body(
    tmp_path,
) -> None:
    """owner 事实与正文不对齐时合并返回 CONFLICT，且不写回 canonical。"""

    db_path = str(tmp_path / "merge-conflict.db")
    await _create_schema(db_path)
    body = "团队改用关系型数据库保存日志快照，每天审阅两次文档并归档历史指标"
    stale_fact = "项目使用 SQLite 存储会话记录"
    await _seed_row(
        db_path,
        1,
        body,
        {
            "key_facts": [stale_fact],
            "fact_source_evidence": fact_evidence([stale_fact]),
        },
    )
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    writes: list[tuple[int, dict[str, Any], str | None]] = []

    async def _update(memory_id, updates, expected_revision):
        writes.append((memory_id, updates, expected_revision))
        return await engine.update_memory(memory_id, updates, expected_revision)

    async def _search(_query: DedupQuery):
        return [_dedup_document(body, [stale_fact])]

    coordinator = CanonicalMergeCoordinator(
        config_provider=lambda: MemoryDedupConfig(mode="enforce"),
        search_similar=_search,
        load_memory=engine.get_memory,
        update_memory=_update,
    )

    outcome = await coordinator.merge(_near_duplicate_candidate(body, [stale_fact]))

    assert outcome.status is MergeStatus.CONFLICT
    assert outcome.reason_code == DEDUP_REASON_MERGE_CONFLICT
    assert writes == []
    row = await _read_row(db_path, 1)
    assert row["text"] == body
    assert json.loads(row["metadata"])["key_facts"] == [stale_fact]
    await storage.close()


@pytest.mark.asyncio
async def test_one_sided_fact_metadata_is_rejected_without_touching_canonical(
    tmp_path,
) -> None:
    """只提供事实一侧时按 fact_evidence_mismatch 拒绝，canonical 行前后一致。"""

    db_path = str(tmp_path / "one-sided-facts.db")
    await _create_schema(db_path)
    await _seed_row(db_path, 1, _OLD_BODY, {"canonical_summary": _OLD_BODY})
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    before = await _read_row(db_path, 1)

    assert (
        await engine.update_memory(
            1,
            {"content": _NEW_BODY, "metadata": {"key_facts": [_NEW_BODY]}},
            expected_revision=_REVISION,
        )
        is False
    )

    assert engine.get_last_write_reason_code() == "fact_evidence_mismatch"
    assert await _read_row(db_path, 1) == before
    await storage.close()


# --- 读取面：聚焦图 API 的来源门 ---


def _graph_api_host(engine: Any, graph_store: Any) -> Any:
    """构造继承真实 ``GraphApiMixin`` 的聚焦图端点宿主。"""

    class _Host(GraphApiMixin):
        def _ok(self, data):
            return ok_response(data)

        def _error(self, message):
            return error_response(message)

        async def _ensure_plugin_ready(self):
            return {"memory_engine": engine}, None

        def _get_graph_store(self, _engine):
            return graph_store

        def _build_graph_view_payload(self, snapshot, stats, **kwargs):
            return {"snapshot": snapshot, "stats": stats, **kwargs}

    return _Host()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("variant", "denied"),
    [
        ("valid", False),
        ("orphan", True),
        ("mark_write", True),
        ("missing_fact_evidence", True),
    ],
)
async def test_graph_focus_gate_follows_canonical_source(
    tmp_path, variant: str, denied: bool
) -> None:
    """聚焦图 API 只在 canonical 来源通过读取门时查询图，否则拒绝且不泄露正文。"""

    db_path = str(tmp_path / f"graph-focus-{variant}.db")
    await _create_schema(db_path)
    canary = "GRAPH_FOCUS_PRIVATE_BODY_CANARY"
    metadata = _active_metadata(
        key_facts=[canary],
        fact_source_evidence=fact_evidence([canary]),
    )
    if variant == "orphan":
        metadata["summary_source_orphan"] = True
    elif variant == "mark_write":
        metadata["gate_disposition"] = "mark_write"
    elif variant == "missing_fact_evidence":
        metadata.pop("key_facts")
        metadata.pop("fact_source_evidence")
    await _seed_row(db_path, 1, canary, metadata)

    storage = _DocumentStorage(db_path)
    engine = _cache_engine(storage)
    engine.get_statistics = AsyncMock(return_value={"total": 1})
    graph_store = SimpleNamespace(
        get_subgraph_for_memories=AsyncMock(
            return_value={"nodes": [], "edges": [], "entries": [], "memories": []}
        )
    )
    host = _graph_api_host(engine, graph_store)
    request_mock = SimpleNamespace(
        get_json=AsyncMock(return_value={"memory_id": 1, "query": "canary"})
    )

    with patch("core.platform.transport.page_api.graph_api.request", request_mock):
        result = await host.query_graph()

    assert (result.get("code") == "graph_boundary_required") is denied
    assert canary not in json.dumps(result, ensure_ascii=False)
    assert graph_store.get_subgraph_for_memories.await_count == (0 if denied else 1)
    await storage.close()
