"""AtomStore 测试 — 插入、获取、生命周期、统计、FTS和清理。"""

import asyncio
import time

import pytest

from core.features.memory.domain.memory_atom import AtomStatus, AtomType, MemoryAtom
from core.storage.atom_store import AtomStore


def _make_atom(**overrides) -> MemoryAtom:
    """Create a MemoryAtom with test defaults."""
    defaults = dict(
        parent_memory_id=1,
        atom_type=AtomType.FACTUAL,
        content="测试记忆内容",
        importance=0.6,
        confidence=0.8,
        session_id="sess-1",
        persona_id="p1",
    )
    defaults.update(overrides)
    return MemoryAtom(**defaults)  # type: ignore[arg-type]


class TestAtomStoreCRUD:
    """Basic CRUD operations for AtomStore."""

    @pytest.mark.asyncio
    async def test_insert_and_get(self, tmp_db_path):
        """Insert one atom then retrieve it by id."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom = _make_atom(content="西湖很美")
        atom_id = await store.insert(atom)
        assert atom_id > 0
        assert atom.atom_id == atom_id

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.content == "西湖很美"
        assert fetched.atom_type == AtomType.FACTUAL
        assert fetched.status == AtomStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, tmp_db_path):
        """Getting a non-existent id returns None."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        assert await store.get(99999) is None

    @pytest.mark.asyncio
    async def test_insert_many(self, tmp_db_path):
        """Insert multiple atoms in a batch and verify all stored."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atoms = [
            _make_atom(content=f"记忆_{i}", parent_memory_id=10 + i) for i in range(5)
        ]
        ids = await store.insert_many(atoms)
        assert len(ids) == 5
        for atom_id in ids:
            assert atom_id > 0
        assert await store.count_atoms() == 5

    @pytest.mark.asyncio
    async def test_insert_many_empty(self, tmp_db_path):
        """insert_many with empty list returns empty list."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        assert await store.insert_many([]) == []

    @pytest.mark.asyncio
    async def test_get_by_parent(self, tmp_db_path):
        """Retrieve all atoms belonging to one parent memory."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="a", parent_memory_id=100))
        await store.insert(_make_atom(content="b", parent_memory_id=100))
        await store.insert(_make_atom(content="c", parent_memory_id=200))

        children = await store.get_by_parent(100)
        assert len(children) == 2
        contents = {a.content for a in children}
        assert contents == {"a", "b"}

    @pytest.mark.asyncio
    async def test_get_by_parent_empty(self, tmp_db_path):
        """get_by_parent returns empty list for unknown parent."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        assert await store.get_by_parent(999) == []


