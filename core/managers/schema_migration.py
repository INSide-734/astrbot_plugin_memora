"""启动期安全 Schema 迁移协调与失败恢复。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from astrbot.api import logger

from ..features.memory.infrastructure.schema_manager import (
    CURRENT_DB_VERSION,
    SchemaManager,
    SchemaMigrationPlan,
    WriteJournalCreateCallback,
)
from .backup_manager import BackupManager
from .backup_snapshot import atomic_write_json

_STATE_FILE = ".schema_migration_state.json"


class MigrationBackupProvider(Protocol):
    """声明迁移协调器所需的最小备份接口。"""

    async def create_backup(self, kind: str = "manual") -> dict[str, object]:
        """创建指定类型的校验后备份。"""


@dataclass(frozen=True, slots=True)
class SchemaMigrationResult:
    """返回不含路径、正文或内部数据库标识的迁移结果。"""

    migration_id: str
    from_version: int
    to_version: int
    stage: str
    reason_code: str
    canonical_count: int


class SchemaMigrationError(RuntimeError):
    """携带稳定 reason code 的启动阻断错误。"""

    def __init__(self, reason_code: str, *, stage: str) -> None:
        """保存公开 reason code 和失败阶段，不附带底层异常正文。"""

        super().__init__(reason_code)
        self.reason_code = reason_code
        self.stage = stage


class SchemaMigrationCoordinator:
    """执行检查、配置门、备份、迁移、验证与失败恢复。"""

    def __init__(
        self,
        schema_manager: SchemaManager,
        *,
        db_path: str | Path,
        data_dir: str | Path,
        auto_migrate: bool,
        create_backup: bool,
        backup_manager: MigrationBackupProvider | None = None,
    ) -> None:
        """保存启动期依赖与迁移配置快照。"""

        self.schema_manager = schema_manager
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.auto_migrate = bool(auto_migrate)
        self.create_backup = bool(create_backup)
        self.backup_manager = backup_manager or BackupManager(str(self.data_dir))
        self._state_path = self.data_dir / _STATE_FILE

    async def run(
        self,
        write_journal_create_table_cb: WriteJournalCreateCallback | None = None,
    ) -> SchemaMigrationResult:
        """执行一次可恢复的启动期 Schema 准备流程。"""

        blocked_state = self.read_persisted_state()
        if blocked_state.get("stage") == "blocked":
            raise SchemaMigrationError("schema_migration_blocked", stage="blocked")

        inspection = await self.schema_manager.inspect_schema()
        if inspection.fresh:
            migration_id = f"schema-fresh-v{CURRENT_DB_VERSION}"
            self._persist_state(
                migration_id=migration_id,
                from_version=0,
                to_version=CURRENT_DB_VERSION,
                stage="creating",
                reason_code="fresh_schema_creating",
                canonical_count=0,
            )
            await self.schema_manager.create_fresh_schema(write_journal_create_table_cb)
            result = SchemaMigrationResult(
                migration_id=migration_id,
                from_version=0,
                to_version=CURRENT_DB_VERSION,
                stage="fresh_created",
                reason_code="fresh_schema_created",
                canonical_count=0,
            )
            self._persist_result(result)
            return result

        try:
            plan = self.schema_manager.build_migration_plan(
                inspection,
                require_write_journal=write_journal_create_table_cb is not None,
            )
        except ValueError as exc:
            reason_code = "schema_version_unsupported"
            migration_id = f"schema-v{inspection.version}-unsupported"
            self._persist_state(
                migration_id=migration_id,
                from_version=inspection.version,
                to_version=CURRENT_DB_VERSION,
                stage="blocked",
                reason_code=reason_code,
                canonical_count=inspection.canonical_count,
            )
            raise SchemaMigrationError(reason_code, stage="blocked") from exc

        if plan is None:
            result = SchemaMigrationResult(
                migration_id=f"schema-v{CURRENT_DB_VERSION}-current",
                from_version=CURRENT_DB_VERSION,
                to_version=CURRENT_DB_VERSION,
                stage="current",
                reason_code="schema_current",
                canonical_count=inspection.canonical_count,
            )
            self._persist_result(result)
            return result

        if not self.auto_migrate:
            self._persist_plan(
                plan,
                stage="required",
                reason_code="schema_migration_required",
            )
            raise SchemaMigrationError(
                "schema_migration_required",
                stage="required",
            )

        backup_result: dict[str, object] | None = None
        if self.create_backup:
            self._persist_plan(
                plan,
                stage="backing_up",
                reason_code="pre_migration_backup_started",
            )
            try:
                backup_result = await self.backup_manager.create_backup(
                    kind="pre_migration"
                )
            except asyncio.CancelledError:
                self._persist_plan(
                    plan,
                    stage="cancelled",
                    reason_code="pre_migration_backup_cancelled",
                )
                raise
            except Exception as exc:
                self._persist_plan(
                    plan,
                    stage="failed",
                    reason_code="pre_migration_backup_failed",
                )
                logger.error(
                    "Schema 迁移前备份失败: migration_id=%s reason_code=%s",
                    plan.migration_id,
                    "pre_migration_backup_failed",
                )
                raise SchemaMigrationError(
                    "pre_migration_backup_failed",
                    stage="failed",
                ) from exc

        self._persist_plan(
            plan,
            stage="migrating",
            reason_code="schema_migration_started",
        )
        try:
            validation = await self.schema_manager.migrate_existing_schema(
                plan,
                write_journal_create_table_cb,
            )
            if not validation.valid:
                raise RuntimeError(validation.reason_code)
        except asyncio.CancelledError:
            self._persist_plan(
                plan,
                stage="cancelled",
                reason_code="schema_migration_cancelled",
            )
            raise
        except Exception as exc:
            await self._recover_failed_migration(plan, backup_result, exc)

        result = SchemaMigrationResult(
            migration_id=plan.migration_id,
            from_version=plan.from_version,
            to_version=plan.to_version,
            stage="completed",
            reason_code="schema_migration_completed",
            canonical_count=plan.canonical_count,
        )
        self._persist_result(result, plan=plan)
        return result

    def read_persisted_state(self) -> dict[str, object]:
        """读取并过滤迁移状态，只返回固定安全字段。"""

        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        allowed = {
            "migration_id",
            "from_version",
            "to_version",
            "stage",
            "reason_code",
            "canonical_count",
            "columns_added",
            "tables_added",
            "indexes_added",
            "triggers_added",
        }
        return {key: value[key] for key in allowed if key in value}

    async def _recover_failed_migration(
        self,
        plan: SchemaMigrationPlan,
        backup_result: dict[str, object] | None,
        cause: Exception,
    ) -> None:
        """恢复迁移前状态；恢复失败时持久化 blocked 并停止启动。"""

        try:
            if backup_result is not None:
                await self.schema_manager.close_connection()
                await asyncio.to_thread(
                    self._restore_canonical_snapshot,
                    backup_result,
                    plan.canonical_count,
                    plan.from_version,
                )
            else:
                inspection = await self.schema_manager.inspect_schema()
                if (
                    inspection.canonical_count != plan.canonical_count
                    or inspection.version != plan.from_version
                ):
                    raise RuntimeError("schema_transaction_rollback_invalid")
        except asyncio.CancelledError:
            self._persist_plan(
                plan,
                stage="blocked",
                reason_code="schema_migration_recovery_cancelled",
            )
            raise
        except Exception as restore_exc:
            self._persist_plan(
                plan,
                stage="blocked",
                reason_code="schema_migration_restore_failed",
            )
            logger.error(
                "Schema 迁移恢复失败: migration_id=%s reason_code=%s",
                plan.migration_id,
                "schema_migration_restore_failed",
            )
            raise SchemaMigrationError(
                "schema_migration_restore_failed",
                stage="blocked",
            ) from restore_exc

        self._persist_plan(
            plan,
            stage="rolled_back",
            reason_code="schema_migration_rolled_back",
        )
        logger.error(
            "Schema 迁移失败并已恢复: migration_id=%s reason_code=%s",
            plan.migration_id,
            "schema_migration_rolled_back",
        )
        raise SchemaMigrationError(
            "schema_migration_rolled_back",
            stage="rolled_back",
        ) from cause

    def _restore_canonical_snapshot(
        self,
        backup_result: dict[str, object],
        expected_canonical_count: int,
        expected_version: int,
    ) -> None:
        """校验迁移前快照后原子替换已关闭的 canonical 数据库。"""

        backup_directory = Path(str(backup_result.get("directory", "")))
        source = backup_directory / self.db_path.name
        data_root = self.data_dir.resolve()
        backup_root = (data_root / "backups").resolve()
        backup_directory_resolved = backup_directory.resolve()
        target = self.db_path.resolve()
        if target.parent != data_root:
            raise RuntimeError("schema_restore_target_invalid")
        if (
            backup_directory.is_symlink()
            or backup_directory_resolved.parent != backup_root
            or not source.is_file()
            or source.is_symlink()
            or source.resolve().parent != backup_directory_resolved
        ):
            raise RuntimeError("schema_restore_source_invalid")
        self._validate_snapshot_baseline(
            source,
            expected_canonical_count=expected_canonical_count,
            expected_version=expected_version,
        )

        temporary = target.with_name(f".schema-restore-{uuid.uuid4().hex}.db")
        try:
            shutil.copy2(source, temporary)
            self._validate_snapshot_baseline(
                temporary,
                expected_canonical_count=expected_canonical_count,
                expected_version=expected_version,
            )
            for suffix in ("-wal", "-shm"):
                sidecar = target.with_name(target.name + suffix)
                sidecar.unlink(missing_ok=True)
            os.replace(temporary, target)
            self._validate_snapshot_baseline(
                target,
                expected_canonical_count=expected_canonical_count,
                expected_version=expected_version,
            )
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_snapshot_baseline(
        db_path: Path,
        *,
        expected_canonical_count: int,
        expected_version: int,
    ) -> None:
        """校验快照完整性、canonical 数量和最高 Schema 版本。"""

        connection = sqlite3.connect(db_path)
        try:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if quick_check.lower() != "ok":
                raise RuntimeError("schema_restore_quick_check_failed")
            canonical_count = int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
            table_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            version = 0
            if "db_version" in table_names:
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM db_version"
                    ).fetchone()[0]
                )
            if canonical_count != expected_canonical_count:
                raise RuntimeError("schema_restore_canonical_count_mismatch")
            if version != expected_version:
                raise RuntimeError("schema_restore_version_mismatch")
        finally:
            connection.close()

    def _persist_result(
        self,
        result: SchemaMigrationResult,
        *,
        plan: SchemaMigrationPlan | None = None,
    ) -> None:
        """持久化完成结果及低基数变更计数。"""

        self._persist_state(
            migration_id=result.migration_id,
            from_version=result.from_version,
            to_version=result.to_version,
            stage=result.stage,
            reason_code=result.reason_code,
            canonical_count=result.canonical_count,
            columns_added=len(plan.missing_columns) if plan else 0,
            tables_added=len(plan.missing_tables) if plan else 0,
            indexes_added=len(plan.missing_indexes) if plan else 0,
            triggers_added=len(plan.missing_triggers) if plan else 0,
        )

    def _persist_plan(
        self,
        plan: SchemaMigrationPlan,
        *,
        stage: str,
        reason_code: str,
    ) -> None:
        """持久化计划阶段及安全计数摘要。"""

        self._persist_state(
            migration_id=plan.migration_id,
            from_version=plan.from_version,
            to_version=plan.to_version,
            stage=stage,
            reason_code=reason_code,
            canonical_count=plan.canonical_count,
            columns_added=len(plan.missing_columns),
            tables_added=len(plan.missing_tables),
            indexes_added=len(plan.missing_indexes),
            triggers_added=len(plan.missing_triggers),
        )

    def _persist_state(
        self,
        *,
        migration_id: str,
        from_version: int,
        to_version: int,
        stage: str,
        reason_code: str,
        canonical_count: int,
        columns_added: int = 0,
        tables_added: int = 0,
        indexes_added: int = 0,
        triggers_added: int = 0,
    ) -> None:
        """原子写入不含路径、正文或内部 ID 的迁移状态。"""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._state_path,
            {
                "migration_id": migration_id,
                "from_version": int(from_version),
                "to_version": int(to_version),
                "stage": stage,
                "reason_code": reason_code,
                "canonical_count": int(canonical_count),
                "columns_added": int(columns_added),
                "tables_added": int(tables_added),
                "indexes_added": int(indexes_added),
                "triggers_added": int(triggers_added),
            },
        )


__all__ = [
    "SchemaMigrationCoordinator",
    "SchemaMigrationError",
    "SchemaMigrationResult",
]
