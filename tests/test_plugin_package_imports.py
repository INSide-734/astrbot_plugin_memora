from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def _namespace_package(name: str, path: Path | None = None) -> types.ModuleType:
    """构造可用于隔离导入测试的命名空间包。"""
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [] if path is None else [str(path)]
    return module


def test_feature_modules_import_under_astrbot_package_name(monkeypatch):
    """验证功能模块可在 AstrBot 的真实插件包命名空间下导入。"""
    plugin_root = Path(__file__).resolve().parent.parent
    plugin_package = "data.plugins.astrbot_plugin_memora"

    def belongs_to_isolated_tree(name: str) -> bool:
        """判断模块是否属于本测试需要隔离和恢复的导入树。"""
        return (
            name == "core"
            or name.startswith("core.")
            or name in {"data", "data.plugins", plugin_package}
            or name.startswith(plugin_package + ".")
        )

    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if belongs_to_isolated_tree(name)
    }
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if Path(path or ".").resolve() != plugin_root],
    )
    for module_name in saved_modules:
        sys.modules.pop(module_name, None)

    data_package = _namespace_package("data")
    plugins_package = _namespace_package("data.plugins")
    memora_package = _namespace_package(plugin_package, plugin_root)
    data_package.plugins = plugins_package
    plugins_package.astrbot_plugin_memora = memora_package
    sys.modules.update(
        {
            "data": data_package,
            "data.plugins": plugins_package,
            plugin_package: memora_package,
        }
    )

    try:
        trace_module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.api.recall_trace_api"
        )
        recorder_module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.injection.recorder"
        )
        perf_tracker_module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.monitoring.perf_tracker"
        )
        assert trace_module.RecallTraceApiMixin.__name__ == "RecallTraceApiMixin"
        assert recorder_module.InjectionDecisionRecorder.__name__ == (
            "InjectionDecisionRecorder"
        )
        assert perf_tracker_module.PerfTracker.__name__ == "PerfTracker"
    finally:
        for module_name in list(sys.modules):
            if belongs_to_isolated_tree(module_name):
                sys.modules.pop(module_name, None)
        sys.modules.update(saved_modules)
