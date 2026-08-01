"""安全 Schema 迁移闭环契约测试。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.managers.backup_manager import BackupManager
from core.managers.backup_snapshot import snapshot_sqlite
from core.managers.memory_engine import MemoryEngine
from core.managers.schema_manager import SchemaManager
from core.managers.schema_migration import (
    SchemaMigrationCoordinator,
    SchemaMigrationError,
)


def _create_legacy_database(
    db_path: Path,
    *,
    version: int = 7,
    canonical_count: int = 2,
) -> None:
    """创建缺少三列但带历史版本号的旧 canonical 数据库。"""

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
            """
        )
        connection.executemany(
            "INSERT INTO documents (text, metadata) VALUES (?, '{}')",
            [(f"legacy-{index}",) for index in range(canonical_count)],
        )
        connection.execute(
            """
            CREATE TABLE db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO db_version (
                version,
                description,
                migrated_at,
                migration_duration_seconds
            ) VALUES (?, 'legacy', '2026-01-01T00:00:00+00:00', 0.0)
            """,
            (version,),
        )
        connection.commit()
    finally:
        connection.close()


def _read_schema_snapshot(db_path: Path) -> tuple[int, int, set[str]]:
    """读取 canonical 数量、最高版本和 documents 列集合。"""

    connection = sqlite3.connect(db_path)
    try:
        canonical_count = int(
            connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        )
        version = int(
            connection.execute("SELECT MAX(version) FROM db_version").fetchone()[0]
        )
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        return canonical_count, version, columns
    finally:
        connection.close()


class _RecordingBackupManager:
    """记录迁移备份事件并返回可控结果。"""

    def __init__(
        self,
        events: list[str],
        *,
        result: dict[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """保存事件列表以及可选结果或异常。"""

        self.events = events
        self.result = result or {
            "name": "pre_migration_test",
            "directory": "unused",
        }
        self.error = error

    async def create_backup(self, kind: str = "manual") -> dict[str, object]:
        """记录备份类型，并按测试设置返回或抛出异常。"""

        self.events.append(f"backup:{kind}")
        if self.error is not None:
            raise self.error
        return self.result


class _SnapshotBackupManager:
    """为恢复测试创建独立 SQLite 快照。"""

    def __init__(self, db_path: Path, data_dir: Path) -> None:
        """保存 canonical 数据库和测试数据目录。"""

        self.db_path = db_path
        self.data_dir = data_dir

    async def create_backup(self, kind: str = "manual") -> dict[str, object]:
        """创建固定目录中的完整 SQLite 快照。"""

        assert kind == "pre_migration"
        backup_dir = self.data_dir / "backups" / "pre_migration_test"
        backup_dir.mkdir(parents=True, exist_ok=False)
        await asyncio.to_thread(
            snapshot_sqlite,
            self.db_path,
            backup_dir / self.db_path.name,
        )
        return {
            "name": "pre_migration_test",
            "directory": str(backup_dir),
        }


class _FailingConnection:
    """在首个字段迁移后阻断下一条变更 SQL。"""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """包装真实 aiosqlite 连接。"""

        self._connection = connection
        self._alter_seen = False

    async def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        """执行 SQL，并在首条 ALTER 后的回填阶段注入故障。"""

        normalized = " ".join(sql.split())
        if normalized.startswith("ALTER TABLE documents"):
            self._alter_seen = True
        elif self._alter_seen and normalized.startswith("UPDATE documents SET"):
            raise sqlite3.OperationalError("injected migration failure")
        return await self._connection.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """把未覆盖的连接操作委托给真实连接。"""

        return getattr(self._connection, name)


@pytest.mark.asyncio
async def test_old_schema_with_auto_migrate_disabled_does_not_mutate(
    tmp_path: Path,
) -> None:
    """旧库关闭自动迁移时不得执行 ALTER 或 UPDATE。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    connection = await aiosqlite.connect(db_path)
    statements: list[str] = []
    await connection.set_trace_callback(statements.append)
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=False,
        create_backup=True,
        backup_manager=_RecordingBackupManager([]),
    )

    with pytest.raises(SchemaMigrationError) as caught:
        await coordinator.run()
    await connection.close()

    assert caught.value.reason_code == "schema_migration_required"
    assert not any(
        "ALTER TABLE" in statement or statement.lstrip().startswith("UPDATE ")
        for statement in statements
    )
    assert _read_schema_snapshot(db_path) == (
        2,
        7,
        {"id", "text", "metadata"},
    )


@pytest.mark.asyncio
async def test_pre_migration_backup_precedes_first_change_sql(tmp_path: Path) -> None:
    """启用备份时，pre_migration 必须先于首条变更 SQL。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    connection = await aiosqlite.connect(db_path)
    events: list[str] = []

    def _trace(statement: str) -> None:
        """只记录事务和会改变 Schema/数据的语句。"""

        normalized = " ".join(statement.split())
        if normalized.startswith(("BEGIN", "ALTER", "UPDATE", "CREATE")):
            events.append(f"sql:{normalized}")

    await connection.set_trace_callback(_trace)
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(events),
    )

    result = await coordinator.run()
    await connection.close()

    assert events[0] == "backup:pre_migration"
    assert result.stage == "completed"
    assert result.migration_id == "schema-v7-to-v8"
    assert result.from_version == 7
    assert result.to_version == 8
    assert result.canonical_count == 2


@pytest.mark.asyncio
async def test_backup_failure_prevents_migration(tmp_path: Path) -> None:
    """迁移前备份失败时不得开始迁移。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    connection = await aiosqlite.connect(db_path)
    statements: list[str] = []
    await connection.set_trace_callback(statements.append)
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(
            [],
            error=RuntimeError("backup unavailable"),
        ),
    )

    with pytest.raises(SchemaMigrationError) as caught:
        await coordinator.run()
    await connection.close()

    assert caught.value.reason_code == "pre_migration_backup_failed"
    assert not any("ALTER TABLE" in statement for statement in statements)
    assert _read_schema_snapshot(db_path)[0:2] == (2, 7)


@pytest.mark.asyncio
async def test_mid_migration_failure_restores_canonical_and_version(
    tmp_path: Path,
) -> None:
    """迁移中断后必须从迁移前快照恢复数量、版本和旧结构。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path, canonical_count=3)
    connection = await aiosqlite.connect(db_path)
    failing_connection = _FailingConnection(connection)
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(failing_connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_SnapshotBackupManager(db_path, tmp_path),
    )

    with pytest.raises(SchemaMigrationError) as caught:
        await coordinator.run()

    assert caught.value.reason_code == "schema_migration_rolled_back"
    assert _read_schema_snapshot(db_path) == (
        3,
        7,
        {"id", "text", "metadata"},
    )
    state = coordinator.read_persisted_state()
    assert state["stage"] == "rolled_back"
    assert state["reason_code"] == "schema_migration_rolled_back"
    assert str(db_path) not in str(state)


