"""根据项目元数据、构建产物和 Markdown 模板生成发布说明。"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Sequence

if __package__:
    from .package_plugin import (
        REPO_ROOT,
        PackageError,
        PackageMetadata,
        load_metadata,
    )
else:
    from package_plugin import (
        REPO_ROOT,
        PackageError,
        PackageMetadata,
        load_metadata,
    )


DEFAULT_TEMPLATE = Path(".github/release-notes-template.md")
DEFAULT_CHECKSUMS = Path("dist/SHA256SUMS.txt")
DEFAULT_CHANGELOG = Path("CHANGELOG.md")
DEFAULT_OUTPUT = Path("dist/release-notes.md")
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
CHECKSUM_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ArtifactInfo:
    """描述一个经过 SHA-256 校验的发布产物。"""

    filename: str
    purpose: str
    size: str
    sha256: str


class ReleaseNotesError(RuntimeError):
    """表示发布说明的输入、模板或产物校验失败。"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析发布说明生成参数。"""

    parser = argparse.ArgumentParser(description="生成 Memora GitHub Release 说明")
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help="Markdown 模板路径，相对路径相对于仓库根目录",
    )
    parser.add_argument(
        "--checksums",
        type=Path,
        default=DEFAULT_CHECKSUMS,
        help="SHA-256 清单路径，相对路径相对于仓库根目录",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
        help="变更日志路径，相对路径相对于仓库根目录",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出 Markdown 路径，相对路径相对于仓库根目录",
    )
    parser.add_argument(
        "--commit",
        required=True,
        help="构建来源 commit SHA，支持 SHA-1 或 SHA-256",
    )
    parser.add_argument(
        "--release-type",
        choices=("build", "pre-release", "release"),
        required=True,
        help="发布类型",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _resolve_path(repo_root: Path, path: Path) -> Path:
    """将相对路径解析到仓库根目录。"""

    return path if path.is_absolute() else repo_root / path


def _read_text(path: Path, description: str) -> str:
    """读取 UTF-8 文本并将文件错误转换为领域错误。"""

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseNotesError(f"读取{description}失败：{path}") from exc


def _sha256(path: Path) -> str:
    """分块计算文件的 SHA-256 哈希。"""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseNotesError(f"读取发布产物失败：{path}") from exc
    return digest.hexdigest()


def _extract_changelog_section(path: Path, version: str) -> str:
    """提取 CHANGELOG 中当前版本二级标题下的完整变更内容。"""

    lines = _read_text(path, "CHANGELOG").splitlines()
    heading_pattern = re.compile(rf"^##\s+\[?{re.escape(version)}\]?(?:\s|$)")
    start: int | None = None
    for index, line in enumerate(lines):
        if heading_pattern.match(line):
            start = index + 1
            break
    if start is None:
        raise ReleaseNotesError(f"CHANGELOG 缺少版本 {version} 的变更段落：{path}")

    end = len(lines)
    for index in range(start, len(lines)):
        if re.match(r"^##\s+", lines[index]):
            end = index
            break
    section = "\n".join(lines[start:end]).strip()
    if not section:
        raise ReleaseNotesError(f"CHANGELOG 中版本 {version} 的变更段落为空")
    return section


def _format_size(size: int) -> str:
    """将字节数格式化为适合 Markdown 表格阅读的大小。"""

    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
    return f"{size} B"


def _read_checksum_manifest(path: Path) -> dict[str, str]:
    """读取并校验 sha256sum 生成的文件清单。"""

    manifest: dict[str, str] = {}
    for line_number, line in enumerate(
        _read_text(path, "SHA-256 清单").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2 or not CHECKSUM_PATTERN.fullmatch(parts[0]):
            raise ReleaseNotesError(f"SHA-256 清单第 {line_number} 行格式无效")
        filename = parts[1].removeprefix("*")
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or filename in manifest
        ):
            raise ReleaseNotesError(f"SHA-256 清单包含不安全或重复文件名：{filename}")
        manifest[filename] = parts[0].lower()
    if not manifest:
        raise ReleaseNotesError(f"SHA-256 清单为空：{path}")
    return manifest


def _load_artifacts(
    metadata: PackageMetadata,
    checksum_path: Path,
) -> tuple[ArtifactInfo, ArtifactInfo]:
    """校验 runtime/source ZIP，并返回模板所需的产物信息。"""

    manifest = _read_checksum_manifest(checksum_path)
    artifact_specs = (
        (
            f"{metadata.name}-{metadata.version}-runtime.zip",
            "AstrBot 可直接安装的运行时包",
        ),
        (f"{metadata.name}-{metadata.version}-source.zip", "对应发布提交的源码包"),
    )
    artifacts: list[ArtifactInfo] = []
    for filename, purpose in artifact_specs:
        expected_hash = manifest.get(filename)
        if expected_hash is None:
            raise ReleaseNotesError(f"SHA-256 清单缺少必需产物：{filename}")
        artifact_path = checksum_path.parent / filename
        if not artifact_path.is_file():
            raise ReleaseNotesError(f"发布产物不存在：{artifact_path}")
        actual_hash = _sha256(artifact_path)
        if actual_hash != expected_hash:
            raise ReleaseNotesError(f"发布产物哈希不匹配：{filename}")
        try:
            size = artifact_path.stat().st_size
        except OSError as exc:
            raise ReleaseNotesError(f"读取发布产物大小失败：{artifact_path}") from exc
        artifacts.append(
            ArtifactInfo(
                filename=filename,
                purpose=purpose,
                size=_format_size(size),
                sha256=actual_hash,
            )
        )
    return artifacts[0], artifacts[1]


def _release_kind(release_type: str) -> str:
    """将命令行发布类型转换为模板中的中文名称。"""

    try:
        return {
            "build": "仅构建（不发布）",
            "pre-release": "预发布",
            "release": "正式发布",
        }[release_type]
    except KeyError as exc:
        raise ReleaseNotesError(f"不支持的发布类型：{release_type}") from exc


def render_release_notes(
    repo_root: Path,
    template_path: Path,
    checksum_path: Path,
    changelog_path: Path,
    output_path: Path,
    commit_sha: str,
    release_type: str,
) -> Path:
    """校验发布输入并用模板生成 Markdown 发布说明。"""

    if not COMMIT_PATTERN.fullmatch(commit_sha):
        raise ReleaseNotesError("commit SHA 必须是 40 位或 64 位十六进制字符串")
    try:
        metadata = load_metadata(repo_root)
    except PackageError as exc:
        raise ReleaseNotesError(f"读取插件元数据失败：{exc}") from exc

    resolved_template = _resolve_path(repo_root, template_path)
    resolved_checksum = _resolve_path(repo_root, checksum_path)
    resolved_changelog = _resolve_path(repo_root, changelog_path)
    resolved_output = _resolve_path(repo_root, output_path)
    runtime, source = _load_artifacts(metadata, resolved_checksum)
    changelog = _extract_changelog_section(resolved_changelog, metadata.version)
    values = {
        "package_name": metadata.name,
        "version": metadata.version,
        "release_tag": f"v{metadata.version}",
        "release_kind": _release_kind(release_type),
        "commit_sha": commit_sha.lower(),
        "changelog": changelog,
        "runtime_filename": runtime.filename,
        "runtime_purpose": runtime.purpose,
        "runtime_size": runtime.size,
        "runtime_sha256": runtime.sha256,
        "source_filename": source.filename,
        "source_purpose": source.purpose,
        "source_size": source.size,
        "source_sha256": source.sha256,
    }
    try:
        rendered = Template(_read_text(resolved_template, "发布说明模板")).substitute(
            values
        )
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(
            rendered if rendered.endswith("\n") else f"{rendered}\n",
            encoding="utf-8",
        )
    except (KeyError, ValueError, OSError, UnicodeError) as exc:
        raise ReleaseNotesError(f"生成发布说明失败：{resolved_output}") from exc
    return resolved_output


def main(argv: Sequence[str] | None = None) -> int:
    """执行发布说明生成命令行入口。"""

    args = parse_args(argv)
    try:
        output = render_release_notes(
            repo_root=REPO_ROOT,
            template_path=args.template,
            checksum_path=args.checksums,
            changelog_path=args.changelog,
            output_path=args.output,
            commit_sha=args.commit,
            release_type=args.release_type,
        )
    except ReleaseNotesError as exc:
        print(f"生成发布说明失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成发布说明：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
