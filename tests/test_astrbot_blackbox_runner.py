"""真实 AstrBot 黑盒测试命令入口契约。"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from scripts import run_astrbot_blackbox


def test_blackbox_runner_selects_profile_and_forwards_arguments_with_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runner 应选择档位、透传参数并为 pytest 固定 UTF-8 环境。"""
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

    passthrough_arguments = ["-q", "-vv", "--maxfail=1"]
    assert run_astrbot_blackbox.main(["--profile", "pr", *passthrough_arguments]) == 0
    assert captured["command"] == [
        sys.executable,
        "-m",
        "pytest",
        *run_astrbot_blackbox.PROFILE_TARGETS["pr"],
        *passthrough_arguments,
    ]
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"
