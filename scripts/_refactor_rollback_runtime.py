"""隔离执行生产 runtime 更新器与 Schema 迁移回退闭环。"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import sys
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml


def _ensure_repo_root_importable() -> None:
    """让脚本入口加载当前候选的生产 ``core`` 实现。"""

    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _sha256(path: Path) -> str:
    """计算归档 SHA-256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_version(path: Path) -> str:
    """只读取 runtime ZIP 的根 metadata 版本。"""

    with zipfile.ZipFile(path) as archive:
        payload = yaml.safe_load(
            archive.read("astrbot_plugin_memora/metadata.yaml").decode("utf-8")
        )
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("runtime_metadata_version_missing")
    return version.strip()


def _tree_version(plugin_root: Path) -> str:
    """读取当前激活目录的 metadata 版本。"""

    payload = yaml.safe_load(
        (plugin_root / "metadata.yaml").read_text(encoding="utf-8")
    )
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("runtime_tree_version_missing")
    return version.strip()


def _tree_marker(plugin_root: Path) -> str:
    """计算 runtime 普通文件及相对路径的稳定内容标记。"""

    digest = hashlib.sha256()
    files = sorted(path for path in plugin_root.rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in files):
        raise ValueError("runtime_tree_invalid")
    for path in files:
        digest.update(path.relative_to(plugin_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _create_legacy_database(path: Path) -> None:
    """创建会执行至少一个 ALTER 的匿名 v7 canonical 数据库。"""

    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE TABLE db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            );
            INSERT INTO db_version (
                version,description,migrated_at,migration_duration_seconds
            ) VALUES (7,'rollback-evidence','2026-01-01T00:00:00+00:00',0.0);
            INSERT INTO documents (id,text,metadata) VALUES
                (11,'anonymous migration row one','{}'),
                (22,'anonymous migration row two','{}');
            """
        )
        connection.commit()


def _legacy_snapshot(path: Path) -> tuple[int, int, tuple[str, ...]]:
    """读取迁移恢复后的数量、版本和 documents 列。"""

    with sqlite3.connect(path) as connection:
        count = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        version = int(
            connection.execute("SELECT MAX(version) FROM db_version").fetchone()[0]
        )
        columns = tuple(
            sorted(
                str(row[1])
                for row in connection.execute("PRAGMA table_info(documents)")
            )
        )
    return count, version, columns


class _FailAfterFirstAlter:
    """在真实首个 ALTER 后的回填语句注入迁移失败。"""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.partial_write_seen = False

    async def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        """委托 SQL，并在已观察到部分结构写入后失败。"""

        normalized = " ".join(sql.split())
        if normalized.startswith("ALTER TABLE documents"):
            self.partial_write_seen = True
        elif self.partial_write_seen and normalized.startswith("UPDATE documents SET"):
            raise sqlite3.OperationalError("injected migration failure")
        return await self._connection.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """把连接协议其余部分委托给真实 aiosqlite 连接。"""

        return getattr(self._connection, name)


class _SnapshotBackup:
    """使用生产 snapshot_sqlite 创建协调器可恢复的快照。"""

    def __init__(self, database: Path, data_dir: Path) -> None:
        self.database = database
        self.data_dir = data_dir
        self.created = False

    async def create_backup(self, kind: str = "manual") -> dict[str, object]:
        """创建严格位于隔离 data/backups 下的迁移快照。"""

        from core.managers.backup_snapshot import snapshot_sqlite

        if kind != "pre_migration":
            raise ValueError("unexpected_backup_kind")
        directory = self.data_dir / "backups" / "pre_migration_evidence"
        directory.mkdir(parents=True, exist_ok=False)
        await asyncio.to_thread(
            snapshot_sqlite,
            self.database,
            directory / self.database.name,
        )
        self.created = True
        return {"name": directory.name, "directory": str(directory)}


class _MissingBackup:
    """返回不存在的合法形状路径以验证恢复失败的 blocked 边界。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.created = False

    async def create_backup(self, kind: str = "manual") -> dict[str, object]:
        """记录备份阶段已执行，但让恢复源验证 fail-closed。"""

        if kind != "pre_migration":
            raise ValueError("unexpected_backup_kind")
        directory = self.data_dir / "backups" / "missing_evidence"
        return {"name": directory.name, "directory": str(directory)}


async def _run_migration_case(
    root: Path,
    *,
    restore_fails: bool,
) -> dict[str, Any]:
    """执行真实协调器的部分迁移及快照恢复或 blocked 分支。"""

    _ensure_repo_root_importable()
    import aiosqlite

    from core.managers.schema_manager import SchemaManager
    from core.managers.schema_migration import (
        SchemaMigrationCoordinator,
        SchemaMigrationError,
    )

    root.mkdir(parents=True, exist_ok=False)
    database = root / "memora.db"
    _create_legacy_database(database)
    baseline = _legacy_snapshot(database)
    connection = await aiosqlite.connect(database)
    failing = _FailAfterFirstAlter(connection)
    manager = SchemaManager(failing)
    backup: Any = (
        _MissingBackup(root) if restore_fails else _SnapshotBackup(database, root)
    )
    coordinator = SchemaMigrationCoordinator(
        manager,
        db_path=database,
        data_dir=root,
        auto_migrate=True,
        create_backup=True,
        backup_manager=backup,
    )
    reason_code = "migration_unexpected_success"
    stage = "unknown"
    try:
        await coordinator.run()
    except SchemaMigrationError as exc:
        reason_code = exc.reason_code
        stage = exc.stage
    finally:
        await manager.close_connection()
    state = coordinator.read_persisted_state()
    current = _legacy_snapshot(database)
    persisted_blocked = state.get("stage") == "blocked"
    reentry_reason = None
    if persisted_blocked:
        reentry = SchemaMigrationCoordinator(
            SchemaManager(None),
            db_path=database,
            data_dir=root,
            auto_migrate=True,
            create_backup=True,
            backup_manager=backup,
        )
        try:
            await reentry.run()
        except SchemaMigrationError as exc:
            reentry_reason = exc.reason_code
    return {
        "reason_code": reason_code,
        "stage": stage,
        "partial_write_seen": failing.partial_write_seen,
        "snapshot_created": bool(getattr(backup, "created", True)),
        "snapshot_restored": current == baseline
        and reason_code == "schema_migration_rolled_back",
        "persisted": persisted_blocked,
        "reentry_reason_code": reentry_reason,
    }


class _DownloadManager:
    """返回已在调用前计算 SHA 的本地 runtime 归档。"""

    def __init__(self, downloaded: Any) -> None:
        self.downloaded = downloaded

    async def download(self) -> Any:
        """模拟 UpdateManager 的已校验下载边界，不访问网络。"""

        return self.downloaded


class _StarManager:
    """提供生产安装器需要的最小 AstrBot 插件管理能力。"""

    def __init__(
        self,
        plugin_store: Path,
        reload_callback: Callable[[str], Awaitable[tuple[bool, str | None]]],
        failed_callback: Callable[[str], Awaitable[tuple[bool, str | None]]],
    ) -> None:
        star = SimpleNamespace(
            name="astrbot_plugin_memora",
            root_dir_name="astrbot_plugin_memora",
            reserved=False,
        )
        self.plugin_store_path = str(plugin_store)
        self.context = SimpleNamespace(
            get_registered_star=lambda name: (
                star if name == "astrbot_plugin_memora" else None
            ),
            get_all_stars=lambda: [star],
        )
        self._reload_callback = reload_callback
        self._failed_callback = failed_callback
        self.reload_calls = 0
        self.reload_failed_calls = 0
        self.requirements_calls = 0

    async def reload(self, plugin_name: str) -> tuple[bool, str | None]:
        """把新 runtime 重载委托给受控迁移场景。"""

        self.reload_calls += 1
        return await self._reload_callback(plugin_name)

    async def reload_failed_plugin(
        self,
        root_dir_name: str,
    ) -> tuple[bool, str | None]:
        """在代码恢复后尝试重新激活旧 runtime。"""

        self.reload_failed_calls += 1
        return await self._failed_callback(root_dir_name)

    async def _ensure_plugin_requirements(
        self,
        plugin_root: str,
        plugin_name: str,
    ) -> None:
        """记录生产安装器确实执行了依赖前置门。"""

        del plugin_root, plugin_name
        self.requirements_calls += 1


async def _run_installer_case(
    old_template: Path,
    new_archive: Path,
    root: Path,
    *,
    restore_fails: bool,
) -> dict[str, Any]:
    """执行一次生产原子切换、重载失败与旧目录恢复。"""

    _ensure_repo_root_importable()
    from core.managers.update_installer import RuntimeUpdateInstaller
    from core.managers.update_manager import DownloadedUpdate, UpdateRelease

    plugin_store = root / "plugins"
    plugin_root = plugin_store / "astrbot_plugin_memora"
    plugin_store.mkdir(parents=True)
    shutil.copytree(old_template, plugin_root)
    old_version = _tree_version(plugin_root)
    old_marker = _tree_marker(plugin_root)
    new_version = _archive_version(new_archive)
    migration_result: dict[str, Any] = {}
    old_activation = False

    async def reload_plugin(plugin_name: str) -> tuple[bool, str | None]:
        """确认新目录已激活，再注入真实部分迁移失败。"""

        if plugin_name != "astrbot_plugin_memora":
            return False, "unexpected_plugin_name"
        if _tree_marker(plugin_root) == old_marker:
            return False, "atomic_switch_not_observed"
        migration_result.update(
            await _run_migration_case(
                root / "migration-data",
                restore_fails=restore_fails,
            )
        )
        return False, str(migration_result.get("reason_code"))

    async def reload_failed_plugin(
        root_dir_name: str,
    ) -> tuple[bool, str | None]:
        """恢复成功分支重激活旧版本；blocked 分支明确拒绝伪通过。"""

        nonlocal old_activation
        directory_restored = (
            root_dir_name == "astrbot_plugin_memora"
            and _tree_marker(plugin_root) == old_marker
            and _tree_version(plugin_root) == old_version
        )
        if restore_fails:
            old_activation = False
            return False, "schema_migration_blocked"
        old_activation = directory_restored and bool(
            migration_result.get("snapshot_restored")
        )
        return old_activation, None if old_activation else "restore_incomplete"

    manager = _StarManager(
        plugin_store,
        reload_plugin,
        reload_failed_plugin,
    )
    payload = new_archive.read_bytes()
    release = UpdateRelease(
        tag=f"v{new_version}",
        version=new_version,
        current_version=old_version,
        published_at="2026-01-01T00:00:00Z",
        notes="rollback evidence",
        runtime_filename=new_archive.name,
        runtime_url="https://example.invalid/runtime.zip",
        checksum_url="https://example.invalid/SHA256SUMS.txt",
        metadata_source="isolated_fixture",
    )
    downloaded = DownloadedUpdate(
        release=release,
        path=new_archive,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        download_source="isolated_fixture",
    )
    restore_calls = 0

    class _AuditedInstaller(RuntimeUpdateInstaller):
        """只记录私有恢复边界，并委托生产实现执行实际 rename。"""

        def _restore_previous_runtime(
            self,
            binding: Any,
            state: dict[str, object],
        ) -> Path:
            """记录调用后直接执行父类实现。"""

            nonlocal restore_calls
            restore_calls += 1
            return super()._restore_previous_runtime(binding, state)

    installer = _AuditedInstaller(
        context=SimpleNamespace(_star_manager=manager),
        data_dir=root / "installer-data",
        plugin_root=plugin_root,
        update_manager=_DownloadManager(downloaded),
    )
    started = await installer.apply_latest()
    backup = plugin_store / f".astrbot_plugin_memora.rollback-{started['operation_id']}"
    switch_observed = (
        started["status"] == "reload_scheduled"
        and _tree_version(plugin_root) == new_version
        and _tree_marker(plugin_root) != old_marker
        and backup.is_dir()
        and _tree_marker(backup) == old_marker
    )
    if installer._reload_task is None:
        raise RuntimeError("runtime_reload_task_missing")
    await installer._reload_task
    final = installer.get_status(str(started["operation_id"]))
    directory_restored = (
        _tree_version(plugin_root) == old_version
        and _tree_marker(plugin_root) == old_marker
    )
    return {
        "switch_observed": switch_observed,
        "restore_previous_runtime_calls": restore_calls,
        "directory_restored": directory_restored,
        "old_runtime_reactivated": old_activation,
        "reload_calls": manager.reload_calls,
        "reload_failed_calls": manager.reload_failed_calls,
        "requirements_calls": manager.requirements_calls,
        "final_status": final.get("status"),
        "manual_restart": final.get("requires_manual_restart"),
        "migration": migration_result,
    }


def _stage(closed: bool, success: str, failure: str) -> dict[str, str]:
    """将布尔断言编码为 closed/remaining 稳定阶段。"""

    return {
        "status": "closed" if closed else "remaining",
        "reason_code": success if closed else failure,
    }


async def _exercise_runtime_update_rollback(
    old_template: Path,
    old_archive: Path,
    new_archive: Path,
    scratch_root: Path,
) -> dict[str, Any]:
    """串联可恢复与恢复失败两条生产回退路径。"""

    old_hash, new_hash = _sha256(old_archive), _sha256(new_archive)
    if old_hash == new_hash:
        return {
            "status": "remaining",
            "reason_code": "runtime_archives_identical",
            "stages": {
                "archive_identity": _stage(
                    False,
                    "runtime_archives_distinct",
                    "runtime_archives_identical",
                )
            },
        }
    try:
        recovered = await _run_installer_case(
            old_template,
            new_archive,
            scratch_root / "recovered",
            restore_fails=False,
        )
        blocked = await _run_installer_case(
            old_template,
            new_archive,
            scratch_root / "blocked",
            restore_fails=True,
        )
    except (
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
        zipfile.BadZipFile,
    ) as exc:
        return {
            "status": "remaining",
            "reason_code": "runtime_update_evidence_failed",
            "error_type": type(exc).__name__,
            "stages": {
                "runtime_installer": _stage(
                    False,
                    "runtime_installer_verified",
                    "runtime_installer_failed",
                )
            },
        }

    migration = recovered["migration"]
    blocked_migration = blocked["migration"]
    stages = {
        "archive_identity": _stage(
            True,
            "runtime_archives_distinct",
            "runtime_archives_identical",
        ),
        "atomic_switch": _stage(
            bool(recovered["switch_observed"] and blocked["switch_observed"]),
            "runtime_atomic_switch_verified",
            "runtime_atomic_switch_remaining",
        ),
        "partial_migration": _stage(
            bool(
                migration.get("partial_write_seen")
                and blocked_migration.get("partial_write_seen")
            ),
            "partial_migration_verified",
            "partial_migration_remaining",
        ),
        "snapshot_restore": _stage(
            bool(migration.get("snapshot_restored")),
            "schema_snapshot_restore_verified",
            "schema_snapshot_restore_remaining",
        ),
        "restore_previous_runtime": _stage(
            recovered["restore_previous_runtime_calls"] == 1
            and blocked["restore_previous_runtime_calls"] == 1
            and recovered["directory_restored"]
            and blocked["directory_restored"],
            "restore_previous_runtime_verified",
            "restore_previous_runtime_remaining",
        ),
        "old_runtime_reactivation": _stage(
            bool(recovered["old_runtime_reactivated"]),
            "old_runtime_reactivation_verified",
            "old_runtime_reactivation_remaining",
        ),
        "restore_failure_blocked": _stage(
            bool(
                blocked_migration.get("persisted")
                and blocked_migration.get("reentry_reason_code")
                == "schema_migration_blocked"
                and blocked["manual_restart"] is True
                and blocked["old_runtime_reactivated"] is False
            ),
            "restore_failure_blocked_verified",
            "restore_failure_blocked_remaining",
        ),
    }
    closed = all(stage["status"] == "closed" for stage in stages.values())
    return {
        "status": "closed" if closed else "remaining",
        "reason_code": (
            "runtime_update_rollback_verified"
            if closed
            else "runtime_update_rollback_remaining"
        ),
        "implementation": "core.managers.RuntimeUpdateInstaller/SchemaMigrationCoordinator",
        "stages": stages,
        "migration": migration,
        "blocked": blocked_migration,
        "old_runtime_reactivated": recovered["old_runtime_reactivated"],
        "blocked_old_runtime_directory_restored": blocked["directory_restored"],
    }


def exercise_runtime_update_rollback(
    old_template: Path,
    old_archive: Path,
    new_archive: Path,
    scratch_root: Path,
) -> dict[str, Any]:
    """同步入口；全部写入均限定在调用者提供的临时根。"""

    return asyncio.run(
        _exercise_runtime_update_rollback(
            old_template,
            old_archive,
            new_archive,
            scratch_root,
        )
    )


__all__ = ["exercise_runtime_update_rollback"]
