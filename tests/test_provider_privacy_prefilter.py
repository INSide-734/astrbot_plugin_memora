"""外部重排 Provider 前隐私预过滤的闭环测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.shared.cost_control import CostControl
from core.features.retrieval.llm_reranker import LLMReranker
from core.features.retrieval.rrf_fusion import HybridResult
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope


def _candidate(
    doc_id: int,
    score: float,
    content: str,
    *,
    privacy_level: str = "shared",
    scope_key: str = "group:42",
    participant_ids: tuple[str, ...] = ("stable-user",),
    role: str = "user",
) -> HybridResult:
    """构造带显式权限证据的候选记忆。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata={
            "privacy_level": privacy_level,
            "scope_key": scope_key,
            "participant_ids": list(participant_ids),
            "role": role,
        },
        score_breakdown={},
    )


def _llm_reranker(client: MagicMock) -> LLMReranker:
    """构造允许单次请求级 LLM 重排的实例。"""

    return LLMReranker(
        llm_client=client,
        batch_size=10,
        cost_control=CostControl(
            mode="quality",
            max_extra_llm_calls_per_turn=1,
            llm_reranker_min_candidates=1,
        ),
    )


def _dual_retriever(
    candidates: list[HybridResult],
    client: MagicMock,
    *,
    strict_mode: bool = False,
    mmr_lambda: float = 0.7,
) -> Any:
    """构造只返回给定文档候选的双路检索器。"""

    from core.features.retrieval.dual_route_retriever import DualRouteRetriever

    document = AsyncMock()
    document.search = AsyncMock(return_value=candidates)
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    loader = AsyncMock(return_value=None)
    return DualRouteRetriever(
        document,
        graph,
        loader,
        config={
            "reranker.strategy": "llm",
            "reranker.mmr_lambda": mmr_lambda,
            "security.strict_mode": strict_mode,
        },
        reranker=_llm_reranker(client),
    )


class _FailingPrefilter:
    """模拟隐私预过滤器自身的普通故障。"""

    def filter(self, *_args: Any, **_kwargs: Any) -> Any:
        """始终抛出普通异常以触发安全降级。"""

        raise RuntimeError("prefilter unavailable")


class _CancelledPrefilter:
    """模拟隐私预过滤阶段收到协程取消。"""

    def filter(self, *_args: Any, **_kwargs: Any) -> Any:
        """抛出取消异常，调用方必须继续传播。"""

        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_group_confidential_candidate_never_reaches_llm_payload() -> None:
    """群聊机密正文不得进入 Provider，shared/public 仍应参与重排。"""

    client = MagicMock()
    client.complete_sync.return_value = "[2.0, 9.0, 5.0]"
    retriever = _dual_retriever(
        [
            _candidate(
                1,
                0.99,
                "CONFIDENTIAL_PAYLOAD_CANARY",
                privacy_level="confidential",
            ),
            _candidate(2, 0.90, "SHARED_PAYLOAD_CANARY"),
            _candidate(3, 0.80, "PUBLIC_PAYLOAD_CANARY", privacy_level="public"),
            _candidate(4, 0.70, "SECOND_SHARED_PAYLOAD_CANARY"),
        ],
        client,
    )

    with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
        results = await retriever.search(
            "匿名查询",
            k=2,
            session_id="group:42",
            chat_type="group",
            user_id="stable-user",
        )

    prompt = client.complete_sync.call_args.args[0]
    assert "CONFIDENTIAL_PAYLOAD_CANARY" not in prompt
    assert "SHARED_PAYLOAD_CANARY" in prompt
    assert "PUBLIC_PAYLOAD_CANARY" in prompt
    assert all(item.doc_id != 1 for item in results)


@pytest.mark.asyncio
async def test_multi_query_confidential_candidate_never_reaches_llm_payload() -> None:
    """多查询融合路径也必须在 Provider 调用前移除群聊机密正文。"""

    from core.features.retrieval.query_planner import QueryPlan

    client = MagicMock()
    client.complete_sync.return_value = "[2.0, 9.0, 5.0, 3.0]"
    retriever = _dual_retriever([], client)
    confidential = _candidate(
        1,
        0.99,
        "MULTI_QUERY_CONFIDENTIAL_CANARY",
        privacy_level="confidential",
    )

    async def search_by_query(query: str, *_args: Any, **_kwargs: Any) -> Any:
        """为每条子查询返回同一机密项和两条不同的可见项。"""

        offset = 0 if query == "查询甲" else 2
        return [
            confidential,
            _candidate(2 + offset, 0.90, f"可见正文-{2 + offset}"),
            _candidate(
                3 + offset,
                0.80,
                f"公开正文-{3 + offset}",
                privacy_level="public",
            ),
        ]

    retriever.document_retriever.search.side_effect = search_by_query
    plan = QueryPlan(
        original_query="匿名查询",
        intent="default",
        entities=(),
        focus_terms=(),
        temporal_anchor=None,
        reference_time=datetime.now(timezone.utc),
        queries=("查询甲", "查询乙"),
        required_facets=(),
        ambiguity_flags=(),
        memory_types=(),
    )

    with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
        results = await retriever.search(
            "匿名查询",
            k=3,
            session_id="group:42",
            chat_type="group",
            user_id="stable-user",
            query_plan=plan,
        )

    prompt = client.complete_sync.call_args.args[0]
    assert "MULTI_QUERY_CONFIDENTIAL_CANARY" not in prompt
    assert "可见正文" in prompt
    assert "公开正文" in prompt
    assert all(item.doc_id != 1 for item in results)


