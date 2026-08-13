"""原子质量配置与 Store 生命周期闭环测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.base.config_validator import validate_config
from core.features.memory.application.atom_lifecycle_manager import AtomLifecycleManager
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.platform.composition.component_factory import ComponentFactory


class _ConfigManager:
    """提供 ComponentFactory 构造 engine config 所需的点路径读取。"""

    def __init__(self, values: dict[str, object]) -> None:
        """保存测试使用的点路径配置映射。"""

        self.values = values

    def get(self, path: str, default=None):
        """返回测试配置值，缺失时使用调用方默认值。"""

        return self.values.get(path, default)


def test_pydantic_validates_atom_quality_filter_as_typed_model() -> None:
    """顶层 Atom 质量配置必须经过 Pydantic 字段与范围验证。"""

    config = validate_config(
        {
            "atom_quality_filter": {
                "atom_quality_filter_enabled": False,
                "atom_min_confidence": 0.72,
                "atom_min_importance": 0.4,
                "atom_min_content_length": 8,
                "atom_info_check_enabled": False,
                "atom_probationary_enabled": False,
                "atom_probationary_ttl_days": 4.0,
                "atom_dedup_enabled": False,
                "atom_dedup_threshold": 0.8,
                "atom_cold_storage_enabled": False,
                "atom_cold_days_threshold": 21.0,
                "atom_cold_max_importance": 0.25,
            }
        }
    )

    assert config.atom_quality_filter.atom_min_confidence == pytest.approx(0.72)
    assert config.atom_quality_filter.atom_probationary_enabled is False
    assert config.atom_quality_filter.atom_cold_days_threshold == pytest.approx(21.0)

    with pytest.raises(ValueError):
        validate_config({"atom_quality_filter": {"atom_min_confidence": 1.5}})


def test_component_factory_maps_atom_quality_config_to_engine() -> None:
    """Factory 必须把顶层质量配置映射成引擎实际读取的扁平键。"""

    manager = _ConfigManager(
        {
            "atom_quality_filter.atom_min_confidence": 0.74,
            "atom_quality_filter.atom_probationary_enabled": False,
            "atom_quality_filter.atom_cold_days_threshold": 30.0,
        }
    )
    factory = ComponentFactory(None, manager, "D:/memora-test")

    engine_config = factory._build_engine_config(
        stopwords_dir=Path(factory.data_dir) / "stopwords",
        graph_memory_enabled=True,
    )

    assert engine_config["atom_min_confidence"] == pytest.approx(0.74)
    assert engine_config["atom_probationary_enabled"] is False
    assert engine_config["atom_cold_days_threshold"] == pytest.approx(30.0)


def test_memory_processor_applies_runtime_quality_thresholds() -> None:
    """Processor 运行时必须读取质量阈值，而不是固定使用分类器默认值。"""

    processor = MemoryProcessor(
        config={
            "atom_enabled": True,
            "atom_min_confidence": 0.9,
            "atom_quality_filter_enabled": True,
        }
    )

    atoms = processor.classify_atoms_from_metadata(
        {
            "key_facts": ["用户喜欢拿铁咖啡"],
            "emotional_intensity": 0.8,
        },
        parent_importance=0.8,
    )

    assert atoms == []


def test_memory_processor_passes_emotional_intensity_to_atom_metadata() -> None:
    """Processor 到分类器的边界必须保留情绪强度。"""

    processor = MemoryProcessor(
        config={"atom_enabled": True, "atom_quality_filter_enabled": False}
    )

    atoms = processor.classify_atoms_from_metadata(
        {
            "key_facts": ["用户喜欢拿铁咖啡"],
            "emotion_tags": ["开心"],
            "emotional_intensity": 0.91,
        },
        parent_importance=0.8,
    )

    assert atoms[0].metadata["emotional_intensity"] == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_store_uses_emotional_intensity_and_restores_tags(
    tmp_db_path: str,
) -> None:
    """Store 最终 TTL 计算不得覆盖情绪强度，并应恢复情绪标签。"""

    store = AtomStore(
        tmp_db_path,
        config={
            "atom_probationary_enabled": False,
            "atom_probationary_ttl_days": 3.0,
        },
    )
    await store.initialize()
    atom = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.FACTUAL,
        content="一次情绪强烈的重要经历",
        importance=0.8,
        confidence=0.9,
        emotion_tags=["感动"],
        metadata={"emotional_intensity": 0.9},
    )

    atom_id = await store.insert(atom)
    restored = await store.get(atom_id)

    assert restored is not None
    assert restored.ttl_days >= 365.0
    assert restored.emotion_tags == ["感动"]
    assert restored.metadata["emotional_intensity"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_store_touch_many_updates_one_transaction_boundary(
    tmp_db_path: str,
) -> None:
    """批量访问反馈必须同时更新多个 Atom，并忽略重复 ID。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    atoms = [
        MemoryAtom(parent_memory_id=1, content="原子一"),
        MemoryAtom(parent_memory_id=2, content="原子二"),
    ]
    ids = await store.insert_many(atoms)
    before = [await store.get(atom_id) for atom_id in ids]
    await asyncio.sleep(0.01)

    await store.touch_many([ids[0], ids[1], ids[0]])
    after = [await store.get(atom_id) for atom_id in ids]

    assert all(item is not None for item in before + after)
    for earlier, later in zip(before, after, strict=True):
        assert earlier is not None
        assert later is not None
        assert later.last_accessed_at > earlier.last_accessed_at


