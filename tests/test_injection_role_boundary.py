"""逐事实来源门在候选选择和三类模型传输中的行为契约。"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from core.features.injection.application.executor import (
    InjectionExecutionContext,
    InjectionExecutor,
)
from core.features.injection.application.injection_adapter import InjectionAdapter
from core.features.injection.application.memory_formatter import (
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
)
from core.features.injection.application.selection import select_candidates
from core.features.injection.domain.models import DeliveryMode, InjectionOutcome
from core.features.quality.application.gate_disposition_filter import filter_mark_write
from core.features.recall.application.recall_routing import RecallRoutingMixin
from core.features.retrieval.provider_privacy_prefilter import (
    ProviderPrivacyContext,
    ProviderPrivacyPrefilter,
    filter_confidential_from_group,
)
from core.features.retrieval.rrf_fusion import HybridResult
from tests.injection_executor_support import (
    decision_stub,
    delivered_part_text,
    request_stub,
    resolved_reference,
    tool_capable_provider,
    with_user_evidence,
)

_DELIVERIES = (
    DeliveryMode.EXTRA_USER_CONTENT,
    DeliveryMode.USER_MESSAGE_BEFORE,
    DeliveryMode.USER_MESSAGE_AFTER,
    DeliveryMode.FAKE_TOOL_CALL,
    DeliveryMode.FAKE_TOOL_CALL_DEEPSEEK_V4,
)


def _candidate(content: str, *, role: str = "user", score: float = 1.0) -> dict:
    candidate = with_user_evidence(
        {"id": 17, "content": content, "score": score, "metadata": {}}
    )
    candidate["metadata"]["fact_source_evidence"] = [
        [resolved_reference(content, role=role)]
    ]
    candidate["metadata"]["source_evidence"] = [resolved_reference(content, role=role)]
    return candidate


def _context(memories: list[dict]) -> InjectionExecutionContext:
    return InjectionExecutionContext(
        query="当前问题",
        memories=memories,
        allowed_source_roles=frozenset({"user"}),
        provider=tool_capable_provider(),
        cognitive_budget_chars=0,
        prospective_budget_chars=0,
    )


def _payload(request: Any, delivery: DeliveryMode) -> str:
    if delivery is DeliveryMode.EXTRA_USER_CONTENT:
        return delivered_part_text() if request.extra_user_content_parts else ""
    if delivery in (DeliveryMode.USER_MESSAGE_BEFORE, DeliveryMode.USER_MESSAGE_AFTER):
        return str(request.prompt or "")
    return "\n".join(str(item.get("content") or "") for item in request.contexts)


@pytest.mark.parametrize(
    "roles", [frozenset({"assistant"}), frozenset({"user", "assistant"}), {"user"}]
)
def test_model_context_rejects_non_user_or_mutable_policy(roles) -> None:
    with pytest.raises(ValueError, match="injection_source_roles_invalid"):
        InjectionExecutionContext(
            query="当前问题", memories=[], allowed_source_roles=roles
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", _DELIVERIES)
async def test_assistant_only_candidate_never_mutates_model_request(delivery) -> None:
    request = request_stub()
    original = (request.prompt, deepcopy(request.contexts), request.system_prompt)
    result = await InjectionExecutor(InjectionAdapter()).execute(
        request,
        decision_stub(delivery),
        _context([_candidate("助手虚构事实", role="assistant")]),
    )

    assert result.outcome is InjectionOutcome.EMPTY
    assert result.selected_count == 0
    assert result.dropped_count == 1
    assert request.extra_user_content_parts == []
    assert (request.prompt, request.contexts, request.system_prompt) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", _DELIVERIES)
async def test_rebuilt_and_assistant_candidates_never_displace_user_candidate(
    delivery,
) -> None:
    """高分助手候选与重建后的混合候选都不得挤掉低分完全支持的候选。"""

    user_fact = "用户明确喜欢低糖饮料"
    rejected_fact = "ASSISTANT_UNCONFIRMED_PLAN"
    mixed = _candidate(f"{user_fact}；{rejected_fact}", score=10_000)
    mixed["metadata"].update(
        key_facts=[user_fact, rejected_fact],
        fact_source_evidence=[
            [resolved_reference(user_fact)],
            [resolved_reference(rejected_fact, role="assistant")],
        ],
        source_evidence=[resolved_reference(user_fact)],
        intent_match=1.0,
        topics=[rejected_fact],
        participants=[rejected_fact],
        derived_projections=[
            {"type": "episode_summary", "summary": rejected_fact, "confidence": 0.9}
        ],
        scope_key="PRIVATE_BOUNDARY",
        revision_token="PRIVATE_REVISION",
        privacy_level="confidential",
        identity={"sender_id": "PRIVATE_IDENTITY"},
    )
    mixed["_matched_facets"] = {"event": 1.0}
    assistant = _candidate("ASSISTANT_HIGH_SCORE", role="assistant", score=20_000)
    assistant["id"] = 18
    supported = _candidate("低分完整用户事实", score=0.01)
    supported["id"] = 19
    inputs = [assistant, mixed, supported]
    snapshot = deepcopy(inputs)
    request = request_stub()
    result = await InjectionExecutor(InjectionAdapter()).execute(
        request, replace(decision_stub(delivery), max_memories=1), _context(inputs)
    )

    payload = _payload(request, delivery)
    assert result.outcome is InjectionOutcome.INJECTED
    assert result.selected_count == 1
    assert result.dropped_count == 2
    assert "低分完整用户事实" in payload
    for forbidden in (
        user_fact,
        rejected_fact,
        "ASSISTANT_HIGH_SCORE",
        "PRIVATE_BOUNDARY",
        "PRIVATE_REVISION",
        "PRIVATE_IDENTITY",
        "source_evidence",
        "fact_source_evidence",
        "message_id",
        "message_seq",
        "message_fingerprint",
        "scope_key",
        "privacy_level",
        "revision_token",
    ):
        assert forbidden not in payload
    assert request.system_prompt == "stable-system-prefix"
    assert inputs == snapshot


@pytest.mark.parametrize(
    "defect",
    [
        "missing_facts",
        "missing_evidence",
        "misaligned",
        "malformed_group",
        "legacy",
        "unknown_role",
        "missing_sequence",
        "boolean_id",
        "invalid_range",
        "invalid_fingerprint",
        "mixed_malformed",
        "missing_group",
    ],
)
def test_invalid_fact_evidence_cannot_change_user_candidate_ranking(defect) -> None:
    invalid = _candidate("不可信的高分候选", score=10_000)
    invalid["id"] = 18
    metadata = invalid["metadata"]
    reference = metadata["fact_source_evidence"][0][0]
    if defect == "missing_facts":
        metadata.pop("key_facts")
    elif defect == "missing_evidence":
        metadata.pop("fact_source_evidence")
    elif defect == "misaligned":
        metadata["fact_source_evidence"].append([resolved_reference("多余证据")])
    elif defect == "malformed_group":
        metadata["fact_source_evidence"] = [reference]
    elif defect == "legacy":
        metadata["fact_source_evidence"] = [
            [{"message_index": 0, "start": 0, "end": 1}]
        ]
    elif defect == "unknown_role":
        reference["role"] = "owner"
    elif defect == "missing_sequence":
        reference["message_seq"] = None
    elif defect == "boolean_id":
        reference["message_id"] = True
    elif defect == "invalid_range":
        reference["end"] = reference["start"]
    elif defect == "invalid_fingerprint":
        reference["message_fingerprint"] = "not-a-sha256"
    elif defect == "mixed_malformed":
        metadata["key_facts"].append("另一条有用户来源的事实")
        metadata["fact_source_evidence"].append(
            [resolved_reference("另一条有用户来源的事实")]
        )
        reference["message_id"] = None
    elif defect == "missing_group":
        metadata["key_facts"].append("缺少来源的事实")
        metadata["fact_source_evidence"].append([])
    trusted = _candidate("用户来源事实", score=0.1)

    selected, dropped = select_candidates(
        replace(decision_stub(), max_memories=1),
        [invalid, trusted],
        allowed_source_roles=frozenset({"user"}),
    )

    assert [item["content"] for item in selected] == ["用户来源事实"]
    assert [item["id"] for item in dropped] == [18]


@pytest.mark.parametrize("summary_role", ["user", "assistant"])
def test_summary_requires_its_own_user_evidence(summary_role) -> None:
    candidate = _candidate("独立摘要", role=summary_role)
    candidate["metadata"].update(
        key_facts=["事实甲", "事实乙"],
        fact_source_evidence=[
            [
                resolved_reference("事实甲"),
                resolved_reference("事实甲", role="assistant"),
            ],
            [resolved_reference("事实乙")],
        ],
        intent_match=1.0,
        temporal_value=1.0,
        source_value=1.0,
    )
    candidate["_matched_facets"] = {"event": 1.0}
    selected, dropped = select_candidates(
        decision_stub(), [candidate], allowed_source_roles=frozenset({"user"})
    )

    if summary_role == "user":
        assert selected[0]["content"] == "独立摘要"
        # 记录的事实不在正文中：正文路径保留，旧事实不得进入载荷。
        assert selected[0]["metadata"]["key_facts"] == []
        assert selected[0]["metadata"]["intent_match"] == 1.0
        assert selected[0]["_matched_facets"] == {"event": 1.0}
        payload = format_memories_for_injection(selected)
        assert "独立摘要" in payload
        assert "事实甲" not in payload
        assert "事实乙" not in payload
        return

    # 摘要没有自己的用户证据、记录的事实又不在正文中：正文不能按事实归属，
    # 事实也不能重建载荷，候选整体 fail closed，且不产生按旧事实重建的副本。
    assert selected == []
    assert dropped == [candidate]


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", _DELIVERIES)
async def test_misaligned_fact_metadata_falls_back_to_canonical_body(
    delivery, caplog
) -> None:
    """正文改写后残留的旧事实不得进入注入载荷，正文路径保留且回落可观察。"""

    stale_fact = "用户已经搬到上海"
    candidate = _candidate("用户现在住在杭州")
    candidate["metadata"].update(
        key_facts=[stale_fact],
        fact_source_evidence=[[resolved_reference(stale_fact)]],
    )
    caplog.set_level(logging.DEBUG)
    request = request_stub()
    result = await InjectionExecutor(InjectionAdapter()).execute(
        request, decision_stub(delivery), _context([candidate])
    )

    assert result.outcome is InjectionOutcome.INJECTED
    assert (result.selected_count, result.dropped_count) == (1, 0)
    payload = _payload(request, delivery)
    assert "用户现在住在杭州" in payload
    assert stale_fact not in payload
    assert "事实元数据与当前正文不一致" in caplog.text


def test_final_formatter_inputs_strip_all_internal_fields_and_preserve_filters() -> (
    None
):
    candidate = _candidate("允许的用户事实")
    canary = "INTERNAL_SOURCE_CANARY"
    candidate["metadata"].update(
        message_id=canary,
        message_seq=canary,
        message_fingerprint=canary,
        identity=canary,
        sender_id=canary,
        scope_key=canary,
        privacy_level=canary,
        revision_token=canary,
        source_mapping={"source": canary},
        participant_identity_sources={"source": canary},
        session_id=canary,
        persona_id=canary,
        derived_projections=[
            {
                "type": "episode_summary",
                "summary": "允许的派生摘要",
                "confidence": 0.8,
                "source_memory_ids": [canary],
                "revision_token": canary,
            }
        ],
    )
    normalized = RecallRoutingMixin._safe_candidates(
        [
            SimpleNamespace(
                doc_id=17,
                content=candidate["content"],
                final_score=1.0,
                metadata=candidate["metadata"],
            )
        ]
    )
    selected, _ = select_candidates(
        decision_stub(), normalized, allowed_source_roles=frozenset({"user"})
    )
    fake = format_memories_for_fake_tool_call(
        selected, "当前问题", session_filtered=False, persona_filtered=True
    )
    outputs = [
        format_memories_for_injection(selected),
        json.dumps(fake, ensure_ascii=False),
        format_memories_for_fake_tool_call_deepseek_v4(selected, "当前问题"),
    ]

    assert canary not in json.dumps(selected, ensure_ascii=False)
    assert selected[0]["id"] == 17
    for payload in outputs:
        assert "允许的用户事实" in payload
        assert "允许的派生摘要" in payload
        assert canary not in payload
        for key in (
            "source_evidence",
            "fact_source_evidence",
            "message_fingerprint",
            "scope_key",
            "privacy_level",
            "revision_token",
            "source_mapping",
        ):
            assert key not in payload
    fake_payload = json.loads(fake[1]["content"])
    assert fake_payload["applied_filters"] == {
        "session_filtered": False,
        "persona_filtered": True,
    }
    fake_result = fake_payload["results"][0]
    assert fake_result["content"] == "允许的用户事实"
    for key in (
        "id",
        "session_id",
        "persona_id",
        "create_time",
        "last_access_time",
    ):
        assert key not in fake_result


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", _DELIVERIES)
async def test_existing_visibility_boundaries_compose_with_role_policy(
    delivery,
) -> None:
    candidates = []
    cases = [
        ("允许事实", {"session_id": "current"}),
        ("机密事实", {"session_id": "current", "privacy_level": "confidential"}),
        ("标记事实", {"session_id": "current", "gate_disposition": "mark_write"}),
        ("其他会话", {"session_id": "other"}),
        ("其他人格", {"persona_id": "other"}),
        ("助手来源", {"session_id": "current", "role": "assistant"}),
    ]
    for index, (text, boundary) in enumerate(cases, 1):
        candidate = _candidate(text, role=boundary.get("role", "user"))
        candidate["metadata"].update(privacy_level="shared")
        candidate["metadata"].update(boundary)
        candidates.append(
            HybridResult(
                doc_id=index,
                final_score=1.0,
                rrf_score=1.0,
                bm25_score=None,
                vector_score=None,
                content=text,
                metadata=candidate["metadata"],
            )
        )
    provider_candidates = (
        ProviderPrivacyPrefilter()
        .filter(
            candidates, ProviderPrivacyContext(chat_type="group", scope_key="current")
        )
        .candidates
    )
    assert [item.doc_id for item in provider_candidates] == [1, 3, 6]
    visible = filter_mark_write(
        filter_confidential_from_group(provider_candidates, "group")
    )
    request = request_stub()
    result = await InjectionExecutor(InjectionAdapter()).execute(
        request,
        decision_stub(delivery),
        _context(RecallRoutingMixin._safe_candidates(visible)),
    )

    payload = _payload(request, delivery)
    assert result.outcome is InjectionOutcome.INJECTED
    assert result.selected_count == 1
    assert "允许事实" in payload
    for text, _ in cases[1:]:
        assert text not in payload
    assert request.system_prompt == "stable-system-prefix"
