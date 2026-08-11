"""平台 Provider adapter 的唯一导入契约。"""

from importlib.util import find_spec


def test_provider_package_reexports_adapter_implementation() -> None:
    """平台包必须恒等导出实现对象，且不再保留旧模块。"""

    from core.platform import provider as platform_provider
    from core.platform.provider import adapters

    for name in adapters.__all__:
        assert getattr(platform_provider, name) is getattr(adapters, name)

    assert find_spec("core.provider_adapters") is None
