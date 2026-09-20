"""AtomStore 测试 — 插入、获取、生命周期、统计、FTS和清理。"""

import asyncio
import json
import time

import aiosqlite
import pytest

from core.features.memory.domain.memory_atom import AtomStatus, AtomType, MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from core.features.memory.infrastructure.write_op_serialization import (
    _deserialize_atom_from_repair,
    serialize_atom_for_repair,
)
from tests.fact_evidence_helpers import fact_evidence, source_evidence


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
    defaults.setdefault("source_evidence", source_evidence(defaults["content"]))
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
    async def test_insert_many_cancellation_rolls_back_batch(self, tmp_db_path):
        """取消发生在批量插入中途时不得留下未提交行或已分配 ID。"""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atoms = [
            _make_atom(content=f"取消_{i}", parent_memory_id=20 + i) for i in range(3)
        ]
        original_insert = store._insert_atom
        calls = 0

        async def cancelling_insert(db, atom):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError()
            return await original_insert(db, atom)

        store._insert_atom = cancelling_insert  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await store.insert_many(atoms)

        assert await store.count_atoms() == 0
        assert [atom.atom_id for atom in atoms] == [0, 0, 0]

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

    @pytest.mark.asyncio
    async def test_evidence_survives_repair_and_store_roundtrip(self, tmp_db_path):
        store = AtomStore(tmp_db_path)
        await store.initialize()
        original = _make_atom(content="用户喜欢绿茶")
        payload = serialize_atom_for_repair(original)
        replay = _deserialize_atom_from_repair(payload, 1, "sess-1", "p1")
        assert replay is not None
        atom_id = await store.insert(replay)
        fetched = await store.get(atom_id)
        assert fetched is not None
        assert fetched.source_evidence == original.source_evidence
        assert fetched.content == original.content
        payload.pop("source_evidence")
        assert _deserialize_atom_from_repair(payload, 1, "sess-1", "p1") is None
        with pytest.raises(ValueError, match="grounding_source_evidence_invalid"):
            await store.insert(_make_atom(source_evidence=[]))


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


async def _write_canonical_document(
    db_path: str,
    memory_id: int,
    *,
    revision: str = "rev-17",
    status: str = "active",
    facts: list[str] | None = None,
    privacy_level: str = "shared",
) -> None:
    """写入 canonical 文档行，供父来源校验与事实集合使用。"""

    metadata: dict[str, object] = {
        "scope_key": "scope-a",
        "privacy_level": privacy_level,
        "session_id": "scope-a",
        "persona_id": "persona-a",
        "memory_status": status,
    }
    if facts:
        metadata["key_facts"] = list(facts)
        metadata["fact_source_evidence"] = fact_evidence(list(facts))
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT OR REPLACE INTO documents
               (id,text,metadata,created_at,updated_at) VALUES(?,?,?,?,?)""",
            (
                memory_id,
                "；".join(facts) if facts else f"匿名正文-{memory_id}",
                json.dumps(metadata, ensure_ascii=False),
                "2026-07-21T00:00:00+00:00",
                revision,
            ),
        )
        await db.commit()


def _bound_atom(content: str, *, memory_id: int = 17, revision: str = "rev-17"):
    """构造已绑定当前父来源的 Atom。"""

    return _make_atom(
        content=content,
        parent_memory_id=memory_id,
        parent_revision=revision,
        parent_scope_key="scope-a",
        parent_privacy_level="shared",
        session_id="scope-a",
        persona_id="persona-a",
    )


class TestAtomStoreCurrentScopeCount:
    """父 canonical 当前有效口径的 Atom 计数。"""

    @pytest.mark.asyncio
    async def test_count_current_atoms_ignores_stale_and_orphan_rows(
        self, tmp_db_path
    ) -> None:
        """陈旧 revision 与无父来源的 Atom 不计入当前口径，原始计数保留。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, revision="rev-16")
        await store.insert(_bound_atom("旧事实", revision="rev-16"))
        await _write_canonical_document(tmp_db_path, 17, revision="rev-17")
        await store.insert(_bound_atom("当前事实"))
        # legacy 行允许写入无父来源的 Atom，公开读取与当前口径都不认它。
        await store.insert(_make_atom(content="孤儿事实", parent_memory_id=99))

        assert await store.count_atoms() == 3
        assert await store.count_current_atoms() == 1

    @pytest.mark.asyncio
    async def test_count_current_atoms_ignores_non_recallable_parent(
        self, tmp_db_path
    ) -> None:
        """父 canonical 归档后，其 Atom 不再计入当前口径。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17)
        await store.insert(_bound_atom("当前事实"))
        assert await store.count_current_atoms() == 1

        await _write_canonical_document(tmp_db_path, 17, status="archived")

        assert await store.count_current_atoms() == 0
        assert await store.count_atoms() == 1

    @pytest.mark.asyncio
    async def test_current_scope_count_reads_canonical_documents(
        self, tmp_db_path
    ) -> None:
        """canonical 读取端口返回正文与 metadata，供事实校验与重派生使用。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A", "事实B"])

        documents = await store.load_canonical_documents([17, 18])

        assert set(documents) == {17}
        assert documents[17]["text"] == "事实A；事实B"
        assert documents[17]["metadata"]["key_facts"] == ["事实A", "事实B"]
        assert documents[17]["updated_at"] == "rev-17"


