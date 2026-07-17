"""测试 core/social — Social Relationship Typing.

Covers:
- Models: SocialRelation, RelationChange, helpers
- RelationStore: CRUD, multi-group isolation
- RelationManager: difficulty-gated updates, defaults, tag management
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from core.social.models import (
    RELATION_CATEGORIES,
    RELATION_DIFFICULTY,
    RelationChange,
    SocialRelation,
    get_difficulty,
    get_relation_category,
)
from core.social.relation_store import RelationStore
from core.social.relation_manager import RelationManager


# ============================================================================
# Helpers
# ============================================================================


async def _create_store(tmp_db_path: str) -> RelationStore:
    s = RelationStore(db_path=tmp_db_path)
    await s.initialize()
    return s


async def _create_manager(tmp_db_path: str) -> RelationManager:
    return RelationManager(await _create_store(tmp_db_path))


# ============================================================================
# Models & helpers
# ============================================================================


class TestRelationCategories:
    """验证 the 6-category taxonomy."""

    def test_six_categories_exist(self):
        assert set(RELATION_CATEGORIES) == {
            "blood", "geographic", "career", "emotional", "interest", "intimacy",
        }

    def test_blood_contains_kinship(self):
        assert "parent_child" in RELATION_CATEGORIES["blood"]
        assert "siblings" in RELATION_CATEGORIES["blood"]
        assert "relatives" in RELATION_CATEGORIES["blood"]

    def test_emotional_contains_bonds(self):
        assert "lover" in RELATION_CATEGORIES["emotional"]
        assert "best_friend" in RELATION_CATEGORIES["emotional"]
        assert "rival" in RELATION_CATEGORIES["emotional"]

    def test_intimacy_contains_tiers(self):
        assert "core_intimate" in RELATION_CATEGORIES["intimacy"]
        assert "daily_normal" in RELATION_CATEGORIES["intimacy"]
        assert "stranger" in RELATION_CATEGORIES["intimacy"]

    def test_get_relation_category_known(self):
        assert get_relation_category("parent_child") == "blood"
        assert get_relation_category("colleague") == "career"
        assert get_relation_category("lover") == "emotional"

    def test_get_relation_category_unknown(self):
        assert get_relation_category("nonexistent") is None


class TestRelationDifficulty:
    """Cover every listed difficulty entry."""

    def test_blood_is_high(self):
        assert RELATION_DIFFICULTY["parent_child"] >= 0.98
        assert RELATION_DIFFICULTY["siblings"] >= 0.95
        assert RELATION_DIFFICULTY["relatives"] >= 0.90

    def test_fellow_passenger_is_low(self):
        assert RELATION_DIFFICULTY["fellow_passenger"] <= 0.10

    def test_stranger_is_low(self):
        assert RELATION_DIFFICULTY["stranger"] <= 0.15

    def test_core_intimate_is_high(self):
        assert RELATION_DIFFICULTY["core_intimate"] >= 0.85

    def test_get_difficulty_known(self):
        assert get_difficulty("colleague") == 0.60
        assert get_difficulty("best_friend") == 0.80

    def test_get_difficulty_fallback(self):
        assert get_difficulty("invented_type") == 0.40

    def test_all_listed_types_have_difficulty(self):
        """Every type name across all categories must have a difficulty entry."""
        for cat, members in RELATION_CATEGORIES.items():
            for rel_type in members:
                assert rel_type in RELATION_DIFFICULTY, (
                    f"{rel_type} (from {cat}) missing from "
                    "RELATION_DIFFICULTY"
                )


class TestSocialRelationModel:
    """SocialRelation dataclass tests."""

    def test_create_default(self):
        rel = SocialRelation(
            from_user="u1",
            to_user="u2",
            relation_type="stranger",
            strength=0.1,
            frequency=0,
            last_interaction=time.time(),
            group_id="g1",
        )
        assert rel.tags == []

    def test_to_dict_roundtrip(self):
        rel = SocialRelation(
            from_user="alice",
            to_user="bob",
            relation_type="colleague",
            strength=0.55,
            frequency=12,
            last_interaction=1234567890.0,
            group_id="group_42",
            tags=["work", "project_x"],
        )
        d = rel.to_dict()
        assert d["from_user"] == "alice"
        assert d["tags"] == ["work", "project_x"]

    def test_from_row_with_json_tags(self):
        row = {
            "from_user": "x",
            "to_user": "y",
            "relation_type": "classmate",
            "strength": 0.3,
            "frequency": 5,
            "last_interaction": 100.0,
            "group_id": "g",
            "tags": json.dumps(["tag1", "tag2"], ensure_ascii=False),
        }
        rel = SocialRelation.from_row(row)
        assert rel.tags == ["tag1", "tag2"]

    def test_from_row_with_invalid_tags_json(self):
        row = {
            "from_user": "x",
            "to_user": "y",
            "relation_type": "classmate",
            "strength": 0.3,
            "frequency": 5,
            "last_interaction": 100.0,
            "group_id": "g",
            "tags": "not-valid-json",
        }
        rel = SocialRelation.from_row(row)
        assert rel.tags == []

    def test_from_row_with_list_tags(self):
        row = {
            "from_user": "x",
            "to_user": "y",
            "relation_type": "classmate",
            "strength": 0.3,
            "frequency": 5,
            "last_interaction": 100.0,
            "group_id": "g",
            "tags": ["a", "b"],
        }
        rel = SocialRelation.from_row(row)
        assert rel.tags == ["a", "b"]


class TestRelationChangeModel:
    """RelationChange is a simple dataclass."""

    def test_create(self):
        rc = RelationChange(
            from_user="a",
            to_user="b",
            relation_type="colleague",
            delta=0.05,
            new_strength=0.35,
            reason="chat",
        )
        assert rc.delta == 0.05
        assert rc.reason == "chat"


# ============================================================================
# RelationStore
# ============================================================================


class TestRelationStoreCRUD:
    """Storage-layer tests."""

    @pytest.mark.asyncio
    async def test_upsert_and_get(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        rel = SocialRelation(
            from_user="u1", to_user="u2", relation_type="colleague",
            strength=0.4, frequency=3, last_interaction=time.time(),
            group_id="g1",
        )
        await store.upsert_relation(rel)

        got = await store.get_relation("u1", "u2", "colleague", "g1")
        assert got is not None
        assert got.from_user == "u1"
        assert got.strength == 0.4

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        got = await store.get_relation("u1", "u2", "stranger", "g1")
        assert got is None

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        rel = SocialRelation(
            from_user="u1", to_user="u2", relation_type="colleague",
            strength=0.4, frequency=1, last_interaction=time.time(),
            group_id="g1",
        )
        await store.upsert_relation(rel)
        rel.strength = 0.7
        rel.frequency = 5
        await store.upsert_relation(rel)

        got = await store.get_relation("u1", "u2", "colleague", "g1")
        assert got.strength == 0.7
        assert got.frequency == 5

    @pytest.mark.asyncio
    async def test_group_isolation(self, tmp_db_path):
        """相同 user pair in two groups should yield two distinct records."""
        store = await _create_store(tmp_db_path)
        rel_g1 = SocialRelation(
            from_user="a", to_user="b", relation_type="classmate",
            strength=0.3, frequency=2, last_interaction=time.time(),
            group_id="group_1",
        )
        rel_g2 = SocialRelation(
            from_user="a", to_user="b", relation_type="classmate",
            strength=0.8, frequency=10, last_interaction=time.time(),
            group_id="group_2",
        )
        await store.upsert_relation(rel_g1)
        await store.upsert_relation(rel_g2)

        got1 = await store.get_relation("a", "b", "classmate", "group_1")
        got2 = await store.get_relation("a", "b", "classmate", "group_2")
        assert got1.strength == 0.3
        assert got2.strength == 0.8

    @pytest.mark.asyncio
    async def test_get_group_relations(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        for i in range(3):
            await store.upsert_relation(SocialRelation(
                from_user=f"u{i}", to_user=f"v{i}",
                relation_type="colleague", strength=0.1 * (i + 1),
                frequency=i, last_interaction=time.time(),
                group_id="mygroup",
            ))
        results = await store.get_group_relations("mygroup")
        assert len(results) == 3
        # Sorted strongest first
        assert results[0].strength >= results[-1].strength

    @pytest.mark.asyncio
    async def test_get_user_network(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        await store.upsert_relation(SocialRelation(
            from_user="center", to_user="a", relation_type="friend",
            strength=0.5, frequency=1, last_interaction=time.time(),
            group_id="g",
        ))
        await store.upsert_relation(SocialRelation(
            from_user="b", to_user="center", relation_type="colleague",
            strength=0.3, frequency=1, last_interaction=time.time(),
            group_id="g2",
        ))
        network = await store.get_user_network("center")
        assert len(network) == 2

    @pytest.mark.asyncio
    async def test_get_user_relations_in_group(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        await store.upsert_relation(SocialRelation(
            from_user="me", to_user="other", relation_type="friend",
            strength=0.5, frequency=1, last_interaction=time.time(),
            group_id="alpha",
        ))
        await store.upsert_relation(SocialRelation(
            from_user="me", to_user="someone", relation_type="colleague",
            strength=0.3, frequency=1, last_interaction=time.time(),
            group_id="beta",
        ))
        results = await store.get_user_relations_in_group("me", "alpha")
        assert len(results) == 1
        assert results[0].to_user == "other"

    @pytest.mark.asyncio
    async def test_delete_relation(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        rel = SocialRelation(
            from_user="d1", to_user="d2", relation_type="rival",
            strength=0.2, frequency=1, last_interaction=time.time(),
            group_id="g",
        )
        await store.upsert_relation(rel)
        assert await store.delete_relation("d1", "d2", "rival", "g") is True
        assert await store.get_relation("d1", "d2", "rival", "g") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        assert await store.delete_relation("x", "y", "z", "g") is False

    @pytest.mark.asyncio
    async def test_delete_user_relations(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        for i in range(3):
            await store.upsert_relation(SocialRelation(
                from_user="target", to_user=f"peer_{i}",
                relation_type="colleague", strength=0.2,
                frequency=1, last_interaction=time.time(),
                group_id="del_group",
            ))
        removed = await store.delete_user_relations("target", "del_group")
        assert removed == 3
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_list_all_and_count(self, tmp_db_path):
        store = await _create_store(tmp_db_path)
        assert await store.count() == 0
        await store.upsert_relation(SocialRelation(
            from_user="a", to_user="b", relation_type="neighbor",
            strength=0.5, frequency=1, last_interaction=time.time(),
            group_id="g",
        ))
        assert await store.count() == 1
        assert len(await store.list_all()) == 1

    def test_rejects_unapproved_table_identifier(self):
        store = RelationStore(db_path="social.db")
        store._TABLE = 'social_relations; DROP TABLE social_relations;--'
        with pytest.raises(ValueError, match="Unsupported relation table"):
            _ = store._table_sql


# ============================================================================
# RelationManager
# ============================================================================


class TestRelationManagerDefaults:
    """默认 creation and initial state."""

    @pytest.mark.asyncio
    async def test_get_or_create_new(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("u1", "u2", "g1")
        assert rel.from_user == "u1"
        assert rel.to_user == "u2"
        assert rel.relation_type == "stranger"
        assert rel.strength == 0.1
        assert rel.frequency == 0
        assert rel.group_id == "g1"

        # Verify it was persisted
        stored = await manager._store.get_relation("u1", "u2", "stranger", "g1")
        assert stored is not None

    @pytest.mark.asyncio
    async def test_get_or_create_existing(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        rel1 = await manager.get_or_create("a", "b", "g", relation_type="colleague")
        rel2 = await manager.get_or_create("a", "b", "g", relation_type="colleague")
        assert rel2.strength == rel1.strength
        assert rel2.frequency == rel1.frequency


class TestRelationManagerDifficultyGate:
    """验证 that the difficulty gate dampens strength changes correctly."""

    @pytest.mark.asyncio
    async def test_parent_child_nearly_immutable(self, tmp_db_path):
        """parent_child difficulty 0.98 → only 2% of delta passes through."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("mom", "child", "home",
                                          relation_type="parent_child")
        change = RelationChange(
            from_user="mom", to_user="child",
            relation_type="parent_child",
            delta=0.50, new_strength=0.0, reason="test",
        )
        rel = await manager.update_relation(change)
        # actual = 0.50 * (1 - 0.98) = 0.01
        assert rel.strength == pytest.approx(0.1 + 0.01, abs=0.005)

    @pytest.mark.asyncio
    async def test_fellow_passenger_highly_mutable(self, tmp_db_path):
        """fellow_passenger difficulty 0.05 → 95% of delta passes through."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("p1", "p2", "train",
                                          relation_type="fellow_passenger")
        change = RelationChange(
            from_user="p1", to_user="p2",
            relation_type="fellow_passenger",
            delta=0.30, new_strength=0.0, reason="chat",
        )
        rel = await manager.update_relation(change)
        # actual = 0.30 * (1 - 0.05) = 0.285
        assert rel.strength == pytest.approx(0.1 + 0.285, abs=0.01)

    @pytest.mark.asyncio
    async def test_colleague_mid_difficulty(self, tmp_db_path):
        """colleague difficulty 0.60 → 40% of delta passes."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("a", "b", "office",
                                          relation_type="colleague")
        change = RelationChange(
            from_user="a", to_user="b",
            relation_type="colleague",
            delta=0.20, new_strength=0.0, reason="meeting",
        )
        rel = await manager.update_relation(change)
        # actual = 0.20 * (1 - 0.60) = 0.08
        assert rel.strength == pytest.approx(0.1 + 0.08, abs=0.01)

    @pytest.mark.asyncio
    async def test_strength_clamps_to_one(self, tmp_db_path):
        """Strength must never exceed 1.0."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("a", "b", "g",
                                          relation_type="stranger")
        change = RelationChange(
            from_user="a", to_user="b",
            relation_type="stranger",
            delta=100.0, new_strength=0.0, reason="overflow_test",
        )
        rel = await manager.update_relation(change)
        assert rel.strength == 1.0

    @pytest.mark.asyncio
    async def test_negative_delta_decreases(self, tmp_db_path):
        """负 deltas should decrease strength through the gate."""
        manager = await _create_manager(tmp_db_path)
        # Start with higher base
        store_ = manager._store
        rel = SocialRelation(
            from_user="a", to_user="b", relation_type="colleague",
            strength=0.8, frequency=5, last_interaction=time.time(),
            group_id="",
        )
        await store_.upsert_relation(rel)

        change = RelationChange(
            from_user="a", to_user="b",
            relation_type="colleague",
            delta=-0.30, new_strength=0.0, reason="argument",
        )
        rel = await manager.update_relation(change)
        # actual = -0.30 * 0.40 = -0.12
        assert rel.strength == pytest.approx(0.68, abs=0.01)
        assert rel.strength > 0.0

    @pytest.mark.asyncio
    async def test_strength_clamps_to_zero(self, tmp_db_path):
        """Strength must never go below 0.0."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("a", "b", "g",
                                          relation_type="stranger")
        change = RelationChange(
            from_user="a", to_user="b",
            relation_type="stranger",
            delta=-100.0, new_strength=0.0, reason="underflow_test",
        )
        rel = await manager.update_relation(change)
        assert rel.strength == 0.0


