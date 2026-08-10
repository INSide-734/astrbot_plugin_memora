"""记忆再巩固候选复核 Page API 契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.reconsolidation.application.reconsolidation import (
    ReconsolidationManager,
)
from core.features.reconsolidation.infrastructure.reconsolidation_store import (
    ReconsolidationStore,
)
from core.page_api import PAGE_API_ALIAS_PREFIXES, PAGE_API_PREFIX, PluginPageApi


def _mock_request(
    *,
    args: dict[str, str] | None = None,
    payload: object = None,
) -> MagicMock:
    """构造带查询参数和 JSON 请求体的 Quart request 替身。"""

    mocked = MagicMock()
    mocked.args = args or {}
    mocked.get_json = AsyncMock(return_value=payload)
    return mocked


async def _stage_candidate(
    store: ReconsolidationStore,
    *,
    memory_id: int = 7,
    suffix: str = "one",
) -> dict:
    """向真实 Store 写入一条带敏感内部字段的 pending 候选。"""

    return await store.stage_candidate(
        memory_id=memory_id,
        source_revision=f"source-revision-{suffix}",
        old_content=f"旧正文-{suffix}",
        old_metadata={
            "session_id": f"secret-session-{suffix}",
            "privacy": "confidential",
        },
        proposed_content=f"新正文-{suffix}",
        change_summary=f"修正摘要-{suffix}",
        evidence_type="llm_revision",
    )


async def _api_with_candidates(
    tmp_path: Path,
    *,
    count: int = 1,
) -> tuple[PluginPageApi, ReconsolidationStore, SimpleNamespace, list[dict]]:
    """构造已就绪的页面 API、真实候选 Store 和可控 canonical 回调。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidates = [
        await _stage_candidate(store, memory_id=index + 1, suffix=str(index + 1))
        for index in range(count)
    ]
    canonical_state: dict[int, dict] = {}
    revision = 0

    async def update_memory(
        memory_id: int,
        updates: dict,
        *,
        expected_revision: str,
    ) -> bool:
        """模拟 canonical CAS，并保存可供 Manager 核验的新快照。"""

        nonlocal revision
        revision += 1
        current_revision = f"current-revision-{revision}"
        metadata = dict(updates.get("metadata") or {})
        metadata["updated_at"] = current_revision
        canonical_state[memory_id] = {
            "text": str(updates.get("content") or ""),
            "updated_at": current_revision,
            "metadata": metadata,
        }
        return True

    async def get_memory(memory_id: int) -> dict | None:
        """读取当前模拟 canonical 快照。"""

        return canonical_state.get(memory_id)

    engine = SimpleNamespace(
        reconsolidation_store=store,
        update_memory=AsyncMock(side_effect=update_memory),
        get_memory=AsyncMock(side_effect=get_memory),
    )
    engine.reconsolidation = ReconsolidationManager(
        store,
        get_memory_cb=engine.get_memory,
        update_memory_cb=engine.update_memory,
    )
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(
            memory_engine=engine,
            conversation_manager=None,
            index_validator=None,
        ),
        context=MagicMock(),
        _ensure_plugin_ready=AsyncMock(return_value=(True, "")),
    )
    return PluginPageApi(plugin), store, engine, candidates


@pytest.mark.asyncio
async def test_reconsolidation_routes_are_registered_under_every_prefix(
    tmp_path: Path,
) -> None:
    """列表、详情和动作必须在主前缀及兼容前缀保持相同方法。"""

    api, store, _, _ = await _api_with_candidates(tmp_path)

    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in api.plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        assert (f"{prefix}/review/reconsolidation", ("GET",)) in registered
        assert (f"{prefix}/review/reconsolidation/detail", ("GET",)) in registered
        assert (f"{prefix}/review/reconsolidation/action", ("POST",)) in registered
    metadata = {item["path"]: item for item in api.get_route_metadata()}
    assert metadata[f"{PAGE_API_PREFIX}/review/reconsolidation"]["risk"] == "read"
    assert (
        metadata[f"{PAGE_API_PREFIX}/review/reconsolidation/action"]["risk"] == "write"
    )


