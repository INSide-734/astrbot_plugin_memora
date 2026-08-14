"""在隔离 AstrBot 运行时中验证 Memora 插件生命周期契约。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from _plugin_lifecycle_harness import worker_entry
except ModuleNotFoundError:  # 允许 pytest 以 scripts namespace 导入。
    from scripts._plugin_lifecycle_harness import worker_entry

REPORT_SCHEMA = "memora-plugin-lifecycle-report-v1"
PLUGIN_DIR_NAME = "astrbot_plugin_memora"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REQUIRED_NAMESPACE_CYCLES = 3
REPO_ROOT = Path(__file__).resolve().parents[1]


class LifecycleVerificationError(RuntimeError):
    """表示输入、隔离环境或 worker 协议错误。"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析公开生命周期验证命令。"""

    parser = argparse.ArgumentParser(description="验证 Memora 插件生命周期契约")
    parser.add_argument("--astrbot-versions", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--astrbot-source",
        action="append",
        default=[],
        metavar="VERSION=PATH",
        help="显式指定版本源码根；未指定时从相邻 AstrBot Git checkout 解析",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def parse_versions(raw: str) -> tuple[str, ...]:
    """校验并返回无重复的 AstrBot 版本列表。"""

    versions = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not versions:
        raise LifecycleVerificationError("astrbot_versions_empty")
    if len(versions) != len(set(versions)):
        raise LifecycleVerificationError("astrbot_versions_duplicate")
    if any(not VERSION_PATTERN.fullmatch(item) for item in versions):
        raise LifecycleVerificationError("astrbot_version_invalid")
    return versions


def parse_source_overrides(values: Sequence[str]) -> dict[str, Path]:
    """解析 ``VERSION=PATH`` 映射并拒绝歧义条目。"""

    result: dict[str, Path] = {}
    for value in values:
        version, separator, raw_path = value.partition("=")
        if not separator or not VERSION_PATTERN.fullmatch(version) or not raw_path:
            raise LifecycleVerificationError("astrbot_source_invalid")
        if version in result:
            raise LifecycleVerificationError("astrbot_source_duplicate")
        result[version] = Path(raw_path).resolve()
    return result


def _static_astrbot_version(source_root: Path) -> str | None:
    """从源码元数据读取版本，避免导入未受信源码。"""

    try:
        import tomllib

        payload = tomllib.loads(
            (source_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        version = payload.get("project", {}).get("version")
    except (OSError, UnicodeError, ValueError):
        return None
    return str(version) if isinstance(version, str) else None


def _validate_source_root(source_root: Path, version: str) -> Path:
    """确认源码根是目标版本且包含 AstrBot 包。"""

    if source_root.is_symlink() or not (source_root / "astrbot").is_dir():
        raise LifecycleVerificationError("astrbot_source_missing")
    if _static_astrbot_version(source_root) != version:
        raise LifecycleVerificationError("astrbot_source_version_mismatch")
    return source_root


def _find_git_checkout() -> Path | None:
    """查找相邻、可解析官方版本标签的 AstrBot Git checkout。"""

    for candidate in sorted(REPO_ROOT.parent.glob("AstrBot*")):
        if (candidate / ".git").exists() or (candidate / ".git").is_file():
            return candidate.resolve()
    return None


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    """拒绝链接和路径穿越后解出 Git tar 归档。"""

    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            unsafe = path.is_absolute() or ".." in path.parts
            if unsafe or member.issym() or member.islnk():
                raise LifecycleVerificationError("astrbot_archive_unsafe")
        archive.extractall(destination, filter="data")


def _export_version(checkout: Path, version: str, destination: Path) -> str:
    """从本地 Git 标签导出目标 AstrBot 版本并返回完整提交号。"""

    destination = destination.resolve()
    tag = f"v{version}"
    archive_path = (destination.parent / f"astrbot-{version}.tar").resolve()
    try:
        revision = subprocess.run(
            ["git", "rev-parse", f"{tag}^{{commit}}"],
            cwd=checkout,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
        subprocess.run(
            ["git", "archive", "--format=tar", f"--output={archive_path}", tag],
            cwd=checkout,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        destination.mkdir()
        _safe_extract_tar(archive_path, destination)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LifecycleVerificationError("astrbot_tag_export_failed") from exc
    finally:
        archive_path.unlink(missing_ok=True)
    _validate_source_root(destination, version)
    return revision


def _copy_plugin_runtime(source: Path, destination: Path) -> None:
    """复制生命周期导入所需的当前插件运行时文件。"""

    destination.mkdir(parents=True)
    for name in ("main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt"):
        item = source / name
        if item.is_symlink() or not item.is_file():
            raise LifecycleVerificationError("plugin_runtime_incomplete")
        shutil.copy2(item, destination / name)
    core = source / "core"
    if core.is_symlink() or not core.is_dir():
        raise LifecycleVerificationError("plugin_runtime_incomplete")
    shutil.copytree(
        core,
        destination / "core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """在目标目录原子写入稳定 JSON 报告。"""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_worker_subprocess(
    *,
    version: str,
    astrbot_source: Path | None,
    plugin_root: Path,
    data_dir: Path,
    report: Path,
    cycles: int = REQUIRED_NAMESPACE_CYCLES,
    inject_initialization_failure: bool = False,
    scenario_mode: str = "all",
    timeout: float = 300.0,
) -> dict[str, Any]:
    """启动隔离 worker 并校验其 JSON 报告协议。"""

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_worker",
        "--version",
        version,
        "--plugin-root",
        str(plugin_root),
        "--data-dir",
        str(data_dir),
        "--worker-report",
        str(report),
        "--cycles",
        str(cycles),
        "--scenario-mode",
        scenario_mode,
    ]
    if inject_initialization_failure:
        command.append("--inject-initialization-failure")
    runtime_root = plugin_root.parent.parent.parent
    environment = os.environ.copy()
    python_paths = [str(runtime_root)]
    if astrbot_source is not None:
        python_paths.insert(0, str(astrbot_source))
    if prior := environment.get("PYTHONPATH"):
        python_paths.append(prior)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    environment["ASTRBOT_ROOT"] = str(runtime_root)
    try:
        completed = subprocess.run(
            command,
            cwd=runtime_root,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LifecycleVerificationError("worker_timeout") from exc
    if not report.is_file():
        raise LifecycleVerificationError("worker_report_missing")
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleVerificationError("worker_report_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != REPORT_SCHEMA:
        raise LifecycleVerificationError("worker_report_invalid")
    payload["worker_exit_code"] = completed.returncode
    return payload


def prepare_empty_data_root(path: Path) -> Path:
    """创建或确认空的隔离数据根。"""

    path = path.resolve()
    if path == Path(path.anchor):
        raise LifecycleVerificationError("data_dir_unsafe")
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise LifecycleVerificationError("data_dir_unsafe")
        if any(path.iterdir()):
            raise LifecycleVerificationError("data_dir_not_empty")
    else:
        path.mkdir(parents=True)
    return path


def run_verification(args: argparse.Namespace) -> dict[str, Any]:
    """解析多版本源码并汇总所有隔离 worker 结果。"""

    versions = parse_versions(args.astrbot_versions)
    overrides = parse_source_overrides(args.astrbot_source)
    if set(overrides).difference(versions):
        raise LifecycleVerificationError("astrbot_source_unused")
    data_root = prepare_empty_data_root(args.data_dir)
    checkout = _find_git_checkout()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=".memora-lifecycle-") as temporary_text:
        temporary = Path(temporary_text)
        for version in versions:
            source = overrides.get(version)
            revision: str | None = None
            source_kind = "explicit"
            if source is not None:
                source = _validate_source_root(source, version)
            else:
                if checkout is None:
                    raise LifecycleVerificationError("astrbot_checkout_missing")
                source = temporary / f"astrbot-{version}"
                revision = _export_version(checkout, version, source)
                source_kind = "git_tag"
            plugin_root = (
                temporary / f"runtime-{version}" / "data" / "plugins" / PLUGIN_DIR_NAME
            )
            _copy_plugin_runtime(REPO_ROOT, plugin_root)
            worker = run_worker_subprocess(
                version=version,
                astrbot_source=source,
                plugin_root=plugin_root,
                data_dir=data_root / version,
                report=temporary / f"worker-{version}.json",
            )
            worker["source"] = {"kind": source_kind, "revision": revision}
            results.append(worker)
    status = (
        "passed"
        if all(
            item.get("status") == "passed" and item.get("worker_exit_code") == 0
            for item in results
        )
        else "failed"
    )
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "astrbot_versions": list(versions),
        "contract": {
            "namespace_cycles": REQUIRED_NAMESPACE_CYCLES,
            "provider_wait": {
                "foreground_seconds": 5,
                "retry_base_seconds": 2,
                "retry_factor": 1.5,
                "retry_cap_seconds": 30,
                "maximum_attempts": 60,
            },
            "state_model": [
                "CREATED",
                "WAITING_PROVIDER",
                "INITIALIZING",
                "RUNNING",
                "ROLLING_BACK",
                "STOPPING",
                "STOPPED",
                "FAILED",
            ],
        },
        "versions": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """运行命令并以 0/1/2 区分通过、违约和工具错误。"""

    values = list(argv) if argv is not None else sys.argv[1:]
    if "--_worker" in values:
        return worker_entry(values)
    args = parse_args(values)
    try:
        payload = run_verification(args)
        exit_code = 0 if payload["status"] == "passed" else 1
    except LifecycleVerificationError as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "status": "error",
            "reason_code": str(exc),
        }
        exit_code = 2
    atomic_write_json(args.report, payload)
    print(json.dumps({"status": payload["status"]}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