class TestRelationManagerHighFrequency:
    """Simulate repeated interactions driving strength upward."""

    @pytest.mark.asyncio
    async def test_frequent_interactions_raise_strength(self, tmp_db_path):
        """之后 50 small positive deltas, strength should grow noticeably."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("u1", "u2", "g",
                                          relation_type="fellow_passenger")
        for _ in range(50):
            change = RelationChange(
                from_user="u1", to_user="u2",
                relation_type="fellow_passenger",
                delta=0.02, new_strength=0.0, reason="chat",
            )
            rel = await manager.update_relation(change)

        # total raw = 50 * 0.02 = 1.0
        # actual per step = 0.02 * 0.95 = 0.019
        # total = 0.95, clamped at 1.0
        assert rel.strength > 0.8
        assert rel.frequency == 50


class TestRelationManagerTags:
    """Tag management tests."""

    @pytest.mark.asyncio
    async def test_update_tags(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        rel = await manager.get_or_create("u1", "u2", "g",
                                          relation_type="colleague")
        updated = await manager.update_tags(
            "u1", "u2", "colleague", "g",
            tags=["office", "lunch_buddy"],
        )
        assert updated is not None
        assert updated.tags == ["office", "lunch_buddy"]

        # Verify persistence
        stored = await manager._store.get_relation("u1", "u2", "colleague", "g")
        assert stored.tags == ["office", "lunch_buddy"]

    @pytest.mark.asyncio
    async def test_update_tags_nonexistent(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        result = await manager.update_tags(
            "no", "one", "stranger", "g", tags=["test"],
        )
        assert result is None


class TestRelationManagerMultiGroup:
    """Relations should be scoped per group_id."""

    @pytest.mark.asyncio
    async def test_same_users_different_groups(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        _ = await manager.get_or_create("alice", "bob", "group_a",
                                        relation_type="colleague")
        _ = await manager.get_or_create("alice", "bob", "group_b",
                                        relation_type="gaming_teammate")

        # Apply a delta only in group_a
        change = RelationChange(
            from_user="alice", to_user="bob",
            relation_type="colleague", delta=0.5,
            new_strength=0.0, reason="meeting",
        )
        await manager.update_relation(change)

        # group_b should still be at default 0.1
        rel_b = await manager._store.get_relation(
            "alice", "bob", "gaming_teammate", "group_b",
        )
        assert rel_b.strength == 0.1

    @pytest.mark.asyncio
    async def test_get_relations_by_group_scope(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        await manager.get_or_create("x", "y", "g1", relation_type="classmate")
        await manager.get_or_create("x", "z", "g1", relation_type="colleague")
        await manager.get_or_create("w", "v", "g2", relation_type="rival")

        g1 = await manager.get_relations_by_group("g1")
        assert len(g1) == 2
        g2 = await manager.get_relations_by_group("g2")
        assert len(g2) == 1


class TestRelationManagerDelete:
    """Deletion tests."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        await manager.get_or_create("a", "b", "g", relation_type="friend")
        assert await manager.delete_relation("a", "b", "friend", "g") is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        assert await manager.delete_relation("x", "y", "z", "g") is False


