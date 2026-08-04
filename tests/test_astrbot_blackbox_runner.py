"""真实 AstrBot 黑盒测试命令入口契约。"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts import run_astrbot_blackbox


def test_blackbox_runner_forces_utf8_for_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pytest 及真实 AstrBot 子进程必须继承稳定的 UTF-8 输出环境。"""
    captured: dict[str, Any] = {}

    def capture_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[list[str]]:
        """记录 runner 传给 subprocess 的命令参数并模拟成功。"""
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_astrbot_blackbox.subprocess, "run", capture_run)

    assert run_astrbot_blackbox.main([]) == 0
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"
