"""插件源码包收集契约测试：Git 跟踪状态、.gitignore 语义与结构排除。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import package_plugin


def _write(path: Path, content: str = "内容") -> None:
    """创建测试文件及其父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_git(repo_root: Path, *args: str) -> None:
    """在临时仓库中执行 Git 命令，失败时抛出。"""

    subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_ALLOW_PROTOCOL": "file"},
    )


def _init_repo(repo_root: Path, gitignore: str = "") -> None:
    """初始化最小 Git 仓库并写入根 .gitignore。"""
    _run_git(repo_root, "init", "-q")
    if gitignore:
        (repo_root / ".gitignore").write_text(gitignore, encoding="utf-8")


def _stage(repo_root: Path, *paths: str) -> None:
    """将文件加入 Git 索引使其成为已跟踪文件（无需提交）。"""
    _run_git(repo_root, "add", "-f", "--", *paths)


def test_worktree_source_respects_gitignore(tmp_path: Path) -> None:
    """源码包应尊重 .gitignore，并保留否定模式与未被忽略的未跟踪文件。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    output_dir = tmp_path / "output"
    source_root.mkdir()
    _init_repo(
        source_root,
        "\n".join(
            (
                ".uv-cache/",
                "*.log",
                ".pytest_memora_data/",
                ".env.*",
                "!.env.example",
            )
        ),
    )
    _write(source_root / "main.py")
    _write(source_root / "ignored.log")
    _write(source_root / ".uv-cache" / "archive.bin")
    _write(source_root / ".pytest_memora_data" / ".secret_key")
    _write(source_root / ".env.local")
    _write(source_root / ".env.example")
    _write(source_root / "draft.py")
    _stage(source_root, "main.py", ".gitignore")

    package_plugin.copy_worktree_source(source_root, staging_root, "memora", output_dir)

    packaged = staging_root / "memora"
    assert (packaged / "main.py").is_file()
    assert (packaged / "draft.py").is_file()
    assert (packaged / ".env.example").is_file()
    assert not (packaged / "ignored.log").exists()
    assert not (packaged / ".env.local").exists()
    assert not (packaged / ".uv-cache").exists()
    assert not (packaged / ".pytest_memora_data").exists()


def test_worktree_source_respects_nested_gitignore(tmp_path: Path) -> None:
    """子目录 .gitignore 同样生效。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    output_dir = tmp_path / "output"
    source_root.mkdir()
    _init_repo(source_root)
    dashboard = source_root / "pages" / "dashboard"
    dashboard.mkdir(parents=True)
    (dashboard / ".gitignore").write_text("*.tsbuildinfo\n", encoding="utf-8")
    _write(source_root / "main.py")
    _write(dashboard / "index.html")
    _write(dashboard / "tsconfig.tsbuildinfo")
    _stage(
        source_root,
        "main.py",
        "pages/dashboard/.gitignore",
        "pages/dashboard/index.html",
    )

    package_plugin.copy_worktree_source(source_root, staging_root, "memora", output_dir)

    packaged = staging_root / "memora"
    assert (packaged / "pages" / "dashboard" / "index.html").is_file()
    assert not (packaged / "pages" / "dashboard" / "tsconfig.tsbuildinfo").exists()


def test_worktree_source_keeps_tracked_file_matching_ignore_pattern(
    tmp_path: Path,
) -> None:
    """已跟踪文件即使命中忽略模式仍应打包（Git 语义：跟踪优先）。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    output_dir = tmp_path / "output"
    source_root.mkdir()
    _init_repo(source_root, "*.txt\n")
    _write(source_root / "main.py")
    _write(source_root / "vendor" / "notes.txt")
    _stage(source_root, "main.py", ".gitignore", "vendor/notes.txt")

    package_plugin.copy_worktree_source(source_root, staging_root, "memora", output_dir)

    packaged = staging_root / "memora"
    assert (packaged / "vendor" / "notes.txt").is_file()


def test_worktree_source_requires_git_repository(tmp_path: Path) -> None:
    """非 Git 仓库无法可靠应用 .gitignore，应明确失败而不是静默打包。"""
    source_root = tmp_path / "repo"
    _write(source_root / "main.py")

    with pytest.raises(package_plugin.PackageError):
        package_plugin.copy_worktree_source(
            source_root, tmp_path / "staging", "memora", tmp_path / "output"
        )


def test_source_package_excludes_vitepress_directory(tmp_path: Path) -> None:
    """源码包应保留公开文档正文，但排除已跟踪的 VitePress 工具目录。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    output_dir = tmp_path / "output"
    source_root.mkdir()
    _init_repo(source_root)
    _write(source_root / "website" / "docs" / "index.md")
    _write(source_root / "website" / "docs" / ".vitepress" / "config.mts")
    _write(source_root / "website" / "docs" / ".vitepress" / "theme" / "index.ts")
    _stage(
        source_root,
        "website/docs/index.md",
        "website/docs/.vitepress/config.mts",
        "website/docs/.vitepress/theme/index.ts",
    )

    package_plugin.copy_worktree_source(source_root, staging_root, "memora", output_dir)

    packaged_docs = staging_root / "memora" / "website" / "docs"
    assert (packaged_docs / "index.md").is_file()
    assert not (packaged_docs / ".vitepress").exists()