@pytest.mark.asyncio
async def test_incomplete_authorization_never_reaches_llm_payload() -> None:
    """缺失或冲突的授权证据不得进入 Provider，也不得回到最终结果。"""

    client = MagicMock()
    client.complete_sync.return_value = "[9.0, 5.0, 2.0]"
    missing_privacy = _candidate(
        1,
        0.99,
        "MISSING_PRIVACY_CANARY",
        scope_key="private:stable-user",
    )
    missing_privacy.metadata.pop("privacy_level")
    missing_scope = _candidate(2, 0.98, "MISSING_SCOPE_CANARY")
    missing_scope.metadata.pop("scope_key")
    missing_participants = _candidate(
        3,
        0.97,
        "MISSING_PARTICIPANTS_CANARY",
        scope_key="private:stable-user",
    )
    missing_participants.metadata.pop("participant_ids")
    conflicting_scope = _candidate(
        4,
        0.96,
        "CONFLICTING_SCOPE_CANARY",
        scope_key="private:stable-user",
    )
    conflicting_scope.metadata["session_id"] = "private:other"
    retriever = _dual_retriever(
        [
            missing_privacy,
            missing_scope,
            missing_participants,
            conflicting_scope,
            _candidate(
                5,
                0.80,
                "VISIBLE_PRIVATE_CANARY_1",
                privacy_level="confidential",
                scope_key="private:stable-user",
            ),
            _candidate(
                6,
                0.70,
                "VISIBLE_PRIVATE_CANARY_2",
                privacy_level="confidential",
                scope_key="private:stable-user",
            ),
            _candidate(
                7,
                0.60,
                "VISIBLE_PRIVATE_CANARY_3",
                privacy_level="confidential",
                scope_key="private:stable-user",
            ),
        ],
        client,
    )

    with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
        results = await retriever.search(
            "匿名查询",
            k=2,
            session_id="private:stable-user",
            chat_type="private",
            user_id="stable-user",
        )

    prompt = client.complete_sync.call_args.args[0]
    assert "MISSING_PRIVACY_CANARY" not in prompt
    assert "MISSING_SCOPE_CANARY" not in prompt
    assert "MISSING_PARTICIPANTS_CANARY" not in prompt
    assert "CONFLICTING_SCOPE_CANARY" not in prompt
    assert "VISIBLE_PRIVATE_CANARY" in prompt
    assert {item.doc_id for item in results} <= {5, 6, 7}


def test_prefilter_enforces_scope_stable_identity_and_role() -> None:
    """Provider 候选必须同时满足 scope、稳定身份和 role 约束。"""

    from core.features.retrieval.provider_privacy_prefilter import (
        ProviderPrivacyContext,
        ProviderPrivacyPrefilter,
    )

    allowed = _candidate(
        1,
        0.9,
        "允许正文",
        privacy_level="confidential",
        scope_key="private:stable-user",
    )
    wrong_scope = _candidate(
        2,
        0.8,
        "错误作用域",
        scope_key="private:other",
    )
    wrong_identity = _candidate(
        3,
        0.7,
        "错误身份",
        scope_key="private:stable-user",
        participant_ids=("other-user",),
    )
    invalid_role = _candidate(
        4,
        0.6,
        "非法角色",
        scope_key="private:stable-user",
        role="system",
    )

    outcome = ProviderPrivacyPrefilter().filter(
        [allowed, wrong_scope, wrong_identity, invalid_role],
        ProviderPrivacyContext(
            chat_type="private",
            scope_key="private:stable-user",
            stable_user_id="stable-user",
        ),
    )

    assert outcome.candidates == [allowed]
    assert outcome.input_count == 4
    assert outcome.allowed_count == 1
    assert outcome.filtered_count == 3


