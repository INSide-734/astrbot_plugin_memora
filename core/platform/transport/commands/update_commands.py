"""插件 runtime 更新命令。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ....features.updates.application import RuntimeUpdateInstaller, UpdateManager
from ....features.updates.domain import RuntimeUpdateError, UpdateError
from ....platform.resources.i18n_backend import t


class UpdateCommandMixin:
    """为命令处理器提供更新检查、安装与安全下载能力。"""

    _update_manager: UpdateManager | None = None
    _update_installer: RuntimeUpdateInstaller | None = None

    @staticmethod
    def _format_update_size(size: int) -> str:
        """把字节数转换为适合命令消息展示的单位。"""
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KiB"
        return f"{size / (1024 * 1024):.2f} MiB"

    async def handle_update(
        self,
        event: AstrMessageEvent,
        action: str = "check",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 ``/memora update check|download|apply`` 命令。"""
        manager = self._update_manager
        if manager is None:
            yield event.plain_result(t("update.unavailable"))
            return
        if not manager.is_enabled():
            yield event.plain_result(t("update.disabled"))
            return

        normalized_action = (action or "check").strip().lower()
        if normalized_action not in {"check", "download", "apply"}:
            yield event.plain_result(t("update.invalid_action"))
            return

        try:
            if normalized_action == "check":
                yield event.plain_result(t("update.checking"))
                release = await manager.check()
                if release is None:
                    yield event.plain_result(
                        t("update.up_to_date", version=manager.current_version)
                    )
                    return
                notes = release.notes.strip() or t("common.none")
                yield event.plain_result(
                    t(
                        "update.available",
                        current=release.current_version,
                        latest=release.version,
                        source=t(f"update.sources.{release.metadata_source}"),
                        filename=release.runtime_filename,
                        notes=notes,
                    )
                )
                return

            if normalized_action == "apply":
                installer = self._update_installer
                if installer is None:
                    yield event.plain_result(t("update.unavailable"))
                    return
                yield event.plain_result(t("update.applying"))
                result = await installer.apply_latest()
                yield event.plain_result(
                    t(
                        "update.apply_scheduled",
                        version=str(result.get("version", "")),
                    )
                )
                return

            yield event.plain_result(t("update.downloading"))
            downloaded = await manager.download()
            yield event.plain_result(
                t(
                    "update.downloaded",
                    version=downloaded.release.version,
                    source=t(f"update.sources.{downloaded.download_source}"),
                    size=self._format_update_size(downloaded.size),
                    sha256=downloaded.sha256,
                    path=str(downloaded.path),
                )
            )
        except asyncio.CancelledError:
            raise
        except (RuntimeUpdateError, UpdateError) as exc:
            logger.warning("插件更新操作未完成: %s", exc)
            yield event.plain_result(t("update.failed", reason=str(exc)))
        except Exception:
            logger.error("插件更新操作发生未预期错误", exc_info=True)
            yield event.plain_result(
                t("update.failed", reason=t("common.unknown_error"))
            )


__all__ = ["UpdateCommandMixin"]
