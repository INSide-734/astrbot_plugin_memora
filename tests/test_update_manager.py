"""更新服务的版本、镜像、校验和暂存测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.features.updates.application.manager import UpdateManager
from core.features.updates.domain import UpdateError


def _release_payload(version: str = "v1.1.0") -> dict[str, object]:
    """构造最小 GitHub latest release 响应。"""
    return {
        "tag_name": version,
        "body": "修复更新链路",
        "published_at": "2026-07-27T00:00:00Z",
        "assets": [
            {"name": "astrbot_plugin_memora-1.1.0-runtime.zip"},
            {"name": "SHA256SUMS.txt"},
        ],
    }


def test_version_and_mirror_url_rules(tmp_path: Path) -> None:
    """版本比较与镜像前缀应保持确定且拒绝危险协议。"""
    manager = UpdateManager(
        tmp_path,
        {"update_settings": {"mirror_url": "https://mirror.example/proxy"}},
        current_version="1.0.0",
    )

    assert manager._is_newer("1.0", "v1.0.1")
    assert not manager._is_newer("1.1.0", "v1.0.0")
    assert manager._build_url("https://github.com/example/release.zip") == (
        "https://mirror.example/proxy/https://github.com/example/release.zip"
    )

    unsafe = UpdateManager(
        tmp_path,
        {"update_settings": {"mirror_url": "file:///tmp/mirror"}},
    )
    with pytest.raises(UpdateError):
        unsafe._candidate_urls("https://example.test/file")


def test_reads_astrbot_http_and_socks_proxy(
    tmp_path: Path,
) -> None:
    """更新请求应读取 AstrBot 支持的 HTTP 与 SOCKS5 代理。"""
    manager = UpdateManager(
        tmp_path,
        host_config_source=lambda: {"http_proxy": " https://127.0.0.1:7890 "},
    )
    assert manager._astrbot_http_proxy() == "https://127.0.0.1:7890"

    socks_manager = UpdateManager(
        tmp_path,
        host_config_source=lambda: {"http_proxy": "socks5://127.0.0.1:7891"},
    )
    assert socks_manager._astrbot_http_proxy() == "socks5://127.0.0.1:7891"
    with socks_manager._http_client():
        pass

    broken_manager = UpdateManager(
        tmp_path,
        host_config_source=lambda: (_ for _ in ()).throw(RuntimeError("配置不可用")),
    )
    assert broken_manager._astrbot_http_proxy() == ""


@pytest.mark.asyncio
async def test_check_falls_back_from_mirror_to_official(tmp_path: Path) -> None:
    """镜像元数据不可用时应自动回退到官方 Release API。"""
    manager = UpdateManager(
        tmp_path,
        {"update_settings": {"mirror_url": "https://mirror.example"}},
        current_version="1.0.0",
    )
    seen: list[str] = []

    async def fake_request(url: str, max_bytes: int) -> bytes:
        """模拟镜像失败、官方成功。"""
        seen.append(url)
        if url.startswith("https://mirror.example"):
            raise UpdateError("mirror unavailable")
        return json.dumps(_release_payload()).encode("utf-8")

    manager._request_bytes = fake_request  # type: ignore[method-assign]
    release = await manager.check()

    assert release is not None
    assert release.version == "1.1.0"
    assert release.metadata_source == "official"
    assert seen[0].startswith("https://mirror.example/")
    assert seen[1].startswith("https://api.github.com/")


@pytest.mark.asyncio
async def test_download_verifies_checksum_and_atomically_stages(tmp_path: Path) -> None:
    """下载成功后只应发布已校验的包，并清理 part 文件。"""
    manager = UpdateManager(tmp_path, current_version="1.0.0")
    release = manager._build_release(_release_payload(), "official")
    assert release is not None
    payload = b"runtime zip bytes"
    digest = hashlib.sha256(payload).hexdigest()

    async def fake_request(url: str, max_bytes: int) -> bytes:
        """模拟 SHA256SUMS 下载。"""
        assert url == release.checksum_url
        return f"{digest} *{release.runtime_filename}\n".encode("utf-8")

    async def fake_download(url: str, path: Path) -> tuple[int, str]:
        """模拟 runtime 文件下载。"""
        path.write_bytes(payload)
        return len(payload), digest

    manager._request_bytes = fake_request  # type: ignore[method-assign]
    manager._download_to_file = fake_download  # type: ignore[method-assign]
    result = await manager.download(release)

    assert result.path == tmp_path / "updates" / release.runtime_filename
    assert result.path.read_bytes() == payload
    assert result.sha256 == digest
    assert not list(result.path.parent.glob("*.part"))


@pytest.mark.asyncio
async def test_download_rejects_checksum_mismatch_and_cleans_temp(
    tmp_path: Path,
) -> None:
    """摘要不匹配时不得替换旧包，临时文件必须删除。"""
    manager = UpdateManager(tmp_path, current_version="1.0.0")
    release = manager._build_release(_release_payload(), "official")
    assert release is not None
    destination = tmp_path / "updates" / release.runtime_filename
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")

    async def fake_request(url: str, max_bytes: int) -> bytes:
        """返回错误摘要。"""
        return ("0" * 64 + "  " + release.runtime_filename).encode("utf-8")

    async def fake_download(url: str, path: Path) -> tuple[int, str]:
        """写入不匹配的 runtime 内容。"""
        path.write_bytes(b"new")
        return 3, hashlib.sha256(b"new").hexdigest()

    manager._request_bytes = fake_request  # type: ignore[method-assign]
    manager._download_to_file = fake_download  # type: ignore[method-assign]
    with pytest.raises(UpdateError, match="SHA-256"):
        await manager.download(release)

    assert destination.read_bytes() == b"old"
    assert not list(destination.parent.glob("*.part"))