def test_prefilter_rejects_missing_or_conflicting_authorization_evidence() -> None:
    """Provider 边界必须拒绝缺失或互相冲突的候选授权证据。"""

    from core.features.retrieval.provider_privacy_prefilter import (
        ProviderPrivacyContext,
        ProviderPrivacyPrefilter,
    )

    allowed = _candidate(
        1,
        0.9,
        "允许正文",
        privacy_level="confidential",
        scope_key="private:stable-user",
    )
    canonical_shape = _candidate(7, 0.3, "合法 canonical 正文")
    canonical_shape.metadata = {
        "privacy_level": "confidential",
        "session_id": "private:stable-user",
        "persona_id": "persona:current",
        "participant_ids": ["stable-user"],
    }
    empty_metadata = _candidate(2, 0.8, "空授权证据")
    empty_metadata.metadata = {}
    missing_privacy = _candidate(
        3,
        0.7,
        "缺少隐私证据",
        scope_key="private:stable-user",
    )
    missing_privacy.metadata.pop("privacy_level")
    missing_scope = _candidate(4, 0.6, "缺少作用域证据")
    missing_scope.metadata.pop("scope_key")
    missing_participants = _candidate(
        5,
        0.5,
        "缺少稳定参与者证据",
        scope_key="private:stable-user",
    )
    missing_participants.metadata.pop("participant_ids")
    conflicting_scope = _candidate(
        6,
        0.4,
        "冲突作用域证据",
        scope_key="private:stable-user",
    )
    conflicting_scope.metadata["session_id"] = "private:other"

    outcome = ProviderPrivacyPrefilter().filter(
        [
            allowed,
            canonical_shape,
            empty_metadata,
            missing_privacy,
            missing_scope,
            missing_participants,
            conflicting_scope,
        ],
        ProviderPrivacyContext(
            chat_type="private",
            scope_key="private:stable-user",
            stable_user_id="stable-user",
        ),
    )

    assert outcome.candidates == [allowed, canonical_shape]
    assert outcome.input_count == 7
    assert outcome.allowed_count == 2
    assert outcome.filtered_count == 5


@pytest.mark.asyncio
async def test_prefilter_failure_in_strict_mode_skips_external_rerank() -> None:
    """严格模式下预过滤故障必须跳过外部重排并保持基础顺序。"""

    client = MagicMock()
    client.complete_sync.return_value = "[1.0, 9.0, 5.0]"
    retriever = _dual_retriever(
        [
            _candidate(1, 0.9, "第一条"),
            _candidate(2, 0.8, "第二条"),
            _candidate(3, 0.7, "第三条"),
        ],
        client,
        strict_mode=True,
    )
    retriever._provider_prefilter = _FailingPrefilter()

    with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
        results = await retriever.search(
            "匿名查询",
            k=2,
            session_id="group:42",
            chat_type="group",
            user_id="stable-user",
        )

    client.complete_sync.assert_not_called()
    assert [item.doc_id for item in results] == [1, 2]


@pytest.mark.asyncio
async def test_prefilter_failure_in_compat_mode_falls_back_to_local_mmr() -> None:
    """兼容模式下预过滤故障必须仅在本地执行 MMR。"""

    client = MagicMock()
    client.complete_sync.return_value = "[1.0, 9.0, 5.0]"
    retriever = _dual_retriever(
        [
            _candidate(1, 1.0, "相同 主题"),
            _candidate(2, 0.99, "相同 主题"),
            _candidate(3, 0.60, "完全 不同"),
        ],
        client,
        strict_mode=False,
        mmr_lambda=0.1,
    )
    retriever._provider_prefilter = _FailingPrefilter()

    with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
        results = await retriever.search(
            "匿名查询",
            k=2,
            session_id="group:42",
            chat_type="group",
            user_id="stable-user",
        )

    client.complete_sync.assert_not_called()
    assert [item.doc_id for item in results] == [1, 3]


@pytest.mark.asyncio
async def test_prefilter_cancellation_propagates() -> None:
    """预过滤阶段的取消异常不得被重排降级逻辑吞掉。"""

    client = MagicMock()
    retriever = _dual_retriever(
        [
            _candidate(1, 0.9, "第一条"),
            _candidate(2, 0.8, "第二条"),
            _candidate(3, 0.7, "第三条"),
        ],
        client,
    )
    retriever._provider_prefilter = _CancelledPrefilter()

    with pytest.raises(asyncio.CancelledError):
        await retriever.search(
            "匿名查询",
            k=2,
            session_id="group:42",
            chat_type="group",
            user_id="stable-user",
        )
    client.complete_sync.assert_not_called()
