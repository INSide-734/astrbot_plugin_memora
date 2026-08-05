"""Memory Evolution 派生候选复核 Page API 测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    RelationType,
    RelationView,
)
from core.page_api import PAGE_API_PREFIX, PluginPageApi
from core.storage.memory_evolution_store import MemoryEvolutionStore


def _mock_request(*, args: dict[str, str] | None = None, payload=None):
    """构造带查询参数和 JSON 请求体的 Quart request 替身。"""

    request = MagicMock()
    request.args = args or {}
    request.get_json = AsyncMock(return_value=payload)
    return request


async def _api_with_candidate(tmp_path):
    """构造已就绪且包含一个高影响候选的页面 API。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(
        DerivedApplyPlan(
            relations=(
                RelationView(
                    "conflict-api",
                    11,
                    12,
                    RelationType.CONTRADICTS,
                    0.93,
                    "private:user",
                    "confidential",
                    DerivedState.CANDIDATE,
                    "source-revision-11",
                    "source-revision-12",
                ),
            ),
            source_revisions={11: "source-revision-11", 12: "source-revision-12"},
            origin_job_id="internal-job-id",
        )
    )
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(
            memory_engine=MagicMock(),
            memory_evolution_store=store,
            conversation_manager=None,
            index_validator=None,
        ),
        _ensure_plugin_ready=AsyncMock(return_value=(True, "")),
        context=MagicMock(),
    )
    return PluginPageApi(plugin), store


@pytest.mark.asyncio
async def test_derived_review_routes_are_registered(tmp_path) -> None:
    """主前缀必须注册派生候选列表、详情和动作路由。"""

    api, store = await _api_with_candidate(tmp_path)

    api.register_routes()

    paths = [call[0][0] for call in api.plugin.context.register_web_api.call_args_list]
    assert f"{PAGE_API_PREFIX}/review/derived" in paths
    assert f"{PAGE_API_PREFIX}/review/derived/detail" in paths
    assert f"{PAGE_API_PREFIX}/review/derived/action" in paths
    await store.close()


@pytest.mark.asyncio
async def test_list_and_detail_expose_only_review_safe_fields(tmp_path) -> None:
    """列表和详情不得暴露 source、scope、privacy、正文或 job 信息。"""

    api, store = await _api_with_candidate(tmp_path)
    with patch(
        "core.api.memory_evolution_review_api.request",
        _mock_request(),
    ):
        listed = await api.list_memory_evolution_review_candidates()
    with patch(
        "core.api.memory_evolution_review_api.request",
        _mock_request(args={"candidate_id": "conflict-api"}),
    ):
        detailed = await api.get_memory_evolution_review_candidate()

    assert listed["status"] == "ok"
    assert detailed["status"] == "ok"
    serialized = json.dumps(
        {"listed": listed, "detailed": detailed},
        ensure_ascii=False,
    )
    for forbidden in (
        "source_memory_id",
        "target_memory_id",
        "source_revision",
        "target_revision",
        "scope_key",
        "privacy_level",
        "origin_job_id",
        "internal-job-id",
    ):
        assert forbidden not in serialized
    await store.close()


@pytest.mark.asyncio
async def test_reject_action_uses_candidate_revision_and_returns_safe_result(
    tmp_path,
) -> None:
    """拒绝动作必须使用候选 revision CAS，响应保持最小字段集合。"""

    api, store = await _api_with_candidate(tmp_path)
    payload = {
        "candidate_id": "conflict-api",
        "action": "reject",
        "expected_revision": 1,
    }
    with patch(
        "core.api.memory_evolution_review_api.request",
        _mock_request(payload=payload),
    ):
        response = await api.apply_memory_evolution_review_action()

    assert response == {
        "status": "ok",
        "data": {
            "candidate_id": "conflict-api",
            "candidate_revision": 2,
            "state": DerivedState.REJECTED.value,
            "action": "reject",
        },
    }
    await store.close()


@pytest.mark.asyncio
async def test_action_rejects_stale_candidate_revision(tmp_path) -> None:
    """过期候选 revision 必须返回稳定冲突码，不能覆盖新状态。"""

    api, store = await _api_with_candidate(tmp_path)
    await store.review_relation_candidate(
        "conflict-api",
        action="reject",
        expected_revision=1,
    )
    payload = {
        "candidate_id": "conflict-api",
        "action": "replay",
        "expected_revision": 1,
    }
    with patch(
        "core.api.memory_evolution_review_api.request",
        _mock_request(payload=payload),
    ):
        response = await api.apply_memory_evolution_review_action()

    assert response["status"] == "error"
    assert response["code"] == "derived_review_conflict"
    candidate = await store.get_relation_review_candidate("conflict-api")
    assert candidate is not None
    assert candidate["state"] == DerivedState.REJECTED.value
    assert candidate["revision"] == 2
    await store.close()
