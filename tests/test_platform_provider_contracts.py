"""平台 Provider adapter 的迁移兼容契约。"""


def test_provider_adapter_old_path_reuses_platform_implementation() -> None:
    """旧路径与平台包必须导出同一组 Provider adapter 对象。"""

    from core import provider_adapters as legacy
    from core.platform import provider as platform_provider
    from core.platform.provider import adapters

    for name in adapters.__all__:
        assert getattr(legacy, name) is getattr(adapters, name)
        assert getattr(platform_provider, name) is getattr(adapters, name)
