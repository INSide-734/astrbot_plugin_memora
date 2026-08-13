"""验证质量监控评分器在初始化、写入与 Page API 之间共享。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.managers.memory_engine import MemoryEngine
from core.platform.composition.plugin_initializer import PluginInitializer
from core.platform.transport.page_api.quality_api import QualityApiMixin


class _QualityApiStub(QualityApiMixin):
    """提供只包含质量接口依赖的轻量测试替身。"""

    def __init__(self, plugin: object) -> None:
        """保存待解析质量评分器的插件对象。"""

        self.plugin = plugin


@pytest.mark.asyncio
async def test_runtime_write_and_quality_api_share_one_scorer(
    tmp_path: Path,
) -> None:
    """初始化完成后，写入样本应立即出现在质量接口统计中。"""

    config = MagicMock()
    initializer = PluginInitializer(MagicMock(), config, str(tmp_path))
    initializer.embedding_provider = MagicMock()
    initializer.llm_provider = MagicMock()
    initializer._faiss_checker.load_vec_db_class = MagicMock(return_value=MagicMock())

    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
    )
    memory_processor = MagicMock()
    initializer._component_factory.build_all = AsyncMock(
        return_value={
            "db": MagicMock(),
            "graph_db": None,
            "memory_engine": engine,
            "memory_processor": memory_processor,
            "memory_quarantine_store": MagicMock(),
            "memory_quality_gate": MagicMock(),
            "conversation_manager": MagicMock(),
            "identity_runtime": SimpleNamespace(close=AsyncMock()),
            "index_validator": MagicMock(),
            "decay_scheduler": None,
            "injection_decision_store": MagicMock(),
            "injection_decision_recorder": MagicMock(),
        }
    )
    initializer._create_prompt_protection_service = MagicMock(return_value=MagicMock())
    initializer._initialize_cognitive_components = AsyncMock()

    with patch("core.platform.composition.plugin_initializer.report_debug_event"):
        await initializer._run_full_init()

    engine._record_add_memory_observability(
        doc_id=17,
        content="用户喜欢喝深度烘焙咖啡。",
        metadata={"source_type": "private_chat", "importance": 0.8},
        atoms=None,
        duration_s=0.01,
    )

    api = _QualityApiStub(SimpleNamespace(initializer=initializer))
    result = await api.get_quality_stats()

    assert api._get_quality_scorer() is initializer.quality_scorer
    assert getattr(engine, "_quality_scorer", None) is initializer.quality_scorer
    assert result["status"] == "ok"
    assert result["data"]["total_scored"] == 1
    assert result["data"]["avg_overall"] > 0
