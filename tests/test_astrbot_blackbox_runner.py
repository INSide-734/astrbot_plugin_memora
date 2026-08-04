"""真实 AstrBot 黑盒测试命令入口契约。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from runtime_tests.harness.config import build_isolated_environment
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
    assert "runtime_tests/test_message_contract.py" in captured["command"]


def test_blackbox_runner_exposes_only_real_live_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """live 档位应只选择显式真实 Provider 场景。"""
    captured: dict[str, Any] = {}

    def capture_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[list[str]]:
        """记录 live runner 的子进程参数并模拟成功。"""
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_astrbot_blackbox.subprocess, "run", capture_run)

    assert run_astrbot_blackbox.main(["--profile", "live", "-q"]) == 0
    assert captured["command"] == [
        sys.executable,
        "-m",
        "pytest",
        "runtime_tests/test_live_provider.py",
        "-q",
    ]


def test_process_log_sanitization_keeps_utf8_and_last_200_lines(
    tmp_path: Path,
) -> None:
    """进程日志应限制行数，并保留中文、脱敏秘密和移除 ANSI。"""
    password = "Dashboard-Secret-A9"
    test_token = "test-token-secret"
    provider_secret = "provider-secret-1234567890"
    port, reservation = reserve_loopback_port()
    process = AstrBotProcess(
        root=tmp_path,
        port=port,
        reservation=reservation,
        password=password,
        test_token=test_token,
        sensitive_values=(provider_secret,),
    )
    retained_lines = [f"正常日志 {index}" for index in range(199)]
    retained_lines.append(
        "\x1b[32m中文日志 "
        f"{password} {test_token} {provider_secret} {provider_secret[:12]} {tmp_path}"
        "\x1b[0m"
    )
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
        assert provider_secret not in sanitized_log
        assert provider_secret[:12] not in sanitized_log
        assert str(tmp_path) not in sanitized_log
        assert "[DASHBOARD_PASSWORD]" in sanitized_log
        assert "[TEST_TOKEN]" in sanitized_log
        assert "[PROVIDER_SECRET]" in sanitized_log
        assert "[SCENARIO_ROOT]" in sanitized_log
        assert "\x1b[" not in sanitized_log
    finally:
        process.close()


def test_subprocess_environment_drops_parent_secrets_and_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 与 AstrBot 子进程环境不得继承父进程凭据或代理。"""
    monkeypatch.setenv("UNRELATED_VISIBLE_SETTING", "visible")
    monkeypatch.setenv("SYNTHETIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy-secret@example.test")
    monkeypatch.setenv("MEMORA_LIVE_API_KEY", "must-not-leak")

    environment = build_isolated_environment(
        tmp_path,
        extra_env={"MEMORA_TEST_DRIVER_TOKEN": "scenario-token"},
    )

    assert environment["UNRELATED_VISIBLE_SETTING"] == "visible"
    assert environment["ASTRBOT_ROOT"] == str(tmp_path)
    assert environment["MEMORA_TEST_DRIVER_TOKEN"] == "scenario-token"
    assert "SYNTHETIC_API_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "MEMORA_LIVE_API_KEY" not in environment


def test_process_close_rewrites_persisted_log_without_provider_secret(
    tmp_path: Path,
) -> None:
    """场景关闭后保留的诊断日志也不得含完整 key、key 前缀或 ANSI。"""
    provider_secret = "provider-secret-1234567890"
    port, reservation = reserve_loopback_port()
    process = AstrBotProcess(
        root=tmp_path,
        port=port,
        reservation=reservation,
        password="Dashboard-Secret-A9",
        test_token="test-token-secret",
        sensitive_values=(provider_secret,),
    )
    process._log_path.write_text(
        f"\x1b[31m{provider_secret} {provider_secret[:12]}\x1b[0m",
        encoding="utf-8",
    )

    process.close()

    persisted = process._log_path.read_text(encoding="utf-8")
    assert provider_secret not in persisted
    assert provider_secret[:12] not in persisted
    assert "[PROVIDER_SECRET]" in persisted
    assert "\x1b[" not in persisted