@pytest.mark.asyncio
async def test_restore_failure_enters_persistent_blocked_state(tmp_path: Path) -> None:
    """快照恢复也失败时必须持久化 blocked 状态并停止启动。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    connection = await aiosqlite.connect(db_path)
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(_FailingConnection(connection)),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(
            [],
            result={
                "name": "pre_migration_missing",
                "directory": str(tmp_path / "missing-backup"),
            },
        ),
    )

    with pytest.raises(SchemaMigrationError) as caught:
        await coordinator.run()

    assert caught.value.reason_code == "schema_migration_restore_failed"
    assert coordinator.read_persisted_state()["stage"] == "blocked"


@pytest.mark.asyncio
async def test_fresh_database_creates_schema_without_backup(tmp_path: Path) -> None:
    """新数据库直接建当前结构，不创建无意义迁移备份。"""

    db_path = tmp_path / "memora.db"
    connection = await aiosqlite.connect(db_path)
    events: list[str] = []
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(events),
    )

    result = await coordinator.run()
    await connection.close()

    assert events == []
    assert result.stage == "fresh_created"
    assert _read_schema_snapshot(db_path)[0:2] == (0, 8)


@pytest.mark.asyncio
async def test_empty_provider_schema_is_treated_as_fresh_install(
    tmp_path: Path,
) -> None:
    """Provider 预建空 documents 表时仍按 fresh install 处理且不备份。"""

    db_path = tmp_path / "memora.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
    )
    connection.commit()
    connection.close()
    async_connection = await aiosqlite.connect(db_path)
    events: list[str] = []
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(async_connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(events),
    )

    result = await coordinator.run()
    await async_connection.close()

    assert result.stage == "fresh_created"
    assert events == []
    assert _read_schema_snapshot(db_path)[0:2] == (0, 8)


@pytest.mark.asyncio
async def test_backup_manager_accepts_pre_migration_kind(tmp_path: Path) -> None:
    """真实备份管理器发布 pre_migration 类型的校验后快照。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    manager = BackupManager(str(tmp_path))

    result = await manager.create_backup(kind="pre_migration")

    assert result["backup_type"] == "pre_migration"
    assert result["status"] == "ready"
    assert (Path(str(result["directory"])) / "memora.db").is_file()


