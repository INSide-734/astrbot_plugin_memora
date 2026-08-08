"""P1b 平台资源 locator 与实时 Hub 契约测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.platform.resources import (
    PluginResourceLocator,
    ResourceNotAllowedError,
    ResourceNotFoundError,
)
from core.platform.transport import HubState, RealtimeHub, RealtimeHubClosed


def _locator(tmp_path: Path) -> PluginResourceLocator:
    """创建含最小 Schema 的插件资源目录。"""

    (tmp_path / "_conf_schema.json").write_text('{"enabled":{"type":"bool"}}')
    (tmp_path / "metadata.yaml").write_text("name: astrbot_plugin_memora\n")
    return PluginResourceLocator(tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        "/etc/passwd",
        "../_conf_schema.json",
        "core/../prompts/x.txt",
        "unknown.txt",
        "C:\\Windows\\win.ini",
    ],
)
def test_resource_locator_rejects_escape_and_unlisted_names(
    tmp_path: Path, name: str
) -> None:
    """locator 不允许任意绝对路径、穿越或未登记资源。"""

    with pytest.raises(ResourceNotAllowedError):
        _locator(tmp_path).read_bytes(name)


def test_resource_locator_prefers_valid_host_schema_then_bundle_source(
    tmp_path: Path,
) -> None:
    """Schema 顺序固定为 host 注入优先，再 bundle/source fallback。"""

    locator = _locator(tmp_path)
    host_schema = {"host": {"type": "string"}}
    assert locator.load_schema(host_schema) == host_schema
    host_schema["host"]["type"] = "mutated"
    assert locator.load_schema({"host": {"type": "string"}}) == {
        "host": {"type": "string"}
    }
    assert locator.load_schema(None) == {"enabled": {"type": "bool"}}


def test_resource_locator_uses_package_reader_when_source_is_missing(
    tmp_path: Path,
) -> None:
    """runtime bundle reader 可提供 source checkout 中不存在的白名单资源。"""

    locator = PluginResourceLocator(
        tmp_path,
        package_reader=lambda name: (
            b'{"runtime":{"type":"string"}}' if name == "_conf_schema.json" else None
        ),
    )
    assert locator.load_schema(None) == {"runtime": {"type": "string"}}


def test_resource_locator_reports_missing_allowlisted_resource(tmp_path: Path) -> None:
    """白名单资源缺失时返回稳定的资源错误，而不是泄露绝对路径。"""

    with pytest.raises(ResourceNotFoundError, match="resource_not_found"):
        _locator(tmp_path).read_bytes("core/prompts/missing.txt")


@pytest.mark.asyncio
async def test_hub_closes_waiting_subscription_with_sentinel() -> None:
    """关闭会唤醒已等待的订阅，并禁止后续发布或订阅。"""

    hub = RealtimeHub(queue_size=1)
    client_id, queue = hub.subscribe()
    waiting = asyncio.create_task(queue.get())
    await asyncio.sleep(0)

    await hub.close()

    assert await waiting is hub.CLOSE_SENTINEL
    assert hub.state is HubState.CLOSED
    assert hub.connected == 0
    assert await hub.publish("memory_created", {"count": 1}) is False
    with pytest.raises(RealtimeHubClosed):
        hub.subscribe()
    hub.unsubscribe(client_id)
    await hub.close()


@pytest.mark.asyncio
async def test_hub_removes_full_client_and_keeps_live_delivery() -> None:
    """满队列客户端被移除，其他客户端仍能收到 JSON 事件。"""

    hub = RealtimeHub(queue_size=1)
    dead_id, dead_queue = hub.subscribe()
    live_id, live_queue = hub.subscribe()
    dead_queue.put_nowait("blocking")

    delivered = await hub.publish("memory_created", {"doc_id": 42})

    assert delivered is True
    assert dead_id not in hub.queues
    assert live_id in hub.queues
    payload = json.loads(await live_queue.get())
    assert payload["event"] == "memory_created"
    assert payload["data"] == {"doc_id": 42}


@pytest.mark.asyncio
async def test_hub_close_evicts_buffered_events_before_sentinel() -> None:
    """关闭不让旧客户端继续收到缓存事件，首先读取到关闭 sentinel。"""

    hub = RealtimeHub(queue_size=2)
    _client_id, queue = hub.subscribe()
    await hub.publish("memory_created", {"doc_id": 42})

    await hub.drain()

    assert await queue.get() is hub.CLOSE_SENTINEL
