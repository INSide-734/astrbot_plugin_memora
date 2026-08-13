"""LIFE-02 所有权契约：旧 RelationshipTracker 不得再形成生产权威状态。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.base.runtime_feature_config import RuntimeFeatureConfigSections
from core.features.memory.application.memory_engine import MemoryEngine
from core.platform.composition.engine_runtime_config import ENGINE_RUNTIME_FIELDS
from core.platform.config import resolve_config_ownership

_ROOT = Path(__file__).resolve().parents[1]


def test_relationship_tracker_module_removed_from_production() -> None:
    """旧 JSON warmth 追踪器必须从生产包中移除。"""

    with pytest.raises(ImportError):
        importlib.import_module("core.managers.relationship_tracker")


def test_relationship_tracking_absent_from_config_contract() -> None:
    """Schema、Pydantic 模型、运行时投影与所有权不得再声明旧配置域。"""

    schema = json.loads((_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "relationship_tracking" not in schema
    assert "relationship_tracking" not in RuntimeFeatureConfigSections.model_fields
    assert not any(
        field.source_path.startswith("relationship_tracking.")
        for field in ENGINE_RUNTIME_FIELDS
    )
    with pytest.raises(KeyError):
        resolve_config_ownership("relationship_tracking.enabled")


@pytest.mark.asyncio
async def test_engine_never_builds_legacy_relationship_tracker(tmp_path: Path) -> None:
    """即使遗留配置仍出现在用户配置中，引擎也不得构建旧 Tracker 或写状态文件。"""

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
            "relationship_tracking.enabled": True,
            "data_dir": str(tmp_path),
        },
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch(
            "core.features.memory.application.memory_engine_lifecycle.BM25Retriever"
        ) as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert not hasattr(engine, "relationship_tracker")
    finally:
        await engine.close()
    assert not (tmp_path / "relationship_state.json").exists()
