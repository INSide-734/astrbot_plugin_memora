"""反馈排序信号模型与固定构造器契约。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import pytest

from core.features.learning.domain.models import (
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    build_trusted_feedback_event,
)


def test_trusted_builder_derives_window_reward_and_dedupe_key() -> None:
    """调用方只能提供枚举结果，window/reward/dedupe 由内部确定。"""

    observed_at = datetime(2026, 7, 21, 10, 15, tzinfo=timezone.utc)
    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key="decision-synthetic",
        variant_key="document_route",
        outcome=FeedbackOutcome.POSITIVE,
        scope_domain="scope-synthetic",
        persona_domain="persona-synthetic",
        observed_at=observed_at,
        window_seconds=3600,
    )
    replay = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key="decision-synthetic",
        variant_key="document_route",
        outcome=FeedbackOutcome.POSITIVE,
        scope_domain="scope-synthetic",
        persona_domain="persona-synthetic",
        observed_at=observed_at,
        window_seconds=3600,
    )

    assert event.outcome.reward == 1.0
    assert event.dedupe_key == replay.dedupe_key
    assert len(event.dedupe_key) == 64
    assert event.observed_at.tzinfo is timezone.utc


def test_builder_rejects_free_outcome_and_naive_time() -> None:
    """自由 reward/结果和无时区时间不能进入可信事件。"""

    with pytest.raises(ValueError, match="feedback_outcome_invalid"):
        build_trusted_feedback_event(
            adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
            decision_key="decision",
            variant_key="document_route",
            outcome=cast(FeedbackOutcome, "positive"),
            scope_domain="scope",
            persona_domain=None,
            observed_at=datetime.now(timezone.utc),
            window_seconds=3600,
        )
    with pytest.raises(ValueError, match="feedback_event_time_invalid"):
        build_trusted_feedback_event(
            adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
            decision_key="decision",
            variant_key="document_route",
            outcome=FeedbackOutcome.POSITIVE,
            scope_domain="scope",
            persona_domain=None,
            observed_at=datetime(2026, 7, 21),
            window_seconds=3600,
        )


def test_policy_and_aggregate_enforce_weight_bounds() -> None:
    """策略和聚合都必须保持有限权重及和为一的 baseline。"""

    with pytest.raises(ValueError, match="feedback_policy_baseline_invalid"):
        FeedbackSignalPolicy(
            baseline_document_weight=0.8,
            baseline_graph_weight=0.3,
        )
    with pytest.raises(ValueError, match="feedback_policy_integer_invalid"):
        FeedbackSignalPolicy(max_events_per_window=0)

    aggregate = FeedbackSignalAggregate(
        scope_domain="scope-synthetic",
        persona_domain=None,
        window_start=datetime(2026, 7, 21, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 22, tzinfo=timezone.utc),
        accepted_count=8,
        independent_window_count=2,
        decayed_support=0.75,
        proposed_document_weight=0.75,
        proposed_graph_weight=0.25,
        delta_from_baseline=0.05,
        status="candidate",
        policy_version=1,
    )

    assert aggregate.proposed_document_weight + aggregate.proposed_graph_weight == 1.0


def test_event_shape_has_no_free_metadata_or_reward_field() -> None:
    """事件模型不能提供自由 metadata、payload、query 或 reward 逃生口。"""

    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.TOOL_OUTCOME,
        decision_key="decision-synthetic",
        variant_key="graph_route",
        outcome=FeedbackOutcome.NEUTRAL,
        scope_domain="scope-synthetic",
        persona_domain=None,
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        window_seconds=3600,
    )

    assert not hasattr(event, "reward")
    assert not hasattr(event, "metadata")
    assert not hasattr(event, "query")
    assert event.outcome.reward == 0.5
