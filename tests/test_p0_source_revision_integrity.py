"""P0 canonical/source revision 不变量的失败驱动测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.managers.memory_engine import MemoryEngine
from core.models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
)
from core.retrieval.vector_retriever import VectorRetriever
from core.storage.memory_evolution_store import MemoryEvolutionStore

UTC = timezone.utc


def _plan(revision: str = "r17") -> DerivedApplyPlan:
    return DerivedApplyPlan(
        relations=(
            RelationView(
                "relation-17-18",
                17,
                18,
                RelationType.RELATED,
                0.9,
                "private:user-a",
                "shared",
                DerivedState.ACTIVE,
                revision,
                "r18",
            ),
        ),
        projections=(
            ProjectionView(
                "projection-17-18",
                ProjectionType.EPISODE_SUMMARY,
                "同一事件的两条证据",
                (17, 18),
                "private:user-a",
                "shared",
                0.8,
            ),
        ),
        projection_sources=(
            ProjectionSourceView("projection-17-18", 17, revision, "primary", 0),
            ProjectionSourceView("projection-17-18", 18, "r18", "supporting", 1),
        ),
    )


async def _create_documents(path: str, *, revision: str = "r17") -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        await db.executemany(
            "INSERT INTO documents(id,doc_id,text,metadata,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                (
                    17,
                    "doc-17",
                    "证据 17",
                    json.dumps(
                        {"scope_key": "private:user-a", "privacy_level": "shared"}
                    ),
                    datetime(2026, 7, 20, tzinfo=UTC).isoformat(),
                    revision,
                ),
                (
                    18,
                    "doc-18",
                    "证据 18",
                    json.dumps(
                        {"scope_key": "private:user-a", "privacy_level": "shared"}
                    ),
                    datetime(2026, 7, 20, tzinfo=UTC).isoformat(),
                    "r18",
                ),
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_apply_plan_rejects_missing_canonical_source(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path)

    missing = DerivedApplyPlan(
        relations=(
            RelationView(
                "relation-17-99",
                17,
                99,
                RelationType.RELATED,
                0.9,
                "private:user-a",
                "shared",
                DerivedState.ACTIVE,
                "r17",
                "r99",
            ),
        ),
        projections=(),
        projection_sources=(),
    )
    with pytest.raises(ValueError, match="source_memory_not_found"):
        await store.apply_derived_plan(missing)

    await store.close()


@pytest.mark.asyncio
async def test_apply_plan_rejects_stale_canonical_revision(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path, revision="r17-current")

    with pytest.raises(ValueError, match="source_revision_mismatch"):
        await store.apply_derived_plan(_plan("r17-old"))

    await store.close()


@pytest.mark.asyncio
async def test_deleted_source_is_not_active_for_derived_reads(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path)
    await store.apply_derived_plan(_plan())

    assert await store.invalidate_for_deleted_source(17) == 2
    assert await store.active_relations_for_seeds([17]) == []
    assert await store.active_projection_bundles_for_seeds([17]) == []

    await store.close()


@pytest.mark.asyncio
async def test_update_metadata_advances_document_revision():
    session = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    document_storage = MagicMock()
    document_storage.get_documents = AsyncMock(
        return_value=[{"id": 17, "metadata": json.dumps({"scope_key": "scope"})}]
    )
    document_storage.get_session.return_value = session_context
    faiss_db = MagicMock(document_storage=document_storage)
    retriever = VectorRetriever(faiss_db)

    assert await retriever.update_metadata(17, {"importance": 0.9}) is True
    statement = session.execute.await_args.args[0]
    assert "updated_at" in str(statement)


@pytest.mark.asyncio
async def test_operational_metadata_update_preserves_document_revision():
    """运行态计数更新不得改变 canonical source revision。"""

    session = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    document_storage = MagicMock()
    document_storage.get_documents = AsyncMock(
        return_value=[
            {
                "id": 17,
                "metadata": json.dumps({"access_count": 1}),
                "updated_at": "r-current",
            }
        ]
    )
    document_storage.get_session.return_value = session_context
    faiss_db = MagicMock(document_storage=document_storage)
    retriever = VectorRetriever(faiss_db)

    assert (
        await retriever.update_metadata(
            17,
            {"access_count": 2},
            advance_revision=False,
        )
        is True
    )
    statement = session.execute.await_args.args[0]
    assert "SET metadata = :metadata" in str(statement)
    assert "updated_at = :updated_at" not in str(statement)


@pytest.mark.asyncio
async def test_engine_rejects_stale_update_and_invalidates_deleted_source():
    faiss_db = MagicMock()
    faiss_db.document_storage = MagicMock()
    faiss_db.document_storage.get_documents = AsyncMock(
        return_value=[
            {
                "id": 17,
                "text": "原文",
                "metadata": {"scope_key": "private:user-a"},
                "updated_at": "r-current",
            }
        ]
    )
    engine = MemoryEngine(db_path=":memory:", faiss_db=faiss_db)
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    evolution_store = MagicMock()
    evolution_store.invalidate_for_deleted_source = AsyncMock(return_value=1)
    engine.memory_evolution_store = evolution_store

    assert (
        await engine.update_memory(17, {"importance": 0.9}, expected_revision="r-stale")
        is False
    )
    engine.hybrid_retriever.update_metadata.assert_not_awaited()

    engine.hybrid_retriever.delete_memory = AsyncMock(return_value=True)
    engine.graph_memory_manager = None
    engine.atom_store = None
    assert await engine.delete_memory(17) is True
    evolution_store.invalidate_for_deleted_source.assert_awaited_once_with(17)


@pytest.mark.asyncio
async def test_engine_schedules_evolution_only_after_canonical_add_succeeds():
    faiss_db = MagicMock()
    engine = MemoryEngine(db_path=":memory:", faiss_db=faiss_db)
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.add_memory = AsyncMock(return_value=17)
    engine.graph_memory_manager = None
    engine.atom_store = None
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._retrieval.apply_interference = MagicMock(return_value=None)
    engine._retrieval.extract_triggers = MagicMock(return_value=None)
    engine._create_tracked_task = MagicMock()
    source = MagicMock(memory_id=17)
    manager = MagicMock()
    manager.store.load_sources = AsyncMock(return_value=[source])
    manager.schedule_consider = AsyncMock()
    engine.memory_evolution_manager = manager

    assert await engine.add_memory("canonical") == 17
    manager.store.load_sources.assert_awaited_once_with((17,))
    manager.schedule_consider.assert_awaited_once_with(source)

    manager.store.load_sources.reset_mock()
    manager.schedule_consider.reset_mock()
    engine.hybrid_retriever.add_memory.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        await engine.add_memory("failed")
    manager.store.load_sources.assert_not_awaited()
    manager.schedule_consider.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_job_invalidates_only_its_derived_objects(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path)
    await store.apply_derived_plan(
        DerivedApplyPlan(
            relations=_plan().relations,
            projections=_plan().projections,
            projection_sources=_plan().projection_sources,
            origin_job_id="job-1",
        )
    )

    assert await store.rollback_job("job-1") == 2
    assert await store.active_relations_for_seeds([17]) == []
    assert await store.active_projection_bundles_for_seeds([17]) == []
    await store.close()


@pytest.mark.asyncio
async def test_cleanup_orphaned_derived_preserves_projection_with_other_sources(
    tmp_path,
):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path)
    await store.apply_derived_plan(_plan())
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute("DELETE FROM documents WHERE id=?", (18,))
        await db.commit()

    changed = await store.cleanup_orphaned_derived()
    assert changed >= 2
    assert await store.active_relations_for_seeds([17]) == []
    bundles = await store.active_projection_bundles_for_seeds([17])
    assert len(bundles) == 1
    assert bundles[0].projection.source_memory_ids == (17,)
    await store.close()


@pytest.mark.asyncio
async def test_rebuild_from_canonical_invalidates_old_and_requeues_sources(tmp_path):
    from core.managers.memory_evolution_gate import MemoryEvolutionGate
    from core.managers.memory_evolution_manager import MemoryEvolutionManager

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path)
    await store.apply_derived_plan(
        _plan(),
    )
    manager = MemoryEvolutionManager(
        store,
        MemoryEvolutionGate(
            {
                "enabled": True,
                "mode": "shadow",
                "trigger_threshold": 0.5,
                "max_pending_jobs": 20,
            }
        ),
        AsyncMock(),
        {"enabled": True, "mode": "shadow", "trigger_threshold": 0.5},
    )

    result = await manager.rebuild_from_canonical()

    assert result["success"] is True
    assert result["canonical_sources"] == 2
    assert result["scheduled_jobs"] == 2
    assert await store.active_relations_for_seeds([17]) == []
    assert await store.pending_count() == 2
    await manager.stop()
    await store.close()


@pytest.mark.asyncio
async def test_rebuild_failure_returns_degraded_result_without_losing_canonical(
    tmp_path,
):
    from core.managers.memory_evolution_gate import MemoryEvolutionGate
    from core.managers.memory_evolution_manager import MemoryEvolutionManager

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _create_documents(store.db_path)
    store.invalidate_all_derived = AsyncMock(
        side_effect=RuntimeError("derived backend unavailable")
    )
    manager = MemoryEvolutionManager(
        store,
        MemoryEvolutionGate({"enabled": True, "mode": "shadow"}),
        AsyncMock(),
        {"enabled": True, "mode": "shadow"},
    )

    result = await manager.rebuild_from_canonical()
    sources = await store.load_all_sources()

    assert result == {
        "success": False,
        "canonical_sources": 0,
        "scheduled_jobs": 0,
        "reason_code": "derived_rebuild_failed",
    }
    assert [source.memory_id for source in sources] == [17, 18]
    await manager.stop()
    await store.close()


@pytest.mark.asyncio
async def test_rebuild_propagates_cancellation():
    from core.managers.memory_evolution_manager import MemoryEvolutionManager

    store = MagicMock()
    store.invalidate_all_derived = AsyncMock(side_effect=asyncio.CancelledError())
    manager = MemoryEvolutionManager(store, MagicMock(), MagicMock(), {})

    with pytest.raises(asyncio.CancelledError):
        await manager.rebuild_from_canonical()
