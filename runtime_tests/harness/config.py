"""真实 AstrBot 场景环境与官方命令配置的共享边界。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .live import LIVE_ENV_KEYS

SCENARIO_ENV_KEYS = (
    "ASTRBOT_DASHBOARD_INITIAL_PASSWORD",
    "ASTRBOT_RESET_DASHBOARD_PASSWORD",
    "MEMORA_TEST_DRIVER_TOKEN",
    *LIVE_ENV_KEYS,
)

_SECRET_ENV_SUFFIXES = (
    "_API_KEY",
    "_ACCESS_KEY",
    "_AUTH_TOKEN",
    "_CREDENTIAL",
    "_CREDENTIALS",
    "_PASSWORD",
    "_SECRET",
    "_TOKEN",
)
_SECRET_ENV_EXACT = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
}


def build_isolated_environment(
    root: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """复制父环境，但剥离凭据、代理和场景变量后设置隔离根目录。"""
    environment = os.environ.copy()
    for key in list(environment):
        normalized = key.upper()
        if (
            key in SCENARIO_ENV_KEYS
            or normalized in _SECRET_ENV_EXACT
            or normalized.endswith(_SECRET_ENV_SUFFIXES)
        ):
            environment.pop(key, None)
    environment["ASTRBOT_ROOT"] = str(root)
    if extra_env:
        environment.update(extra_env)
    return environment


def read_command_config(root: Path) -> dict[str, Any]:
    """读取指定场景根目录下由 AstrBot 官方 CLI 生成的命令配置。"""
    config_path = root / "data" / "cmd_config.json"
    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def write_command_config(root: Path, config: dict[str, Any]) -> None:
    """以 AstrBot 兼容的 UTF-8 BOM 格式写回场景命令配置。"""
    config_path = root / "data" / "cmd_config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


def configure_dashboard(
    config: dict[str, Any],
    port: int,
    *,
    password_change_required: bool | None = None,
) -> None:
    """统一设置回环 Dashboard 端点，并可同步首次改密标记。"""
    dashboard = config["dashboard"]
    dashboard["host"] = "127.0.0.1"
    dashboard["port"] = port
    if password_change_required is not None:
        dashboard["password_change_required"] = password_change_required
