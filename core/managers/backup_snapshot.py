"""备份快照、校验和原子文件写入辅助。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .backup_models import FileRole, SnapshotResult


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """计算文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sqlite(source: Path, target: Path) -> SnapshotResult:
    """使用 SQLite Online Backup API 创建可独立打开的数据库快照。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    source_connection = sqlite3.connect(str(source))
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
        quick_check = str(target_connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check.lower() != "ok":
            raise sqlite3.DatabaseError("sqlite quick_check failed")
    finally:
        target_connection.close()
        source_connection.close()
    return SnapshotResult(
        name=target.name,
        role=FileRole.CANONICAL,
        source=source,
        target=target,
        size_bytes=target.stat().st_size,
        sha256=sha256_file(target),
        quick_check=quick_check,
    )


def copy_regular_file(source: Path, target: Path, *, role: FileRole) -> SnapshotResult:
    """复制普通状态文件并返回校验结果。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return SnapshotResult(
        name=target.name,
        role=role,
        source=source,
        target=target,
        size_bytes=target.stat().st_size,
        sha256=sha256_file(target),
    )


def atomic_write_json(path: Path, value: Any) -> None:
    """以同目录临时文件和原子替换写入 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def ensure_free_space(root: Path, required_bytes: int) -> None:
    """确保目标目录所在文件系统有足够剩余空间。"""

    if required_bytes < 0:
        raise ValueError("required_bytes must be non-negative")
    usage = shutil.disk_usage(root)
    if usage.free < required_bytes:
        raise OSError("insufficient disk space")
