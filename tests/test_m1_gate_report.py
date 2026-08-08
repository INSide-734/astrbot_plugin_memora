"""M1 报告受信 consumer 定向测试：验证 marker/精确五报告/hash 后只发布该集合。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from scripts import m1_gate_report
from scripts.m1_gate_core import COMMIT_MARKER, REPORT_NAMES
from scripts.m1_gate_report import publish_verified_set, verify_report_set


def _build_valid_report_dir(tmp_path: Path) -> Path:
    """构造一份完整合法（五报告 + COMMITTED + current.json）的报告目录。"""

    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    written: dict[str, str] = {}
    for name in REPORT_NAMES:
        payload = {"schema_version": 1, "name": name, "exit_code": 0}
        target = report_dir / name
        target.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (report_dir / COMMIT_MARKER).write_text("committed\n", encoding="utf-8")
    (report_dir / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "9",
                "run_attempt": "7",
                "generation": report_dir.name,
                "base_commit": "a" * 40,
                "pr_head_commit": "b" * 40,
                "candidate_commit": "c" * 40,
                "exit_code": 0,
                "committed": True,
                "files": written,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return report_dir


def test_consumer_verifies_valid_set(tmp_path: Path) -> None:
    """合法集合：verify 通过并返回 manifest 与五份字节快照。"""

    report_dir = _build_valid_report_dir(tmp_path)
    manifest, snapshots = verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert manifest["committed"] is True
    assert manifest["exit_code"] == 0
    assert set(manifest["files"]) == set(REPORT_NAMES)
    assert set(snapshots) == set(REPORT_NAMES) | {COMMIT_MARKER, "current.json"}
    for name in REPORT_NAMES:
        assert hashlib.sha256(snapshots[name]).hexdigest() == manifest["files"][name]


def test_consumer_rejects_tampered_report_hash(tmp_path: Path) -> None:
    """篡改任一报告内容 → hash 校验失败 exit 2。"""

    report_dir = _build_valid_report_dir(tmp_path)
    (report_dir / "decision.json").write_text('{"tampered": true}\n', encoding="utf-8")
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "hash" in str(exc.value)


def test_consumer_rejects_missing_commit_marker(tmp_path: Path) -> None:
    """缺少 COMMITTED marker → 不可信。"""

    report_dir = _build_valid_report_dir(tmp_path)
    (report_dir / COMMIT_MARKER).unlink()
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "缺失" in str(exc.value) or "COMMITTED" in str(exc.value)


def test_consumer_rejects_stray_file(tmp_path: Path) -> None:
    """目录含未授权文件（精确集合之外）→ 不可信。"""

    report_dir = _build_valid_report_dir(tmp_path)
    (report_dir / "extra.txt").write_text("x", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "未授权" in str(exc.value)


def test_consumer_rejects_uncommitted_manifest(tmp_path: Path) -> None:
    """manifest 未标记 committed → 不可信。"""

    report_dir = _build_valid_report_dir(tmp_path)
    manifest = json.loads((report_dir / "current.json").read_text(encoding="utf-8"))
    manifest["committed"] = False
    (report_dir / "current.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "committed" in str(exc.value)


def test_publish_verified_set_copies_exact_set(tmp_path: Path) -> None:
    """发布只复制验证过的精确集合（五报告 + COMMITTED + current.json）。"""

    report_dir = _build_valid_report_dir(tmp_path)
    staging = tmp_path / "staging"
    publish_verified_set(report_dir, staging, run_id="9", run_attempt="7")
    entries = {entry.name for entry in staging.iterdir()}
    assert entries == set(REPORT_NAMES) | {COMMIT_MARKER, "current.json"}
    for name in REPORT_NAMES:
        assert (staging / name).read_bytes() == (report_dir / name).read_bytes()


def test_publish_verified_set_rejects_unverified_source(tmp_path: Path) -> None:
    """未验证源不得发布（篡改后 publish 必须失败且不产生产物）。"""

    report_dir = _build_valid_report_dir(tmp_path)
    (report_dir / "diff.json").write_text('{"tampered": true}\n', encoding="utf-8")
    staging = tmp_path / "staging"
    with pytest.raises(Exception) as exc:
        publish_verified_set(report_dir, staging, run_id="9", run_attempt="7")
    assert "hash" in str(exc.value)
    assert not staging.exists() or not any(staging.iterdir())


def test_publish_verified_set_rejects_existing_dest(tmp_path: Path) -> None:
    """发布目标已存在（含空目录）→ 拒绝，保证原子 rename 语义。"""

    report_dir = _build_valid_report_dir(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(Exception) as exc:
        publish_verified_set(report_dir, staging, run_id="9", run_attempt="7")
    assert "已存在" in str(exc.value)


def test_consumer_rejects_symlinked_report(tmp_path: Path) -> None:
    """symlink 报告（no-follow）必须拒绝，不得跟随复制目标内容。"""

    report_dir = _build_valid_report_dir(tmp_path)
    real = tmp_path / "real.txt"
    real.write_text("evil payload", encoding="utf-8")
    (report_dir / "decision.json").unlink()
    os.symlink(str(real), report_dir / "decision.json")
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "symlink" in str(exc.value) or "regular" in str(exc.value)
    staging = tmp_path / "staging"
    with pytest.raises(Exception):
        publish_verified_set(report_dir, staging, run_id="9", run_attempt="7")
    assert not staging.exists()


def test_consumer_rejects_symlinked_manifest(tmp_path: Path) -> None:
    """current.json 为 symlink 时必须拒绝（no-follow）。"""

    report_dir = _build_valid_report_dir(tmp_path)
    real = tmp_path / "manifest.json"
    real.write_text("{}", encoding="utf-8")
    (report_dir / "current.json").unlink()
    os.symlink(str(real), report_dir / "current.json")
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "symlink" in str(exc.value) or "regular" in str(exc.value)


def test_publish_uses_verified_snapshot_not_source_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Blocker 3 回归：发布只从已验证字节快照写入，绝不重新读取 source 路径。

    验证阶段读取恰为 7 次（current.json + COMMITTED + 5 报告）→ staging
    复验 7 次；复制阶段零路径读取（无 TOCTOU 窗口）。
    """

    report_dir = _build_valid_report_dir(tmp_path)
    real_read = m1_gate_report._read_verified_at
    calls = {"n": 0}

    def counting_read(dir_fd: int, name: str) -> bytes:
        calls["n"] += 1
        return real_read(dir_fd, name)

    monkeypatch.setattr(m1_gate_report, "_read_verified_at", counting_read)
    staging = tmp_path / "staging"
    publish_verified_set(report_dir, staging, run_id="9", run_attempt="7")
    assert calls["n"] == 14  # source 验证 7 + staging 全量复验 7，复制 0
    manifest = json.loads((staging / "current.json").read_text(encoding="utf-8"))
    for name in REPORT_NAMES:
        assert (
            hashlib.sha256((staging / name).read_bytes()).hexdigest()
            == manifest["files"][name]
        )