class TestRelationManagerEdgeCases:
    """Edge-case and boundary tests."""

    @pytest.mark.asyncio
    async def test_apply_delta_auto_provisions(self, tmp_db_path):
        """Calling apply_delta on a non-existent relation creates it first."""
        manager = await _create_manager(tmp_db_path)
        rel = await manager.apply_delta(
            "new_u1", "new_u2", "any_group",
            delta=0.2, reason="hello",
            relation_type="classmate",
        )
        assert rel is not None
        assert rel.from_user == "new_u1"
        # strength should be 0.1 + 0.2*(1-0.50) = 0.1 + 0.10 = 0.20
        assert rel.strength == pytest.approx(0.20, abs=0.01)

    @pytest.mark.asyncio
    async def test_all_relation_types_gate(self, tmp_db_path):
        """Smoke-test: every relation type can be created and updated."""
        manager = await _create_manager(tmp_db_path)
        for cat, members in RELATION_CATEGORIES.items():
            for rt in members:
                rel = await manager.get_or_create("from", "to", "test_g",
                                                  relation_type=rt)
                change = RelationChange(
                    from_user="from", to_user="to",
                    relation_type=rt, delta=0.1,
                    new_strength=0.0, reason="smoke_test",
                )
                rel = await manager.update_relation(change)
                assert 0.0 <= rel.strength <= 1.0, (
                    f"{rt} strength {rel.strength} out of range"
                )

    @pytest.mark.asyncio
    async def test_list_all_integration(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        await manager.get_or_create("a", "b", "g1")
        await manager.get_or_create("c", "d", "g1")
        assert len(await manager.list_all()) == 2


class TestRelationManagerManualCrud:
    """管理员 CRUD 保持人工编辑语义、原子性与修订版本检查。"""

    @pytest.mark.asyncio
    async def test_manual_create_does_not_fake_interaction(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)

        created = await manager.create_manual_relation(
            from_user=" alice ",
            to_user=" bob ",
            group_id=" g1 ",
            relation_type="colleague",
            strength=0.4,
            tags=[" work ", "work", "trusted"],
        )

        assert created.from_user == "alice"
        assert created.to_user == "bob"
        assert created.group_id == "g1"
        assert created.strength == 0.4
        assert created.tags == ["work", "trusted"]
        assert created.frequency == 0
        assert created.last_interaction == 0.0

    @pytest.mark.asyncio
    async def test_duplicate_manual_create_leaves_existing_unchanged(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=["work"],
        )

        with pytest.raises(EntityAlreadyExistsError):
            await manager.create_manual_relation(
                from_user="alice",
                to_user="bob",
                group_id="g1",
                relation_type="colleague",
                strength=0.9,
                tags=["replacement"],
            )

        current = await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        )
        assert current == created

    @pytest.mark.asyncio
    async def test_manual_update_migrates_relation_type_atomically(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=["work"],
        )

        updated = await manager.update_manual_relation(
            identity=("alice", "bob", "colleague", "g1"),
            relation_type="best_friend",
            strength=0.8,
            tags=[" trusted ", "trusted"],
            expected_revision=manager.revision_for(created),
        )

        assert updated.relation_type == "best_friend"
        assert updated.strength == 0.8
        assert updated.tags == ["trusted"]
        assert updated.frequency == 0
        assert updated.last_interaction == 0.0
        assert await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        ) is None
        assert await manager._store.get_relation(
            "alice", "bob", "best_friend", "g1"
        ) == updated

    @pytest.mark.asyncio
    async def test_stale_manual_update_exposes_current_and_preserves_row(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=[],
        )

        with pytest.raises(EditConflictError) as caught:
            await manager.update_manual_relation(
                identity=("alice", "bob", "colleague", "g1"),
                relation_type="colleague",
                strength=0.9,
                tags=[],
                expected_revision="stale",
            )

        assert caught.value.current_entity == created.to_dict()
        assert caught.value.current_revision == manager.revision_for(created)
        current = await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        )
        assert current is not None and current.strength == 0.4

    @pytest.mark.asyncio
    async def test_manual_update_rejects_occupied_destination_without_changes(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        source = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=["source"],
        )
        destination = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="best_friend",
            strength=0.7,
            tags=["destination"],
        )

        with pytest.raises(EntityAlreadyExistsError):
            await manager.update_manual_relation(
                identity=("alice", "bob", "colleague", "g1"),
                relation_type="best_friend",
                strength=0.8,
                tags=["replacement"],
                expected_revision=manager.revision_for(source),
            )

        assert await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        ) == source
        assert await manager._store.get_relation(
            "alice", "bob", "best_friend", "g1"
        ) == destination

    @pytest.mark.asyncio
    async def test_revision_checked_delete_removes_relation(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=[],
        )

        deleted = await manager.delete_manual_relation(
            identity=("alice", "bob", "colleague", "g1"),
            expected_revision=manager.revision_for(created),
        )

        assert deleted is True
        assert await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        ) is None

    @pytest.mark.asyncio
    async def test_stale_delete_leaves_relation_intact(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=[],
        )

        with pytest.raises(EditConflictError) as caught:
            await manager.delete_manual_relation(
                identity=("alice", "bob", "colleague", "g1"),
                expected_revision="stale",
            )

        assert caught.value.current_entity == created.to_dict()
        assert caught.value.current_revision == manager.revision_for(created)
        assert await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        ) == created

    @pytest.mark.asyncio
    async def test_missing_manual_update_and_delete_raise_not_found(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        identity = ("missing", "user", "colleague", "g1")

        with pytest.raises(EntityNotFoundError):
            await manager.update_manual_relation(
                identity=identity,
                relation_type="best_friend",
                strength=0.8,
                tags=[],
                expected_revision="irrelevant",
            )
        with pytest.raises(EntityNotFoundError):
            await manager.delete_manual_relation(
                identity=identity,
                expected_revision="irrelevant",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("overrides", "expected_field"),
        [
            ({"from_user": "   "}, "from_user"),
            ({"from_user": "a" * 129}, "from_user"),
            ({"to_user": "alice"}, "to_user"),
            ({"relation_type": "unknown"}, "relation_type"),
            ({"strength": True}, "strength"),
            ({"strength": float("inf")}, "strength"),
            ({"strength": 1.01}, "strength"),
            ({"tags": "not-a-list"}, "tags"),
            ({"tags": ["x" * 65]}, "tags.0"),
        ],
    )
    async def test_manual_create_rejects_invalid_fields_structurally(
        self, tmp_db_path, overrides, expected_field
    ):
        manager = await _create_manager(tmp_db_path)
        values = {
            "from_user": "alice",
            "to_user": "bob",
            "group_id": "g1",
            "relation_type": "colleague",
            "strength": 0.4,
            "tags": ["work"],
        }
        values.update(overrides)

        with pytest.raises(EntityValidationError) as caught:
            await manager.create_manual_relation(**values)

        assert expected_field in caught.value.field_errors


class TestRelationManagerAutomaticConcurrency:
    """自动学习必须基于锁内最新记录更新，而不是回写陈旧对象。"""

    @pytest.mark.asyncio
    async def test_stale_automatic_delta_uses_latest_admin_values(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        await manager._store.upsert_relation(
            SocialRelation(
                from_user="alice",
                to_user="bob",
                relation_type="colleague",
                strength=0.2,
                frequency=2,
                last_interaction=100.0,
                group_id="g1",
                tags=["stale"],
            )
        )
        stale = await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        )
        assert stale is not None

        await manager._store.upsert_relation(
            SocialRelation(
                from_user="alice",
                to_user="bob",
                relation_type="colleague",
                strength=0.5,
                frequency=7,
                last_interaction=200.0,
                group_id="g1",
                tags=["automatic-latest"],
            )
        )
        latest = await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        )
        assert latest is not None
        await manager.update_manual_relation(
            identity=("alice", "bob", "colleague", "g1"),
            relation_type="colleague",
            strength=0.7,
            tags=["admin"],
            expected_revision=manager.revision_for(latest),
        )

        updated = await manager._apply_delta(stale, 0.2, "new-message")

        stored = await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        )
        assert stored is not None
        assert stored.strength == pytest.approx(0.78)
        assert stored.frequency == 8
        assert stored.last_interaction > 200.0
        assert stored.tags == ["admin"]
        assert updated == stored

    @pytest.mark.asyncio
    async def test_stale_automatic_delta_does_not_recreate_migrated_key(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.4,
            tags=["before"],
        )
        stale = await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        )
        assert stale is not None
        migrated = await manager.update_manual_relation(
            identity=("alice", "bob", "colleague", "g1"),
            relation_type="best_friend",
            strength=0.8,
            tags=["admin-migrated"],
            expected_revision=manager.revision_for(created),
        )

        result = await manager._apply_delta(stale, 0.2, "late-message")

        assert result is stale
        assert await manager._store.get_relation(
            "alice", "bob", "colleague", "g1"
        ) is None
        assert await manager._store.get_relation(
            "alice", "bob", "best_friend", "g1"
        ) == migrated

    @pytest.mark.asyncio
    async def test_get_or_create_racing_admin_create_preserves_admin_row(
        self, tmp_db_path
    ):
        class PausingRelationStore(RelationStore):
            def __init__(self, db_path: str) -> None:
                super().__init__(db_path)
                self.missing_read = asyncio.Event()
                self.resume_read = asyncio.Event()
                self._pause_once = True

            async def get_relation(
                self,
                from_user: str,
                to_user: str,
                relation_type: str,
                group_id: str,
            ) -> SocialRelation | None:
                relation = await super().get_relation(
                    from_user, to_user, relation_type, group_id
                )
                if relation is None and self._pause_once:
                    self._pause_once = False
                    self.missing_read.set()
                    await self.resume_read.wait()
                return relation

        store = PausingRelationStore(tmp_db_path)
        await store.initialize()
        automatic_manager = RelationManager(store)
        admin_manager = RelationManager(store)
        automatic_task = asyncio.create_task(
            automatic_manager.get_or_create(
                "alice", "bob", "g1", relation_type="colleague"
            )
        )
        await store.missing_read.wait()
        admin = await admin_manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="g1",
            relation_type="colleague",
            strength=0.8,
            tags=["admin"],
        )
        store.resume_read.set()

        automatic_result = await automatic_task
        stored = await store.get_relation("alice", "bob", "colleague", "g1")
        assert automatic_result == admin
        assert stored == admin


