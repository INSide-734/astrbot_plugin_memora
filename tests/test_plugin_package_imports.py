from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


def test_feature_modules_import_under_astrbot_package_name(monkeypatch):
    plugin_root = Path(__file__).resolve().parent.parent

    monkeypatch.setattr(
        sys,
        "path",
        [
            path
            for path in sys.path
            if Path(path or ".").resolve() != plugin_root
        ],
    )
    for module_name in list(sys.modules):
        if module_name == "core" or module_name.startswith("core."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
        if module_name == "data" or module_name.startswith("data."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    namespace_paths = {
        "data": plugin_root.parent,
        "data.plugins": plugin_root.parent,
        "data.plugins.astrbot_plugin_memora": plugin_root,
    }
    for package_name, package_path in namespace_paths.items():
        package = ModuleType(package_name)
        package.__path__ = [str(package_path)]
        monkeypatch.setitem(sys.modules, package_name, package)

    trace_module = importlib.import_module(
        "data.plugins.astrbot_plugin_memora.core.api.recall_trace_api"
    )
    recorder_module = importlib.import_module(
        "data.plugins.astrbot_plugin_memora.core.injection.recorder"
    )

    assert trace_module.RecallTraceApiMixin.__name__ == "RecallTraceApiMixin"
    assert recorder_module.InjectionDecisionRecorder.__name__ == (
        "InjectionDecisionRecorder"
    )
