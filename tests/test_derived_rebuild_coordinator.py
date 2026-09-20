"""验证统一派生重建协调器的顺序、降级和 canonical 保护。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.domain.memory_atom import MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from core.platform.composition import DatabaseSetup, DerivedRebuildCoordinator
from tests.fact_evidence_helpers import source_evidence

_CANONICAL_REVISION = "2026-09-18T00:00:00+00:00"


async def _canonical_db(
    path: Path,
    *,
    alive: tuple[int, ...] = (1, 2),
    deleted: tuple[int, ...] = (99,),
) -> aiosqlite.Connection:
    """创建带 ``documents`` 的真实 canonical 连接，供残留判定读取快照。

    ``deleted`` 先写入再删除：``AUTOINCREMENT`` 的 ID 序列水位线因此覆盖这些已删除
    来源，与生产库中「物理删除来源后留下派生行」的状态一致。
    """

    connection = await aiosqlite.connect(path)
    await connection.execute(
        "CREATE TABLE documents ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "text TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}', "
        "created_at TEXT, updated_at TEXT)"
    )
    for memory_id in (*alive, *deleted):
        await connection.execute(
            "INSERT INTO documents(id, text, metadata, created_at, updated_at) "
            "VALUES(?, '', ?, ?, ?)",
            (
                int(memory_id),
                json.dumps({"scope_key": "scope-a", "privacy_level": "public"}),
                _CANONICAL_REVISION,
                _CANONICAL_REVISION,
            ),
        )
    for memory_id in deleted:
        await connection.execute(
            "DELETE FROM documents WHERE id = ?", (int(memory_id),)
        )
    await connection.commit()
    return connection


def _build_components(*, evolution_mode: str = "active", db_connection=None):
    """创建不连接真实数据库的协调器测试替身。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=3)
    validator.rebuild_indexes = AsyncMock(
        return_value={
            "success": True,
            "processed": 3,
            "errors": 0,
            "total": 3,
        }
    )
    engine = MagicMock()
    engine.rebuild_graph_index = AsyncMock(return_value={"rebuilt": 3, "skipped": 0})
    engine.note_proposal_pipeline = None
    engine.db_connection = db_connection
    engine.atom_store = MagicMock()
    engine.atom_store.list_parent_ids = AsyncMock(return_value=[])
    engine.atom_store.batch_delete_by_parent = AsyncMock(return_value=0)
    engine.atom_lifecycle_manager = MagicMock()
    engine.atom_lifecycle_manager.rederive_for_sources = AsyncMock(
        return_value={"rederived": 3, "purged": 0, "skipped": 0, "failed": 0}
    )
    engine.faiss_db = MagicMock()
    engine.faiss_db.document_storage = MagicMock()
    engine.faiss_db.document_storage.count_documents = AsyncMock(return_value=3)
    engine.faiss_db.document_storage.get_documents = AsyncMock(
        return_value=[{"id": index} for index in range(1, 4)]
    )
    manager = MagicMock()
    manager.mode = evolution_mode
    manager.rebuild_from_canonical = AsyncMock(
        return_value={
            "success": True,
            "canonical_sources": 3,
            "scheduled_jobs": 3,
            "reason_code": "derived_rebuild_scheduled",
        }
    )
    return (
        DerivedRebuildCoordinator(validator, engine, manager),
        validator,
        engine,
        manager,
    )


@pytest.mark.asyncio
async def test_rebuild_all_runs_in_fixed_order(tmp_path: Path) -> None:
    """索引成功后才进入 graph，再进入 relation/projection 重建。"""

    connection = await _canonical_db(
        tmp_path / "memora.db", alive=(1, 2, 3), deleted=()
    )
    try:
        coordinator, validator, engine, manager = _build_components(
            db_connection=connection
        )
        order: list[str] = []

        async def rebuild_indexes(*_args):
            """记录索引阶段。"""

            return await _record_async(order, "indexes", {"success": True})

        async def rebuild_graph():
            """记录图阶段。"""

            return await _record_async(order, "graph", {"rebuilt": 3})

        async def rebuild_evolution():
            """记录 Evolution 阶段。"""

            return await _record_async(
                order,
                "evolution",
                {"success": True, "scheduled_jobs": 3},
            )

        validator.rebuild_indexes.side_effect = rebuild_indexes
        engine.rebuild_graph_index.side_effect = rebuild_graph
        manager.rebuild_from_canonical.side_effect = rebuild_evolution

        result = await coordinator.rebuild_all()
    finally:
        await connection.close()

    assert result["success"] is True
    assert order == ["indexes", "graph", "evolution"]
    assert result["canonical"]["documents"] == 3


