"""Dashboard 插件更新接口。"""

from __future__ import annotations

import asyncio
from typing import Any

from quart import request

from ..managers.update_installer import RuntimeUpdateError, RuntimeUpdateInstaller
from ..managers.update_manager import UpdateError, UpdateManager
from .response_utils import error_response, ok_response


class UpdateApiMixin:
    """提供更新检查、忽略和 runtime 下载接口。"""

    def _update_write_guard(self) -> dict[str, Any] | None:
        """在更新写操作前复用页面 API 的备份恢复维护守卫。"""
        guard = getattr(self, "_maintenance_write_guard", None)
        if not callable(guard):
            return None
        return guard()

    def _get_update_manager(self) -> UpdateManager | None:
        """取得插件级更新服务。"""
        manager = getattr(getattr(self, "plugin", None), "_update_manager", None)
        return manager if isinstance(manager, UpdateManager) else None

    def _get_update_installer(self) -> RuntimeUpdateInstaller | None:
        """取得插件级 runtime 安装与回滚服务。"""
        installer = getattr(
            getattr(self, "plugin", None),
            "_update_installer",
            None,
        )
        if not callable(getattr(installer, "apply_latest", None)):
            return None
        if not callable(getattr(installer, "get_status", None)):
            return None
        return installer

    @staticmethod
    def _release_payload(release: Any, ignored_version: str | None) -> dict[str, Any]:
        """构造不包含内部对象或完整远端 URL 的页面响应。"""
        if release is None:
            return {
                "available": False,
                "ignored": False,
                "release": None,
                "ignored_version": ignored_version,
            }
        ignored = release.version == ignored_version
        return {
            "available": not ignored,
            "ignored": ignored,
            "ignored_version": ignored_version,
            "release": {
                "version": release.version,
                "tag": release.tag,
                "published_at": release.published_at,
                "notes": release.notes,
                "runtime_filename": release.runtime_filename,
                "source": release.metadata_source,
            },
        }

    async def check_update(self):
        """检查最新 runtime 版本并返回页面所需摘要。"""
        manager = self._get_update_manager()
        if manager is None:
            return error_response("更新服务不可用", code="update_unavailable")
        if not manager.is_enabled():
            installer = self._get_update_installer()
            return ok_response(
                {
                    "enabled": False,
                    "current_version": manager.current_version,
                    "available": False,
                    "ignored": False,
                    "release": None,
                    "capabilities": installer.capabilities()
                    if installer is not None
                    else {
                        "auto_apply": False,
                        "reason_code": "host_update_unavailable",
                    },
                }
            )
        try:
            release = await manager.check()
            payload = self._release_payload(release, manager.ignored_version())
            installer = self._get_update_installer()
            return ok_response(
                {
                    "enabled": True,
                    "current_version": manager.current_version,
                    "capabilities": installer.capabilities()
                    if installer is not None
                    else {
                        "auto_apply": False,
                        "reason_code": "host_update_unavailable",
                    },
                    **payload,
                }
            )
        except asyncio.CancelledError:
            raise
        except UpdateError:
            return error_response("检查插件更新失败", code="update_check_failed")
        except Exception:
            return error_response("检查插件更新失败", code="update_check_failed")

    async def ignore_update(self):
        """保存管理员选择忽略的版本。"""
        blocked = self._update_write_guard()
        if blocked is not None:
            return blocked
        manager = self._get_update_manager()
        if manager is None:
            return error_response("更新服务不可用", code="update_unavailable")
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
            return error_response("version 必须是字符串", code="invalid_request")
        try:
            version = manager.ignore_version(payload["version"][:64])
            return ok_response({"ignored_version": version})
        except UpdateError:
            return error_response("忽略版本无效", code="invalid_request")

    async def download_update(self):
        """下载最新 runtime 包并返回安全暂存文件摘要。"""
        blocked = self._update_write_guard()
        if blocked is not None:
            return blocked
        manager = self._get_update_manager()
        if manager is None:
            return error_response("更新服务不可用", code="update_unavailable")
        try:
            result = await manager.download()
            return ok_response(
                {
                    "version": result.release.version,
                    "size": result.size,
                    "sha256": result.sha256,
                    "source": result.download_source,
                    "runtime_filename": result.release.runtime_filename,
                    "staged": True,
                }
            )
        except asyncio.CancelledError:
            raise
        except UpdateError:
            return error_response("下载插件更新失败", code="update_download_failed")
        except Exception:
            return error_response("下载插件更新失败", code="update_download_failed")

    async def apply_update(self):
        """安装最新 runtime，安排 AstrBot 单插件重载并返回操作状态。"""
        blocked = self._update_write_guard()
        if blocked is not None:
            return blocked
        installer = self._get_update_installer()
        if installer is None:
            return error_response(
                "当前 AstrBot 不支持自动安装插件更新",
                code="update_apply_unavailable",
            )
        try:
            return ok_response(await installer.apply_latest())
        except asyncio.CancelledError:
            raise
        except RuntimeUpdateError:
            return error_response("安装插件更新失败", code="update_apply_failed")
        except UpdateError:
            return error_response("下载插件更新失败", code="update_download_failed")
        except Exception:
            return error_response("安装插件更新失败", code="update_apply_failed")

    async def get_update_status(self):
        """读取一键更新操作的安全状态摘要。"""
        installer = self._get_update_installer()
        if installer is None:
            return error_response(
                "当前 AstrBot 不支持自动安装插件更新",
                code="update_apply_unavailable",
            )
        operation_id = str(request.args.get("operation_id", "")).strip()
        try:
            return ok_response(installer.get_status(operation_id))
        except RuntimeUpdateError:
            return error_response("更新操作不存在", code="update_operation_not_found")
        except Exception:
            return error_response("读取更新状态失败", code="update_status_failed")


__all__ = ["UpdateApiMixin"]
