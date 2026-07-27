"""验证项目的 uv 开发环境契约。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_pyproject() -> dict[str, object]:
    """读取并解析仓库根目录的 ``pyproject.toml``。"""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.is_file(), "仓库缺少 pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def _dependency_names(dependencies: list[str]) -> set[str]:
    """从依赖声明中提取经标准化的分发包名称。"""
    names: set[str] = set()
    for dependency in dependencies:
        name = dependency.split(";", 1)[0]
        for separator in ("[", "<", ">", "=", "!", "~"):
            name = name.split(separator, 1)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def test_uv_project_pins_python_and_disables_package_build() -> None:
    """项目应固定 Python 3.12，并以非打包模式交由 uv 管理。"""
    pyproject = _load_pyproject()
    project = pyproject["project"]
    tool = pyproject["tool"]

    assert isinstance(project, dict)
    assert isinstance(tool, dict)
    assert project["requires-python"] == ">=3.12,<3.13"
    assert tool["uv"]["package"] is False
    assert (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_uv_runtime_dependencies_match_requirements() -> None:
    """uv 与 AstrBot 安装入口应声明同一组直接运行时依赖。"""
    pyproject = _load_pyproject()
    project = pyproject["project"]
    assert isinstance(project, dict)

    requirements = [
        line.strip()
        for line in (REPO_ROOT / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert _dependency_names(project["dependencies"]) == _dependency_names(requirements)


def test_uv_lock_and_test_dependencies_are_versioned() -> None:
    """锁文件与执行后端测试所需的开发依赖应纳入版本控制。"""
    pyproject = _load_pyproject()
    dependency_groups = pyproject["dependency-groups"]
    assert isinstance(dependency_groups, dict)

    required_dev_dependencies = {
        "hypothesis",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
    }
    assert required_dev_dependencies <= _dependency_names(dependency_groups["dev"])
    assert (REPO_ROOT / "uv.lock").is_file(), "仓库缺少 uv.lock"


def test_ruff_and_pre_commit_are_versioned() -> None:
    """uv 应锁定提交前工具，并固定项目级 Ruff 基础规则。"""
    pyproject = _load_pyproject()
    dependency_groups = pyproject["dependency-groups"]
    tool = pyproject["tool"]
    assert isinstance(dependency_groups, dict)
    assert isinstance(tool, dict)

    assert {"pre-commit", "ruff"} <= _dependency_names(dependency_groups["dev"])
    assert tool["ruff"]["target-version"] == "py312"
    assert tool["ruff"]["lint"]["select"] == ["E4", "E7", "E9", "F", "I"]


def test_pre_commit_hooks_cover_ruff_and_file_integrity() -> None:
    """提交前钩子应覆盖 Python 质量检查与基础文件完整性。"""
    config = yaml.safe_load(
        (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    repositories = {repository["repo"]: repository for repository in config["repos"]}

    ruff_hooks = {
        hook["id"]
        for hook in repositories["https://github.com/astral-sh/ruff-pre-commit"][
            "hooks"
        ]
    }
    integrity_hooks = {
        hook["id"]
        for hook in repositories["https://github.com/pre-commit/pre-commit-hooks"][
            "hooks"
        ]
    }

    assert {"ruff-check", "ruff-format"} <= ruff_hooks
    assert {
        "check-merge-conflict",
        "check-toml",
        "check-yaml",
        "end-of-file-fixer",
        "trailing-whitespace",
    } <= integrity_hooks
