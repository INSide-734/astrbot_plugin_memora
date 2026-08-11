"""自主学习每日维护调度器的生产装配契约。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.learning.infrastructure.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceProvider,
)


def _config_manager() -> MagicMock:
    """构造仅启用自主学习、关闭其他每日任务的配置管理器。

    返回:
        只向工厂暴露本测试所需配置值的管理器替身。
    """

    values = {
        "auto_learning.enabled": True,
        "backup_settings.enabled": False,
        "forgetting_agent.auto_cleanup_enabled": False,
        "graph_memory.enabled": False,
        "importance_decay.decay_rate": 0,
        "semantic_compression.enabled": False,
    }
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: values.get(key, default)
    manager.get_section.return_value = {}
    manager.session_manager = {}
    return manager


@pytest.mark.asyncio
async def test_auto_learning_alone_starts_daily_maintenance_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """自主学习是唯一每日任务时，工厂仍须启动并托管调度器。

    参数:
        monkeypatch: 用于隔离工厂外部组件构造的 pytest 替换器。
        tmp_path: 为状态文件和临时 SQLite 提供隔离目录。
    """

    from astrbot.core.provider.provider import Provider

    from core.platform.composition.component_factory import ComponentFactory

    database = MagicMock()
    database.initialize = AsyncMock()
    database.close = AsyncMock()

    engine = MagicMock()
    engine.initialize = AsyncMock()
    engine.close = AsyncMock()
    engine.text_processor = None
    engine.semantic_compressor = None
    engine.anomaly_detector = None
    engine.auto_learning = MagicMock()
    engine.profile_manager = None
    engine.knowledge_manager = None
    engine.note_manager = None
    memory_engine_factory = MagicMock(return_value=engine)

    conversation_store = MagicMock()
    conversation_store.initialize = AsyncMock()
    conversation_store.close = AsyncMock()

    scheduler = MagicMock()
    scheduler.start = AsyncMock()
    scheduler.stop = AsyncMock()
    scheduler_factory = MagicMock(return_value=scheduler)

    monkeypatch.setattr(
        "core.platform.composition.component_factory.MemoryEngine",
        memory_engine_factory,
    )
    monkeypatch.setattr(
        "core.platform.composition.component_factory.ConversationStore",
        MagicMock(return_value=conversation_store),
    )
    monkeypatch.setattr(
        "core.platform.composition.component_factory.DecayScheduler",
        scheduler_factory,
    )

    factory = ComponentFactory(MagicMock(), _config_manager(), str(tmp_path))
    factory._build_identity_runtime = AsyncMock(return_value=None)
    factory._build_injection_components = AsyncMock(
        side_effect=RuntimeError("injection failed")
    )

    faiss_checker = MagicMock()
    faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
    database_setup = MagicMock()
    database_setup.repair_message_counts = AsyncMock()
    database_setup.auto_rebuild_index_if_needed = AsyncMock()
    llm_provider = MagicMock(spec=Provider)
    llm_provider.text_chat = AsyncMock()

    with pytest.raises(RuntimeError, match="injection failed"):
        await factory.build_all(
            MagicMock(),
            llm_provider,
            MagicMock(return_value=database),
            faiss_checker,
            database_setup,
        )

    scheduler_factory.assert_called_once()
    assert scheduler_factory.call_args.kwargs["memory_engine"] is engine
    engine_config = memory_engine_factory.call_args.kwargs["config"]
    assert isinstance(
        engine_config["auto_learning_evidence_provider"],
        FeedbackLearningEvidenceProvider,
    )
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_awaited_once_with()
