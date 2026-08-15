from __future__ import annotations

import importlib
import json
import subprocess
import sys
import textwrap
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
            or name.startswith(("core.", plugin_package + "."))
            or name in {"data", "data.plugins", plugin_package}
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
    data_package.__dict__["plugins"] = plugins_package
    plugins_package.__dict__["astrbot_plugin_memora"] = memora_package
    sys.modules.update(
        {
            "data": data_package,
            "data.plugins": plugins_package,
            plugin_package: memora_package,
        }
    )

    try:
        trace_module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.platform.transport.page_api.recall_trace_api"
        )
        recorder_module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.features.injection.infrastructure.recorder"
        )
        perf_tracker_module = importlib.import_module(
            "data.plugins.astrbot_plugin_memora.core.features.observability.application.perf_tracker"
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
        sys.modules |= saved_modules


def test_command_endpoints_register_under_plugin_entrypoint() -> None:
    """验证真实 AstrBot 注册表把命令组及子命令绑定到插件入口。"""
    plugin_root = Path(__file__).resolve().parent.parent
    expected_commands = (
        "status",
        "health",
        "diagnostics",
        "search",
        "trace",
        "forget",
        "rebuild-index",
        "rebuild-graph",
        "webui",
        "summarize",
        "reset",
        "cleanup",
        "update",
        "help",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import importlib
                import json
                import sys
                import types
                from pathlib import Path

                from astrbot.core.star.star_handler import star_handlers_registry

                package = "data.plugins.astrbot_plugin_memora"
                data_package = types.ModuleType("data")
                data_package.__path__ = []
                plugins_package = types.ModuleType("data.plugins")
                plugins_package.__path__ = []
                plugin_package = types.ModuleType(package)
                plugin_package.__path__ = [str(Path.cwd())]
                data_package.__dict__["plugins"] = plugins_package
                plugins_package.__dict__["astrbot_plugin_memora"] = plugin_package
                sys.modules.update(
                    {
                        "data": data_package,
                        "data.plugins": plugins_package,
                        package: plugin_package,
                    }
                )

                importlib.import_module(
                    f"{package}.core.platform.transport.commands.command_endpoints"
                )
                handlers = [
                    handler
                    for handler in star_handlers_registry
                    if handler.handler_name == "memora"
                    or handler.handler_name
                    in {
                        "status",
                        "health",
                        "diagnostics",
                        "search",
                        "trace",
                        "forget",
                        "rebuild_index",
                        "rebuild_graph",
                        "webui",
                        "summarize",
                        "reset",
                        "cleanup",
                        "update",
                        "help",
                    }
                ]
                group_handler = next(
                    handler for handler in handlers if handler.handler_name == "memora"
                )
                group_filter = next(
                    command_filter
                    for command_filter in group_handler.event_filters
                    if type(command_filter).__name__ == "CommandGroupFilter"
                )
                print(
                    "MEMORA_COMMANDS="
                    + json.dumps(
                        {
                            "commands": sorted(
                                command_filter.command_name
                                for command_filter in group_filter.sub_command_filters
                            ),
                            "group": group_filter.group_name,
                            "handler_modules": sorted(
                                {handler.handler_module_path for handler in handlers}
                            ),
                        },
                        sort_keys=True,
                    )
                )
                """
            ),
        ],
        capture_output=True,
        check=False,
        cwd=plugin_root,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    snapshots = [
        line.removeprefix("MEMORA_COMMANDS=")
        for line in result.stdout.splitlines()
        if line.startswith("MEMORA_COMMANDS=")
    ]
    assert len(snapshots) == 1, result.stdout
    assert json.loads(snapshots[0]) == {
        "commands": sorted(expected_commands),
        "group": "memora",
        "handler_modules": ["data.plugins.astrbot_plugin_memora.main"],
    }
