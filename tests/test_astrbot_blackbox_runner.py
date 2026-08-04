"""真实 AstrBot 黑盒测试命令入口契约。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from runtime_tests.harness.process import AstrBotProcess, reserve_loopback_port
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


def test_process_log_sanitization_keeps_utf8_and_last_200_lines(
    tmp_path: Path,
) -> None:
    """进程日志应限制行数，并保留中文、脱敏秘密和移除 ANSI。"""
    password = "Dashboard-Secret-A9"
    test_token = "test-token-secret"
    port, reservation = reserve_loopback_port()
    process = AstrBotProcess(
        root=tmp_path,
        port=port,
        reservation=reservation,
        password=password,
        test_token=test_token,
    )
    retained_lines = [f"正常日志 {index}" for index in range(199)]
    retained_lines.append(f"\x1b[32m中文日志 {password} {test_token} {tmp_path}\x1b[0m")
    process._log_path.write_text(
        "\n".join(
            [
                "\x1b[31m端口 12345 已被占用\x1b[0m",
                *retained_lines,
            ]
        ),
        encoding="utf-8",
    )

    try:
        sanitized_log = process.read_sanitized_log()
        lines = sanitized_log.splitlines()

        assert len(lines) == 200
        assert "端口 12345 已被占用" not in sanitized_log
        assert "中文日志" in sanitized_log
        assert password not in sanitized_log
        assert test_token not in sanitized_log
        assert str(tmp_path) not in sanitized_log
        assert "[DASHBOARD_PASSWORD]" in sanitized_log
        assert "[TEST_TOKEN]" in sanitized_log
        assert "[SCENARIO_ROOT]" in sanitized_log
        assert "\x1b[" not in sanitized_log
    finally:
        process.close()
