"""插件组合根的基础装配与生命周期组件。"""

from .component_factory import ComponentFactory
from .db_setup import DatabaseSetup
from .derived_rebuild_coordinator import DerivedRebuildCoordinator
from .identity_lifecycle import close_identity_runtime_after_failure
from .plugin_initializer import PluginInitializer
from .provider_loader import ProviderLoader
from .provider_waiter import ProviderWaiter

__all__ = [
    "ComponentFactory",
    "DatabaseSetup",
    "DerivedRebuildCoordinator",
    "PluginInitializer",
    "ProviderLoader",
    "ProviderWaiter",
    "close_identity_runtime_after_failure",
]
