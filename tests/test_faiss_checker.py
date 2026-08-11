"""FAISS 运行时检查器回归测试。"""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from core.initializer import faiss_checker as faiss_checker_module
from core.initializer.faiss_checker import FaissChecker
from core.shared.errors import InitializationError


def test_check_runtime_skips_probe_when_faiss_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """父进程已加载 FAISS 时不应在热重载中重复启动探测子进程。"""
    run = MagicMock()
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.setattr(faiss_checker_module.subprocess, "run", run)

    FaissChecker.check_runtime()

    run.assert_not_called()


def test_check_runtime_uses_cold_start_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷启动探测应为 Windows 上较慢的首次导入保留足够时间。"""
    run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.delitem(sys.modules, "faiss", raising=False)
    monkeypatch.setattr(faiss_checker_module.subprocess, "run", run)

    FaissChecker.check_runtime()

    run.assert_called_once_with(
        [sys.executable, "-c", "import faiss"],
        shell=False,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_check_runtime_reports_timeout_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探测超时应与 FAISS 安装或 CPU 不兼容错误明确区分。"""
    monkeypatch.delitem(sys.modules, "faiss", raising=False)
    monkeypatch.setattr(
        faiss_checker_module.subprocess,
        "run",
        MagicMock(
            side_effect=subprocess.TimeoutExpired(
                cmd=[sys.executable, "-c", "import faiss"],
                timeout=30,
            )
        ),
    )

    with pytest.raises(InitializationError, match="30 秒内未完成"):
        FaissChecker.check_runtime()
