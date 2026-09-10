"""规则引擎与处置优先级契约。"""

from __future__ import annotations

import pytest

from core.features.quality.application.gate_rule_engine import (
    CandidateView,
    RuleOutcome,
    evaluate_disposition,
    evaluate_rules,
)
from core.features.quality.domain.gate_config import (
    GateProfile,
    GateRuleConfig,
    RuleAction,
    RulePredicate,
)

VIEW = CandidateView(
    content="我养了两只猫",
    summary="用户养猫",
    key_facts=("用户养了两只猫",),
    topics=("宠物",),
    participants=(),
    importance=0.6,
    chat_type="private",
)


def _profile(**overrides) -> GateProfile:
    data = {"name": "p"}
    data.update(overrides)
    return GateProfile.model_validate(data)


def forced_outcome(forced: str | None) -> RuleOutcome:
    """构造仅含 force_disposition 的规则结果。"""
    return RuleOutcome(forced_disposition=forced)


def test_regex_and_contains_predicates():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "when": {"op": "regex", "field": "content", "pattern": "猫"},
                "action": {"kind": "importance_delta", "delta": 0.2},
            },
            {
                "id": "r2",
                "when": {"op": "contains", "field": "topics", "values": ["宠物"]},
                "action": {"kind": "importance_delta", "delta": 0.1},
            },
        ]
    )
    outcome = evaluate_rules(VIEW, profile)
    assert outcome.importance_delta == pytest.approx(0.3)
    assert set(outcome.matched_rule_ids) == {"r1", "r2"}


def test_not_and_or_composition():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "when": {
                    "op": "or",
                    "children": [
                        {
                            "op": "not",
                            "child": {"op": "exists", "field": "participants"},
                        },
                        {"op": "contains", "field": "content", "values": ["狗"]},
                    ],
                },
                "action": {"kind": "force_disposition", "value": "discard"},
            }
        ]
    )
    assert evaluate_rules(VIEW, profile).forced_disposition == "discard"


def test_first_force_disposition_wins_by_order():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "force_disposition", "value": "mark_write"},
            },
            {
                "id": "r2",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "force_disposition", "value": "discard"},
            },
        ]
    )
    assert evaluate_rules(VIEW, profile).forced_disposition == "mark_write"


def test_disabled_rule_skipped():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "enabled": False,
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "force_disposition", "value": "discard"},
            }
        ]
    )
    assert evaluate_rules(VIEW, profile).forced_disposition is None


def test_set_importance_overrides_delta():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "importance_delta", "delta": 0.3},
            },
            {
                "id": "r2",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "set_importance", "value": 0.9},
            },
        ]
    )
    outcome = evaluate_rules(VIEW, profile)
    assert outcome.set_importance == 0.9


def test_add_topics_set_privacy_drop_atoms():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "add_topics", "values": ["猫", "日常"]},
            },
            {
                "id": "r2",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "set_privacy", "value": "confidential"},
            },
            {
                "id": "r3",
                "when": {"op": "exists", "field": "content"},
                "action": {"kind": "drop_atoms", "value": True},
            },
        ]
    )
    outcome = evaluate_rules(VIEW, profile)
    assert outcome.add_topics == ("猫", "日常")
    assert outcome.set_privacy == "confidential" and outcome.drop_atoms is True


def test_numeric_cmp_importance():
    profile = _profile(
        rules=[
            {
                "id": "r1",
                "when": {
                    "op": "numeric_cmp",
                    "field": "importance",
                    "cmp": "gte",
                    "value": 0.5,
                },
                "action": {"kind": "importance_delta", "delta": 0.1},
            }
        ]
    )
    assert evaluate_rules(VIEW, profile).importance_delta == pytest.approx(0.1)


def test_disposition_priority_chain():
    profile = _profile(
        disposition="quarantine",
        disposition_overrides={
            "grounding_judge_unavailable": "mark_write",
            "grounding_numeric_conflict": "discard",
        },
    )
    forced = evaluate_rules(
        VIEW,
        _profile(
            rules=[
                {
                    "id": "r1",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "force_disposition", "value": "discard"},
                }
            ]
        ),
    ).forced_disposition
    assert (
        evaluate_disposition(
            ("grounding_judge_unavailable",), evaluate_rules(VIEW, profile), profile
        )
        == "mark_write"
    )
    # 多原因码命中多条 override 时按安全序取最保守：discard 优先于 mark_write。
    assert (
        evaluate_disposition(
            ("grounding_numeric_conflict", "grounding_judge_unavailable"),
            evaluate_rules(VIEW, profile),
            profile,
        )
        == "discard"
    )
    assert evaluate_disposition((), forced_outcome(forced), profile) == "discard"
    assert (
        evaluate_disposition(
            ("grounding_claim_missing",), evaluate_rules(VIEW, profile), profile
        )
        == "quarantine"
    )


def test_unknown_op_raises():
    # 配置校验已在保存期拦截未知 op；引擎仍需对绕过校验的输入防御性失败。
    rule = GateRuleConfig.model_construct(
        id="r1",
        enabled=True,
        when=RulePredicate.model_construct(op="bogus", field="content"),
        action=RuleAction.model_construct(kind="drop_atoms", value=True),
    )
    profile = GateProfile.model_construct(name="p", rules=(rule,))
    with pytest.raises(ValueError, match="gate_unknown_op:bogus"):
        evaluate_rules(VIEW, profile)