class TestRelationStorePooledTransactions:
    """连接池复用前必须清理失败写入留下的事务。"""

    @pytest.mark.asyncio
    async def test_duplicate_strict_create_rolls_back_size_one_pool(
        self, tmp_db_path
    ):
        await RelationStore.close_pool()
        store = await _create_store(tmp_db_path)
        await RelationStore.init_pool(tmp_db_path, pool_size=1)
        manager = RelationManager(store)
        try:
            created = await manager.create_manual_relation(
                from_user="alice",
                to_user="bob",
                group_id="g1",
                relation_type="colleague",
                strength=0.4,
                tags=["first"],
            )
            with pytest.raises(EntityAlreadyExistsError):
                await manager.create_manual_relation(
                    from_user="alice",
                    to_user="bob",
                    group_id="g1",
                    relation_type="colleague",
                    strength=0.9,
                    tags=["duplicate"],
                )

            assert RelationStore._pool is not None
            async with RelationStore._pool.acquire() as connection:
                assert connection.in_transaction is False

            updated = await manager.update_manual_relation(
                identity=("alice", "bob", "colleague", "g1"),
                relation_type="colleague",
                strength=0.8,
                tags=["updated"],
                expected_revision=manager.revision_for(created),
            )
            assert updated.strength == 0.8
        finally:
            await RelationStore.close_pool()

    @pytest.mark.asyncio
    async def test_cancelled_revision_update_rolls_back_pooled_transaction(
        self, tmp_db_path, monkeypatch
    ):
        await RelationStore.close_pool()
        store = await _create_store(tmp_db_path)
        await RelationStore.init_pool(tmp_db_path, pool_size=1)
        manager = RelationManager(store)
        try:
            created = await manager.create_manual_relation(
                from_user="alice",
                to_user="bob",
                group_id="g1",
                relation_type="colleague",
                strength=0.4,
                tags=[],
            )

            def cancel_revision(_entity):
                raise asyncio.CancelledError()

            monkeypatch.setattr(
                "core.social.relation_store.compute_entity_revision",
                cancel_revision,
            )
            with pytest.raises(asyncio.CancelledError):
                await store.update_relation_if_revision(
                    ("alice", "bob", "colleague", "g1"),
                    relation_type="colleague",
                    strength=0.8,
                    tags=[],
                    expected_revision=manager.revision_for(created),
                )

            assert RelationStore._pool is not None
            async with RelationStore._pool.acquire() as connection:
                assert connection.in_transaction is False
        finally:
            await RelationStore.close_pool()


