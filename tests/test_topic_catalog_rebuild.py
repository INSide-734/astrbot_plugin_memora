"""验证 catalog 新 generation 发布后的旧派生行回收。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.platform.composition import DerivedRebuildCoordinator


def _metadata(topic: str) -> str:
    """返回可参与 catalog 的最小 canonical metadata。"""

    return json.dumps(
        {
            "topics": [topic],
            "scope_key": "scope-a",
            "chat_type": "private",
            "privacy_level": "shared",
            "resolver_revision": "resolver-1",
            "source_provenance_complete": True,
            "status": "active",
        }
    )


async def _open_database(tmp_path) -> aiosqlite.Connection:
    """创建仅用于 generation 回收测试的 canonical 数据库。"""

    db = await aiosqlite.connect(tmp_path / "topic-rebuild.db")
    await db.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    await create_topic_catalog_schema(db)
    await db.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?)",
        (1, "正文", _metadata("旧话题"), "2026-01-01T00:00:00+00:00", "r1"),
    )
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_successful_rebuild_retires_previous_generation(tmp_path) -> None:
    """新 generation 复核成功后只回收旧派生行并保留 canonical。"""

    db = await _open_database(tmp_path)
    try:
        store = TopicCatalogStore(db)
        assert (await store.rebuild_from_canonical(now=10.0))["generation"] == 1
        await db.execute(
            "UPDATE documents SET metadata=?, updated_at=? WHERE id=1",
            (_metadata("新话题"), "r2"),
        )
        await db.commit()

        validator = MagicMock()
        validator._get_document_count = AsyncMock(return_value=1)
        engine = MagicMock()
        engine.rebuild_graph_index = AsyncMock(return_value={"success": True})
        engine.note_proposal_pipeline = None
        coordinator = DerivedRebuildCoordinator(
            validator,
            engine,
            MagicMock(mode="disabled"),
            store,
        )

        result = await coordinator.rebuild_all(rebuild_indexes=False)

        assert result["success"] is True
        assert await store.generation_counts(1) == (0, 0)
        assert await store.generation_counts(2) == (1, 1)
        assert await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone() == (1,)
    finally:
        await db.close()
