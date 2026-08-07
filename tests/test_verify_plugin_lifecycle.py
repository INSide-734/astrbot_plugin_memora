"""生命周期 evidence harness 的独立定向测试。"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import pytest

from scripts._plugin_lifecycle_harness import namespace_contract_passed
from scripts.verify_plugin_lifecycle import (
    LifecycleVerificationError,
    parse_source_overrides,
    parse_versions,
    prepare_empty_data_root,
    run_worker_subprocess,
)


def _write_minimal_runtime(plugin_root: Path) -> None:
    """创建可由真实 AstrBot PluginManager 重载的最小插件。"""

    plugin_root.mkdir(parents=True)
    (plugin_root / "main.py").write_text(
        """from astrbot.api.star import Star, register

@register("Memora", "fixture", "fixture", "1.0.0")
class MemoraPlugin(Star):
    def __init__(self, context, config=None):
        super().__init__(context)
        self.config = config

    async def terminate(self):
        self.stopped = True
""",
        encoding="utf-8",
    )
    (plugin_root / "metadata.yaml").write_text(
        """name: astrbot_plugin_memora
display_name: Memora Fixture
desc: isolated lifecycle fixture
version: 1.0.0
author: fixture
repo: https://example.invalid/memora
astrbot_version: ">=4.24.2"
""",
        encoding="utf-8",
    )


def _passing_namespace(cycles: int = 3) -> dict[str, object]:
    """返回满足单注册和最终归零的 namespace 结果。"""

    snapshots = [
        {
            "map_registrations": 1,
            "registry_registrations": 1,
            "handlers": 2,
            "decorated_tools": 1,
            "runtime_tools": 0,
            "routes": 4,
        }
        for _ in range(cycles)
    ]
    return {
        "cycles": snapshots,
        "final_resources": {
            "registrations": 0,
            "handlers": 0,
            "decorated_tools": 0,
            "runtime_tools": 0,
            "stale_routes": 0,
            "tasks": 0,
            "connections": 0,
            "handles": 0,
        },
    }


def test_parse_versions_rejects_duplicates_and_invalid_values() -> None:
    """版本矩阵不得静默去重或接受模糊版本。"""

    assert parse_versions("4.24.2, 4.26.7,4.27.1") == (
        "4.24.2",
        "4.26.7",
        "4.27.1",
    )
    with pytest.raises(LifecycleVerificationError, match="duplicate"):
        parse_versions("4.26.7,4.26.7")
    with pytest.raises(LifecycleVerificationError, match="invalid"):
        parse_versions("latest")


def test_parse_source_overrides_rejects_ambiguous_entries(tmp_path: Path) -> None:
    """同一版本只能绑定一个明确源码根。"""

    mapping = parse_source_overrides([f"4.26.7={tmp_path}"])
    assert mapping == {"4.26.7": tmp_path.resolve()}
    with pytest.raises(LifecycleVerificationError, match="duplicate"):
        parse_source_overrides([f"4.26.7={tmp_path}", f"4.26.7={tmp_path / 'other'}"])


def test_prepare_empty_data_root_rejects_existing_content(tmp_path: Path) -> None:
    """生命周期命令不得复用可能属于用户的既有数据目录。"""

    data_dir = tmp_path / "isolated"
    assert prepare_empty_data_root(data_dir) == data_dir.resolve()
    (data_dir / "canary").write_text("occupied", encoding="utf-8")
    with pytest.raises(LifecycleVerificationError, match="not_empty"):
        prepare_empty_data_root(data_dir)


def test_namespace_contract_requires_stable_single_registration_and_zero_resources() -> (
    None
):
    """任一重复注册或残留资源都必须阻断生命周期门禁。"""

    passing = _passing_namespace()
    assert namespace_contract_passed(passing, 3) is True

    duplicate = _passing_namespace()
    duplicate["cycles"][1]["registry_registrations"] = 2  # type: ignore[index]
    assert namespace_contract_passed(duplicate, 3) is False

    stale = _passing_namespace()
    stale["final_resources"]["stale_routes"] = 1  # type: ignore[index]
    assert namespace_contract_passed(stale, 3) is False


def test_worker_uses_real_plugin_manager_for_three_cycles(tmp_path: Path) -> None:
    """隔离 worker 必须真实完成 load、两轮 reload、terminate 和 unbind。"""

    runtime_root = tmp_path / "runtime"
    plugin_root = runtime_root / "data" / "plugins" / "astrbot_plugin_memora"
    _write_minimal_runtime(plugin_root)
    version = importlib.metadata.version("astrbot")
    report = tmp_path / "worker.json"

    result = run_worker_subprocess(
        version=version,
        astrbot_source=None,
        plugin_root=plugin_root,
        data_dir=tmp_path / "data",
        report=report,
        scenario_mode="namespace",
    )

    assert result["worker_exit_code"] == 0
    assert result["status"] == "passed"
    assert [item["cycle"] for item in result["namespace"]["cycles"]] == [1, 2, 3]
    assert all(value == 0 for value in result["namespace"]["final_resources"].values())
    serialized = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in serialized


def test_initialization_failure_report_does_not_claim_migration_rollback(
    tmp_path: Path,
) -> None:
    """插件初始化失败注入不得被解释成数据库迁移回退证据。"""

    runtime_root = tmp_path / "runtime"
    plugin_root = runtime_root / "data" / "plugins" / "astrbot_plugin_memora"
    _write_minimal_runtime(plugin_root)
    version = importlib.metadata.version("astrbot")

    result = run_worker_subprocess(
        version=version,
        astrbot_source=None,
        plugin_root=plugin_root,
        data_dir=tmp_path / "data",
        report=tmp_path / "worker-failure.json",
        cycles=1,
        inject_initialization_failure=True,
        scenario_mode="namespace",
    )

    assert result["worker_exit_code"] == 0
    assert result["status"] == "passed"
    assert result["namespace"]["failure_scope"] == "plugin_initialize_only"
    assert result["namespace"]["migration_rollback_evidence"] == "not_claimed"