@pytest.mark.skipif(os.name != "posix", reason="非 UTF-8 文件名字节仅 POSIX 可复现")
def test_worktree_source_handles_non_utf8_filename(tmp_path: Path) -> None:
    """POSIX 仓库中非 UTF-8 文件名应按文件系统字节原样打包，不得解码失败。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    output_dir = tmp_path / "output"
    source_root.mkdir()
    _init_repo(source_root, "*.log\n")
    decoded_name = os.fsdecode(b"caf\xe9.py")
    _write(source_root / "main.py")
    _write(source_root / decoded_name)
    _stage(source_root, "main.py", ".gitignore", decoded_name)

    package_plugin.copy_worktree_source(source_root, staging_root, "memora", output_dir)

    assert (staging_root / "memora" / decoded_name).is_file()


def _commit(repo_root: Path, message: str) -> None:
    """提交临时仓库当前已暂存内容。"""

    _run_git(
        repo_root,
        "-c",
        "user.name=Memora Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        message,
    )


def test_git_source_includes_pinned_submodule_content(tmp_path: Path) -> None:
    """从 Git HEAD 打包源码时应归档 gitlink 固定提交，而非遗漏子模块内容。"""

    dependency_root = tmp_path / "dependency"
    dependency_root.mkdir()
    _init_repo(dependency_root)
    _write(dependency_root / "test_contract.py", "assert True\n")
    _stage(dependency_root, "test_contract.py")
    _commit(dependency_root, "添加测试契约")

    dependency_remote = tmp_path / "dependency.git"
    subprocess.run(
        [
            "git",
            "clone",
            "--no-local",
            "--bare",
            str(dependency_root),
            str(dependency_remote),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    source_root = tmp_path / "repo"
    source_root.mkdir()
    _init_repo(source_root)
    _write(source_root / "main.py")
    _stage(source_root, "main.py")
    _commit(source_root, "初始化主仓库")
    _run_git(
        source_root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(dependency_remote),
        "tests",
    )
    _commit(source_root, "添加测试子模块")

    staging_root = tmp_path / "staging"
    package_plugin.copy_git_source(source_root, staging_root, "memora")

    packaged = staging_root / "memora"
    assert (packaged / "main.py").is_file()
    assert (packaged / "tests" / "test_contract.py").is_file()


def test_worktree_source_includes_initialized_submodule_content(
    tmp_path: Path,
) -> None:
    """源码工作树打包应包含已初始化子模块的当前文件。"""

    dependency_root = tmp_path / "dependency"
    dependency_root.mkdir()
    _init_repo(dependency_root)
    _write(dependency_root / "test_contract.py", "assert True\n")
    _stage(dependency_root, "test_contract.py")
    _commit(dependency_root, "添加测试契约")

    dependency_remote = tmp_path / "dependency.git"
    subprocess.run(
        [
            "git",
            "clone",
            "--no-local",
            "--bare",
            str(dependency_root),
            str(dependency_remote),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    source_root = tmp_path / "repo"
    source_root.mkdir()
    _init_repo(source_root)
    _write(source_root / "main.py")
    _stage(source_root, "main.py")
    _commit(source_root, "初始化主仓库")
    _run_git(
        source_root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(dependency_remote),
        "tests",
    )

    _write(source_root / "tests" / "draft.py", "draft = True\n")
    staging_root = tmp_path / "staging"
    package_plugin.copy_worktree_source(
        source_root,
        staging_root,
        "memora",
        tmp_path / "output",
    )

    packaged = staging_root / "memora"
    assert (packaged / "main.py").is_file()
    assert (packaged / "tests" / "test_contract.py").is_file()
    assert (packaged / "tests" / "draft.py").is_file()


def test_create_zip_normalizes_member_metadata(tmp_path: Path) -> None:
    """相同内容在不同源文件时间戳下应生成完全相同的 ZIP。"""

    staging_root = tmp_path / "staging"
    source = staging_root / "memora" / "main.py"
    _write(source, "print('memora')\n")
    first_archive = tmp_path / "first.zip"
    second_archive = tmp_path / "second.zip"

    os.utime(source, (946684800, 946684800))
    package_plugin.create_zip(staging_root, first_archive)

    os.utime(source, (1704067200, 1704067200))
    package_plugin.create_zip(staging_root, second_archive)

    assert first_archive.read_bytes() == second_archive.read_bytes()
