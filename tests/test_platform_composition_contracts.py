"""平台 composition 组件的旧路径兼容契约。"""

from core.initializer import db_setup as legacy_db_setup
from core.initializer import (
    derived_rebuild_coordinator as legacy_derived_rebuild_coordinator,
)
from core.initializer import identity_lifecycle as legacy_identity_lifecycle
from core.initializer import provider_waiter as legacy_provider_waiter
from core.platform.composition import (
    DatabaseSetup,
    DerivedRebuildCoordinator,
    ProviderWaiter,
    close_identity_runtime_after_failure,
)


def test_provider_waiter_old_path_reuses_composition_implementation() -> None:
    """旧 initializer 路径只能导出 composition 的唯一 ProviderWaiter。"""

    assert legacy_provider_waiter.ProviderWaiter is ProviderWaiter


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
