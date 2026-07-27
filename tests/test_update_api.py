"""Dashboard 更新接口的响应、校验和取消传播测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.api.update_api import UpdateApiMixin
from core.managers.update_manager import DownloadedUpdate, UpdateError, UpdateRelease


def _release() -> UpdateRelease:
    """构造页面接口所需的最小发布对象。"""
    return UpdateRelease(
        tag="v1.1.0",
        version="1.1.0",
        current_version="1.0.0",
        published_at="2026-07-27T00:00:00Z",
        notes="修复更新链路",
        runtime_filename="astrbot_plugin_memora-1.1.0-runtime.zip",
        runtime_url="https://github.com/example/runtime.zip",
        checksum_url="https://github.com/example/SHA256SUMS.txt",
        metadata_source="mirror",
    )


def _api() -> UpdateApiMixin:
    """构造绑定到插件更新服务的接口对象。"""
    return UpdateApiMixin()


def _bind(api: UpdateApiMixin, manager) -> None:
    """为接口对象注入插件更新服务。"""
    api.plugin = SimpleNamespace(_update_manager=manager)


def _bind_installer(api: UpdateApiMixin, manager, installer) -> None:
    """为接口对象同时注入下载服务和 runtime 安装器。"""
    api.plugin = SimpleNamespace(
        _update_manager=manager,
        _update_installer=installer,
    )


@pytest.mark.asyncio
async def test_check_update_returns_release_summary_without_urls(
    tmp_path: Path,
) -> None:
    """检查接口应返回发布摘要，但不暴露远端下载 URL。"""
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path, current_version="1.0.0")
    manager.check = AsyncMock(return_value=_release())  # type: ignore[method-assign]
    api = _api()
    _bind(api, manager)

    result = await api.check_update()

    assert result["status"] == "ok"
    assert result["data"]["available"] is True
    assert result["data"]["release"]["source"] == "mirror"
    assert "runtime_url" not in result["data"]["release"]
    assert "checksum_url" not in result["data"]["release"]


@pytest.mark.asyncio
async def test_check_update_marks_ignored_version_unavailable(tmp_path: Path) -> None:
    """管理员忽略当前版本后，页面不应继续显示更新卡片。"""
    release = _release()
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path, current_version="1.0.0")
    manager.check = AsyncMock(return_value=release)  # type: ignore[method-assign]
    manager.ignored_version = lambda: release.version  # type: ignore[method-assign]
    api = _api()
    _bind(api, manager)

    result = await api.check_update()

    assert result["data"]["available"] is False
    assert result["data"]["ignored"] is True
    assert result["data"]["ignored_version"] == "1.1.0"


@pytest.mark.asyncio
async def test_update_actions_delegate_to_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """忽略和下载接口应调用管理器，并返回稳定的 JSON 摘要。"""
    from core.api import update_api

    release = _release()
    result_path = Path("updates") / release.runtime_filename
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path)
    manager.ignore_version = lambda version: version  # type: ignore[method-assign]
    manager.download = AsyncMock(
        return_value=DownloadedUpdate(
            release=release,
            path=result_path,
            size=12,
            sha256="a" * 64,
            download_source="official",
        )
    )
    api = _api()
    _bind(api, manager)

    request = SimpleNamespace(get_json=AsyncMock(return_value={"version": "1.1.0"}))
    monkeypatch.setattr(update_api, "request", request)
    ignored = await api.ignore_update()
    downloaded = await api.download_update()

    assert ignored == {"status": "ok", "data": {"ignored_version": "1.1.0"}}
    assert downloaded["status"] == "ok"
    assert downloaded["data"]["source"] == "official"
    assert downloaded["data"]["sha256"] == "a" * 64
    assert downloaded["data"]["staged"] is True
    assert "path" not in downloaded["data"]
    manager.download.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_apply_and_status_delegate_to_runtime_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一键更新与状态查询应只返回安装器的安全状态摘要。"""
    from core.api import update_api
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path)
    operation = {
        "operation_id": "a" * 32,
        "version": "1.1.0",
        "status": "reload_scheduled",
        "rollback_performed": False,
        "requires_manual_restart": False,
    }
    installer = SimpleNamespace(
        apply_latest=AsyncMock(return_value=operation),
        get_status=lambda operation_id: {
            **operation,
            "operation_id": operation_id,
            "status": "succeeded",
        },
    )
    api = _api()
    _bind_installer(api, manager, installer)

    applied = await api.apply_update()
    monkeypatch.setattr(
        update_api,
        "request",
        SimpleNamespace(args={"operation_id": "a" * 32}),
    )
    status = await api.get_update_status()

    assert applied == {"status": "ok", "data": operation}
    assert status["status"] == "ok"
    assert status["data"]["status"] == "succeeded"
    installer.apply_latest.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_apply_update_respects_maintenance_guard(tmp_path: Path) -> None:
    """备份恢复事务存在时不得切换插件 runtime。"""
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path)
    installer = SimpleNamespace(apply_latest=AsyncMock())
    api = _api()
    _bind_installer(api, manager, installer)
    api._maintenance_write_guard = lambda: {  # type: ignore[attr-defined]
        "status": "error",
        "code": "maintenance_blocked",
    }

    result = await api.apply_update()

    assert result["code"] == "maintenance_blocked"
    installer.apply_latest.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_update_propagates_cancellation() -> None:
    """取消下载请求时必须保留 asyncio.CancelledError 语义。"""
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager("updates")
    manager.download = AsyncMock(side_effect=asyncio.CancelledError)  # type: ignore[method-assign]
    api = _api()
    _bind(api, manager)

    with pytest.raises(asyncio.CancelledError):
        await api.download_update()


@pytest.mark.asyncio
async def test_update_write_actions_respect_maintenance_guard(tmp_path: Path) -> None:
    """备份恢复阻塞写入时，不应开始更新包下载。"""
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path)
    manager.download = AsyncMock()  # type: ignore[method-assign]
    api = _api()
    _bind(api, manager)
    api._maintenance_write_guard = lambda: {  # type: ignore[attr-defined]
        "status": "error",
        "code": "maintenance_blocked",
    }

    result = await api.download_update()

    assert result["code"] == "maintenance_blocked"
    manager.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_api_returns_stable_error_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理器失败时接口不应泄漏内部错误文本。"""
    from core.api import update_api
    from core.managers.update_manager import UpdateManager

    manager = UpdateManager(tmp_path, current_version="1.0.0")
    manager.check = AsyncMock(side_effect=UpdateError("远端细节"))  # type: ignore[method-assign]
    manager.ignore_version = lambda _version: (_ for _ in ()).throw(  # type: ignore[method-assign]
        UpdateError("写入失败")
    )
    api = _api()
    _bind(api, manager)
    monkeypatch.setattr(
        update_api,
        "request",
        SimpleNamespace(get_json=AsyncMock(return_value={"version": "1.1.0"})),
    )

    checked = await api.check_update()
    ignored = await api.ignore_update()

    assert checked == {
        "status": "error",
        "message": "检查插件更新失败",
        "code": "update_check_failed",
    }
    assert ignored == {
        "status": "error",
        "message": "忽略版本无效",
        "code": "invalid_request",
    }