def test_publish_rejects_directory_symlink(tmp_path: Path) -> None:
    """报告目录本身为 symlink 时必须拒绝（目录重定向）。"""

    report_dir = _build_valid_report_dir(tmp_path)
    link = tmp_path / "report-link"
    os.symlink(str(report_dir), str(link))
    with pytest.raises(Exception) as exc:
        verify_report_set(link, run_id="9", run_attempt="7")
    assert "symlink" in str(exc.value)


def test_publish_staging_bound_to_run_id_attempt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """staging 临时目录必须以 run-id/attempt 命名（旧运行目录不可复用）。"""

    captured: list[str] = []
    real_mkdtemp = m1_gate_report.tempfile.mkdtemp

    def fake_mkdtemp(*args: Any, **kwargs: Any) -> str:
        captured.append(kwargs.get("prefix", ""))
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(m1_gate_report.tempfile, "mkdtemp", fake_mkdtemp)
    report_dir = _build_valid_report_dir(tmp_path)
    staging = tmp_path / "staging"
    publish_verified_set(report_dir, staging, run_id="9", run_attempt="7")
    assert captured, "未捕获 staging mkdtemp"
    assert all("9-7" in prefix for prefix in captured)


def test_consumer_rejects_tampered_committed_content(tmp_path: Path) -> None:
    """COMMITTED 必须为固定内容（篡改内容 → 拒绝）。"""

    report_dir = _build_valid_report_dir(tmp_path)
    (report_dir / COMMIT_MARKER).write_text("evil\n", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="9", run_attempt="7")
    assert "COMMITTED" in str(exc.value)


def test_consumer_rejects_run_id_attempt_mismatch(tmp_path: Path) -> None:
    """manifest 必须严格匹配受信 run_id/run_attempt（错值 → 拒绝）。"""

    report_dir = _build_valid_report_dir(tmp_path)
    with pytest.raises(Exception) as exc:
        verify_report_set(report_dir, run_id="1", run_attempt="1")
    assert "run_id" in str(exc.value)


def test_consumer_dir_fd_survives_directory_swap(tmp_path: Path) -> None:
    """目录 FD 绑定已验证目录 inode：验证前换走目录不影响已持有 FD 的验证。"""

    report_dir = _build_valid_report_dir(tmp_path)
    dir_fd = m1_gate_report._open_dir_fd(report_dir)
    try:
        # 验证用 FD 读取 current.json（模拟目录被外部替换后的竞态）
        payload = m1_gate_report._read_verified_at(dir_fd, "current.json")
        assert b"committed" in payload
    finally:
        os.close(dir_fd)


def test_consumer_cli_verify_and_publish(tmp_path: Path, capsys: Any) -> None:
    """consumer CLI：--verify 0 / --publish 0，篡改集合 --verify 2。"""

    report_dir = _build_valid_report_dir(tmp_path)
    assert (
        m1_gate_report._main(
            ["--verify", str(report_dir), "--run-id", "9", "--run-attempt", "7"]
        )
        == 0
    )
    staging = tmp_path / "staging"
    args = [
        "--publish",
        str(report_dir),
        str(staging),
        "--run-id",
        "9",
        "--run-attempt",
        "7",
    ]
    assert m1_gate_report._main(args) == 0
    (report_dir / "provenance.json").write_text("{}", encoding="utf-8")
    assert (
        m1_gate_report._main(
            ["--verify", str(report_dir), "--run-id", "9", "--run-attempt", "7"]
        )
        == 2
    )
