"""runtime 更新安装、重载和回滚测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.features.updates.application.installer import RuntimeUpdateInstaller
from core.features.updates.domain import (
    DownloadedUpdate,
    RuntimeUpdateError,
    UpdateRelease,
)


def _write_plugin_tree(plugin_root: Path, version: str, marker: str) -> None:
    """创建用于目录切换测试的最小旧插件目录。"""
    plugin_root.mkdir(parents=True)
    (plugin_root / "main.py").write_text(marker, encoding="utf-8")
    (plugin_root / "metadata.yaml").write_text(
        "\n".join(
            (
                "name: astrbot_plugin_memora",
                "author: INSide-734",
                f"version: {version}",
                "description: 测试插件",
            )
        ),
        encoding="utf-8",
    )
    (plugin_root / "source-only.txt").write_text("旧源码文件", encoding="utf-8")


def _write_runtime_zip(path: Path, version: str, marker: str = "new") -> None:
    """创建满足正式打包结构的最小 runtime ZIP。"""
    package = "astrbot_plugin_memora"
    files = {
        "main.py": marker.encode(),
        "metadata.yaml": (
            "\n".join(
                (
                    f"name: {package}",
                    "author: INSide-734",
                    f"version: {version}",
                    "description: 测试插件",
                )
            )
        ).encode(),
        "_conf_schema.json": b"{}",
        "requirements.txt": b"astrbot\n",
        "LICENSE": b"license",
        "README.md": b"readme",
        "README_EN.md": b"readme",
        "README_RU.md": b"readme",
        "logo.png": b"png",
        "core/__init__.py": b"",
        "static/__init__.py": b"",
        "pages/dashboard/index.html": b"<html></html>",
        "pages/dashboard/assets/index.js": b"console.log('ok')",
        ".astrbot-plugin/i18n/zh-CN.json": b"{}",
        ".astrbot-plugin/i18n/en-US.json": b"{}",
        ".astrbot-plugin/i18n/ru-RU.json": b"{}",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, payload in files.items():
            archive.writestr(f"{package}/{relative}", payload)


def _downloaded_update(path: Path, version: str = "1.1.0") -> DownloadedUpdate:
    """构造已通过下载阶段校验的更新结果。"""
    payload = path.read_bytes()
    release = UpdateRelease(
        tag=f"v{version}",
        version=version,
        current_version="1.0.0",
        published_at="2026-07-27T00:00:00Z",
        notes="更新说明",
        runtime_filename=path.name,
        runtime_url="https://example.test/runtime.zip",
        checksum_url="https://example.test/SHA256SUMS.txt",
        metadata_source="official",
    )
    return DownloadedUpdate(
        release=release,
        path=path,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        download_source="official",
    )


def _installer(
    tmp_path: Path,
    *,
    reload_result: tuple[bool, str | None] = (True, None),
) -> tuple[RuntimeUpdateInstaller, SimpleNamespace, Path]:
    """构造绑定伪 AstrBot 插件管理器的安装器。"""
    plugin_store = tmp_path / "plugins"
    plugin_root = plugin_store / "astrbot_plugin_memora"
    _write_plugin_tree(plugin_root, "1.0.0", "old")
    star = SimpleNamespace(
        name="astrbot_plugin_memora",
        root_dir_name="astrbot_plugin_memora",
        reserved=False,
    )
    star_context = SimpleNamespace(
        get_registered_star=lambda name: (
            star if name == "astrbot_plugin_memora" else None
        ),
        get_all_stars=lambda: [star],
    )
    star_manager = SimpleNamespace(
        plugin_store_path=str(plugin_store),
        context=star_context,
        reload=AsyncMock(return_value=reload_result),
        reload_failed_plugin=AsyncMock(return_value=(True, None)),
        _ensure_plugin_requirements=AsyncMock(return_value=None),
    )
    context = SimpleNamespace(_star_manager=star_manager)
    manager = SimpleNamespace(download=AsyncMock())
    installer = RuntimeUpdateInstaller(
        context=context,
        data_dir=tmp_path / "plugin-data",
        plugin_root=plugin_root,
        update_manager=manager,
    )
    return installer, star_manager, plugin_root


@pytest.mark.asyncio
async def test_apply_latest_switches_runtime_and_marks_reload_success(
    tmp_path: Path,
) -> None:
    """已校验 runtime 应替换源码目录并在单插件重载后完成。"""
    installer, star_manager, plugin_root = _installer(tmp_path)
    archive = tmp_path / "astrbot_plugin_memora-1.1.0-runtime.zip"
    _write_runtime_zip(archive, "1.1.0")
    installer.update_manager.download.return_value = _downloaded_update(archive)

    started = await installer.apply_latest()
    assert started["status"] == "reload_scheduled"
    assert (plugin_root / "main.py").read_text(encoding="utf-8") == "new"
    assert not (plugin_root / "source-only.txt").exists()

    assert installer._reload_task is not None
    await installer._reload_task
    status = installer.get_status(started["operation_id"])

    assert status["status"] == "succeeded"
    assert status["version"] == "1.1.0"
    assert status["rollback_performed"] is False
    star_manager.reload.assert_awaited_once_with("astrbot_plugin_memora")
    star_manager._ensure_plugin_requirements.assert_awaited_once()
    assert not list(plugin_root.parent.glob(".astrbot_plugin_memora.rollback-*"))


@pytest.mark.asyncio
async def test_reload_failure_restores_previous_plugin_and_reports_rollback(
    tmp_path: Path,
) -> None:
    """新版本重载失败时应恢复旧目录并重新载入旧插件。"""
    installer, star_manager, plugin_root = _installer(
        tmp_path,
        reload_result=(False, "导入失败"),
    )
    archive = tmp_path / "astrbot_plugin_memora-1.1.0-runtime.zip"
    _write_runtime_zip(archive, "1.1.0")
    installer.update_manager.download.return_value = _downloaded_update(archive)

    started = await installer.apply_latest()
    assert installer._reload_task is not None
    await installer._reload_task
    status = installer.get_status(started["operation_id"])

    assert status["status"] == "rolled_back"
    assert status["rollback_performed"] is True
    assert status["requires_manual_restart"] is False
    assert (plugin_root / "main.py").read_text(encoding="utf-8") == "old"
    assert (plugin_root / "source-only.txt").read_text(encoding="utf-8") == "旧源码文件"
    star_manager.reload_failed_plugin.assert_awaited_once_with("astrbot_plugin_memora")


@pytest.mark.asyncio
async def test_apply_rejects_runtime_with_mismatched_metadata_version(
    tmp_path: Path,
) -> None:
    """ZIP 元数据版本与 Release 不一致时不得修改当前插件目录。"""
    installer, star_manager, plugin_root = _installer(tmp_path)
    archive = tmp_path / "astrbot_plugin_memora-1.1.0-runtime.zip"
    _write_runtime_zip(archive, "2.0.0")
    installer.update_manager.download.return_value = _downloaded_update(archive)

    with pytest.raises(RuntimeUpdateError, match="版本"):
        await installer.apply_latest()

    assert (plugin_root / "main.py").read_text(encoding="utf-8") == "old"
    star_manager.reload.assert_not_awaited()
    state = json.loads(
        (tmp_path / "plugin-data" / "updates" / "install-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["status"] == "failed"


@pytest.mark.asyncio
async def test_apply_rejects_plugin_directory_outside_astrbot_store(
    tmp_path: Path,
) -> None:
    """当前插件目录与 AstrBot 注册目录不一致时拒绝自更新。"""
    installer, star_manager, plugin_root = _installer(tmp_path)
    archive = tmp_path / "astrbot_plugin_memora-1.1.0-runtime.zip"
    _write_runtime_zip(archive, "1.1.0")
    installer.update_manager.download.return_value = _downloaded_update(archive)
    star_manager.plugin_store_path = str(tmp_path / "another-store")

    with pytest.raises(RuntimeUpdateError, match="插件目录"):
        await installer.apply_latest()

    assert (plugin_root / "main.py").read_text(encoding="utf-8") == "old"
    star_manager.reload.assert_not_awaited()
