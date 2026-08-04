"""不含场景秘密的 AstrBot 会话模板构建器。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_CLI_PREFIX = [sys.executable, "-m", "astrbot.cli.__main__"]
_PLUGIN_NAME = "astrbot_plugin_memora"
_SCENARIO_ENV_KEYS = (
    "ASTRBOT_DASHBOARD_INITIAL_PASSWORD",
    "ASTRBOT_RESET_DASHBOARD_PASSWORD",
    "MEMORA_TEST_DRIVER_TOKEN",
)
_STAGED_ENTRIES = (
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "core",
    "static",
)
_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "AGENTS.md",
    "DESIGN.md",
)


def run_astrbot_cli(
    arguments: list[str],
    root: Path,
    *,
    extra_env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> None:
    """在指定 AstrBot 根目录执行真实 CLI，并将失败输出纳入异常。"""
    environment = os.environ.copy()
    for key in _SCENARIO_ENV_KEYS:
        environment.pop(key, None)
    environment["ASTRBOT_ROOT"] = str(root)
    if extra_env:
        environment.update(extra_env)

    completed = subprocess.run(
        [*_CLI_PREFIX, *arguments],
        cwd=root,
        env=environment,
        input=input_text,
        stdin=subprocess.DEVNULL if input_text is None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        output = completed.stdout.strip()
        raise RuntimeError(
            f"AstrBot CLI 执行失败（退出码 {completed.returncode}）：{output}"
        )


def _stage_memora(repo_root: Path, staging_root: Path) -> Path:
    """按发布白名单把 Memora 源码复制到独立暂存目录。"""
    staged_plugin = staging_root / _PLUGIN_NAME
    staging_root.mkdir(parents=True, exist_ok=True)
    if staged_plugin.exists():
        raise FileExistsError(f"Memora 暂存目录已存在：{staged_plugin}")
    staged_plugin.mkdir()

    for name in _STAGED_ENTRIES:
        source = repo_root / name
        if not source.exists():
            continue
        destination = staged_plugin / name
        if source.is_dir():
            shutil.copytree(source, destination, ignore=_COPY_IGNORE)
        else:
            shutil.copy2(source, destination)
    return staged_plugin


@dataclass(frozen=True, slots=True)
class AstrBotTemplate:
    """保存一次会话内可复用且不含场景秘密的 AstrBot 根目录。"""

    root: Path

    @classmethod
    def build(
        cls,
        repo_root: Path,
        template_root: Path,
        staging_root: Path,
    ) -> AstrBotTemplate:
        """通过真实 CLI 初始化模板并安装白名单源码与测试驱动。"""
        repo_root = repo_root.resolve()
        template_root = template_root.resolve()
        staging_root = staging_root.resolve()
        template_root.mkdir(parents=True, exist_ok=True)

        run_astrbot_cli(["init"], template_root, input_text="y\n")
        staged_memora = _stage_memora(repo_root, staging_root)
        test_driver = (
            repo_root
            / "runtime_tests"
            / "fixtures"
            / "plugins"
            / "astrbot_plugin_memora_test_driver"
        )
        run_astrbot_cli(["plug", "install", str(staged_memora)], template_root)
        run_astrbot_cli(["plug", "install", str(test_driver)], template_root)

        # AstrBot 4.26.7 导入 core 时会先生成随机 Dashboard 凭据；模板不能
        # 固化该场景状态，场景副本会用显式密码再次通过官方 init 生成配置。
        (template_root / "data" / "cmd_config.json").unlink(missing_ok=True)
        return cls(root=template_root)
