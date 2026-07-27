"""Memora runtime 发布包更新服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from astrbot.api import logger

from ..utils.version import PLUGIN_VERSION

_REPOSITORY = "INSide-734/astrbot_plugin_memora"
_RELEASE_API_URL = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_RELEASE_DOWNLOAD_BASE = f"https://github.com/{_REPOSITORY}/releases/download"
_CHECKSUM_FILENAME = "SHA256SUMS.txt"
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_VERSION_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-+].*)?$", re.IGNORECASE)
_CHECKSUM_PATTERN = re.compile(r"^(?P<digest>[0-9a-fA-F]{64})\s+[* ]?(?P<name>.+?)\s*$")
_STATE_FILENAME = "update-state.json"


class UpdateError(RuntimeError):
    """更新元数据、下载或校验失败。"""


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


class UpdateManager:
    """查询 GitHub Release 并把 runtime 包下载到插件数据目录。"""

    def __init__(
        self,
        data_dir: str | Path,
        config_source: Mapping[str, object] | object | None = None,
        *,
        current_version: str = PLUGIN_VERSION,
        host_config_source: object | None = None,
    ) -> None:
        """初始化更新服务。

        参数:
            data_dir: AstrBot 为插件分配的数据目录，更新包只会写入其 ``updates`` 子目录。
            config_source: ``ConfigManager`` 或配置字典；每次请求都会重新读取设置。
            current_version: 当前插件版本，默认来自 ``metadata.yaml``。
            host_config_source: AstrBot ``Context.get_config``，用于读取 ``http_proxy``。
        """
        self.data_dir = Path(data_dir)
        self.config_source = config_source
        self.current_version = str(current_version).strip()
        self.host_config_source = host_config_source

    def _settings(self) -> dict[str, object]:
        """读取并规范化更新配置。"""
        source = self.config_source
        if source is None:
            return {}
        get_section = getattr(source, "get_section", None)
        if callable(get_section):
            value = get_section("update_settings")
        elif isinstance(source, Mapping):
            value = source.get("update_settings", {})
        else:
            value = {}
        return dict(value) if isinstance(value, Mapping) else {}

    def is_enabled(self) -> bool:
        """返回是否允许执行更新请求。"""
        return bool(self._settings().get("enabled", True))

    def _timeout_seconds(self) -> int:
        """返回受边界约束的网络超时时间。"""
        value = self._settings().get("timeout_seconds", 30)
        try:
            return min(120, max(5, int(value)))
        except (TypeError, ValueError):
            return 30

    def _astrbot_http_proxy(self) -> str:
        """读取 AstrBot 的 HTTP、HTTPS 或 SOCKS5 代理设置。"""
        source = self.host_config_source
        if callable(source):
            try:
                source = source()
            except Exception:
                return ""
        if not isinstance(source, Mapping):
            return ""
        value = source.get("http_proxy", "")
        if not isinstance(value, str):
            return ""
        value = value.strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
            return ""
        return value

    def _http_client(self) -> httpx.Client:
        """创建遵循 AstrBot 代理设置的同步 HTTP 客户端。"""
        proxy = self._astrbot_http_proxy()
        return httpx.Client(
            proxy=proxy or None,
            follow_redirects=True,
            timeout=self._timeout_seconds(),
            trust_env=True,
        )

    def _mirror_prefix(self) -> str:
        """返回通过协议、主机和路径校验的镜像前缀。"""
        value = self._settings().get("mirror_url", "")
        if not isinstance(value, str):
            return ""
        value = value.strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
        ):
            raise UpdateError("镜像地址必须是无凭据、无查询参数的 HTTP(S) 地址。")
        return value.rstrip("/")

    def _build_url(self, official_url: str) -> str:
        """将官方 URL 按配置转换成镜像 URL。"""
        mirror = self._mirror_prefix()
        if not mirror:
            return official_url
        if "{url}" in mirror:
            return mirror.replace("{url}", official_url)
        return f"{mirror}/{official_url}"

    def _candidate_urls(self, official_url: str) -> list[tuple[str, str]]:
        """返回按镜像优先、官方回退排序的资源地址。"""
        mirror = self._mirror_prefix()
        candidates: list[tuple[str, str]] = []
        if mirror:
            candidates.append(("mirror", self._build_url(official_url)))
        candidates.append(("official", official_url))
        return candidates

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        """解析发布版本的数字部分。"""
        match = _VERSION_PATTERN.fullmatch(value.strip())
        if not match:
            return ()
        return tuple(int(part) for part in match.group(1).split("."))

    @classmethod
    def _is_newer(cls, current: str, candidate: str) -> bool:
        """判断候选版本是否严格高于当前版本。"""
        current_parts = cls._version_tuple(current)
        candidate_parts = cls._version_tuple(candidate)
        if not current_parts or not candidate_parts:
            return False
        width = max(len(current_parts), len(candidate_parts))
        return (current_parts + (0,) * (width - len(current_parts))) < (
            candidate_parts + (0,) * (width - len(candidate_parts))
        )

    @staticmethod
    def _normalize_version(tag: str) -> str:
        """从 release tag 中提取不带 ``v`` 前缀的版本号。"""
        match = _VERSION_PATTERN.fullmatch(tag.strip())
        if not match:
            raise UpdateError("Release 版本号格式无效。")
        return match.group(1)

    @staticmethod
    def _payload_to_release(payload: object) -> Mapping[str, object]:
        """校验 GitHub latest release JSON 的最小结构。"""
        if not isinstance(payload, Mapping):
            raise UpdateError("Release 返回内容格式无效。")
        return payload

    @staticmethod
    def _asset_names(payload: Mapping[str, object]) -> set[str]:
        """读取 release 资产名称，用于确认 runtime 与校验清单存在。"""
        assets = payload.get("assets", [])
        if not isinstance(assets, list):
            return set()
        names: set[str] = set()
        for asset in assets:
            if isinstance(asset, Mapping) and isinstance(asset.get("name"), str):
                names.add(asset["name"])
        return names

    def _build_release(
        self,
        payload: object,
        metadata_source: str,
    ) -> UpdateRelease | None:
        """把 Release JSON 转为可下载信息；当前已是最新时返回 ``None``。"""
        release = self._payload_to_release(payload)
        tag_value = release.get("tag_name")
        if not isinstance(tag_value, str) or not tag_value.strip():
            raise UpdateError("Release 缺少有效版本标签。")
        tag = tag_value.strip()
        version = self._normalize_version(tag)
        if release.get("draft") is True:
            return None
        if release.get("prerelease") is True:
            return None
        if not self._is_newer(self.current_version, version):
            return None

        runtime_filename = f"astrbot_plugin_memora-{version}-runtime.zip"
        asset_names = self._asset_names(release)
        if asset_names and (
            runtime_filename not in asset_names or _CHECKSUM_FILENAME not in asset_names
        ):
            raise UpdateError("Release 缺少 runtime 包或 SHA256SUMS.txt。")
        encoded_tag = quote(tag, safe="")
        runtime_url = f"{_RELEASE_DOWNLOAD_BASE}/{encoded_tag}/{runtime_filename}"
        checksum_url = f"{_RELEASE_DOWNLOAD_BASE}/{encoded_tag}/{_CHECKSUM_FILENAME}"
        notes = release.get("body")
        published_at = release.get("published_at")
        return UpdateRelease(
            tag=tag,
            version=version,
            current_version=self.current_version,
            published_at=str(published_at or ""),
            notes=str(notes or "")[:4000],
            runtime_filename=runtime_filename,
            runtime_url=runtime_url,
            checksum_url=checksum_url,
            metadata_source=metadata_source,
        )

    async def _request_bytes(self, url: str, max_bytes: int) -> bytes:
        """异步请求有限大小的 HTTP 内容。"""
        return await asyncio.to_thread(self._request_bytes_sync, url, max_bytes)

    def _request_bytes_sync(self, url: str, max_bytes: int) -> bytes:
        """在线程中执行阻塞 HTTP 请求。"""
        chunks: list[bytes] = []
        size = 0
        try:
            with self._http_client() as client:
                with client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Memora-Updater/1.0",
                    },
                ) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        raise UpdateError("远端响应超过安全大小限制。")
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise UpdateError("远端响应超过安全大小限制。")
                        chunks.append(chunk)
        except UpdateError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise UpdateError("无法访问更新源。") from exc
        return b"".join(chunks)

    async def _download_to_file(self, url: str, path: Path) -> tuple[int, str]:
        """异步下载文件并返回字节数与 SHA-256。"""
        return await asyncio.to_thread(self._download_to_file_sync, url, path)

    def _download_to_file_sync(self, url: str, path: Path) -> tuple[int, str]:
        """在线程中流式下载文件并限制最大大小。"""
        digest = hashlib.sha256()
        size = 0
        try:
            with self._http_client() as client:
                with client.stream(
                    "GET",
                    url,
                    headers={"User-Agent": "Memora-Updater/1.0"},
                ) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > _MAX_DOWNLOAD_BYTES:
                        raise UpdateError("更新包超过安全大小限制。")
                    with path.open("wb") as target:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            size += len(chunk)
                            if size > _MAX_DOWNLOAD_BYTES:
                                raise UpdateError("更新包超过安全大小限制。")
                            target.write(chunk)
                            digest.update(chunk)
        except UpdateError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise UpdateError("更新包下载失败。") from exc
        return size, digest.hexdigest()

    async def _fetch_release(self) -> tuple[object, str]:
        """获取 latest Release JSON，镜像失败时回退官方地址。"""
        last_error: UpdateError | None = None
        for source, url in self._candidate_urls(_RELEASE_API_URL):
            try:
                return json.loads(
                    (await self._request_bytes(url, _MAX_METADATA_BYTES)).decode(
                        "utf-8"
                    )
                ), source
            except asyncio.CancelledError:
                raise
            except (UpdateError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_error = (
                    exc
                    if isinstance(exc, UpdateError)
                    else UpdateError("Release 返回内容无效。")
                )
                logger.debug("更新源元数据获取失败 source=%s", source)
        raise last_error or UpdateError("无法获取更新信息。")

    async def check(self) -> UpdateRelease | None:
        """检查是否存在高于当前版本的 runtime 发布。"""
        if not self.is_enabled():
            raise UpdateError("插件更新功能已禁用。")
        payload, source = await self._fetch_release()
        return self._build_release(payload, source)

    @staticmethod
    def _parse_checksum(content: bytes, filename: str) -> str:
        """从 SHA256SUMS 文本中读取指定文件的摘要。"""
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UpdateError("SHA256SUMS.txt 编码无效。") from exc
        for line in text.splitlines():
            match = _CHECKSUM_PATTERN.fullmatch(line.strip())
            if match and Path(match.group("name")).name == filename:
                return match.group("digest").lower()
        raise UpdateError("SHA256SUMS.txt 中未找到 runtime 包摘要。")

    def _updates_dir(self) -> Path:
        """创建并返回受数据目录约束的更新暂存目录。"""
        if self.data_dir.is_symlink():
            raise UpdateError("插件数据目录不能是符号链接。")
        updates_dir = self.data_dir / "updates"
        if updates_dir.exists() and updates_dir.is_symlink():
            raise UpdateError("更新暂存目录不能是符号链接。")
        updates_dir.mkdir(parents=True, exist_ok=True)
        return updates_dir

    def ignored_version(self) -> str | None:
        """读取管理员忽略的版本号。"""
        try:
            state_path = self.data_dir / "updates" / _STATE_FILENAME
            if state_path.is_symlink():
                return None
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        value = payload.get("ignored_version") if isinstance(payload, Mapping) else None
        return str(value).strip() if isinstance(value, str) and value.strip() else None

    def ignore_version(self, version: str) -> str:
        """持久化要忽略的版本号并返回规范化版本。"""
        normalized = self._normalize_version(version)
        updates_dir = self._updates_dir()
        state_path = updates_dir / _STATE_FILENAME
        if state_path.is_symlink():
            raise UpdateError("更新状态文件不能是符号链接。")
        temporary_path = updates_dir / f".{_STATE_FILENAME}.{uuid.uuid4().hex}.part"
        try:
            temporary_path.write_text(
                json.dumps({"ignored_version": normalized}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary_path, state_path)
        except OSError as exc:
            raise UpdateError("无法保存忽略版本设置。") from exc
        finally:
            temporary_path.unlink(missing_ok=True)
        return normalized

    async def download(self, release: UpdateRelease | None = None) -> DownloadedUpdate:
        """下载最新 runtime 包，校验摘要后原子保存到 ``data/updates``。"""
        if not self.is_enabled():
            raise UpdateError("插件更新功能已禁用。")
        release = release or await self.check()
        if release is None:
            raise UpdateError("当前已是最新版本。")

        updates_dir = self._updates_dir()
        destination = updates_dir / release.runtime_filename
        if destination.is_symlink():
            raise UpdateError("更新目标不能是符号链接。")

        last_error: UpdateError | None = None
        for source, checksum_url in self._candidate_urls(release.checksum_url):
            temp_path = (
                updates_dir / f".{release.runtime_filename}.{uuid.uuid4().hex}.part"
            )
            try:
                checksum_content = await self._request_bytes(
                    checksum_url, _MAX_METADATA_BYTES
                )
                expected_digest = self._parse_checksum(
                    checksum_content, release.runtime_filename
                )
                runtime_url = (
                    self._build_url(release.runtime_url)
                    if source == "mirror"
                    else release.runtime_url
                )
                size, actual_digest = await self._download_to_file(
                    runtime_url, temp_path
                )
                if actual_digest.lower() != expected_digest:
                    raise UpdateError("更新包 SHA-256 校验失败。")
                os.replace(temp_path, destination)
                return DownloadedUpdate(
                    release=release,
                    path=destination,
                    size=size,
                    sha256=actual_digest.lower(),
                    download_source=source,
                )
            except asyncio.CancelledError:
                raise
            except UpdateError as exc:
                last_error = exc
                logger.debug("更新包获取失败 source=%s", source)
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug("更新临时文件清理失败")
        raise last_error or UpdateError("更新包下载失败。")


__all__ = ["DownloadedUpdate", "UpdateError", "UpdateManager", "UpdateRelease"]
