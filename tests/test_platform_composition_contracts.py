"""平台 composition 组件的新 owner 与 Provider 契约。"""

import importlib.util
from unittest.mock import MagicMock

from astrbot.api.provider import Provider

from core.platform import composition as composition_package
from core.platform.composition import (
    ComponentFactory,
    DatabaseSetup,
    DerivedRebuildCoordinator,
    FaissChecker,
    PluginInitializer,
    ProviderLoader,
    ProviderWaiter,
    close_identity_runtime_after_failure,
)
from core.platform.composition import component_factory as composition_component_factory
from core.platform.composition import (
    plugin_initializer as composition_plugin_initializer,
)
from core.platform.composition import provider_loader as composition_provider_loader


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


def test_composition_package_exports_owned_components() -> None:
    """composition 包应从新 owner 导出基础装配组件。"""

    assert composition_package.__all__ == [
        "ComponentFactory",
        "DatabaseSetup",
        "DerivedRebuildCoordinator",
        "FaissChecker",
        "PluginInitializer",
        "ProviderLoader",
        "ProviderWaiter",
        "close_identity_runtime_after_failure",
    ]
    assert composition_package.ComponentFactory is ComponentFactory
    assert composition_package.DatabaseSetup is DatabaseSetup
    assert composition_package.DerivedRebuildCoordinator is DerivedRebuildCoordinator
    assert composition_package.FaissChecker is FaissChecker
    assert composition_package.PluginInitializer is PluginInitializer
    assert composition_package.ProviderLoader is ProviderLoader
    assert composition_package.ProviderWaiter is ProviderWaiter
    assert (
        composition_package.close_identity_runtime_after_failure
        is close_identity_runtime_after_failure
    )


def test_migrated_composition_compatibility_modules_are_removed() -> None:
    """已迁移的 Composition 旧模块不得重新出现。"""

    legacy_modules = (
        "core.plugin_initializer",
        "core.initializer.component_factory",
        "core.initializer.faiss_checker",
        "core.initializer.provider_waiter",
    )

    assert all(importlib.util.find_spec(name) is None for name in legacy_modules)


def test_composition_uses_public_provider_contract() -> None:
    """组合根应统一使用 AstrBot 公开 Provider 类型。"""

    assert composition_component_factory.Provider is Provider
    assert composition_plugin_initializer.Provider is Provider
    assert composition_provider_loader.Provider is Provider


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
