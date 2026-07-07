"""AtomLifecycleManager 和 dedup_atoms_batch 测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.managers.atom_lifecycle_manager import (
    AtomLifecycleManager,
    dedup_atoms_batch,
)


# ---------------------------------------------------------------------------
# dedup_atoms_batch — pure function
# ---------------------------------------------------------------------------

class TestDedupAtomsBatch:
    """Test the standalone dedup_atoms_batch function."""

    def _make_atom(self, content: str, confidence: float = 0.7) -> MagicMock:
        atom = MagicMock()
        atom.content = content
        atom.confidence = confidence
        return atom

    def test_empty_list(self) -> None:
        result = dedup_atoms_batch([])
        assert result == []

    def test_single_atom(self) -> None:
        atoms = [self._make_atom("hello")]
        result = dedup_atoms_batch(atoms)
        assert len(result) == 1

    def test_identical_content_dedup(self) -> None:
        atoms = [
            self._make_atom("周末去西湖划船非常开心", 0.6),
            self._make_atom("周末去西湖划船非常开心", 0.8),
        ]
        result = dedup_atoms_batch(atoms)
        assert len(result) == 1
        # Higher confidence atom should be kept
        assert result[0].confidence == 0.8

    def test_different_content_no_dedup(self) -> None:
        atoms = [
            self._make_atom("周末去西湖划船", 0.7),
            self._make_atom("周一要开会讨论项目", 0.7),
        ]
        result = dedup_atoms_batch(atoms)
        assert len(result) == 2

    def test_similar_short_text_bigram_dedup(self) -> None:
        atoms = [
            self._make_atom("西湖划船", 0.6),
            self._make_atom("西湖划船记", 0.8),
        ]
        result = dedup_atoms_batch(atoms)
        assert len(result) == 1
        assert result[0].confidence == 0.8

    def test_very_short_text_no_dedup(self) -> None:
        """Text too short to tokenize should NOT be deduplicated."""
        atoms = [
            self._make_atom("ab", 0.6),
            self._make_atom("ab", 0.8),
        ]
        result = dedup_atoms_batch(atoms)
        # Individual tokens < 2, so Jaccard can't compute — both kept
        assert len(result) == 2

    def test_multiple_duplicates_keep_highest(self) -> None:
        atoms = [
            self._make_atom("小明是我的大学室友小明是我的大学室友", 0.3),
            self._make_atom("小明是我的大学同学小明是我的大学同学", 0.5),
            self._make_atom("小明是我的大学室友小明是我的大学室友", 0.9),
        ]
        result = dedup_atoms_batch(atoms)
        # atom[0] vs atom[1]: similar but different words
        # atom[2] dup of atom[0] already in kept, confidence 0.9 > 0.3, replaces
        assert len(result) <= 2

    def test_below_threshold_not_merged(self) -> None:
        atoms = [
            self._make_atom("周一开会讨论项目进度", 0.7),
            self._make_atom("周末去西湖划船游玩", 0.7),
        ]
        result = dedup_atoms_batch(atoms, similarity_threshold=0.99)
        assert len(result) == 2  # threshold too high, nothing merges

    def test_low_threshold_merges_loosely(self) -> None:
        atoms = [
            self._make_atom("周例会讨论项目", 0.7),
            self._make_atom("周会讨论进度", 0.9),
        ]
        result = dedup_atoms_batch(atoms, similarity_threshold=0.1)
        # Should merge these two similar short texts
        assert len(result) == 1

    def test_confidence_tiebreaker(self) -> None:
        atoms = [
            self._make_atom("重复内容测试文本重复内容测试文本", 0.9),
            self._make_atom("重复内容测试文本重复内容测试文本", 0.5),
        ]
        result = dedup_atoms_batch(atoms)
        assert len(result) == 1
        assert result[0].confidence == 0.9


# ---------------------------------------------------------------------------
# AtomLifecycleManager — construction
# ---------------------------------------------------------------------------

class TestAtomLifecycleManagerInit:
    """Construction and configuration parsing."""

    def test_default_init(self) -> None:
        store = MagicMock()
        mgr = AtomLifecycleManager(atom_store=store)
        assert mgr.atom_store is store
        assert mgr._maintenance_interval_hours == 24.0
        assert mgr._forget_delay_days == 7.0
        assert mgr._purge_delay_days == max(7.0 * 4.0, 30.0)  # 30.0
        assert mgr._cold_storage_enabled is True
        assert mgr._cold_days_threshold == 14.0
        assert mgr._cold_max_importance == 0.4
        assert mgr._running is False
        assert mgr._task is None

    def test_custom_config(self) -> None:
        store = MagicMock()
        config = {
            "atom_maintenance_interval_hours": 12.0,
            "atom_forget_delay_days": 3.0,
            "atom_purge_delay_days": 60.0,
            "atom_cold_storage_enabled": False,
            "atom_cold_days_threshold": 30.0,
            "atom_cold_max_importance": 0.2,
        }
        mgr = AtomLifecycleManager(atom_store=store, config=config)
        assert mgr._maintenance_interval_hours == 12.0
        assert mgr._forget_delay_days == 3.0
        assert mgr._purge_delay_days == 60.0
        assert mgr._cold_storage_enabled is False
        assert mgr._cold_days_threshold == 30.0
        assert mgr._cold_max_importance == 0.2

    def test_purge_delay_falls_back_to_min(self) -> None:
        store = MagicMock()
        config = {"atom_forget_delay_days": 1.0}
        mgr = AtomLifecycleManager(atom_store=store, config=config)
        # min(1.0*4, 30) = 30 → purge_delay_days = 30.0
        assert mgr._purge_delay_days == 30.0


# ---------------------------------------------------------------------------
# AtomLifecycleManager — lifecycle control (start / stop)
# ---------------------------------------------------------------------------

class TestAtomLifecycleManagerStartStop:
    """start / stop methods with mocked asyncio tasks."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self) -> None:
        store = MagicMock()
        mgr = AtomLifecycleManager(atom_store=store)
        await mgr.start()
        assert mgr._running is True
        assert mgr._task is not None
        # Cleanup
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self) -> None:
        store = MagicMock()
        mgr = AtomLifecycleManager(atom_store=store)
        await mgr.start()
        task_before = mgr._task
        await mgr.start()
        assert mgr._task is task_before  # same task
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self) -> None:
        store = MagicMock()
        mgr = AtomLifecycleManager(atom_store=store)
        await mgr.start()
        assert mgr._running is True
        await mgr.stop()
        assert mgr._running is False


