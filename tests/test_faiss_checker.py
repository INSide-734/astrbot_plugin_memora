"""FAISS 运行时检查器回归测试。"""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.platform.composition import faiss_checker as faiss_checker_module
from core.platform.composition.faiss_checker import FaissChecker
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


def _write_index_file(tmp_path) -> str:
    """写出一个最小索引文件占位。"""

    index_path = tmp_path / "memora.index"
    index_path.write_bytes(b"index-bytes")
    return str(index_path)


@pytest.mark.asyncio
async def test_dimension_mismatch_deletes_index_without_quarantine(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """维度不匹配时应删除派生索引，而不是标记为损坏。"""
    index_path = _write_index_file(tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "faiss",
        SimpleNamespace(read_index=MagicMock(return_value=SimpleNamespace(d=384))),
    )
    provider = MagicMock()
    provider.get_dim.return_value = 768

    await FaissChecker.check_and_fix_dimension_mismatch(index_path, provider)

    assert not (tmp_path / "memora.index").exists()
    assert list(tmp_path.glob("*.corrupt_*")) == []


@pytest.mark.asyncio
async def test_provider_dimension_error_keeps_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Provider 取维度失败与索引损坏无关，不得隔离仍有效的派生索引。"""
    index_path = _write_index_file(tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "faiss",
        SimpleNamespace(read_index=MagicMock(return_value=SimpleNamespace(d=384))),
    )
    provider = MagicMock()
    provider.get_dim.side_effect = RuntimeError("provider unavailable")

    await FaissChecker.check_and_fix_dimension_mismatch(index_path, provider)

    assert (tmp_path / "memora.index").exists()
    assert list(tmp_path.glob("*.corrupt_*")) == []


@pytest.mark.asyncio
async def test_unreadable_index_is_quarantined(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """只有真正读不出索引时才隔离坏文件。"""
    index_path = _write_index_file(tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "faiss",
        SimpleNamespace(read_index=MagicMock(side_effect=RuntimeError("bad index"))),
    )

    await FaissChecker.check_and_fix_dimension_mismatch(index_path, MagicMock())

    assert not (tmp_path / "memora.index").exists()
    assert len(list(tmp_path.glob("*.corrupt_*"))) == 1
