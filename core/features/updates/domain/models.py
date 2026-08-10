"""插件更新 feature 的领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UpdateRelease:
    """可下载的最新 runtime 发布信息。"""

    tag: str
    version: str
    current_version: str
    published_at: str
    notes: str
    runtime_filename: str
    runtime_url: str
    checksum_url: str
    metadata_source: str


@dataclass(frozen=True, slots=True)
class DownloadedUpdate:
    """已通过 SHA-256 校验并安全落盘的更新包。"""

    release: UpdateRelease
    path: Path
    size: int
    sha256: str
    download_source: str


__all__ = ["DownloadedUpdate", "UpdateRelease"]