@pytest.mark.asyncio
async def test_manual_reinforcement_is_scoped_and_revalidates_source() -> None:
    """新证据强化只能搜索相同作用域，并重新验证旧 Atom 父来源。"""

    existing = MemoryAtom(
        atom_id=91,
        parent_memory_id=4,
        content="用户喜欢喝无糖燕麦拿铁",
        session_id="session-a",
        persona_id="persona-a",
    )
    store = AsyncMock()
    store.search_fts = AsyncMock(return_value=[existing])
    store.filter_current_sources = AsyncMock(return_value=[existing])
    store.reinforce = AsyncMock()
    manager = AtomLifecycleManager(store)
    new_atom = MemoryAtom(
        parent_memory_id=5,
        content="用户喜欢喝无糖燕麦拿铁",
        confidence=0.88,
        session_id="session-a",
        persona_id="persona-a",
    )

    reinforced = await manager.run_manual_reinforcement(
        [new_atom],
        similarity_threshold=0.5,
    )

    assert reinforced == 1
    assert store.search_fts.await_args.kwargs["session_id"] == "session-a"
    assert store.search_fts.await_args.kwargs["persona_id"] == "persona-a"
    store.filter_current_sources.assert_awaited_once_with([existing])
    store.reinforce.assert_awaited_once_with(91, new_confidence=0.88)


def _make_write_engine(config: dict | None = None) -> Any:
    """构造可运行 add_memory 写入编排的最小 MemoryEngine。"""

    engine: Any = MemoryEngine(
        db_path=":memory:",
        faiss_db=object(),
        config={"atom_enabled": True, **(config or {})},
    )
    engine.hybrid_retriever = AsyncMock()
    engine.hybrid_retriever.add_memory = AsyncMock(return_value=123)
    engine.get_memory = AsyncMock(
        return_value={
            "id": 123,
            "text": "父 canonical 正文",
            "updated_at": "2026-07-28T01:02:03+00:00",
            "metadata": {
                "session_id": "session-a",
                "persona_id": "persona-a",
                "privacy_level": "shared",
            },
        }
    )
    engine.atom_store = AsyncMock()
    engine.graph_memory_manager = AsyncMock()
    engine.graph_memory_manager.index_memory = AsyncMock()
    engine.atom_lifecycle_manager = AsyncMock()
    engine.atom_lifecycle_manager.run_manual_reinforcement = AsyncMock(return_value=0)
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._retrieval.apply_interference = MagicMock(return_value=None)
    engine._retrieval.extract_triggers = MagicMock(return_value=None)
    engine._create_tracked_task = MagicMock()
    engine._schedule_evolution_after_write = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_add_memory_deduplicates_and_indexes_only_persisted_atoms() -> None:
    """写入链应先同批去重，并只把成功 Atom 交给图与观测。"""

    engine = _make_write_engine(
        {"atom_dedup_enabled": True, "atom_dedup_threshold": 0.7}
    )
    lower = MemoryAtom(
        parent_memory_id=0,
        content="用户喜欢喝无糖燕麦拿铁",
        confidence=0.7,
    )
    higher = MemoryAtom(
        parent_memory_id=0,
        content="用户喜欢喝无糖燕麦拿铁",
        confidence=0.9,
    )

    async def insert_many(atoms: list[MemoryAtom]) -> list[int]:
        """模拟批量成功并写回真实 Atom ID。"""

        for index, atom in enumerate(atoms, start=1):
            atom.atom_id = index
        return [atom.atom_id for atom in atoms]

    engine.atom_store.insert_many = AsyncMock(side_effect=insert_many)
    scorer = MagicMock()
    scorer.score_atom.return_value = MagicMock()
    engine._quality_scorer = scorer

    await engine.add_memory(
        "父 canonical 正文",
        session_id="session-a",
        persona_id="persona-a",
        metadata={"privacy_level": "shared"},
        atoms=[lower, higher],
    )

    insert_call = engine.atom_store.insert_many.await_args
    assert insert_call is not None
    persisted_batch = insert_call.args[0]
    assert persisted_batch == [higher]
    engine.atom_lifecycle_manager.run_manual_reinforcement.assert_awaited_once()
    graph_atoms = engine.graph_memory_manager.index_memory.await_args.args[3]
    assert graph_atoms == [higher]
    assert scorer.score_atom.call_count == 1
    assert scorer.score_atom.call_args.args[0]["content"] == higher.content


@pytest.mark.asyncio
async def test_add_memory_partial_failure_excludes_failed_atoms_from_graph() -> None:
    """部分 Atom 写入失败时，图索引只能消费已获得 ID 的成功子集。"""

    engine = _make_write_engine({"atom_dedup_enabled": False})
    succeeded = MemoryAtom(parent_memory_id=0, content="已成功原子")
    failed = MemoryAtom(parent_memory_id=0, content="失败原子")

    async def insert_many(atoms: list[MemoryAtom]) -> list[int]:
        """模拟前一分块成功、后一分块失败的真实批量边界。"""

        atoms[0].atom_id = 301
        raise RuntimeError("第二分块失败")

    engine.atom_store.insert_many = AsyncMock(side_effect=insert_many)
    engine.atom_store.insert = AsyncMock(side_effect=RuntimeError("单条修复失败"))

    await engine.add_memory(
        "父 canonical 正文",
        session_id="session-a",
        persona_id="persona-a",
        metadata={"privacy_level": "shared"},
        atoms=[succeeded, failed],
    )

    graph_atoms = engine.graph_memory_manager.index_memory.await_args.args[3]
    assert graph_atoms == [succeeded]
