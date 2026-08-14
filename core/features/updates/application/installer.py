"""已校验 runtime 包的安装、AstrBot 重载与失败回滚应用服务。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from astrbot.api import logger

from ..domain import DownloadedUpdate, RuntimeUpdateError, UpdateError
from .manager import UpdateManager

_PACKAGE_NAME = "astrbot_plugin_memora"
_STATE_FILENAME = "install-state.json"
_OPERATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE_STATUSES = {"preparing", "reload_scheduled", "rolling_back"}
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_RELOAD_DELAY_SECONDS = 0.75
_RUNTIME_ROOT_FILES = (
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
_PAGE_I18N_LOCALES = ("zh-CN", "en-US", "ru-RU")
_PUBLIC_STATE_FIELDS = (
    "operation_id",
    "version",
    "previous_version",
    "status",
    "started_at",
    "finished_at",
    "rollback_performed",
    "requires_manual_restart",
    "error_code",
)


@dataclass(frozen=True, slots=True)
class _RuntimeBinding:
    """一次安装使用的 AstrBot 插件注册与目录绑定。"""

    star_manager: Any
    plugin_name: str
    root_dir_name: str
    plugin_root: Path


class RuntimeUpdateInstaller:
    """把已校验 runtime ZIP 安装到当前 AstrBot 插件并支持回滚。"""

    def __init__(
        self,
        *,
        context: object,
        data_dir: str | Path,
        plugin_root: str | Path,
        update_manager: UpdateManager | object,
    ) -> None:
        """初始化自更新安装器并收敛重载完成后的持久化状态。

        参数:
            context: AstrBot 插件 ``Context``，用于访问宿主插件管理器。
            data_dir: AstrBot 分配的插件数据目录。
            plugin_root: 当前 Memora 插件代码目录。
            update_manager: 已负责 Release、镜像、代理和 SHA-256 的下载服务。
        """
        self.context = context
        self.data_dir = Path(data_dir)
        self.plugin_root = Path(plugin_root).absolute()
        self.update_manager = update_manager
        self._state_path = self.data_dir / "updates" / _STATE_FILENAME
        self._apply_lock = asyncio.Lock()
        self._reload_task: asyncio.Task[None] | None = None
        self._reconcile_loaded_runtime()

    def capabilities(self) -> dict[str, object]:
        """返回当前 AstrBot 是否支持受控 runtime 安装。"""
        try:
            self._resolve_binding()
        except RuntimeUpdateError:
            return {"auto_apply": False, "reason_code": "host_update_unavailable"}
        return {"auto_apply": True, "reason_code": None}

    def get_status(self, operation_id: str) -> dict[str, object]:
        """读取指定更新操作的安全状态摘要。

        参数:
            operation_id: ``apply_latest`` 返回的十六进制操作 ID。

        返回:
            不包含本机路径或内部异常文本的状态字典。

        异常:
            RuntimeUpdateError: 操作 ID 非法、状态不存在或不匹配。
        """
        normalized = str(operation_id or "").strip().lower()
        if not _OPERATION_ID_PATTERN.fullmatch(normalized):
            raise RuntimeUpdateError("更新操作 ID 无效。")
        state = self._read_state()
        if state is None or state.get("operation_id") != normalized:
            raise RuntimeUpdateError("更新操作不存在。")
        return self._public_state(state)

    async def apply_latest(self) -> dict[str, object]:
        """下载、校验、切换最新 runtime，并安排 AstrBot 单插件重载。

        返回:
            状态为 ``reload_scheduled`` 的安全操作摘要。

        异常:
            asyncio.CancelledError: 下载或准备阶段被取消。
            RuntimeUpdateError: 宿主不兼容、归档非法或目录切换失败。
            UpdateError: Release 下载或 SHA-256 校验失败。
        """
        async with self._apply_lock:
            binding = self._resolve_binding()
            active = self._read_state()
            if active is not None and active.get("status") in _ACTIVE_STATUSES:
                raise RuntimeUpdateError("已有插件更新操作正在进行。")

            operation_id = uuid.uuid4().hex
            started_at = time.time()
            initial_state: dict[str, object] = {
                "operation_id": operation_id,
                "version": "",
                "previous_version": self._read_metadata_version(binding.plugin_root),
                "status": "preparing",
                "started_at": started_at,
                "finished_at": None,
                "rollback_performed": False,
                "requires_manual_restart": False,
                "error_code": None,
                "backup_name": f".{binding.root_dir_name}.rollback-{operation_id}",
                "candidate_name": f".{binding.root_dir_name}.update-{operation_id}",
                "failed_name": f".{binding.root_dir_name}.failed-{operation_id}",
            }
            self._write_state(initial_state)

            candidate_container: Path | None = None
            try:
                downloaded = await self.update_manager.download()
                initial_state["version"] = downloaded.release.version
                self._write_state(initial_state)
                candidate_container, candidate_root = await self._prepare_candidate(
                    binding,
                    downloaded,
                    initial_state,
                )
                await self._ensure_requirements(binding, candidate_root)
            except asyncio.CancelledError:
                self._cleanup_candidate_if_present(binding, candidate_container)
                self._finish_state(initial_state, "cancelled", "operation_cancelled")
                raise
            except RuntimeUpdateError:
                self._cleanup_candidate_if_present(binding, candidate_container)
                self._finish_state(initial_state, "failed", "archive_invalid")
                raise
            except UpdateError:
                self._cleanup_candidate_if_present(binding, candidate_container)
                self._finish_state(initial_state, "failed", "download_failed")
                raise
            except Exception as exc:
                self._cleanup_candidate_if_present(binding, candidate_container)
                self._finish_state(initial_state, "failed", "prepare_failed")
                raise RuntimeUpdateError("准备 runtime 更新失败。") from exc

            switch_task = asyncio.create_task(
                asyncio.to_thread(
                    self._switch_runtime,
                    binding,
                    candidate_container,
                    candidate_root,
                    initial_state,
                )
            )
            cancelled: asyncio.CancelledError | None = None
            try:
                await asyncio.shield(switch_task)
            except asyncio.CancelledError as exc:
                cancelled = exc
                try:
                    await switch_task
                except Exception as switch_exc:
                    self._finish_state(initial_state, "failed", "switch_failed")
                    raise RuntimeUpdateError("切换 runtime 更新失败。") from switch_exc
            except Exception as exc:
                self._finish_state(initial_state, "failed", "switch_failed")
                raise RuntimeUpdateError("切换 runtime 更新失败。") from exc

            initial_state["status"] = "reload_scheduled"
            initial_state["error_code"] = None
            self._write_state(initial_state)
            self._reload_task = asyncio.create_task(
                self._reload_and_finalize(binding, initial_state)
            )
            self._reload_task.add_done_callback(self._consume_reload_task_result)
            if cancelled is not None:
                raise cancelled
            return self._public_state(initial_state)

    def _resolve_binding(self) -> _RuntimeBinding:
        """校验当前目录确实是 AstrBot 注册的非内置插件目录。"""
        star_manager = getattr(self.context, "_star_manager", None)
        reload_plugin = getattr(star_manager, "reload", None)
        reload_failed_plugin = getattr(star_manager, "reload_failed_plugin", None)
        plugin_store = getattr(star_manager, "plugin_store_path", None)
        star_context = getattr(star_manager, "context", None)
        if (
            not callable(reload_plugin)
            or not callable(reload_failed_plugin)
            or not plugin_store
            or star_context is None
        ):
            raise RuntimeUpdateError("当前 AstrBot 未提供插件重载能力。")
        if self.plugin_root.is_symlink() or not self.plugin_root.is_dir():
            raise RuntimeUpdateError("当前插件目录无效。")

        store_root = Path(plugin_store).resolve()
        actual_root = self.plugin_root.resolve()
        expected_root = (store_root / actual_root.name).resolve()
        if actual_root != expected_root or actual_root.parent != store_root:
            raise RuntimeUpdateError("当前插件目录不属于 AstrBot 插件目录。")

        get_registered = getattr(star_context, "get_registered_star", None)
        star = get_registered(_PACKAGE_NAME) if callable(get_registered) else None
        if star is None:
            get_all = getattr(star_context, "get_all_stars", None)
            stars = get_all() if callable(get_all) else []
            star = next(
                (
                    item
                    for item in stars
                    if getattr(item, "root_dir_name", None) == actual_root.name
                ),
                None,
            )
        if (
            star is None
            or bool(getattr(star, "reserved", False))
            or getattr(star, "root_dir_name", None) != actual_root.name
        ):
            raise RuntimeUpdateError("当前插件未按可更新插件注册。")
        plugin_name = str(getattr(star, "name", "") or "").strip()
        if not plugin_name:
            raise RuntimeUpdateError("当前插件缺少注册名称。")
        return _RuntimeBinding(
            star_manager=star_manager,
            plugin_name=plugin_name,
            root_dir_name=actual_root.name,
            plugin_root=actual_root,
        )

    async def _prepare_candidate(
        self,
        binding: _RuntimeBinding,
        downloaded: DownloadedUpdate,
        state: dict[str, object],
    ) -> tuple[Path, Path]:
        """在线程中验证并解压候选目录，取消时等待线程安全收尾。"""
        prepare_task = asyncio.create_task(
            asyncio.to_thread(
                self._extract_candidate,
                binding,
                downloaded,
                state,
            )
        )
        try:
            return await asyncio.shield(prepare_task)
        except asyncio.CancelledError:
            candidate_container, _ = await prepare_task
            self._remove_operation_tree(
                candidate_container,
                binding.plugin_root.parent,
                f".{binding.root_dir_name}.update-",
            )
            raise

    def _cleanup_candidate_if_present(
        self,
        binding: _RuntimeBinding,
        candidate_container: Path | None,
    ) -> None:
        """在目录切换前失败时清理已经完成解压的候选树。"""
        if candidate_container is None or not candidate_container.exists():
            return
        self._remove_operation_tree(
            candidate_container,
            binding.plugin_root.parent,
            f".{binding.root_dir_name}.update-",
        )

    def _extract_candidate(
        self,
        binding: _RuntimeBinding,
        downloaded: DownloadedUpdate,
        state: dict[str, object],
    ) -> tuple[Path, Path]:
        """严格验证 runtime ZIP 路径、元数据、大小并解压候选树。"""
        archive_path = downloaded.path
        if not archive_path.is_file():
            raise RuntimeUpdateError("runtime 更新包不存在。")
        candidate_name = str(state["candidate_name"])
        candidate_container = binding.plugin_root.parent / candidate_name
        if candidate_container.exists():
            raise RuntimeUpdateError("runtime 候选目录已存在。")
        candidate_container.mkdir()

        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                self._validate_archive_members(infos)
                for info in infos:
                    if info.is_dir():
                        continue
                    relative = PurePosixPath(info.filename)
                    destination = candidate_container.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
        except RuntimeUpdateError:
            self._remove_operation_tree(
                candidate_container,
                binding.plugin_root.parent,
                f".{binding.root_dir_name}.update-",
            )
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            self._remove_operation_tree(
                candidate_container,
                binding.plugin_root.parent,
                f".{binding.root_dir_name}.update-",
            )
            raise RuntimeUpdateError("runtime 压缩包无法安全解压。") from exc

        candidate_root = candidate_container / _PACKAGE_NAME
        version = self._read_metadata_version(candidate_root)
        if version != downloaded.release.version:
            self._remove_operation_tree(
                candidate_container,
                binding.plugin_root.parent,
                f".{binding.root_dir_name}.update-",
            )
            raise RuntimeUpdateError("runtime 元数据版本与 Release 版本不一致。")
        return candidate_container, candidate_root

    @staticmethod
    def _validate_archive_members(infos: list[zipfile.ZipInfo]) -> None:
        """拒绝路径穿越、符号链接、重复项、禁用目录和解压炸弹。"""
        if not infos or len(infos) > _MAX_ARCHIVE_MEMBERS:
            raise RuntimeUpdateError("runtime 压缩包成员数量无效。")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise RuntimeUpdateError("runtime 压缩包包含重复成员。")
        total_size = 0
        normalized: list[PurePosixPath] = []
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            mode = info.external_attr >> 16
            total_size += max(0, info.file_size)
            if (
                not path.parts
                or path.is_absolute()
                or "\\" in name
                or ":" in path.parts[0]
                or ".." in path.parts
                or path.parts[0] != _PACKAGE_NAME
                or info.flag_bits & 0x1
                or stat.S_ISLNK(mode)
            ):
                raise RuntimeUpdateError("runtime 压缩包包含不安全成员。")
            relative_parts = path.parts[1:]
            if (
                path.name.endswith(".zip")
                or {".git", "node_modules"}.intersection(relative_parts)
                or (
                    relative_parts
                    and relative_parts[0]
                    in {"data", "storage", "tests", "scripts", "docs"}
                )
            ):
                raise RuntimeUpdateError("runtime 压缩包包含禁止内容。")
            normalized.append(path)
        if total_size > _MAX_UNCOMPRESSED_BYTES:
            raise RuntimeUpdateError("runtime 压缩包解压后超过安全大小限制。")

        names_set = set(names)
        required = {f"{_PACKAGE_NAME}/{relative}" for relative in _RUNTIME_ROOT_FILES}
        required.update(
            f"{_PACKAGE_NAME}/.astrbot-plugin/i18n/{locale}.json"
            for locale in _PAGE_I18N_LOCALES
        )
        if not required.issubset(names_set):
            raise RuntimeUpdateError("runtime 压缩包缺少必需文件。")
        if not any(path.parts[1:2] == ("core",) for path in normalized):
            raise RuntimeUpdateError("runtime 压缩包缺少 core 目录。")
        if not any(path.parts[1:2] == ("static",) for path in normalized):
            raise RuntimeUpdateError("runtime 压缩包缺少 static 目录。")
        if not any(
            path.parts[1:4] == ("pages", "dashboard", "assets") for path in normalized
        ):
            raise RuntimeUpdateError("runtime 压缩包缺少 Dashboard 资源。")

    async def _ensure_requirements(
        self,
        binding: _RuntimeBinding,
        candidate_root: Path,
    ) -> None:
        """在切换目录前复用 AstrBot 的缺失依赖安装检查。"""
        ensure_requirements = getattr(
            binding.star_manager,
            "_ensure_plugin_requirements",
            None,
        )
        if callable(ensure_requirements):
            await ensure_requirements(str(candidate_root), binding.plugin_name)

    def _switch_runtime(
        self,
        binding: _RuntimeBinding,
        candidate_container: Path,
        candidate_root: Path,
        state: dict[str, object],
    ) -> None:
        """同卷保留旧目录并把完整候选目录切换为当前插件目录。"""
        backup = binding.plugin_root.parent / str(state["backup_name"])
        if backup.exists() or not candidate_root.is_dir():
            raise RuntimeUpdateError("runtime 目录切换前置条件不满足。")
        self._rename_directory(binding.plugin_root, backup)
        try:
            self._rename_directory(candidate_root, binding.plugin_root)
        except BaseException:
            self._rename_directory(backup, binding.plugin_root)
            raise
        try:
            candidate_container.rmdir()
        except OSError:
            logger.warning(
                "runtime 候选容器清理失败 operation_id=%s", state["operation_id"]
            )

    async def _reload_and_finalize(
        self,
        binding: _RuntimeBinding,
        state: dict[str, object],
    ) -> None:
        """延迟重载新插件；失败时恢复旧目录并重新载入旧版本。"""
        await asyncio.sleep(_RELOAD_DELAY_SECONDS)
        reload_plugin = getattr(binding.star_manager, "reload")
        try:
            result = await reload_plugin(binding.plugin_name)
            if not self._reload_succeeded(result):
                raise RuntimeUpdateError("AstrBot 返回插件重载失败。")
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._rollback_after_reload_failure(binding, state)
            return

        self._finish_state(state, "succeeded", None)
        await asyncio.to_thread(self._cleanup_after_success, binding, state)

    async def _rollback_after_reload_failure(
        self,
        binding: _RuntimeBinding,
        state: dict[str, object],
    ) -> None:
        """恢复旧代码目录，并通过 AstrBot 的失败插件入口重新载入。"""
        state["status"] = "rolling_back"
        state["rollback_performed"] = True
        state["error_code"] = "reload_failed"
        self._write_state(state)
        try:
            failed_tree = await asyncio.to_thread(
                self._restore_previous_runtime,
                binding,
                state,
            )
        except Exception:
            state["requires_manual_restart"] = True
            self._finish_state(state, "failed", "rollback_failed")
            return

        recovered = False
        reload_failed = getattr(binding.star_manager, "reload_failed_plugin", None)
        if callable(reload_failed):
            try:
                recovered = self._reload_succeeded(
                    await reload_failed(binding.root_dir_name)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                recovered = False
        state["requires_manual_restart"] = not recovered
        self._finish_state(state, "rolled_back", "reload_failed")
        if recovered:
            await asyncio.to_thread(
                self._remove_operation_tree,
                failed_tree,
                binding.plugin_root.parent,
                f".{binding.root_dir_name}.failed-",
            )

    def _restore_previous_runtime(
        self,
        binding: _RuntimeBinding,
        state: dict[str, object],
    ) -> Path:
        """把重载失败的新目录移开，并恢复保留的旧插件目录。"""
        backup = binding.plugin_root.parent / str(state["backup_name"])
        failed_tree = binding.plugin_root.parent / str(state["failed_name"])
        if not backup.is_dir() or failed_tree.exists():
            raise RuntimeUpdateError("找不到可用的旧 runtime 备份。")
        if binding.plugin_root.exists():
            self._rename_directory(binding.plugin_root, failed_tree)
        self._rename_directory(backup, binding.plugin_root)
        return failed_tree

    def _cleanup_after_success(
        self,
        binding: _RuntimeBinding,
        state: dict[str, object],
    ) -> None:
        """重载成功后删除同卷旧代码备份。"""
        backup = binding.plugin_root.parent / str(state["backup_name"])
        self._remove_operation_tree(
            backup,
            binding.plugin_root.parent,
            f".{binding.root_dir_name}.rollback-",
        )

    @staticmethod
    def _reload_succeeded(result: object) -> bool:
        """兼容 AstrBot 不同版本的重载返回形状。"""
        if isinstance(result, tuple):
            return bool(result and result[0])
        if isinstance(result, bool):
            return result
        return result is None

    @staticmethod
    def _rename_directory(source: Path, destination: Path) -> None:
        """在同一文件系统内重命名目录，兼容 Windows 短暂文件占用。"""
        attempts = 8 if os.name == "nt" else 1
        for attempt in range(attempts):
            try:
                os.rename(source, destination)
                return
            except PermissionError:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(0.05)

    @staticmethod
    def _remove_operation_tree(path: Path, parent: Path, prefix: str) -> None:
        """仅删除已验证为插件目录同级且名称匹配的事务目录。"""
        resolved_parent = parent.resolve()
        if path.parent.resolve() != resolved_parent or not path.name.startswith(prefix):
            raise RuntimeUpdateError("拒绝清理非更新事务目录。")
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    @staticmethod
    def _read_metadata_version(plugin_root: Path) -> str:
        """读取并校验插件目录中的名称与版本。"""
        metadata_path = plugin_root / "metadata.yaml"
        try:
            payload = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeUpdateError("插件 metadata.yaml 无法读取。") from exc
        if not isinstance(payload, dict) or payload.get("name") != _PACKAGE_NAME:
            raise RuntimeUpdateError("插件 metadata.yaml 名称无效。")
        version = payload.get("version")
        if not isinstance(version, str) or not version.strip():
            raise RuntimeUpdateError("插件 metadata.yaml 版本无效。")
        return version.strip()

    def _reconcile_loaded_runtime(self) -> None:
        """新插件实例加载后完成上一个重载或回滚操作的状态收敛。"""
        state = self._read_state()
        if state is None or state.get("status") not in {
            "reload_scheduled",
            "rolling_back",
        }:
            return
        try:
            current_version = self._read_metadata_version(self.plugin_root)
        except RuntimeUpdateError:
            return
        if state.get("status") == "reload_scheduled" and current_version == state.get(
            "version"
        ):
            self._finish_state(state, "succeeded", None)
            try:
                binding = self._resolve_binding()
                self._cleanup_after_success(binding, state)
            except Exception:
                logger.warning("更新成功后的旧 runtime 清理将在后续重试")
        elif state.get("status") == "rolling_back" and current_version == state.get(
            "previous_version"
        ):
            state["rollback_performed"] = True
            self._finish_state(state, "rolled_back", "reload_failed")

    def _read_state(self) -> dict[str, object] | None:
        """读取最近一次安装事务状态，损坏内容按不存在处理。"""
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_state(self, state: dict[str, object]) -> None:
        """通过唯一临时文件原子写入安装事务状态。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(
            f".{self._state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self._state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _finish_state(
        self,
        state: dict[str, object],
        status: str,
        error_code: str | None,
    ) -> None:
        """将事务写为终态并记录稳定错误码。"""
        state["status"] = status
        state["error_code"] = error_code
        state["finished_at"] = time.time()
        self._write_state(state)

    @staticmethod
    def _public_state(state: dict[str, object]) -> dict[str, object]:
        """投影页面与命令可见的状态字段白名单。"""
        return {key: state.get(key) for key in _PUBLIC_STATE_FIELDS}

    @staticmethod
    def _consume_reload_task_result(task: asyncio.Task[None]) -> None:
        """消费后台重载异常，避免未读取任务异常。"""
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug("插件更新重载任务已取消")
        except Exception:
            logger.error("插件更新重载任务发生未预期错误", exc_info=True)


__all__ = ["RuntimeUpdateError", "RuntimeUpdateInstaller"]
