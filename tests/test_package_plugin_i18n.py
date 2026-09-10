"""插件 runtime 包 i18n 资源打包契约测试。"""

from __future__ import annotations

from pathlib import Path

from scripts import package_plugin


def _write(path: Path, content: str = "内容") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_runtime_package_includes_astrbot_page_i18n(tmp_path: Path) -> None:
    """runtime 包必须携带 AstrBot Page 的三语生产资源。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"

    for relative in package_plugin.RUNTIME_ROOT_FILES:
        _write(source_root / relative)
    _write(source_root / "core" / "i18n_backend.py")
    _write(source_root / "static" / "placeholder.txt")
    _write(source_root / package_plugin.PLUGIN_SKILL_PATH)
    _write(source_root / "pages" / "dashboard" / "index.html")
    _write(source_root / "pages" / "dashboard" / "assets" / "bundle.js")
    for locale in ("zh-CN", "en-US", "ru-RU"):
        _write(source_root / ".astrbot-plugin" / "i18n" / f"{locale}.json")

    package_plugin.copy_runtime_files(source_root, staging_root, "memora")

    for locale in ("zh-CN", "en-US", "ru-RU"):
        assert (
            staging_root / "memora" / ".astrbot-plugin" / "i18n" / f"{locale}.json"
        ).is_file()