class TestRelationManagerLocatorValidation:
    """已有行 locator 只校验形状，不套用创建阶段的业务限制。"""

    @pytest.mark.asyncio
    async def test_padded_locator_targets_canonical_stripped_row(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="",
            relation_type="colleague",
            strength=0.4,
            tags=["before"],
        )

        padded_identity = (" alice ", " bob ", "colleague", " \t ")
        updated = await manager.update_manual_relation(
            identity=padded_identity,
            relation_type="best_friend",
            strength=0.8,
            tags=["after"],
            expected_revision=manager.revision_for(created),
        )

        assert updated.from_user == "alice"
        assert updated.to_user == "bob"
        assert updated.group_id == ""
        assert updated.relation_type == "best_friend"
        assert await manager._store.get_relation(
            "alice", "bob", "colleague", ""
        ) is None

        assert await manager.delete_manual_relation(
            identity=(" alice ", " bob ", "best_friend", "   "),
            expected_revision=manager.revision_for(updated),
        ) is True
        assert await manager._store.get_relation(
            "alice", "bob", "best_friend", ""
        ) is None

    @pytest.mark.asyncio
    async def test_manual_create_allows_empty_group_id(self, tmp_db_path):
        manager = await _create_manager(tmp_db_path)

        created = await manager.create_manual_relation(
            from_user="alice",
            to_user="bob",
            group_id="",
            relation_type="colleague",
            strength=0.4,
            tags=[],
        )

        assert created.group_id == ""

    @pytest.mark.asyncio
    async def test_empty_group_id_automatic_row_can_be_updated_and_deleted(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        automatic = await manager.get_or_create(
            "alice", "bob", "", relation_type="colleague"
        )

        updated = await manager.update_manual_relation(
            identity=("alice", "bob", "colleague", ""),
            relation_type="best_friend",
            strength=0.8,
            tags=["admin"],
            expected_revision=manager.revision_for(automatic),
        )
        assert updated.group_id == ""
        assert updated.relation_type == "best_friend"

        assert await manager.delete_manual_relation(
            identity=("alice", "bob", "best_friend", ""),
            expected_revision=manager.revision_for(updated),
        ) is True
        assert await manager._store.get_relation(
            "alice", "bob", "best_friend", ""
        ) is None

    @pytest.mark.asyncio
    async def test_legacy_locator_can_be_revision_checked_deleted(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)
        legacy = SocialRelation(
            from_user="legacy",
            to_user="legacy",
            relation_type="legacy_unknown",
            strength=0.3,
            frequency=4,
            last_interaction=123.0,
            group_id="",
            tags=["legacy"],
        )
        await manager._store.upsert_relation(legacy)

        assert await manager.delete_manual_relation(
            identity=("legacy", "legacy", "legacy_unknown", ""),
            expected_revision=manager.revision_for(legacy),
        ) is True
        assert await manager._store.get_relation(
            "legacy", "legacy", "legacy_unknown", ""
        ) is None

    @pytest.mark.asyncio
    async def test_create_still_rejects_equal_users_and_unknown_type(
        self, tmp_db_path
    ):
        manager = await _create_manager(tmp_db_path)

        with pytest.raises(EntityValidationError) as equal_error:
            await manager.create_manual_relation(
                from_user="alice",
                to_user="alice",
                group_id="",
                relation_type="colleague",
                strength=0.4,
                tags=[],
            )
        assert "to_user" in equal_error.value.field_errors

        with pytest.raises(EntityValidationError) as type_error:
            await manager.create_manual_relation(
                from_user="alice",
                to_user="bob",
                group_id="",
                relation_type="legacy_unknown",
                strength=0.4,
                tags=[],
            )
        assert "relation_type" in type_error.value.field_errors