async def _record_async(order: list[str], name: str, result: dict) -> dict:
    """记录阶段调用顺序并返回阶段结果。"""

    order.append(name)
    return result


@pytest.mark.asyncio
async def test_index_failure_keeps_canonical_and_continues_degraded_rebuild(
    tmp_path: Path,
) -> None:
    """FTS/向量阶段失败时仍可报告后续阶段，但整体必须降级。"""

    connection = await _canonical_db(
        tmp_path / "memora.db", alive=(1, 2, 3), deleted=()
    )
    coordinator, validator, engine, manager = _build_components(
        db_connection=connection
    )
    validator.rebuild_indexes.return_value = {
        "success": False,
        "processed": 1,
        "errors": 2,
        "total": 3,
    }
    try:
        result = await coordinator.rebuild_all()
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["degraded"] is True
    assert result["reason_code"] == "index_rebuild_failed"
    assert result["canonical"]["documents"] == 3
    assert result["stages"]["graph"]["status"] == "completed"
    assert result["stages"]["evolution"]["status"] == "completed"
    validator._get_document_count.assert_awaited_once_with()
    manager.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_graph_failure_does_not_claim_all_derived_success(
    tmp_path: Path,
) -> None:
    """图重建异常时 relation/projection 阶段仍执行，但结果保持失败。"""

    connection = await _canonical_db(
        tmp_path / "memora.db", alive=(1, 2, 3), deleted=()
    )
    coordinator, _validator, engine, manager = _build_components(
        db_connection=connection
    )
    engine.rebuild_graph_index.side_effect = RuntimeError("graph provider failed")
    try:
        result = await coordinator.rebuild_all()
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["reason_code"] == "graph_rebuild_failed"
    assert result["stages"]["graph"]["reason_code"] == "graph_rebuild_failed"
    manager.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_evolution_failure_returns_stable_degraded_result(
    tmp_path: Path,
) -> None:
    """派生 relation/projection 重建失败时不得伪装成完整成功。"""

    connection = await _canonical_db(
        tmp_path / "memora.db", alive=(1, 2, 3), deleted=()
    )
    coordinator, _validator, _engine, manager = _build_components(
        db_connection=connection
    )
    manager.rebuild_from_canonical.return_value = {
        "success": False,
        "reason_code": "derived_rebuild_failed",
    }
    try:
        result = await coordinator.rebuild_all()
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["reason_code"] == "derived_rebuild_failed"
    assert result["stages"]["evolution"]["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_canonical_stops_before_mutating_derived_indexes() -> None:
    """无法读取 canonical 时不应调用任何派生重建入口。"""

    coordinator, validator, engine, manager = _build_components()
    validator._get_document_count.side_effect = RuntimeError("database unavailable")

    result = await coordinator.rebuild_all()

    assert result["success"] is False
    assert result["reason_code"] == "canonical_unavailable"
    validator.rebuild_indexes.assert_not_awaited()
    engine.rebuild_graph_index.assert_not_awaited()
    manager.rebuild_from_canonical.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_rebuild_propagates() -> None:
    """取消信号必须向调用方传播，不能被阶段降级逻辑吞掉。"""

    coordinator, validator, _engine, _manager = _build_components()
    validator.rebuild_indexes.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await coordinator.rebuild_all()


@pytest.mark.asyncio
async def test_disabled_evolution_is_reported_as_skipped(tmp_path: Path) -> None:
    """关闭 Evolution 时不创建派生任务，但索引与图重建仍可成功。"""

    connection = await _canonical_db(
        tmp_path / "memora.db", alive=(1, 2, 3), deleted=()
    )
    coordinator, _validator, _engine, manager = _build_components(
        evolution_mode="disabled", db_connection=connection
    )
    try:
        result = await coordinator.rebuild_all()
    finally:
        await connection.close()

    assert result["success"] is True
    assert result["stages"]["evolution"]["status"] == "skipped"
    manager.rebuild_from_canonical.assert_not_awaited()


def _atoms_engine(
    db_connection,
    *,
    rederive_result: dict | None = None,
    parents: tuple[int, ...] = (1, 2, 99),
    storage=None,
):
    """构造带 canonical 快照与 Atom 端口的重建引擎替身。"""

    storage = storage or SimpleNamespace(
        count_documents=AsyncMock(return_value=2),
        get_documents=AsyncMock(return_value=[{"id": 1}, {"id": 2}]),
    )
    store = MagicMock()
    store.list_parent_ids = AsyncMock(return_value=list(parents))
    store.batch_delete_by_parent = AsyncMock(return_value=1)
    manager = MagicMock()
    manager.rederive_for_sources = AsyncMock(
        return_value=rederive_result
        or {"rederived": 2, "purged": 0, "skipped": 0, "failed": 0}
    )
    engine = SimpleNamespace(
        db_connection=db_connection,
        faiss_db=SimpleNamespace(document_storage=storage),
        atom_store=store,
        atom_lifecycle_manager=manager,
    )
    return engine, store, manager


@pytest.mark.asyncio
async def test_atoms_stage_rederives_sources_and_cleans_residue(tmp_path: Path) -> None:
    """atoms 阶段按 canonical 分页重派生，并清除父已不存在的残留行。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, store, manager = _atoms_engine(connection)
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["atoms"])
    finally:
        await connection.close()

    assert result["success"] is True
    stage = result["stages"]["atoms"]
    assert stage["status"] == "completed"
    assert stage["rebuilt"] == 2
    assert stage["residue_cleaned"] == 1
    assert stage["total"] == 2
    manager.rederive_for_sources.assert_awaited_once_with([1, 2], "rebuild_atoms")
    store.batch_delete_by_parent.assert_awaited_once_with([99])


@pytest.mark.asyncio
async def test_atoms_stage_keeps_parent_added_during_scan(tmp_path: Path) -> None:
    """扫描期间新增来源的 Atom 行必须保留，已删除父来源的残留行仍被回收。"""

    db_path = tmp_path / "memora.db"
    connection = await _canonical_db(db_path, alive=(1, 2, 3), deleted=())
    store = AtomStore(str(db_path))
    await store.initialize()

    def _atom(parent_memory_id: int) -> MemoryAtom:
        """构造带父来源证据的 Atom 行。"""

        content = f"atom-{parent_memory_id}"
        return MemoryAtom(
            parent_memory_id=parent_memory_id,
            content=content,
            source_evidence=source_evidence(content),
            parent_revision=_CANONICAL_REVISION,
            parent_scope_key="scope-a",
            parent_privacy_level="public",
        )

    await store.insert_many([_atom(parent_id) for parent_id in (1, 2, 3)])
    # 物理删除来源 3：Atom 行残留，ID 序列水位线仍覆盖 3。
    await connection.execute("DELETE FROM documents WHERE id = 3")
    await connection.commit()

    async def add_concurrent_source() -> None:
        """扫描期间写入 canonical 行与派生 Atom 行，模拟并发新增来源。"""

        await connection.execute(
            "INSERT INTO documents(id, text, metadata, created_at, updated_at) "
            "VALUES(4, '正文', ?, ?, ?)",
            (
                json.dumps({"scope_key": "scope-a", "privacy_level": "public"}),
                _CANONICAL_REVISION,
                _CANONICAL_REVISION,
            ),
        )
        await connection.commit()
        await store.insert(_atom(4))

    pages = 0

    async def get_documents(metadata_filters, limit=None, offset=0):
        """返回一页 canonical 来源，并在首屏之后触发并发写入。"""

        nonlocal pages
        pages += 1
        if pages == 1:
            await add_concurrent_source()
            return [{"id": 1}, {"id": 2}]
        return []

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    engine, _port_store, _manager = _atoms_engine(
        connection,
        storage=SimpleNamespace(
            count_documents=AsyncMock(return_value=2),
            get_documents=AsyncMock(side_effect=get_documents),
        ),
    )
    engine.atom_store = store
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["atoms"])
        kept_atoms = [atom.content for atom in await store.get_by_parent_raw(4)]
        reaped_atoms = await store.get_by_parent_raw(3)
    finally:
        await connection.close()

    assert result["success"] is True
    assert result["stages"]["atoms"]["residue_cleaned"] == 1
    assert kept_atoms == ["atom-4"]
    assert reaped_atoms == []


@pytest.mark.asyncio
async def test_atoms_stage_runs_between_indexes_and_graph(tmp_path: Path) -> None:
    """atoms 阶段在固定顺序中位于 indexes 之后、graph 之前，与请求顺序无关。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    order: list[str] = []

    async def rebuild_indexes(*_args):
        """记录索引阶段。"""

        return await _record_async(order, "indexes", {"success": True})

    async def rederive(memory_ids, reason):
        """记录 atoms 阶段。"""

        return await _record_async(
            order,
            "atoms",
            {"rederived": len(memory_ids), "purged": 0, "skipped": 0, "failed": 0},
        )

    async def rebuild_graph():
        """记录图阶段。"""

        return await _record_async(order, "graph", {"rebuilt": 2})

    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, _store, manager = _atoms_engine(connection)
    manager.rederive_for_sources.side_effect = rederive
    engine.rebuild_graph_index = AsyncMock(side_effect=rebuild_graph)
    validator.rebuild_indexes = AsyncMock(side_effect=rebuild_indexes)
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["graph", "atoms", "indexes"])
    finally:
        await connection.close()

    assert order == ["indexes", "atoms", "graph"]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_atoms_stage_partial_failure_degrades_with_reason(tmp_path: Path) -> None:
    """单来源重派生失败时阶段降级计数，不伪装成完整成功。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, store, _manager = _atoms_engine(
        connection,
        rederive_result={"rederived": 1, "purged": 0, "skipped": 0, "failed": 1},
    )
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["atoms"])
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["degraded"] is True
    assert result["reason_code"] == "atoms_rebuild_partial_failed"
    assert result["stages"]["atoms"]["reason_code"] == "atoms_rebuild_partial_failed"
    assert result["stages"]["atoms"]["failed"] == 1
    store.batch_delete_by_parent.assert_awaited_once_with([99])


@pytest.mark.asyncio
async def test_atoms_stage_residue_failure_degrades_with_reason(tmp_path: Path) -> None:
    """残留清理失败只降级计数，不影响已重派生的来源。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, store, _manager = _atoms_engine(connection)
    store.batch_delete_by_parent = AsyncMock(side_effect=RuntimeError("cleanup_failed"))
    engine.atom_store.batch_delete_by_parent = store.batch_delete_by_parent
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["atoms"])
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["reason_code"] == "atoms_rebuild_partial_failed"
    assert result["stages"]["atoms"]["rebuilt"] == 2
    assert result["stages"]["atoms"]["residue_failed"] == 1


