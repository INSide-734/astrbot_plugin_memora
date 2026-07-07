from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_recall_trace_api_imports_under_astrbot_package_name(monkeypatch):
    plugin_root = Path(__file__).resolve().parent.parent
    instance_core = plugin_root.parents[2]

    monkeypatch.setattr(
        sys,
        "path",
        [
            path
            for path in sys.path
            if Path(path or ".").resolve() != plugin_root
        ],
    )
    sys.path.insert(0, str(instance_core))
    for module_name in list(sys.modules):
        if module_name == "core" or module_name.startswith("core."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
        if module_name.startswith("data.plugins.astrbot_plugin_livingmemory."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    module = importlib.import_module(
        "data.plugins.astrbot_plugin_livingmemory.core.api.recall_trace_api"
    )

    assert module.RecallTraceApiMixin.__name__ == "RecallTraceApiMixin"
