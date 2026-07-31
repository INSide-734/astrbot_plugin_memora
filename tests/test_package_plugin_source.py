"""插件源码包的文档站排除契约测试。"""

from __future__ import annotations

from pathlib import Path

from scripts import package_plugin


def _write(path: Path, content: str = "内容") -> None:
    """创建测试文件及其父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_source_package_excludes_vitepress_directory(tmp_path: Path) -> None:
    """源码包应保留公开文档正文，但排除 VitePress 工具目录。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    output_dir = tmp_path / "output"

    _write(source_root / "website" / "docs" / "index.md")
    _write(source_root / "website" / "docs" / ".vitepress" / "config.mts")
    _write(source_root / "website" / "docs" / ".vitepress" / "theme" / "index.ts")

    package_plugin.copy_worktree_source(
        source_root,
        staging_root,
        "memora",
        output_dir,
    )

    packaged_docs = staging_root / "memora" / "website" / "docs"
    assert (packaged_docs / "index.md").is_file()
    assert not (packaged_docs / ".vitepress").exists()