@pytest.mark.asyncio
async def test_atoms_stage_batch_failure_degrades_with_reason(tmp_path: Path) -> None:
    """批次级重派生失败只降级计数，不中断后续阶段。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, store, manager = _atoms_engine(connection)
    manager.rederive_for_sources.side_effect = RuntimeError(
        "canonical_source_unavailable"
    )
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["atoms"])
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["reason_code"] == "atoms_rebuild_partial_failed"
    assert result["stages"]["atoms"]["failed"] == 2
    # 批次失败不阻止残留清理：父已不存在的行仍被回收。
    store.batch_delete_by_parent.assert_awaited_once_with([99])


@pytest.mark.asyncio
async def test_atoms_stage_cancellation_propagates(tmp_path: Path) -> None:
    """atoms 阶段取消继续传播，不降级成失败计数。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, _store, manager = _atoms_engine(connection)
    manager.rederive_for_sources.side_effect = asyncio.CancelledError()
    coordinator = DerivedRebuildCoordinator(validator, engine)

    try:
        with pytest.raises(asyncio.CancelledError):
            await coordinator.rebuild_stages(["atoms"])
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_atoms_stage_residue_scan_failure_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    """残留父来源枚举失败必须降级为失败计数并暴露稳定原因码，不得报阶段成功。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    connection = await _canonical_db(tmp_path / "memora.db", alive=(1, 2))
    engine, store, _manager = _atoms_engine(connection)
    store.list_parent_ids = AsyncMock(
        side_effect=RuntimeError("atom_store_unavailable")
    )
    coordinator = DerivedRebuildCoordinator(validator, engine)
    try:
        result = await coordinator.rebuild_stages(["atoms"])
    finally:
        await connection.close()

    assert result["success"] is False
    assert result["reason_code"] == "atoms_rebuild_partial_failed"
    stage = result["stages"]["atoms"]
    assert stage["status"] == "failed"
    assert stage["residue_failed"] >= 1
    assert stage["residue_reason_code"] == "atom_residue_scan_failed"
    # 枚举失败不阻断已完成的来源重派生。
    assert stage["rebuilt"] == 2
    store.batch_delete_by_parent.assert_not_awaited()


@pytest.mark.asyncio
async def test_atoms_stage_fails_closed_without_canonical_snapshot() -> None:
    """canonical 连接缺失时不得按不完整集合回收父来源，必须降级并暴露原因码。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    engine, store, _manager = _atoms_engine(None)
    store.list_parent_ids = AsyncMock(return_value=[1, 2, 99])
    coordinator = DerivedRebuildCoordinator(validator, engine)

    result = await coordinator.rebuild_stages(["atoms"])

    assert result["success"] is False
    stage = result["stages"]["atoms"]
    assert stage["status"] == "failed"
    assert stage["residue_reason_code"] == "atom_residue_scan_failed"
    assert stage["residue_cleaned"] == 0
    store.batch_delete_by_parent.assert_not_awaited()