class TestAtomStoreLifecycle:
    """Lifecycle operations: status, touch, reinforce, expire, forget."""

    @pytest.mark.asyncio
    async def test_update_status(self, tmp_db_path):
        """update_status changes atom status."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom_id = await store.insert(_make_atom())
        assert await store.update_status(atom_id, AtomStatus.DORMANT)

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.status == AtomStatus.DORMANT

    @pytest.mark.asyncio
    async def test_touch_updates_access_time(self, tmp_db_path):
        """touch bumps last_accessed_at forward."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom_id = await store.insert(_make_atom())
        original = await store.get(atom_id)
        assert original is not None
        await asyncio.sleep(0.01)
        await store.touch(atom_id)
        updated = await store.get(atom_id)
        assert updated is not None
        assert updated.last_accessed_at > original.last_accessed_at

    @pytest.mark.asyncio
    async def test_reinforce_increments_count_and_extends_ttl(self, tmp_db_path):
        """reinforce bumps reinforcement_count and recomputes TTL."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom_id = await store.insert(_make_atom())
        original = await store.get(atom_id)
        assert original is not None
        assert original.reinforcement_count == 0

        await store.reinforce(atom_id)
        reinforced = await store.get(atom_id)
        assert reinforced is not None
        assert reinforced.reinforcement_count == 1
        # TTL may have changed; at minimum expires_at should have shifted
        assert reinforced.expires_at != original.expires_at

    @pytest.mark.asyncio
    async def test_reinforce_with_confidence_ema(self, tmp_db_path):
        """reinforce with new_confidence applies EMA update."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom_id = await store.insert(_make_atom(confidence=0.8))
        await store.reinforce(atom_id, new_confidence=0.9)
        reinforced = await store.get(atom_id)
        assert reinforced is not None
        # EMA: 0.8*0.7 + 0.9*0.3 = 0.56 + 0.27 = 0.83
        assert abs(reinforced.confidence - 0.83) < 0.01

    @pytest.mark.asyncio
    async def test_reinforce_missing_atom_no_error(self, tmp_db_path):
        """reinforce on non-existent atom_id silently returns."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        # Should not raise
        await store.reinforce(99999)

    @pytest.mark.asyncio
    async def test_expire_stale_atoms(self, tmp_db_path):
        """Atoms past expires_at are marked EXPIRED."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        # Insert an atom with a past expires_at by directly manipulating time
        atom = _make_atom()
        atom_id = await store.insert(atom)
        # Force expires_at into the past
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET expires_at = ? WHERE id = ?",
                (time.time() - 10, atom_id),
            )
            await db.commit()

        expired = await store.expire_stale_atoms()
        assert expired >= 1

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.status == AtomStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_forget_expired_atoms(self, tmp_db_path):
        """Expired atoms older than threshold are soft-deleted (FORGOTTEN)."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom = _make_atom()
        atom_id = await store.insert(atom)
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET status = 'expired', expires_at = ? WHERE id = ?",
                (time.time() - 86400 * 10, atom_id),
            )
            await db.commit()

        count = await store.forget_expired_atoms(older_than_days=7.0)
        assert count >= 1

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.status == AtomStatus.FORGOTTEN  # soft-deleted

    @pytest.mark.asyncio
    async def test_cleanup_forgotten_removes_completely(self, tmp_db_path):
        """FORGOTTEN atoms older than threshold are hard-deleted."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom = _make_atom()
        atom_id = await store.insert(atom)
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET status = 'forgotten', expires_at = ? WHERE id = ?",
                (time.time() - 86400 * 15, atom_id),
            )
            await db.commit()

        count = await store.cleanup_forgotten(older_than_days=7.0)
        assert count >= 1
        assert await store.get(atom_id) is None

    @pytest.mark.asyncio
    async def test_migrate_to_cold(self, tmp_db_path):
        """Low-importance atoms old enough are moved to COLD status."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom = _make_atom(importance=0.3)
        atom_id = await store.insert(atom)
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET last_accessed_at = ? WHERE id = ?",
                (time.time() - 86400 * 20, atom_id),
            )
            await db.commit()

        count = await store.migrate_to_cold(
            cold_days_threshold=14.0, max_importance=0.4
        )
        assert count >= 1

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.status == AtomStatus.COLD


class TestAtomStoreStats:
    """Stats and query methods."""

    @pytest.mark.asyncio
    async def test_get_stats(self, tmp_db_path):
        """get_stats returns per-status counts."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom())
        await store.insert(_make_atom())

        stats = await store.get_stats()
        assert stats["active"] == 2
        assert stats["expired"] == 0
        assert "dormant" in stats

    @pytest.mark.asyncio
    async def test_count_atoms(self, tmp_db_path):
        """count_atoms returns total atom count."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        assert await store.count_atoms() == 0

        await store.insert(_make_atom())
        await store.insert(_make_atom())
        assert await store.count_atoms() == 2

    @pytest.mark.asyncio
    async def test_count_by_type(self, tmp_db_path):
        """count_by_type returns breakdown by atom_type."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(atom_type=AtomType.FACTUAL))
        await store.insert(_make_atom(atom_type=AtomType.EPISODIC))
        await store.insert(_make_atom(atom_type=AtomType.EPISODIC))

        breakdown = await store.count_by_type()
        assert breakdown["factual"] == 1
        assert breakdown["episodic"] == 2