@pytest.mark.asyncio
async def test_completed_migration_is_idempotent_on_retry(tmp_path: Path) -> None:
    """迁移成功后重复启动不得再次备份或追加版本记录。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    first_connection = await aiosqlite.connect(db_path)
    first = SchemaMigrationCoordinator(
        SchemaManager(first_connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=False,
    )
    await first.run()
    await first_connection.close()

    events: list[str] = []
    second_connection = await aiosqlite.connect(db_path)
    second = SchemaMigrationCoordinator(
        SchemaManager(second_connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(events),
    )
    result = await second.run()
    version_rows = int(
        (
            await (
                await second_connection.execute(
                    "SELECT COUNT(*) FROM db_version WHERE version = 8"
                )
            ).fetchone()
        )[0]
    )
    await second_connection.close()

    assert result.stage == "current"
    assert events == []
    assert version_rows == 1


@pytest.mark.asyncio
async def test_backup_cancellation_propagates_without_migration(tmp_path: Path) -> None:
    """迁移前备份被取消时传播取消且不执行迁移 SQL。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    connection = await aiosqlite.connect(db_path)
    statements: list[str] = []
    await connection.set_trace_callback(statements.append)
    coordinator = SchemaMigrationCoordinator(
        SchemaManager(connection),
        db_path=db_path,
        data_dir=tmp_path,
        auto_migrate=True,
        create_backup=True,
        backup_manager=_RecordingBackupManager(
            [],
            error=asyncio.CancelledError(),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await coordinator.run()
    await connection.close()

    assert not any("ALTER TABLE" in statement for statement in statements)


@pytest.mark.asyncio
async def test_memory_engine_stops_on_required_manual_migration(
    tmp_path: Path,
) -> None:
    """生产生命周期在 auto_migrate=false 时传播稳定阻断原因。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=MagicMock(),
        config={
            "data_dir": str(tmp_path),
            "migration_settings.auto_migrate": False,
            "migration_settings.create_backup": True,
        },
    )

    with pytest.raises(SchemaMigrationError) as caught:
        await engine.initialize()
    await engine.close()

    assert caught.value.reason_code == "schema_migration_required"
    assert _read_schema_snapshot(db_path) == (
        2,
        7,
        {"id", "text", "metadata"},
    )


@pytest.mark.asyncio
async def test_factory_migrates_before_opening_shared_database_stores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """工厂必须先完成 canonical 迁移，再打开 FAISS 与 Evolution 连接。"""

    from astrbot.core.provider.provider import Provider

    from core.initializer.component_factory import ComponentFactory

    events: list[str] = []
    database = MagicMock()
    database.initialize = AsyncMock(side_effect=lambda: events.append("faiss"))
    database.close = AsyncMock()
    engine = MagicMock()
    engine.initialize = AsyncMock(side_effect=lambda: events.append("schema"))
    engine.close = AsyncMock()
    evolution_store = MagicMock()
    evolution_store.initialize = AsyncMock(
        side_effect=lambda: events.append("evolution")
    )
    evolution_store.close = AsyncMock()
    conversation_store = MagicMock()
    conversation_store.initialize = AsyncMock(
        side_effect=RuntimeError("stop after migration order")
    )
    conversation_store.close = AsyncMock()
    monkeypatch.setattr(
        "core.initializer.component_factory.MemoryEngine",
        MagicMock(return_value=engine),
    )
    monkeypatch.setattr(
        "core.initializer.component_factory.MemoryEvolutionStore",
        MagicMock(return_value=evolution_store),
    )
    monkeypatch.setattr(
        "core.initializer.component_factory.ConversationStore",
        MagicMock(return_value=conversation_store),
    )
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "graph_memory.enabled": False,
    }.get(key, default)
    config.get_section.return_value = {}
    config.session_manager = {}
    factory = ComponentFactory(MagicMock(), config, str(tmp_path))
    faiss_checker = MagicMock()
    faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
    database_setup = MagicMock()
    database_setup.repair_message_counts = AsyncMock()
    database_setup.auto_rebuild_index_if_needed = AsyncMock()
    llm_provider = MagicMock(spec=Provider)
    llm_provider.text_chat = AsyncMock()

    with pytest.raises(RuntimeError, match="stop after migration order"):
        await factory.build_all(
            MagicMock(),
            llm_provider,
            MagicMock(return_value=database),
            faiss_checker,
            database_setup,
        )

    assert events == ["schema", "faiss", "evolution"]
