"""LEARN-03/04 所有权契约：MAB 生产开关与实现必须删除。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.base.runtime_feature_config import RuntimeFeatureConfigSections
from core.managers.memory_engine import MemoryEngine
from core.platform.composition.engine_runtime_config import ENGINE_RUNTIME_FIELDS
from core.platform.config import resolve_config_ownership

_ROOT = Path(__file__).resolve().parents[1]


def test_weight_learner_module_removed_from_production() -> None:
    """MAB 权重学习器必须从生产包中移除。"""

    with pytest.raises(ImportError):
        importlib.import_module("core.managers.weight_learner")


def test_weight_learning_absent_from_config_contract() -> None:
    """Schema、Pydantic、运行时投影与所有权不得声明 weight_learning。"""

    schema = json.loads((_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "weight_learning" not in schema
    assert "weight_learning" not in RuntimeFeatureConfigSections.model_fields
    assert not any(
        field.source_path.startswith("weight_learning.")
        for field in ENGINE_RUNTIME_FIELDS
    )
    with pytest.raises(KeyError):
        resolve_config_ownership("weight_learning.enabled")


@pytest.mark.asyncio
async def test_engine_ignores_legacy_weight_learning_config(tmp_path: Path) -> None:
    """遗留配置不得复活 MAB 学习者或写状态文件。"""

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
            "weight_learning.enabled": True,
            "data_dir": str(tmp_path),
        },
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()
        assert not hasattr(engine, "weight_learner")
    finally:
        await engine.close()

    assert not (tmp_path / "mab_weights.json").exists()
