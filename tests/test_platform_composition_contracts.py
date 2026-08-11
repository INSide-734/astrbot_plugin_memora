"""平台 composition 组件的旧路径兼容契约。"""

from unittest.mock import MagicMock

from astrbot.api.provider import Provider

from core import plugin_reload_lifecycle as legacy_plugin_reload_lifecycle
from core.initializer import db_setup as legacy_db_setup
from core.initializer import (
    derived_rebuild_coordinator as legacy_derived_rebuild_coordinator,
)
from core.initializer import engine_runtime_config as legacy_engine_runtime_config
from core.initializer import identity_lifecycle as legacy_identity_lifecycle
from core.initializer import provider_loader as legacy_provider_loader
from core.initializer import provider_waiter as legacy_provider_waiter
from core.initializer import readiness as legacy_readiness
from core.platform import composition as composition_package
from core.platform.composition import (
    DatabaseSetup,
    DerivedRebuildCoordinator,
    ProviderLoader,
    ProviderWaiter,
    close_identity_runtime_after_failure,
)
from core.platform.composition import (
    engine_runtime_config as composition_engine_runtime_config,
)
from core.platform.composition import provider_loader as composition_provider_loader
from core.platform.composition import readiness as composition_readiness


class _EmbeddingCandidate:
    """提供最小公开能力探针所需的 Embedding 候选。"""

    provider_config = {"id": "embedding-candidate"}

    async def get_embedding(self, content: str) -> list[float]:
        """返回确定性向量，供 adapter 能力探针识别入口。"""

        del content
        return [1.0]


def _config_manager(**values: str | None) -> MagicMock:
    """构造按点号路径返回指定 Provider ID 的配置替身。"""

    manager = MagicMock()
    manager.get.side_effect = values.get
    return manager


def test_provider_waiter_old_path_reuses_composition_implementation() -> None:
    """旧 initializer 路径只能导出 composition 的唯一 ProviderWaiter。"""

    assert legacy_provider_waiter.ProviderWaiter is ProviderWaiter


def test_provider_loader_old_path_reuses_composition_implementation() -> None:
    """旧 initializer 路径只能导出 composition 的唯一 ProviderLoader。"""

    assert legacy_provider_loader.ProviderLoader is ProviderLoader
    assert composition_package.ProviderLoader is ProviderLoader


def test_readiness_old_path_reuses_composition_implementation() -> None:
    """旧初始化就绪路径只能导出 composition 的唯一 mixin。"""

    assert (
        legacy_readiness.InitializerReadinessMixin
        is composition_readiness.InitializerReadinessMixin
    )


def test_reload_lifecycle_old_path_reuses_composition_implementation() -> None:
    """旧重载路径只能导出唯一函数并保留共享 monkeypatch 目标。"""

    from core.platform.composition import reload_lifecycle

    for name in reload_lifecycle.__all__:
        assert getattr(legacy_plugin_reload_lifecycle, name) is getattr(
            reload_lifecycle,
            name,
        )
    assert legacy_plugin_reload_lifecycle.asyncio is reload_lifecycle.asyncio


def test_shutdown_lifecycle_old_path_reuses_composition_implementation() -> None:
    """旧关停路径只能导出 composition 的唯一生产者收敛函数。"""

    from core import plugin_shutdown_lifecycle as legacy_shutdown_lifecycle
    from core.platform.composition import shutdown_lifecycle

    assert (
        legacy_shutdown_lifecycle.stop_runtime_producers
        is shutdown_lifecycle.stop_runtime_producers
    )


def test_provider_loader_accepts_configured_embedding_capability() -> None:
    """配置 ID 指向具备 Embedding 能力的对象时应保留原实例。"""

    embedding = _EmbeddingCandidate()
    context = MagicMock()
    context.get_provider_by_id.return_value = embedding
    context.get_using_provider.return_value = None
    loader = ProviderLoader(
        context,
        _config_manager(**{"provider_settings.embedding_provider_id": "embedding"}),
    )

    selected_embedding, selected_llm = loader.initialize_providers(None, None)

    assert selected_embedding is embedding
    assert selected_llm is None


def test_provider_loader_rejects_invalid_embedding_and_falls_back() -> None:
    """无 Embedding 能力的配置对象应被拒绝并回退到首个有效候选。"""

    fallback = _EmbeddingCandidate()
    context = MagicMock()
    context.provider_manager.inst_map = {"invalid": object()}
    context.get_all_embedding_providers.return_value = [object(), fallback]
    context.get_all_providers.return_value = []
    loader = ProviderLoader(
        context,
        _config_manager(**{"provider_settings.embedding_provider_id": "invalid"}),
    )

    selected_embedding, selected_llm = loader.initialize_providers(
        None,
        None,
        silent=True,
    )

    assert selected_embedding is fallback
    assert selected_llm is None


def test_provider_loader_requires_public_chat_provider_type() -> None:
    """聊天配置只接受 AstrBot 公开 Provider 类型并拒绝任意对象。"""

    chat_provider = MagicMock(spec=Provider)
    context = MagicMock()
    context.provider_manager.inst_map = {"chat": chat_provider}
    context.get_all_embedding_providers.return_value = []
    loader = ProviderLoader(
        context,
        _config_manager(**{"provider_settings.llm_provider_id": "chat"}),
    )

    _, selected_llm = loader.initialize_providers(None, None, silent=True)

    assert selected_llm is chat_provider
    assert composition_provider_loader.Provider is Provider

    context.provider_manager.inst_map = {"chat": object()}
    context.get_all_providers.return_value = []
    _, rejected_llm = loader.initialize_providers(None, None, silent=True)

    assert rejected_llm is None


def test_identity_lifecycle_old_path_reuses_composition_implementation() -> None:
    """旧身份失败清理路径只能导出 composition 的唯一函数。"""

    assert (
        legacy_identity_lifecycle.close_identity_runtime_after_failure
        is close_identity_runtime_after_failure
    )


def test_database_setup_old_path_reuses_composition_implementation() -> None:
    """旧数据库启动维护路径只能导出 composition 的唯一实现。"""

    assert legacy_db_setup.DatabaseSetup is DatabaseSetup


def test_rebuild_coordinator_old_path_reuses_composition_implementation() -> None:
    """旧派生重建路径只能导出 composition 的唯一协调器。"""

    assert (
        legacy_derived_rebuild_coordinator.DerivedRebuildCoordinator
        is DerivedRebuildCoordinator
    )


def test_engine_runtime_config_old_path_reuses_composition_exports() -> None:
    """旧运行时配置投影路径只能导出 composition 的唯一实现。"""

    assert (
        legacy_engine_runtime_config.__all__
        == composition_engine_runtime_config.__all__
    )
    for name in composition_engine_runtime_config.__all__:
        assert getattr(legacy_engine_runtime_config, name) is getattr(
            composition_engine_runtime_config,
            name,
        )
    assert (
        legacy_engine_runtime_config.ConfigReader
        is composition_engine_runtime_config.ConfigReader
    )