@pytest.mark.asyncio
async def test_list_uses_true_server_pagination_and_safe_fields(tmp_path: Path) -> None:
    """列表必须返回真实总数、消费 offset，且只输出 allowlist 字段。"""

    api, store, _, _ = await _api_with_candidates(tmp_path, count=3)
    ordered = await store.list_candidates(status="pending", limit=200)
    request_mock = _mock_request(
        args={"status": "pending", "offset": "1", "limit": "1"}
    )

    with patch("core.api.reconsolidation_review_api.request", request_mock):
        response = await api.list_reconsolidation_review_candidates()

    assert response["status"] == "ok"
    assert response["data"]["enabled"] is True
    assert response["data"]["total"] == 3
    assert response["data"]["offset"] == 1
    assert response["data"]["limit"] == 1
    assert response["data"]["items"][0]["candidate_id"] == ordered[1]["candidate_id"]
    assert set(response["data"]["items"][0]) == {
        "candidate_id",
        "status",
        "change_summary",
        "evidence_type",
        "reason_code",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_detail_exposes_content_diff_but_hides_internal_source_fields(
    tmp_path: Path,
) -> None:
    """详情只增加新旧正文和低敏动作，不返回 canonical 来源或 metadata。"""

    api, store, _, candidates = await _api_with_candidates(tmp_path)
    candidate_id = candidates[0]["candidate_id"]
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(args={"candidate_id": candidate_id}),
    ):
        response = await api.get_reconsolidation_review_candidate()

    assert response["status"] == "ok"
    candidate = response["data"]["candidate"]
    assert candidate["old_content"] == "旧正文-1"
    assert candidate["proposed_content"] == "新正文-1"
    assert set(candidate) == {
        "candidate_id",
        "status",
        "change_summary",
        "evidence_type",
        "reason_code",
        "created_at",
        "updated_at",
        "old_content",
        "proposed_content",
    }
    serialized = json.dumps(response, ensure_ascii=False)
    for forbidden in (
        "memory_id",
        "source_revision",
        "old_metadata",
        "secret-session-1",
        "confidential",
    ):
        assert forbidden not in serialized
    assert response["data"]["actions"] == [
        {
            "action": "stage",
            "reason_code": "proposed",
            "created_at": candidates[0]["created_at"],
        }
    ]


@pytest.mark.asyncio
async def test_approve_action_applies_candidate_with_source_revision_cas(
    tmp_path: Path,
) -> None:
    """批准动作必须通过 Manager 把来源 revision 交给 canonical 更新入口。"""

    api, store, engine, candidates = await _api_with_candidates(tmp_path)
    candidate_id = candidates[0]["candidate_id"]
    payload = {"candidate_id": candidate_id, "action": "approve"}

    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload=payload),
    ):
        response = await api.apply_reconsolidation_review_action()

    assert response == {
        "status": "ok",
        "data": {
            "candidate_id": candidate_id,
            "action": "approve",
            "status": "approved",
        },
    }
    assert engine.update_memory.await_args.kwargs["expected_revision"] == (
        "source-revision-1"
    )
    candidate = await store.get_candidate(candidate_id)
    assert candidate is not None
    assert candidate["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_action_persists_terminal_state_and_audit(tmp_path: Path) -> None:
    """拒绝动作必须持久化 rejected 终态并追加稳定动作审计。"""

    api, store, _, candidates = await _api_with_candidates(tmp_path)
    candidate_id = candidates[0]["candidate_id"]
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": candidate_id, "action": "reject"}),
    ):
        response = await api.apply_reconsolidation_review_action()

    assert response["status"] == "ok"
    assert response["data"]["status"] == "rejected"
    candidate = await store.get_candidate(candidate_id)
    assert candidate is not None
    assert candidate["reason_code"] == "manual_reject"
    actions = await store.list_actions(candidate_id)
    assert [(item["action"], item["reason_code"]) for item in actions] == [
        ("stage", "proposed"),
        ("reject", "manual_reject"),
    ]


@pytest.mark.asyncio
async def test_rollback_action_restores_old_content_with_current_revision_cas(
    tmp_path: Path,
) -> None:
    """回滚动作必须读取当前 revision，并把旧正文交给 canonical 更新入口。"""

    api, store, engine, candidates = await _api_with_candidates(tmp_path)
    candidate_id = candidates[0]["candidate_id"]
    applied = await engine.reconsolidation.apply_candidate(
        candidate_id,
        engine.update_memory,
    )
    assert applied["applied"] is True
    engine.update_memory.reset_mock()
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": candidate_id, "action": "rollback"}),
    ):
        response = await api.apply_reconsolidation_review_action()

    assert response["status"] == "ok"
    assert response["data"]["status"] == "rolled_back"
    assert engine.update_memory.await_args.args[1]["content"] == "旧正文-1"
    assert engine.update_memory.await_args.kwargs["expected_revision"] == (
        "current-revision-1"
    )
    candidate = await store.get_candidate(candidate_id)
    assert candidate is not None
    assert candidate["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_approve_returns_stable_source_revision_mismatch_code(
    tmp_path: Path,
) -> None:
    """canonical 来源已变化时批准必须拒绝候选并返回稳定错误码。"""

    api, store, engine, candidates = await _api_with_candidates(tmp_path)
    engine.update_memory.side_effect = None
    engine.update_memory.return_value = False
    engine.update_memory._last_write_reason_code = "source_revision_mismatch"
    candidate_id = candidates[0]["candidate_id"]
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": candidate_id, "action": "approve"}),
    ):
        response = await api.apply_reconsolidation_review_action()

    assert response["status"] == "error"
    assert response["code"] == "source_revision_mismatch"
    candidate = await store.get_candidate(candidate_id)
    assert candidate is not None
    assert candidate["status"] == "rejected"


