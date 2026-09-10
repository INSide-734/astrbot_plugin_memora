"""G3 EntityHierarchyStore 测试 — IS-A 树、循环检测、搜索扩展。"""

import pytest


class TestEntityHierarchyStore:
    @pytest.fixture
    def make_store(self):
        import os
        import tempfile

        import aiosqlite

        from core.features.memory.infrastructure.hierarchy_store import (
            EntityHierarchyStore,
        )

        async def _make():
            db_path = os.path.join(tempfile.mkdtemp(), "test_hierarchy.db")
            db = await aiosqlite.connect(db_path)
            store = EntityHierarchyStore(db)
            await store.init_table()
            return store, db, db_path

        return _make

    @pytest.mark.asyncio
    async def test_add_and_get_parents(self, make_store):
        store, db, _ = await make_store()
        assert await store.add_relation("dog", "animal")
        assert await store.add_relation("cat", "animal")
        assert "animal" in await store.get_parents("dog")
        assert "animal" in await store.get_parents("cat")
        await db.close()

    @pytest.mark.asyncio
    async def test_ancestors_multi_level(self, make_store):
        store, db, _ = await make_store()
        await store.add_relation("poodle", "dog")
        await store.add_relation("dog", "animal")
        await store.add_relation("animal", "living_thing")
        ancestors = await store.get_ancestors("poodle", max_depth=5)
        assert "dog" in ancestors
        assert "animal" in ancestors
        assert "living_thing" in ancestors
        await db.close()

    @pytest.mark.asyncio
    async def test_cycle_prevention(self, make_store):
        store, db, _ = await make_store()
        await store.add_relation("a", "b")
        await store.add_relation("b", "c")
        assert not await store.add_relation("c", "a")
        await db.close()

    @pytest.mark.asyncio
    async def test_self_reference_rejected(self, make_store):
        store, db, _ = await make_store()
        assert not await store.add_relation("x", "x")
        await db.close()

    @pytest.mark.asyncio
    async def test_detect_existing_cycle(self, make_store):
        store, db, _ = await make_store()
        await store.add_relation("x", "y")
        await store.add_relation("y", "z")
        await store._db.execute(
            "INSERT OR IGNORE INTO entity_hierarchy (child, parent) VALUES (?, ?)",
            ("z", "x"),
        )
        await store._db.commit()
        cycles = await store.detect_cycle()
        assert len(cycles) >= 1
        await db.close()

    @pytest.mark.asyncio
    async def test_search_wangcai_returns_pet(self, make_store):
        """G3: Searching for '旺财' should find '宠物' ancestors via IS-A tree."""
        store, db, _ = await make_store()
        await store.add_relation("旺财", "狗")
        await store.add_relation("狗", "宠物")
        ancestors = await store.get_ancestors("旺财", max_depth=3)
        assert "狗" in ancestors
        assert "宠物" in ancestors
        await db.close()