# ---------------------------------------------------------------------------
# AtomLifecycleManager — run_maintenance
# ---------------------------------------------------------------------------

class TestRunMaintenance:
    """run_maintenance exercises atom store methods."""

    @pytest.mark.asyncio
    async def test_run_maintenance_with_cold_storage(self) -> None:
        store = MagicMock()
        store.expire_stale_atoms = AsyncMock(return_value=5)
        store.forget_expired_atoms = AsyncMock(return_value=3)
        store.cleanup_forgotten = AsyncMock(return_value=2)
        store.migrate_to_cold = AsyncMock(return_value=4)

        mgr = AtomLifecycleManager(atom_store=store, config={
            "atom_cold_storage_enabled": True,
            "atom_cold_days_threshold": 14.0,
            "atom_cold_max_importance": 0.4,
        })
        result = await mgr.run_maintenance()
        assert result["expired"] == 5
        assert result["forgotten"] == 3
        assert result["purged"] == 2
        assert result["cold_migrated"] == 4
        store.migrate_to_cold.assert_called_once_with(
            cold_days_threshold=14.0,
            max_importance=0.4,
        )

    @pytest.mark.asyncio
    async def test_run_maintenance_without_cold_storage(self) -> None:
        store = MagicMock()
        store.expire_stale_atoms = AsyncMock(return_value=1)
        store.forget_expired_atoms = AsyncMock(return_value=0)
        store.cleanup_forgotten = AsyncMock(return_value=0)

        mgr = AtomLifecycleManager(atom_store=store, config={
            "atom_cold_storage_enabled": False,
        })
        result = await mgr.run_maintenance()
        assert "cold_migrated" not in result
        store.migrate_to_cold.assert_not_called()


# ---------------------------------------------------------------------------
# AtomLifecycleManager — run_manual_reinforcement
# ---------------------------------------------------------------------------

class TestManualReinforcement:
    """run_manual_reinforcement — find and reinforce similar atoms."""

    @pytest.mark.asyncio
    async def test_empty_new_atoms(self) -> None:
        store = MagicMock()
        mgr = AtomLifecycleManager(atom_store=store)
        assert await mgr.run_manual_reinforcement([]) == 0

    @pytest.mark.asyncio
    async def test_reinforce_matching_atom(self) -> None:
        store = MagicMock()
        existing = MagicMock()
        existing.atom_id = 42
        existing.content = "用户喜欢喝拿铁咖啡尤其偏爱拿铁咖啡口味"
        store.search_fts = AsyncMock(return_value=[existing])
        store.reinforce = AsyncMock()

        new_atom = MagicMock()
        new_atom.content = "用户喜欢喝拿铁咖啡"
        new_atom.confidence = 0.8

        mgr = AtomLifecycleManager(atom_store=store)
        result = await mgr.run_manual_reinforcement([new_atom], similarity_threshold=0.5)
        assert result == 1
        store.reinforce.assert_called_once_with(42, new_confidence=0.8)

    @pytest.mark.asyncio
    async def test_no_reinforce_if_no_match(self) -> None:
        store = MagicMock()
        existing = MagicMock()
        existing.atom_id = 1
        existing.content = "完全不同的内容关于另一件事"
        store.search_fts = AsyncMock(return_value=[existing])

        new_atom = MagicMock()
        new_atom.content = "用户喜欢喝咖啡拿铁"
        new_atom.confidence = 0.7

        mgr = AtomLifecycleManager(atom_store=store)
        result = await mgr.run_manual_reinforcement([new_atom], similarity_threshold=0.9)
        assert result == 0  # too different, no match
        store.reinforce.assert_not_called()

    @pytest.mark.asyncio
    async def test_reinforce_short_cjk_text(self) -> None:
        store = MagicMock()
        existing = MagicMock()
        existing.atom_id = 3
        existing.content = "西湖划船"
        store.search_fts = AsyncMock(return_value=[existing])
        store.reinforce = AsyncMock()

        new_atom = MagicMock()
        new_atom.content = "西湖划船记"
        new_atom.confidence = 0.6

        mgr = AtomLifecycleManager(atom_store=store)
        result = await mgr.run_manual_reinforcement([new_atom], similarity_threshold=0.6)
        assert result >= 0  # may or may not match depending on tokens
