"""pre-canonical 记忆隔离 Page API 契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.page_api import PAGE_API_PREFIX, PluginPageApi
from core.review.memory_quality_gate import QuarantineApprovalPendingError
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
    assert (f"{PAGE_API_PREFIX}/review/quarantine/repair", ("POST",)) in routes
    metadata = {item["path"]: item for item in api.get_route_metadata()}
    repair_metadata = metadata[f"{PAGE_API_PREFIX}/review/quarantine/repair"]
    assert repair_metadata["auth"] == "admin"
    assert repair_metadata["write_guard"] is True


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


@pytest.mark.asyncio
async def test_repair_approve_forwards_token_and_canonical_id() -> None:
    """repair approve 必须把 token、canonical ID 和 revision 原样交给质量门。"""

    result = {
        "candidate_id": "qc-one",
        "revision": 4,
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
    gate.repair_approval = AsyncMock(return_value=result)
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
            "approval_token": "opaque-token",
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.repair_quarantine_approval()

    assert response["status"] == "ok"
    gate.repair_approval.assert_awaited_once_with(
        "qc-one",
        expected_revision=3,
        canonical_memory_id=91,
        approval_token="opaque-token",
        actor_id="dashboard",
    )
    assert "opaque-token" not in str(response)


@pytest.mark.asyncio
async def test_repair_approve_allows_durable_correlation_without_raw_token() -> None:
    """Durable repair can omit the raw token that does not survive restart."""

    result = {
        "candidate_id": "qc-one",
        "revision": 4,
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
    gate.repair_approval = AsyncMock(return_value=result)
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
            "candidate_correlation": {"candidate_id": "qc-one"},
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.repair_quarantine_approval()

    assert response["status"] == "ok"
    gate.repair_approval.assert_awaited_once_with(
        "qc-one",
        expected_revision=3,
        canonical_memory_id=91,
        approval_token=None,
        actor_id="dashboard",
    )


@pytest.mark.asyncio
async def test_repair_rejects_conflicting_candidate_correlation() -> None:
    """Request correlation cannot redirect repair to another candidate."""

    gate = MagicMock()
    gate.repair_approval = AsyncMock()
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
            "candidate_correlation": {"candidate_id": "qc-other"},
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.repair_quarantine_approval()

    assert response["status"] == "error"
    assert response["code"] == "quarantine_candidate_correlation_invalid"
    gate.repair_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_rejects_malformed_canonical_id_or_correlation() -> None:
    """Malformed canonical IDs and correlations fail closed."""

    gate = MagicMock()
    gate.repair_approval = AsyncMock()
    api = _api(gate)
    payloads = (
        {
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": True,
        },
        {
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
            "candidate_correlation": ["qc-one"],
        },
    )

    for payload in payloads:
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)
        with patch("core.api.quarantine_api.request", request_mock):
            response = await api.repair_quarantine_approval()
        assert response["status"] == "error"
        assert response["code"] in {
            "quarantine_canonical_id_required",
            "quarantine_candidate_correlation_invalid",
        }

    gate.repair_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_maps_gate_correlation_failure_to_stable_error() -> None:
    """Gate correlation failures use a stable non-leaking envelope."""

    gate = MagicMock()
    gate.repair_approval = AsyncMock(
        side_effect=ValueError("quarantine_candidate_correlation_invalid")
    )
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.repair_quarantine_approval()

    assert response["status"] == "error"
    assert response["code"] == "quarantine_candidate_correlation_invalid"
    assert "quarantine_candidate_correlation_invalid" not in response["message"]


@pytest.mark.asyncio
async def test_repair_keeps_write_guard_before_durable_call() -> None:
    """A maintenance block must prevent the durable repair call."""

    blocked = {"status": "error", "code": "maintenance_blocked"}
    gate = MagicMock()
    gate.repair_approval = AsyncMock()
    api = _api(gate)
    api._maintenance_write_guard = MagicMock(return_value=blocked)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.repair_quarantine_approval()

    assert response is blocked
    api._maintenance_write_guard.assert_called_once_with()
    gate.repair_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_keeps_revision_validation_and_token_type_guard() -> None:
    """Revision CAS input and optional token types remain fail-closed."""

    gate = MagicMock()
    gate.repair_approval = AsyncMock()
    api = _api(gate)
    payloads = (
        {
            "candidate_id": "qc-one",
            "expected_revision": True,
            "action": "approve",
            "canonical_memory_id": 91,
        },
        {
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "approve",
            "canonical_memory_id": 91,
            "approval_token": 123,
        },
    )

    for payload in payloads:
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)
        with patch("core.api.quarantine_api.request", request_mock):
            response = await api.repair_quarantine_approval()
        assert response["status"] == "error"
        assert response["code"] in {
            "quarantine_revision_required",
            "quarantine_approval_token_invalid",
        }

    gate.repair_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_block_requires_explicit_canonical_absence_confirmation() -> None:
    """repair block 必须显式确认 canonical 未写入。"""

    result = {
        "candidate_id": "qc-one",
        "revision": 4,
        "status": "blocked",
        "reason_codes": ["summary_quality_low"],
        "content": "用户喜欢咖啡。",
        "metadata": {},
        "importance": 0.8,
        "canonical_memory_id": None,
        "failure_reason": "canonical_write_not_found_confirmed",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    gate = MagicMock()
    gate.repair_blocked = AsyncMock(return_value=result)
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 3,
            "action": "block",
            "confirm_canonical_absent": True,
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.repair_quarantine_approval()

    assert response["status"] == "ok"
    gate.repair_blocked.assert_awaited_once_with(
        "qc-one",
        expected_revision=3,
        actor_id="dashboard",
        confirm_canonical_absent=True,
    )


@pytest.mark.asyncio
async def test_approval_finalize_pending_returns_repair_token() -> None:
    """canonical 已写入而 finalize 未完成时 API 返回脱离 candidate key 的 repair token。"""

    gate = MagicMock()
    gate.approve = AsyncMock(
        side_effect=QuarantineApprovalPendingError("qc-one", 2, "opaque-token")
    )
    api = _api(gate)
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={
            "candidate_id": "qc-one",
            "expected_revision": 1,
            "action": "approve",
        }
    )

    with patch("core.api.quarantine_api.request", request_mock):
        response = await api.apply_quarantine_action()

    assert response["status"] == "error"
    assert response["code"] == "quarantine_approval_pending"
    assert response["data"] == {
        "candidate_id": "qc-one",
        "revision": 2,
        "approval_token": "opaque-token",
    }
