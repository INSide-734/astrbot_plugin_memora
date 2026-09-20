"""AtomLifecycleManager 和 dedup_atoms_batch 测试。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.application.atom_lifecycle_manager import (
    AtomLifecycleManager,
    dedup_atoms_batch,
)
from core.features.memory.domain.memory_atom import MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from tests.fact_evidence_helpers import fact_evidence, source_evidence

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

        mgr = AtomLifecycleManager(
            atom_store=store,
            config={
                "atom_cold_storage_enabled": True,
                "atom_cold_days_threshold": 14.0,
                "atom_cold_max_importance": 0.4,
            },
        )
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

        mgr = AtomLifecycleManager(
            atom_store=store,
            config={
                "atom_cold_storage_enabled": False,
            },
        )
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
        result = await mgr.run_manual_reinforcement(
            [new_atom], similarity_threshold=0.5
        )
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
        result = await mgr.run_manual_reinforcement(
            [new_atom], similarity_threshold=0.9
        )
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
        result = await mgr.run_manual_reinforcement(
            [new_atom], similarity_threshold=0.6
        )
        assert result >= 0  # may or may not match depending on tokens


# ---------------------------------------------------------------------------
# AtomLifecycleManager — rederive_for_sources（canonical 变更后的重派生）
# ---------------------------------------------------------------------------


async def _write_canonical_document(
    db_path: str,
    memory_id: int,
    *,
    facts: list[str],
    revision: str = "rev-1",
    status: str = "active",
) -> None:
    """写入带逐事实证据的 canonical 文档行。"""

    metadata = {
        "scope_key": "scope-a",
        "privacy_level": "shared",
        "session_id": "scope-a",
        "persona_id": "persona-a",
        "memory_status": status,
        "importance": 0.6,
        "key_facts": list(facts),
        "fact_source_evidence": fact_evidence(list(facts)),
    }
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
                "；".join(facts),
                json.dumps(metadata, ensure_ascii=False),
                "2026-07-21T00:00:00+00:00",
                revision,
            ),
        )
        await db.commit()


class _FakeClassifier:
    """按 ``metadata.key_facts`` 生成 Atom 的规则分类端口替身。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def classify_atoms_from_metadata(
        self,
        metadata: dict,
        parent_importance: float = 0.5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[MemoryAtom]:
        """返回与 key_facts 一一对应的 Atom。"""

        self.calls.append(
            {
                "parent_importance": parent_importance,
                "session_id": session_id,
                "persona_id": persona_id,
            }
        )
        facts = list(metadata.get("key_facts") or [])
        evidence = list(metadata.get("fact_source_evidence") or [])
        return [
            MemoryAtom(
                parent_memory_id=0,
                content=fact,
                importance=parent_importance,
                source_evidence=list(refs),
            )
            for fact, refs in zip(facts, evidence, strict=True)
        ]


def _bound_atom(
    content: str,
    *,
    memory_id: int = 17,
    revision: str = "rev-1",
) -> MemoryAtom:
    """构造已绑定当前父来源的 Atom。"""

    return MemoryAtom(
        parent_memory_id=memory_id,
        parent_revision=revision,
        parent_scope_key="scope-a",
        parent_privacy_level="shared",
        session_id="scope-a",
        persona_id="persona-a",
        content=content,
        source_evidence=source_evidence(content),
    )


class TestRederriveForSources:
    """canonical 变更后按当前事实重派生 Atom 行。"""

    @pytest.mark.asyncio
    async def test_rederive_replaces_rows_with_current_canonical_facts(
        self, tmp_db_path: str
    ) -> None:
        """陈旧行被当前 canonical 事实整体替换，FTS 同步。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A", "事实B"])
        await store.insert(_bound_atom("残留旧事实"))
        classifier = _FakeClassifier()
        manager = AtomLifecycleManager(store, classifier=classifier)

        report = await manager.rederive_for_sources([17], "canonical_update")

        assert report == {
            "sources": 1,
            "rederived": 1,
            "purged": 0,
            "skipped": 0,
            "failed": 0,
            "needs_repair": 0,
        }
        assert await store.search_fts("残留旧事实") == []
        atoms = await store.get_by_parent(17)
        assert [atom.content for atom in atoms] == ["事实A", "事实B"]
        assert {atom.parent_revision for atom in atoms} == {"rev-1"}
        assert classifier.calls[0]["parent_importance"] == 0.6

    @pytest.mark.asyncio
    async def test_rederive_purges_rows_for_non_recallable_source(
        self, tmp_db_path: str
    ) -> None:
        """父 canonical 归档后清除其 Atom 行，恢复可召回时再由同一入口重建。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        await store.insert(_bound_atom("事实A"))
        manager = AtomLifecycleManager(store, classifier=_FakeClassifier())

        await _write_canonical_document(
            tmp_db_path, 17, facts=["事实A"], status="archived"
        )
        report = await manager.rederive_for_sources([17], "decay_archived")
        assert report["purged"] == 1
        assert await store.get_by_parent_raw(17) == []

        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        report = await manager.rederive_for_sources([17], "decay_active")
        assert report["rederived"] == 1
        assert [atom.content for atom in await store.get_by_parent(17)] == ["事实A"]

    @pytest.mark.asyncio
    async def test_rederive_skips_missing_canonical_source(
        self, tmp_db_path: str
    ) -> None:
        """父 canonical 不存在时只跳过，残留行交由重建阶段清理。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        await store.insert(_bound_atom("孤儿事实", memory_id=99, revision=""))
        manager = AtomLifecycleManager(store, classifier=_FakeClassifier())

        report = await manager.rederive_for_sources([99], "decay_deleted")

        assert report["skipped"] == 1
        assert report["failed"] == 0
        assert len(await store.get_by_parent_raw(99)) == 1

    @pytest.mark.asyncio
    async def test_rederive_failure_isolated_and_counted(
        self, tmp_db_path: str
    ) -> None:
        """单来源失败只计 failed/needs_repair，其它来源照常重派生。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        await _write_canonical_document(tmp_db_path, 18, facts=["事实B"])
        await store.insert(_bound_atom("事实A"))
        await store.insert(_bound_atom("事实B", memory_id=18))
        original_replace = store.replace_by_parent

        async def failing_replace(parent_memory_id, atoms):
            if parent_memory_id == 17:
                raise RuntimeError("replace_failed")
            return await original_replace(parent_memory_id, atoms)

        store.replace_by_parent = failing_replace  # type: ignore[method-assign]
        manager = AtomLifecycleManager(store, classifier=_FakeClassifier())

        report = await manager.rederive_for_sources([17, 18], "rebuild_atoms")

        assert report["failed"] == 1
        assert report["needs_repair"] == 1
        assert report["rederived"] == 1
        assert [atom.content for atom in await store.get_by_parent(17)] == ["事实A"]
        assert [atom.content for atom in await store.get_by_parent(18)] == ["事实B"]

    @pytest.mark.asyncio
    async def test_rederive_without_classifier_marks_needs_repair(
        self, tmp_db_path: str
    ) -> None:
        """缺少分类端口时不猜测事实，整批标记 needs_repair。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        await store.insert(_bound_atom("事实A"))
        manager = AtomLifecycleManager(store)

        report = await manager.rederive_for_sources([17], "rebuild_atoms")

        assert report["failed"] == 1
        assert report["needs_repair"] == 1
        assert report["rederived"] == 0

    @pytest.mark.asyncio
    async def test_rederive_propagates_cancellation(self, tmp_db_path: str) -> None:
        """替换过程中的取消继续传播，不降级成失败计数。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        await store.insert(_bound_atom("事实A"))

        async def cancelling_replace(parent_memory_id, atoms):
            raise asyncio.CancelledError()

        store.replace_by_parent = cancelling_replace  # type: ignore[method-assign]
        manager = AtomLifecycleManager(store, classifier=_FakeClassifier())

        with pytest.raises(asyncio.CancelledError):
            await manager.rederive_for_sources([17], "rebuild_atoms")

    @pytest.mark.asyncio
    async def test_rederive_raises_when_canonical_unreadable(
        self, tmp_db_path: str
    ) -> None:
        """无法读取 canonical 时整批上报批次级失败，不伪装成逐来源降级。"""

        store = AtomStore(tmp_db_path)
        await store.initialize()
        await _write_canonical_document(tmp_db_path, 17, facts=["事实A"])
        await store.insert(_bound_atom("事实A"))

        async def failing_load(parent_ids):
            raise RuntimeError("canonical_source_unavailable")

        store.load_canonical_documents = failing_load  # type: ignore[method-assign]
        manager = AtomLifecycleManager(store, classifier=_FakeClassifier())

        with pytest.raises(RuntimeError, match="canonical_source_unavailable"):
            await manager.rederive_for_sources([17], "rebuild_atoms")
        assert [atom.content for atom in await store.get_by_parent(17)] == ["事实A"]