class TestAtomStoreDelete:
    """Delete operations."""

    @pytest.mark.asyncio
    async def test_delete_by_parent(self, tmp_db_path):
        """delete_by_parent removes all atoms for a parent memory."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="a", parent_memory_id=500))
        await store.insert(_make_atom(content="b", parent_memory_id=500))
        await store.insert(_make_atom(content="c", parent_memory_id=501))

        deleted = await store.delete_by_parent(500)
        assert deleted == 2
        children = await store.get_by_parent(500)
        assert len(children) == 0
        # Unrelated parent unaffected
        assert len(await store.get_by_parent(501)) == 1

    @pytest.mark.asyncio
    async def test_batch_delete_by_parent(self, tmp_db_path):
        """batch_delete_by_parent removes atoms for multiple parents in bulk."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        for i in range(5):
            await store.insert(_make_atom(content=f"x{i}", parent_memory_id=700 + i))

        deleted = await store.batch_delete_by_parent([700, 701, 702])
        assert deleted == 3
        assert len(await store.get_by_parent(703)) == 1
        assert len(await store.get_by_parent(704)) == 1

    @pytest.mark.asyncio
    async def test_batch_delete_empty_ids(self, tmp_db_path):
        """batch_delete_by_parent with empty list returns 0."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        assert await store.batch_delete_by_parent([]) == 0


class TestAtomStorePlannedQuery:
    """Query for upcoming planned atoms."""

    @pytest.mark.asyncio
    async def test_query_upcoming_planned(self, tmp_db_path):
        """query_upcoming_planned returns PLANNED atoms within the lookahead window."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        future = time.time() + 3600  # 1 hour from now
        past_event = time.time() - 3600

        atom_future = _make_atom(atom_type=AtomType.PLANNED, content="明天开会")
        atom_future.event_time = future
        await store.insert(atom_future)

        atom_past = _make_atom(atom_type=AtomType.PLANNED, content="昨天开会")
        atom_past.event_time = past_event
        await store.insert(atom_past)

        # Factual atom should not be returned
        await store.insert(_make_atom(atom_type=AtomType.FACTUAL, content="事实"))

        results = await store.query_upcoming_planned(lookahead_sec=86400)
        assert len(results) >= 1
        contents = [r.content for r in results]
        assert "明天开会" in contents


class TestAtomStoreEdgeCases:
    """Edge cases and corner conditions."""

    @pytest.mark.asyncio
    async def test_insert_preserves_all_fields(self, tmp_db_path):
        """Inserted atom round-trips with all fields preserved."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom = _make_atom(
            content="完整测试",
            entities=["entity1", "entity2"],
            importance=0.75,
            confidence=0.9,
            session_id="sess-edge",
            persona_id="p-edge",
            metadata={"key": "value", "nested": {"inner": 1}},
        )
        atom_id = await store.insert(atom)

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.content == "完整测试"
        assert fetched.entities == ["entity1", "entity2"]
        assert fetched.importance == 0.75
        assert fetched.confidence == 0.9
        assert fetched.session_id == "sess-edge"
        assert fetched.persona_id == "p-edge"
        assert fetched.metadata["key"] == "value"
        assert fetched.metadata["nested"]["inner"] == 1
        assert fetched.atom_type == AtomType.FACTUAL
        assert fetched.status == AtomStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_insert_sets_created_at_and_last_accessed_at(self, tmp_db_path):
        """Insert populates time-derived fields automatically."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        before = time.time()
        atom_id = await store.insert(_make_atom())
        after = time.time()

        fetched = await store.get(atom_id)
        assert fetched is not None
        assert before <= fetched.created_at <= after
        assert before <= fetched.last_accessed_at <= after
        assert fetched.expires_at > fetched.created_at
        assert fetched.ttl_days > 0
