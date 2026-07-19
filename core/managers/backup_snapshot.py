"""备份快照与 manifest 文件辅助函数。"""

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
    """按块计算普通文件的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sqlite(
    source: Path,
    target: Path,
    *,
    role: FileRole = FileRole.CANONICAL,
) -> SnapshotResult:
    """使用 SQLite Online Backup API 生成可独立打开的数据库快照。

    源数据库只在 helper 内打开，目标数据库由 SQLite 自己创建，因此不会
    把活动数据库的 WAL/SHM 文件拼接成可能不一致的文件集合。
    """

    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        raise ValueError("snapshot source and target must differ")

    temporary_path: Path | None = None
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=target.parent, prefix=f"{target.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary_path = Path(handle.name)

    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(str(source))
        target_connection = sqlite3.connect(str(temporary_path))
        source_connection.backup(target_connection)
        quick_check = target_connection.execute("PRAGMA quick_check").fetchone()
        check_result = str(quick_check[0]) if quick_check else ""
        if check_result.lower() != "ok":
            raise sqlite3.DatabaseError("sqlite quick check failed")
        target_connection.commit()
        target_connection.close()
        target_connection = None
        os.replace(temporary_path, target)
        temporary_path = None
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if source_connection is not None:
            source_connection.close()
        if target_connection is not None:
            target_connection.close()

    return SnapshotResult(
        name=target.name,
        role=role,
        source=source,
        target=target,
        size_bytes=target.stat().st_size,
        sha256=sha256_file(target),
        quick_check=check_result,
    )


def copy_regular_file(
    source: Path,
    target: Path,
    *,
    role: FileRole = FileRole.OPERATIONAL,
) -> SnapshotResult:
    """复制普通状态文件并返回其大小与校验和。"""

    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        raise ValueError("copy source and target must differ")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f"{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    return SnapshotResult(
        name=target.name,
        role=role,
        source=source,
        target=target,
        size_bytes=target.stat().st_size,
        sha256=sha256_file(target),
    )


def atomic_write_json(path: Path, value: Any) -> None:
    """以同目录临时文件和 ``os.replace`` 原子写入 JSON。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def ensure_free_space(root: Path, required_bytes: int) -> None:
    """确保 ``root`` 所在文件系统有足够可用空间。"""

    if required_bytes < 0:
        raise ValueError("required_bytes must be non-negative")
    usage = shutil.disk_usage(Path(root))
    if usage.free < required_bytes:
        raise OSError("insufficient disk space")


__all__ = [
    "atomic_write_json",
    "copy_regular_file",
    "ensure_free_space",
    "sha256_file",
    "snapshot_sqlite",
]
