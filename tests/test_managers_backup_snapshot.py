"""备份快照与恢复状态模型测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.managers.backup_models import BackupType, FileRole, RestoreStatus
from core.managers.backup_snapshot import atomic_write_json, snapshot_sqlite


def _make_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    connection.execute("INSERT INTO values_table(value) VALUES (?)", ("stable",))
    connection.commit()
    connection.close()


def test_backup_models_expose_stable_values() -> None:
    assert BackupType.MANUAL.value == "manual"
    assert BackupType.SCHEDULED.value == "scheduled"
    assert RestoreStatus.STAGED.value == "staged"
    assert FileRole.CANONICAL.value == "canonical"


def test_snapshot_sqlite_creates_self_contained_database(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "snapshot.db"
    _make_database(source)

    result = snapshot_sqlite(source, target)

    assert result.role is FileRole.CANONICAL
    assert result.quick_check == "ok"
    restored = sqlite3.connect(target)
    assert restored.execute("SELECT value FROM values_table").fetchone() == ("stable",)
    restored.close()


def test_atomic_json_write_never_leaves_partial_manifest(tmp_path: Path) -> None:
    destination = tmp_path / "manifest.json"

    atomic_write_json(destination, {"status": "ready"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "ready"}
    assert not list(tmp_path.glob("manifest.json.*.tmp"))
