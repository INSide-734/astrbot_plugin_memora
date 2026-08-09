"""图谱 API 稳定身份展示投影测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.identity import StoredIdentity


def _stored_identity(display_name: str) -> StoredIdentity:
    """构造身份目录返回的当前 QQ 名称。"""

    return StoredIdentity(
        identity_namespace="qq",
        stable_user_id="10001",
        canonical_user_id="10001",
        global_name=display_name,
        scope_type=None,
        scope_id=None,
        scope_name=None,
        display_name=display_name,
        first_seen_at=1.0,
        last_seen_at=2.0,
        global_name_updated_at=2.0,
        scope_name_updated_at=None,
    )


def _stable_person_node() -> dict[str, object]:
    """构造图存储返回的严格稳定 QQ 人物节点。"""

    return {
        "id": 7,
        "key": "person:qq:10001",
        "type": "person",
        "label": "QQ:10001",
        "canonical_value": "qq:10001",
        "weight": 1.0,
    }


def _projection_harness():
    """绑定待测图谱身份投影方法。"""

    from core.api.graph_api import GraphApiMixin

    class Harness:
        _enrich_graph_identity_nodes = GraphApiMixin._enrich_graph_identity_nodes

    return Harness()


@pytest.mark.asyncio
async def test_graph_identity_projection_uses_latest_name_without_mutating_snapshot() -> (
    None
):
    """同一 QQ 改名后仅更新响应展示，原节点、节点 ID 与 QQ 保持不变。"""

    harness = _projection_harness()
    runtime = SimpleNamespace(get_identity=AsyncMock())
    source_node = _stable_person_node()
    snapshot = {"nodes": [source_node], "edges": []}

    runtime.get_identity.return_value = _stored_identity("旧昵称")
    first = await harness._enrich_graph_identity_nodes(snapshot, runtime)
    runtime.get_identity.return_value = _stored_identity("新昵称")
    second = await harness._enrich_graph_identity_nodes(snapshot, runtime)

    assert first["nodes"][0] == {
        **source_node,
        "label": "旧昵称",
        "identity_namespace": "qq",
        "stable_user_id": "10001",
        "display_name": "旧昵称",
    }
    assert second["nodes"][0]["id"] == 7
    assert second["nodes"][0]["stable_user_id"] == "10001"
    assert second["nodes"][0]["label"] == "新昵称"
    assert second["nodes"][0]["display_name"] == "新昵称"
    assert snapshot == {"nodes": [source_node], "edges": []}


@pytest.mark.asyncio
async def test_graph_identity_projection_rejects_legacy_and_inconsistent_nodes() -> (
    None
):
    """旧名称、非人物及稳定字段不一致的节点不得猜测身份。"""

    harness = _projection_harness()
    runtime = SimpleNamespace(
        get_identity=AsyncMock(return_value=_stored_identity("昵称"))
    )
    nodes = [
        {"id": 1, "type": "person", "label": "昵称"},
        {
            **_stable_person_node(),
            "id": 2,
            "key": "person:qq:10002",
        },
        {
            **_stable_person_node(),
            "id": 3,
            "canonical_value": "qq:10002",
        },
        {
            **_stable_person_node(),
            "id": 4,
            "type": "topic",
        },
        {
            **_stable_person_node(),
            "id": 5,
            "label": "QQ:０１",
            "canonical_value": "qq:０１",
            "key": "person:qq:０１",
        },
    ]

    projected = await harness._enrich_graph_identity_nodes({"nodes": nodes}, runtime)

    assert projected["nodes"] == nodes
    runtime.get_identity.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_identity_projection_degrades_errors_and_propagates_cancellation() -> (
    None
):
    """身份读取普通失败保留图节点，任务取消不能被降级吞掉。"""

    harness = _projection_harness()
    node = _stable_person_node()
    runtime = SimpleNamespace(
        get_identity=AsyncMock(side_effect=RuntimeError("private"))
    )

    projected = await harness._enrich_graph_identity_nodes({"nodes": [node]}, runtime)
    assert projected["nodes"] == [node]

    runtime.get_identity.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await harness._enrich_graph_identity_nodes({"nodes": [node]}, runtime)


@pytest.mark.asyncio
async def test_graph_overview_projects_identity_runtime_into_response() -> None:
    """图谱概览端点把共享身份运行时的当前昵称投影到响应节点。"""

    from core.api.graph_api import GraphApiMixin

    graph_store = MagicMock()
    graph_store.get_graph_snapshot = AsyncMock(
        return_value={"nodes": [_stable_person_node()], "edges": []}
    )
    identity_runtime = SimpleNamespace(
        get_identity=AsyncMock(return_value=_stored_identity("当前昵称"))
    )
    engine = MagicMock()
    engine.get_statistics = AsyncMock(return_value={})

    class Harness:
        get_graph_overview = GraphApiMixin.get_graph_overview

        async def _ensure_plugin_ready(self):
            """返回图谱概览所需的最小就绪组件。"""

            return {
                "memory_engine": engine,
                "identity_runtime": identity_runtime,
            }, None

        def _get_graph_store(self, _engine):
            """返回测试图存储。"""

            return graph_store

        def _build_graph_view_payload(self, snapshot, _stats, **_kwargs):
            """保留端点传入的节点以验证身份投影顺序。"""

            return {"nodes": snapshot["nodes"]}

        @staticmethod
        def _ok(data):
            """构造最小成功响应。"""

            return {"status": "ok", "data": data}

        @staticmethod
        def _error(message):
            """构造最小错误响应。"""

            return {"status": "error", "message": message}

    request = SimpleNamespace(args={})
    with patch("core.api.graph_api.request", request):
        result = await Harness().get_graph_overview()

    assert result["data"]["nodes"][0]["label"] == "当前昵称"
    assert result["data"]["nodes"][0]["stable_user_id"] == "10001"
    identity_runtime.get_identity.assert_awaited_once_with("qq", "10001")
