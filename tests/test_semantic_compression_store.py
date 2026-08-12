"""语义摘要 Projection 的真实 SQLite 幂等与 revision 失效测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.features.evolution.application import MemoryEvolutionManager
from core.features.evolution.domain import ProjectionType
from core.features.evolution.infrastructure import MemoryEvolutionStore
from core.managers.semantic_compressor import SemanticCompressor

UTC = timezone.utc
NOW = datetime(2026, 8, 1, tzinfo=UTC)


async def _seed_documents(store: MemoryEvolutionStore) -> None:
    """创建两条同边界、同 topic 的旧 canonical 记忆。"""

    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "id INTEGER PRIMARY KEY,doc_id TEXT,text TEXT,metadata TEXT,"
            "created_at TEXT,updated_at TEXT)"
        )
        metadata = (
            '{"scope_key":"private:user-a","privacy_level":"shared",'
            '"topics":["python","ai"]}'
        )
        await db.executemany(
            "INSERT INTO documents(id,doc_id,text,metadata,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            [
                (
                    17,
                    "d17",
                    "旧记忆一",
                    metadata,
                    "2026-05-01T00:00:00+00:00",
                    "rev-17",
                ),
                (
                    18,
                    "d18",
                    "旧记忆二",
                    metadata,
                    "2026-05-02T00:00:00+00:00",
                    "rev-18",
                ),
            ],
        )
        await db.commit()


def _manager(store: MemoryEvolutionStore) -> MemoryEvolutionManager:
    """构造使用真实 Store、但不启动 worker 的 Evolution Manager。"""

    return MemoryEvolutionManager(
        store,
        SimpleNamespace(mode="active"),
        AsyncMock(),
        {
            "enabled": True,
            "mode": "active",
            "trigger_threshold": 0.7,
            "candidate_limit": 16,
        },
    )


@pytest.mark.asyncio
async def test_semantic_projection_is_idempotent_and_rebuilt_for_new_revision(
    tmp_path,
) -> None:
    """重复扫描不应重复写，任一来源变化后旧摘要应失效并按新 revision 重建。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _seed_documents(store)
    manager = _manager(store)
    compressor = SemanticCompressor(
        source_store=store,
        proposal_applier=manager.apply_projection_proposal,
        age_days=60,
        similarity_threshold=0.8,
    )
    try:
        first = await compressor.compress_old_memories(now=NOW)
        second = await compressor.compress_old_memories(now=NOW)
        bundles = await store.active_projection_bundles_for_seeds(
            [17],
            scope_key="private:user-a",
        )

        assert first["projections_applied"] == 1
        assert second["projections_applied"] == 1
        assert len(bundles) == 1
        assert bundles[0].projection.projection_type is ProjectionType.SEMANTIC_SUMMARY
        assert {item.revision_token for item in bundles[0].sources} == {
            "rev-17",
            "rev-18",
        }

        async with aiosqlite.connect(store.db_path) as db:
            await db.execute(
                "UPDATE documents SET updated_at=? WHERE id=?",
                ("rev-18-new", 18),
            )
            await db.commit()
        await store.invalidate_for_source_revision(18, "rev-18-new")
        assert (
            await store.active_projection_bundles_for_seeds(
                [17],
                scope_key="private:user-a",
            )
            == []
        )

        rebuilt = await compressor.compress_old_memories(now=NOW)
        current = await store.active_projection_bundles_for_seeds(
            [17],
            scope_key="private:user-a",
        )
        assert rebuilt["projections_applied"] == 1
        assert len(current) == 1
        assert {item.revision_token for item in current[0].sources} == {
            "rev-17",
            "rev-18-new",
        }
        async with aiosqlite.connect(store.db_path) as db:
            texts = await (
                await db.execute("SELECT text FROM documents ORDER BY id")
            ).fetchall()
        assert [row[0] for row in texts] == ["旧记忆一", "旧记忆二"]

        async with aiosqlite.connect(store.db_path) as db:
            await db.execute("DELETE FROM documents WHERE id=?", (18,))
            await db.commit()
        await store.invalidate_for_deleted_source(18)
        assert (
            await store.active_projection_bundles_for_seeds(
                [17],
                scope_key="private:user-a",
            )
            == []
        )
    finally:
        await manager.stop()
        await store.close()
