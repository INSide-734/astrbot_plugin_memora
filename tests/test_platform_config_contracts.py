"""平台配置契约的旧路径兼容测试。"""

from core.base import config_ownership as legacy_ownership
from core.base import config_runtime_effects as legacy_runtime_effects
from core.platform.config import ownership as platform_ownership
from core.platform.config import runtime_effects as platform_runtime_effects


def test_runtime_effects_old_path_reuses_platform_exports() -> None:
    """旧影响分类路径只能导出 platform config 的唯一实现。"""

    assert legacy_runtime_effects.__all__ == platform_runtime_effects.__all__
    for name in platform_runtime_effects.__all__:
        assert getattr(legacy_runtime_effects, name) is getattr(
            platform_runtime_effects,
            name,
        )


def test_config_ownership_old_path_reuses_platform_exports() -> None:
    """旧 owner 注册表路径只能导出 platform config 的唯一实现。"""

    assert legacy_ownership.__all__ == platform_ownership.__all__
    for name in platform_ownership.__all__:
        assert getattr(legacy_ownership, name) is getattr(
            platform_ownership,
            name,
        )
