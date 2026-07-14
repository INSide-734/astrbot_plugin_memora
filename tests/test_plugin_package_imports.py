from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def _namespace_package(name: str, path: Path | None = None) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [] if path is None else [str(path)]
    return module


def test_recall_trace_api_imports_under_astrbot_package_name(monkeypatch):
    plugin_root = Path(__file__).resolve().parent.parent
    plugin_package = "data.plugins.astrbot_plugin_memora"

    def belongs_to_isolated_tree(name: str) -> bool:
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
        [
            path
            for path in sys.path
            if Path(path or ".").resolve() != plugin_root
        ],
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
        module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.api.recall_trace_api"
        )
        assert module.RecallTraceApiMixin.__name__ == "RecallTraceApiMixin"
    finally:
        for module_name in list(sys.modules):
            if belongs_to_isolated_tree(module_name):
                sys.modules.pop(module_name, None)
        sys.modules.update(saved_modules)
