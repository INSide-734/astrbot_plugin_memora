"""质量闭环专用的持久化安装密钥。"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path

from ...backup.infrastructure.integrity import QUALITY_LOOP_HMAC_KEY_NAME

_KEY_BYTES = 32
_KEY_MODE = 0o600


def _validate_path(path: Path) -> None:
    """在打开前拒绝符号链接和特殊文件。"""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("quality_loop_hmac_secret_invalid")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != _KEY_MODE:
        raise RuntimeError("quality_loop_hmac_secret_invalid")


def _read_secret(path: Path) -> bytes:
    """读取已存在的密钥并只关闭文件描述符一次。"""
    _validate_path(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("quality_loop_hmac_secret_invalid")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != _KEY_MODE:
            raise RuntimeError("quality_loop_hmac_secret_invalid")
        value = os.read(descriptor, _KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(value) != _KEY_BYTES:
        raise RuntimeError("quality_loop_hmac_secret_invalid")
    return value


def _fsync_directory(path: Path) -> None:
    """尽力持久化新密钥的目录项。"""
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_secret(path: Path, candidate: bytes) -> bool:
    """完整写入临时文件后以独占硬链接发布，避免暴露半写文件。"""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, _KEY_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def load_or_create_quality_loop_secret(data_dir: str | Path) -> bytes:
    """读取或原子创建质量闭环专用的 32 字节 0600 密钥。"""
    path = Path(data_dir) / QUALITY_LOOP_HMAC_KEY_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                path.lstat()
            except FileNotFoundError:
                candidate = secrets.token_bytes(_KEY_BYTES)
                if _create_secret(path, candidate):
                    return candidate
                continue
            return _read_secret(path)
        raise RuntimeError("quality_loop_hmac_secret_unavailable")
    except RuntimeError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeError("quality_loop_hmac_secret_unavailable") from error


__all__ = [
    "QUALITY_LOOP_HMAC_KEY_NAME",
    "load_or_create_quality_loop_secret",
]
