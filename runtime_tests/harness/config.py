"""真实 AstrBot 场景环境与官方命令配置的共享边界。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCENARIO_ENV_KEYS = (
    "ASTRBOT_DASHBOARD_INITIAL_PASSWORD",
    "ASTRBOT_RESET_DASHBOARD_PASSWORD",
    "MEMORA_TEST_DRIVER_TOKEN",
)


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
