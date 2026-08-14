"""生成 Memora AstrBot 插件的运行时包或源码包。"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path("dist")
TOP_LEVEL_PACKAGE = "astrbot_plugin_memora"
PAGE_I18N_LOCALES = ("zh-CN", "en-US", "ru-RU")
PLUGIN_SKILL_PATH = "skills/memora-recall-and-memorize/SKILL.md"
RUNTIME_ROOT_FILES = (
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "LICENSE",
    "README.md",
    "README_EN.md",
    "README_RU.md",
    "logo.png",
)
DOC_NAMES = {"AGENTS.md", "DESIGN.md"}
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "storage",
    "dist",
    "build",
    ".pytest_cache",
    ".hypothesis",
    ".mypy_cache",
    ".ruff_cache",
    ".worktrees",
    ".codex",
    ".agents",
    ".ccg",
    "coverage",
    "htmlcov",
    "playwright-report",
    "test-results",
    ".vite",
    ".vitepress",
    ".vite-build",
    "__pycache__",
}
EXCLUDED_SUFFIXES = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".index",
    ".bin",
)


@dataclass(frozen=True)
class PackageMetadata:
    """打包所需的插件元数据。"""

    name: str
    version: str


class PackageError(RuntimeError):
    """表示打包输入、构建或归档校验失败。"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="打包 Memora AstrBot 插件")
    parser.add_argument(
        "--mode",
        choices=("runtime", "source", "both"),
        default="runtime",
        help="输出类型，默认生成 runtime 包",
    )
    parser.add_argument(
        "--from-git",
        action="store_true",
        help="让 source 包使用当前 Git HEAD，而不是当前工作树",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="输出目录，相对路径相对于仓库根目录",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _validate_filename_component(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise PackageError(f"插件元数据缺少有效的 {field_name}")
    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise PackageError(f"插件元数据中的 {field_name} 不是安全路径片段")
    return value


def load_metadata(repo_root: Path) -> PackageMetadata:
    """读取并校验根目录的 metadata.yaml。"""

    metadata_path = repo_root / "metadata.yaml"
    if not metadata_path.is_file():
        raise PackageError(f"未找到插件元数据：{metadata_path}")
    try:
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PackageError(f"读取插件元数据失败：{metadata_path}") from exc
    if not isinstance(raw, dict):
        raise PackageError("插件元数据必须是 YAML 对象")
    name = _validate_filename_component(raw.get("name"), "name")
    version = _validate_filename_component(raw.get("version"), "version")
    return PackageMetadata(name=name, version=version)


def _copy_file(
    source: Path,
    staging_root: Path,
    package_name: str,
    relative: Path,
) -> None:
    if not source.is_file() or source.is_symlink():
        raise PackageError(f"无法复制非普通文件：{source}")
    destination = staging_root / package_name / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_runtime_tree(
    source_root: Path,
    staging_root: Path,
    package_name: str,
    relative_root: Path,
) -> None:
    if not source_root.is_dir():
        raise PackageError(f"运行时目录不存在：{source_root}")
    for source in sorted(source_root.rglob("*")):
        if not source.is_file() or source.is_symlink():
            continue
        relative = relative_root / source.relative_to(source_root)
        if any(part in DOC_NAMES or part == "__pycache__" for part in relative.parts):
            continue
        _copy_file(source, staging_root, package_name, relative)


def copy_runtime_files(
    repo_root: Path,
    staging_root: Path,
    package_name: str,
) -> None:
    """收集可直接安装的运行时文件。"""

    for relative_text in RUNTIME_ROOT_FILES:
        relative = Path(relative_text)
        source = repo_root / relative
        if not source.is_file():
            raise PackageError(f"运行时必需文件不存在：{source}")
        _copy_file(source, staging_root, package_name, relative)

    _copy_runtime_tree(repo_root / "core", staging_root, package_name, Path("core"))
    _copy_runtime_tree(repo_root / "static", staging_root, package_name, Path("static"))
    _copy_runtime_tree(repo_root / "skills", staging_root, package_name, Path("skills"))
    _copy_runtime_tree(
        repo_root / ".astrbot-plugin",
        staging_root,
        package_name,
        Path(".astrbot-plugin"),
    )

    dashboard_root = repo_root / "pages" / "dashboard"
    dashboard_index = dashboard_root / "index.html"
    dashboard_assets = dashboard_root / "assets"
    if not dashboard_index.is_file():
        raise PackageError(f"Dashboard 构建入口不存在：{dashboard_index}")
    if not dashboard_assets.is_dir():
        raise PackageError(f"Dashboard 构建资源目录不存在：{dashboard_assets}")
    _copy_file(
        dashboard_index,
        staging_root,
        package_name,
        Path("pages") / "dashboard" / "index.html",
    )
    asset_files = [
        path
        for path in sorted(dashboard_assets.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    if not asset_files:
        raise PackageError(f"Dashboard 构建资源目录为空：{dashboard_assets}")
    for source in asset_files:
        relative = (
            Path("pages")
            / "dashboard"
            / "assets"
            / source.relative_to(dashboard_assets)
        )
        _copy_file(source, staging_root, package_name, relative)


def _is_dashboard_assets(relative: Path) -> bool:
    parts = relative.parts
    return any(
        parts[index : index + 3] == ("pages", "dashboard", "assets")
        for index in range(max(0, len(parts) - 2))
    )


def _is_source_excluded(
    relative: Path,
    output_dir: Path,
    repo_root: Path,
) -> bool:
    root_runtime_dirs = {"data", "storage"}
    if any(part in EXCLUDED_DIRS - root_runtime_dirs for part in relative.parts):
        return True
    if relative.parts and relative.parts[0] in root_runtime_dirs:
        return True
    if _is_dashboard_assets(relative):
        return True
    if relative.name.endswith(".zip"):
        return True
    if relative.name == ".env":
        return True
    if relative.name.startswith(".env.") and relative.name != ".env.example":
        return True
    if relative.name.endswith(EXCLUDED_SUFFIXES):
        return True
    candidate = (repo_root / relative).resolve()
    if output_dir != repo_root and output_dir.is_relative_to(repo_root):
        if candidate == output_dir or candidate.is_relative_to(output_dir):
            return True
    return False


def _list_source_files(repo_root: Path) -> list[Path]:
    """列出 Git 认定为源码的仓库相对文件路径。

    返回已跟踪文件与未被忽略的未跟踪文件的并集；.gitignore、
    .git/info/exclude 与全局排除规则全部交由 git ls-files
    --exclude-standard 解释，避免手写模式匹配偏离真实 Git 行为。
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        listing = os.fsdecode(completed.stdout)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackageError(
            f"无法通过 Git 收集源码包文件，{repo_root} 必须是可用的 Git 仓库"
        ) from exc
    paths: list[Path] = []
    for entry in listing.split("\0"):
        if not entry:
            continue
        posix_path = PurePosixPath(entry)
        if posix_path.is_absolute() or ".." in posix_path.parts:
            raise PackageError("Git 列出的源码路径不安全")
        paths.append(Path(*posix_path.parts))
    return paths


def copy_worktree_source(
    repo_root: Path,
    staging_root: Path,
    package_name: str,
    output_dir: Path,
) -> None:
    """收集当前工作树中 Git 认定的源码文件，并排除本地环境与生成物。

    文件清单来自 Git 索引与未忽略的未跟踪文件；随后再应用
    打包级结构排除（如 .vitepress、data/storage 与输出目录）。
    """
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    for relative in sorted(_list_source_files(repo_root)):
        if _is_source_excluded(relative, output_dir, repo_root):
            continue
        source = repo_root / relative
        if source.is_symlink() or not source.is_file():
            continue
        _copy_file(source, staging_root, package_name, relative)


def _safe_tar_relative(name: str) -> Path:
    if "\\" in name:
        raise PackageError("Git 归档包含不安全路径")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PackageError("Git 归档包含不安全路径")
    return Path(*path.parts)


def copy_git_source(
    repo_root: Path,
    staging_root: Path,
    package_name: str,
) -> None:
    """从当前 Git HEAD 收集源码，不回退到工作树。"""

    try:
        completed = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackageError("无法从当前 Git HEAD 生成源码包") from exc

    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            relative = _safe_tar_relative(member.name)
            if _is_source_excluded(relative, repo_root / "dist", repo_root):
                continue
            source = archive.extractfile(member)
            if source is None:
                raise PackageError(f"无法读取 Git 归档成员：{member.name}")
            destination = staging_root / package_name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read())


def create_zip(staging_root: Path, output_path: Path) -> None:
    """按稳定路径顺序创建 ZIP 文件。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path
        for path in staging_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    with zipfile.ZipFile(
        output_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        strict_timestamps=False,
    ) as archive:
        for source in files:
            archive.write(source, source.relative_to(staging_root).as_posix())


def _archive_names(archive_path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise PackageError("压缩包包含重复成员")
            return names
    except zipfile.BadZipFile as exc:
        raise PackageError(f"压缩包无法读取：{archive_path}") from exc


def _validate_archive_paths(names: list[str], package_name: str) -> None:
    for name in names:
        if "\\" in name:
            raise PackageError("压缩包包含不安全路径")
        path = PurePosixPath(name)
        if (
            not path.parts
            or path.is_absolute()
            or ":" in path.parts[0]
            or ".." in path.parts
        ):
            raise PackageError("压缩包包含不安全路径")
        if path.parts[0] != package_name:
            raise PackageError("压缩包成员必须位于插件顶层目录")


def validate_archive(
    archive_path: Path,
    mode: str,
    package_name: str,
) -> None:
    """校验 ZIP 的路径安全、必需文件和排除项。"""

    names = _archive_names(archive_path)
    _validate_archive_paths(names, package_name)
    normalized = [PurePosixPath(name) for name in names]
    name_set = set(names)
    forbidden_anywhere = {"node_modules", ".git"}
    forbidden_root = {"data", "storage"}
    if mode == "runtime":
        forbidden_root.update({"tests", "scripts", "docs"})
    for path in normalized:
        relative_parts = path.parts[1:]
        if (
            path.name.endswith(".zip")
            or forbidden_anywhere.intersection(relative_parts)
            or (relative_parts and relative_parts[0] in forbidden_root)
        ):
            raise PackageError(f"压缩包包含禁止文件：{path.as_posix()}")

    if mode == "runtime":
        missing = [
            f"{package_name}/{relative}"
            for relative in RUNTIME_ROOT_FILES
            if f"{package_name}/{relative}" not in name_set
        ]
        missing.extend(
            f"{package_name}/.astrbot-plugin/i18n/{locale}.json"
            for locale in PAGE_I18N_LOCALES
            if f"{package_name}/.astrbot-plugin/i18n/{locale}.json" not in name_set
        )
        plugin_skill_entry = f"{package_name}/{PLUGIN_SKILL_PATH}"
        if plugin_skill_entry not in name_set:
            missing.append(f"{plugin_skill_entry}（插件 Skill）")
        if missing:
            raise PackageError(f"runtime 包缺少必需文件：{', '.join(missing)}")
        if not any(path.parts[1:2] == ("core",) for path in normalized):
            raise PackageError("runtime 包缺少 core/ 文件")
        if not any(path.parts[1:2] == ("static",) for path in normalized):
            raise PackageError("runtime 包缺少 static/ 文件")
        dashboard_index = f"{package_name}/pages/dashboard/index.html"
        if dashboard_index not in name_set:
            raise PackageError("runtime 包缺少 Dashboard index.html")
        if not any(
            path.parts[1:4] == ("pages", "dashboard", "assets") for path in normalized
        ):
            raise PackageError("runtime 包缺少 Dashboard 构建资源")
    elif mode == "source":
        required_prefixes = (
            f"{package_name}/main.py",
            f"{package_name}/tests/",
            f"{package_name}/scripts/",
        )
        for prefix in required_prefixes:
            if prefix.endswith("/"):
                present = any(name.startswith(prefix) for name in names)
            else:
                present = prefix in name_set
            if not present:
                raise PackageError(f"source 包缺少必需内容：{prefix}")


def run_dashboard_build(repo_root: Path) -> None:
    """在 Dashboard 目录构建生产资源，不自动安装依赖。"""

    dashboard = repo_root / "pages" / "dashboard"
    package_json = dashboard / "package.json"
    if not package_json.is_file():
        raise PackageError(f"未找到 Dashboard package.json：{package_json}")
    npm = shutil.which("npm")
    if not npm:
        raise PackageError("未找到 npm；请安装 Node.js 后重试")
    try:
        completed = subprocess.run(
            [npm, "run", "build"], cwd=dashboard, check=False, shell=False
        )
    except OSError as exc:
        raise PackageError(
            "启动 npm run build 失败；请先在 pages/dashboard 执行 npm ci"
        ) from exc
    if completed.returncode != 0:
        raise PackageError("Dashboard 构建失败；请先在 pages/dashboard 执行 npm ci")


def _resolve_output_dir(repo_root: Path, output_dir: Path) -> Path:
    resolved = output_dir if output_dir.is_absolute() else repo_root / output_dir
    resolved = resolved.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise PackageError(f"输出路径不是目录：{resolved}")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PackageError(f"无法创建输出目录：{resolved}") from exc
    return resolved


def _write_validated_package(
    staging_root: Path,
    output_dir: Path,
    package_name: str,
    version: str,
    mode: str,
) -> Path:
    filename = f"{package_name}-{version}-{mode}.zip"
    target = output_dir / filename
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=f".{filename}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        create_zip(staging_root, temporary_path)
        validate_archive(temporary_path, mode, package_name)
        os.replace(temporary_path, target)
        temporary_path = None
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f"写入压缩包失败：{target}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return target


def create_packages(
    repo_root: Path,
    mode: str,
    from_git: bool,
    output_dir: Path,
) -> list[Path]:
    """按模式构建并输出一个或两个归档。"""

    if mode not in {"runtime", "source", "both"}:
        raise PackageError(f"不支持的打包模式：{mode}")
    metadata = load_metadata(repo_root)
    resolved_output = _resolve_output_dir(repo_root, output_dir)
    results: list[Path] = []

    with tempfile.TemporaryDirectory(prefix=".memora-package-") as temporary_root_text:
        temporary_root = Path(temporary_root_text)
        source_staging = temporary_root / "source"
        runtime_staging = temporary_root / "runtime"

        if mode in {"source", "both"}:
            source_staging.mkdir()
            if from_git:
                copy_git_source(repo_root, source_staging, metadata.name)
            else:
                copy_worktree_source(
                    repo_root, source_staging, metadata.name, resolved_output
                )

        if mode in {"runtime", "both"}:
            run_dashboard_build(repo_root)
            runtime_staging.mkdir()
            copy_runtime_files(repo_root, runtime_staging, metadata.name)

        if mode in {"runtime", "both"}:
            results.append(
                _write_validated_package(
                    runtime_staging,
                    resolved_output,
                    metadata.name,
                    metadata.version,
                    "runtime",
                )
            )
        if mode in {"source", "both"}:
            results.append(
                _write_validated_package(
                    source_staging,
                    resolved_output,
                    metadata.name,
                    metadata.version,
                    "source",
                )
            )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """执行命令行打包入口。"""

    args = parse_args(argv)
    try:
        outputs = create_packages(REPO_ROOT, args.mode, args.from_git, args.output_dir)
    except PackageError as exc:
        print(f"打包失败：{exc}", file=sys.stderr)
        return 1
    for output in outputs:
        print(f"已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
