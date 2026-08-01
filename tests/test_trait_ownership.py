"""LIFE-05 所有权契约：未发布的 Trait Evolution 不得保留隐藏生产入口。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.managers.memory_engine import MemoryEngine

_ROOT = Path(__file__).resolve().parents[1]


def test_trait_evolution_module_removed_from_production() -> None:
    """遗留 Trait Evolution 实现必须从生产包中移除。"""

    with pytest.raises(ImportError):
        importlib.import_module("core.managers.trait_evolution")


def test_trait_evolution_absent_from_public_config_contract() -> None:
    """Schema 与所有权不得声明隐藏的 trait_evolution 配置。"""

    schema = json.loads((_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "trait_evolution" not in schema


@pytest.mark.asyncio
async def test_engine_ignores_legacy_trait_config(tmp_path: Path) -> None:
    """即使遗留配置仍存在，引擎也不得构建 Trait 追踪器或写状态文件。"""

    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
        config={
            "graph_memory_enabled": False,
            "recall_engine.stopwords_path": "",
            "write_reliability.repair_enabled": False,
            "user_profile.enabled": False,
            "auto_learning.enabled": False,
            "knowledge_base.enabled": False,
            "notes.enabled": False,
            "reranker.enabled": False,
            "export.enabled": False,
            "continuity_tracking.enabled": False,
            "trait_evolution.enabled": True,
            "data_dir": str(tmp_path),
        },
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()
        assert not hasattr(engine, "trait_tracker")
    finally:
        await engine.close()

    assert not (tmp_path / "trait_evolution.json").exists()