@pytest.mark.asyncio
async def test_atoms_stage_skips_without_atom_components() -> None:
    """未装配 Atom 组件时 atoms 阶段按跳过报告，不读取 canonical 文档。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    engine = SimpleNamespace(faiss_db=SimpleNamespace(document_storage=MagicMock()))
    coordinator = DerivedRebuildCoordinator(validator, engine)

    result = await coordinator.rebuild_stages(["atoms"])

    assert result["success"] is True
    assert result["stages"]["atoms"]["reason_code"] == "atoms_rebuild_unavailable"
    engine.faiss_db.document_storage.get_documents.assert_not_called()


@pytest.mark.asyncio
async def test_database_setup_uses_coordinator_for_inconsistent_indexes() -> None:
    """启动维护路径应委托协调器，而不是绕过 graph/evolution 阶段。"""

    validator = MagicMock()
    validator.check_consistency = AsyncMock(
        return_value=SimpleNamespace(
            is_consistent=False,
            needs_rebuild=True,
            reason="索引缺失",
            documents_count=3,
            bm25_count=1,
            vector_count=1,
        )
    )
    validator.rebuild_indexes = AsyncMock()
    coordinator = MagicMock()
    coordinator.rebuild_all = AsyncMock(
        return_value={"success": True, "reason_code": "derived_rebuild_completed"}
    )

    result = await DatabaseSetup.auto_rebuild_index_if_needed(
        validator,
        MagicMock(),
        coordinator,
    )

    assert result["success"] is True
    coordinator.rebuild_all.assert_awaited_once_with()
    validator.rebuild_indexes.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_stage_request_keeps_documented_chronology() -> None:
    """请求全部阶段（含乱序与显式 canonical）时，执行与报告顺序都服从固定阶段闭包。"""

    order: list[str] = []

    async def indexes_rebuild(_engine) -> dict:
        """记录 indexes 阶段。"""

        order.append("indexes")
        return {"success": True}

    async def catalog_rebuild() -> dict:
        """记录 catalog 阶段并声明已发布 generation。"""

        order.append("catalog")
        return {"success": True, "generation": 1}

    async def graph_rebuild() -> dict:
        """记录 graph 阶段。"""

        order.append("graph")
        return {"rebuilt": 2}

    async def evolution_rebuild() -> dict:
        """记录 evolution 阶段。"""

        order.append("evolution")
        return {"success": True, "scheduled_jobs": 2}

    async def compression_rebuild() -> dict:
        """记录 semantic_compression 阶段。"""

        order.append("semantic_compression")
        return {"success": True}

    async def notes_rebuild() -> dict:
        """记录 notes 阶段。"""

        order.append("notes")
        return {"success": True}

    validator = SimpleNamespace(
        _get_document_count=AsyncMock(return_value=2),
        rebuild_indexes=AsyncMock(side_effect=indexes_rebuild),
    )
    catalog = SimpleNamespace(
        get_state=AsyncMock(return_value={"status": "ready", "active_generation": 1}),
        rebuild_from_canonical=AsyncMock(side_effect=catalog_rebuild),
        verify_published_generation=AsyncMock(return_value=True),
    )
    manager = SimpleNamespace(
        mode="active",
        rebuild_from_canonical=AsyncMock(side_effect=evolution_rebuild),
    )
    engine = SimpleNamespace(
        rebuild_graph_index=AsyncMock(side_effect=graph_rebuild),
        semantic_compressor=SimpleNamespace(
            rebuild_from_canonical=AsyncMock(side_effect=compression_rebuild)
        ),
        note_proposal_pipeline=SimpleNamespace(
            rebuild_from_canonical=AsyncMock(side_effect=notes_rebuild)
        ),
    )
    coordinator = DerivedRebuildCoordinator(validator, engine, manager, catalog)

    result = await coordinator.rebuild_stages(
        [
            "canonical",
            "notes",
            "evolution",
            "graph",
            "atoms",
            "catalog",
            "indexes",
            "semantic_compression",
        ]
    )

    assert order == [
        "indexes",
        "catalog",
        "graph",
        "evolution",
        "semantic_compression",
        "notes",
    ]
    assert list(result["stages"]) == [
        "indexes",
        "catalog",
        "atoms",
        "graph",
        "evolution",
        "semantic_compression",
        "notes",
    ]
    # 未装配 Atom 组件只影响该阶段状态，不改变它在闭包中的位置。
    assert result["stages"]["atoms"]["reason_code"] == "atoms_rebuild_unavailable"
    assert result["success"] is True