class TestAtomStoreReplaceByParent:
    """重派生使用的按父替换语义。"""

    @pytest.mark.asyncio
    async def test_replace_by_parent_swaps_rows_and_fts(self, tmp_db_path) -> None:
        """替换后旧行与旧 FTS 都不再可见，新行获得新 ID。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17)
        old_ids = await store.insert_many(
            [_bound_atom("旧事实一"), _bound_atom("旧事实二")]
        )

        replacement = _bound_atom("新事实")
        new_ids = await store.replace_by_parent(17, [replacement])

        assert len(new_ids) == 1
        assert replacement.atom_id == new_ids[0]
        assert set(old_ids).isdisjoint(new_ids)
        contents = [atom.content for atom in await store.get_by_parent(17)]
        assert contents == ["新事实"]
        assert await store.search_fts("旧事实一") == []

    @pytest.mark.asyncio
    async def test_replace_by_parent_cancellation_keeps_previous_rows(
        self, tmp_db_path
    ) -> None:
        """替换中途取消必须回滚，保留原有行且不留下已分配 ID。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17)
        await store.insert(_bound_atom("原有事实"))

        replacement = _bound_atom("新事实")
        original_insert = store._insert_atom

        async def cancelling_insert(db, atom):
            raise asyncio.CancelledError()

        store._insert_atom = cancelling_insert  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await store.replace_by_parent(17, [replacement])
        store._insert_atom = original_insert  # type: ignore[method-assign]

        assert replacement.atom_id == 0
        contents = [atom.content for atom in await store.get_by_parent(17)]
        assert contents == ["原有事实"]

    @pytest.mark.asyncio
    async def test_replace_by_parent_rejects_foreign_atom(self, tmp_db_path) -> None:
        """父 ID 不一致的 Atom 不允许借替换写入其它父来源。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17)
        await store.insert(_bound_atom("原有事实"))

        with pytest.raises(ValueError, match="atom_parent_mismatch"):
            await store.replace_by_parent(17, [_bound_atom("越权事实", memory_id=18)])

        contents = [atom.content for atom in await store.get_by_parent(17)]
        assert contents == ["原有事实"]

    @pytest.mark.asyncio
    async def test_list_parent_ids_returns_distinct_sources(self, tmp_db_path) -> None:
        """残留枚举只返回持有 Atom 行的不同父来源。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17)
        await store.insert_many(
            [_bound_atom("事实一"), _bound_atom("事实二"), _bound_atom("事实三")]
        )
        await store.insert(_make_atom(content="孤儿事实", parent_memory_id=99))

        assert await store.list_parent_ids() == [17, 99]