@pytest.mark.asyncio
async def test_action_returns_not_found_and_status_conflict_codes(
    tmp_path: Path,
) -> None:
    """不存在和已处置候选必须分别返回稳定错误码。"""

    api, store, _, candidates = await _api_with_candidates(tmp_path)
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": "missing", "action": "reject"}),
    ):
        missing = await api.apply_reconsolidation_review_action()
    candidate_id = candidates[0]["candidate_id"]
    await store.transition(
        candidate_id,
        expected_status="pending",
        new_status="rejected",
        reason_code="manual_reject",
        action="reject",
    )
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": candidate_id, "action": "approve"}),
    ):
        conflict = await api.apply_reconsolidation_review_action()

    assert missing["code"] == "reconsolidation_review_not_found"
    assert conflict["code"] == "reconsolidation_review_conflict"


@pytest.mark.asyncio
async def test_action_honors_maintenance_write_guard(tmp_path: Path) -> None:
    """存在待恢复备份时，任何候选写动作都不得改变状态。"""

    api, store, _, candidates = await _api_with_candidates(tmp_path)
    backup_manager = MagicMock()
    backup_manager.get_maintenance_state.return_value = {"blocked": True}
    api.plugin._backup_manager = backup_manager
    candidate_id = candidates[0]["candidate_id"]
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": candidate_id, "action": "reject"}),
    ):
        response = await api.apply_reconsolidation_review_action()

    assert response["code"] == "maintenance_blocked"
    candidate = await store.get_candidate(candidate_id)
    assert candidate is not None
    assert candidate["status"] == "pending"


@pytest.mark.asyncio
async def test_handlers_report_disabled_list_state_and_reject_actions(
    tmp_path: Path,
) -> None:
    """功能关闭时列表应返回可渲染空态，写动作仍保持不可用。"""

    api, store, engine, _ = await _api_with_candidates(tmp_path)
    engine.reconsolidation_store = None
    engine.reconsolidation = None
    engine.config = {"reconsolidation.enabled": False}
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(),
    ):
        listed = await api.list_reconsolidation_review_candidates()
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(args={"candidate_id": "one"}),
    ):
        detailed = await api.get_reconsolidation_review_candidate()
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload={"candidate_id": "one", "action": "reject"}),
    ):
        acted = await api.apply_reconsolidation_review_action()

    assert listed == {
        "status": "ok",
        "data": {
            "enabled": False,
            "items": [],
            "total": 0,
            "offset": 0,
            "limit": 50,
        },
    }
    assert detailed["code"] == "reconsolidation_unavailable"
    assert acted["code"] == "reconsolidation_unavailable"


@pytest.mark.asyncio
async def test_list_reports_enabled_store_gap_as_unavailable(tmp_path: Path) -> None:
    """配置已启用但 Store 缺失时必须保留真实初始化故障。"""

    api, store, engine, _ = await _api_with_candidates(tmp_path)
    engine.reconsolidation_store = None
    engine.config = {"reconsolidation.enabled": True}

    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(),
    ):
        listed = await api.list_reconsolidation_review_candidates()

    assert listed["status"] == "error"
    assert listed["code"] == "reconsolidation_unavailable"
    assert listed["message"] == "再巩固候选 Store 未初始化"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"candidate_id": "one", "action": "unknown"},
        {"candidate_id": "one", "action": "reject", "unexpected": True},
    ],
)
async def test_action_rejects_invalid_or_unknown_payload_fields(
    tmp_path: Path,
    payload: object,
) -> None:
    """非法请求体、未知动作和未知字段必须在调用 Manager 前被拒绝。"""

    api, store, _, candidates = await _api_with_candidates(tmp_path)
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(payload=payload),
    ):
        response = await api.apply_reconsolidation_review_action()

    assert response["status"] == "error"
    assert response["code"] == "invalid_request"
    candidate = await store.get_candidate(candidates[0]["candidate_id"])
    assert candidate is not None
    assert candidate["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"status": "unknown"},
        {"limit": "0"},
        {"limit": "true"},
        {"offset": "-1"},
        {"offset": "false"},
    ],
)
async def test_list_rejects_invalid_query_parameters(
    tmp_path: Path,
    args: dict[str, str],
) -> None:
    """列表分页和状态参数必须严格校验，不能静默回退。"""

    api, store, _, _ = await _api_with_candidates(tmp_path)
    with patch(
        "core.api.reconsolidation_review_api.request",
        _mock_request(args=args),
    ):
        response = await api.list_reconsolidation_review_candidates()

    assert response["status"] == "error"
    assert response["code"] == "invalid_request"
