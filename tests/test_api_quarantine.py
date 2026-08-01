"""pre-canonical 记忆隔离 Page API 契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.page_api import PAGE_API_PREFIX, PluginPageApi
from core.review.quarantine_store import MemoryQuarantineStore


def _mock_request(**args):
    """构造带查询参数和可替换 JSON 返回值的 Quart request。"""

    mocked = MagicMock()
    mocked.args = args
    mocked.get_json = AsyncMock(return_value=None)
    return mocked


def _api(gate) -> PluginPageApi:
    """构造发布了质量门且就绪的 Page API。"""

    initializer = SimpleNamespace(
        memory_quality_gate=gate,
        memory_engine=MagicMock(),
        conversation_manager=MagicMock(),
        index_validator=MagicMock(),
    )
    plugin = SimpleNamespace(
        initializer=initializer,
        context=MagicMock(),
        _ensure_plugin_ready=AsyncMock(return_value=(True, "")),
    )
    return PluginPageApi(plugin)


async def _staged_candidate(tmp_path) -> tuple[MemoryQuarantineStore, dict]:
    """创建带内部身份和证据指纹的隔离候选。"""

    store = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
    await store.initialize()
    candidate = await store.stage_candidate(
        candidate_key="quality-api-1",
        reason_codes=["summary_quality_low"],
        content="用户喜欢咖啡。",
        metadata={
            "source_evidence": [
                {
                    "message_index": 0,
                    "start": 0,
                    "end": 7,
                    "message_fingerprint": "secret-fingerprint",
                    "inferred": False,
                }
            ]
        },
        importance=0.7,
        session_id="private-session-id",
        persona_id="private-persona-id",
        source_window={"start_index": 0, "end_index": 1},
        is_group_chat=False,
    )
    return store, candidate


def test_quarantine_routes_are_registered() -> None:
    """列表、详情和动作路由必须在正式 Page API 注册。"""

    api = _api(MagicMock())
    api.register_routes()

    routes = {
        (call.args[0], tuple(call.args[2]))
        for call in api.plugin.context.register_web_api.call_args_list
    }
    assert (f"{PAGE_API_PREFIX}/review/quarantine", ("GET",)) in routes
    assert (f"{PAGE_API_PREFIX}/review/quarantine/detail", ("GET",)) in routes
    assert (f"{PAGE_API_PREFIX}/review/quarantine/action", ("POST",)) in routes


@pytest.mark.asyncio
async def test_list_and_detail_hide_internal_identity_and_fingerprint(tmp_path) -> None:
    """隔离 API 不得泄露会话、人格、候选键或来源指纹。"""

    store, candidate = await _staged_candidate(tmp_path)
    gate = SimpleNamespace(store=store)
    api = _api(gate)

    with patch("core.api.quarantine_api.request", _mock_request(limit="20")):
        listed = await api.list_quarantine_candidates()
    item = listed["data"]["items"][0]
    assert listed["status"] == "ok"
    assert "session_id" not in item
    assert "persona_id" not in item
    assert "candidate_key" not in item
    assert "message_fingerprint" not in item["source_evidence"][0]
    assert "content" not in item

    request_mock = _mock_request(candidate_id=candidate["candidate_id"])
    with patch("core.api.quarantine_api.request", request_mock):
        detail = await api.get_quarantine_candidate_detail()
    detail_item = detail["data"]["item"]
    assert detail_item["content"] == "用户喜欢咖啡。"
    assert "private-session-id" not in str(detail)
    assert "private-persona-id" not in str(detail)
    assert "secret-fingerprint" not in str(detail)


@pytest.mark.asyncio
async def test_approve_requires_revision_and_forwards_optional_correction() -> None:
    """批准动作必须携带 revision，并将可选修正交给质量门复核。"""

    result = {
        "candidate_id": "qc-one",
        "revision": 3,
        "status": "approved",
        "reason_codes": ["summary_quality_low"],
        "content": "用户喜欢手冲咖啡。",
        "metadata": {},
        "importance": 0.8,
        "canonical_memory_id": 91,
        "failure_reason": None,
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    gate = MagicMock()
    gate.approve = AsyncMock(return_value=result)
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 2,
            "action": "approve",
            "payload": {"content": "用户喜欢手冲咖啡。"},
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.apply_quarantine_action()

    assert response["status"] == "ok"
    assert response["data"]["candidate"]["canonical_memory_id"] == 91
    gate.approve.assert_awaited_once_with(
        "qc-one",
        expected_revision=2,
        actor_id="dashboard",
        content="用户喜欢手冲咖啡。",
    )


@pytest.mark.asyncio
async def test_action_returns_stable_revision_conflict_code() -> None:
    """并发处置冲突不得返回异常正文或覆盖新 revision。"""

    gate = MagicMock()
    gate.reject = AsyncMock(side_effect=ValueError("quarantine_revision_conflict"))
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 1,
            "action": "reject",
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.apply_quarantine_action()

    assert response["status"] == "error"
    assert response["code"] == "quarantine_revision_conflict"
