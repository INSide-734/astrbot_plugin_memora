"""门禁配置模型校验契约。"""

import pytest
from pydantic import ValidationError

from core.features.quality.domain.gate_config import (
    BUILTIN_GENERIC_TERMS,
    GateConfig,
    GateProfile,
    QualityFeatureConfig,
)


def test_defaults_match_current_hardcoded_behavior():
    cfg = GateConfig()
    assert cfg.enabled is True and cfg.default_profile == "private"
    assert [p.name for p in cfg.profiles] == ["private", "group"]
    private = cfg.profiles[0]
    assert private.thresholds.min_deterministic_score == 0.42
    assert private.thresholds.min_judge_score == 0.08
    assert private.thresholds.min_inference_score == 0.20
    assert private.checks.numeric_check is True
    assert private.judge.enabled is False
    assert private.disposition == "quarantine" and private.rules == ()
    assert len(BUILTIN_GENERIC_TERMS) == 5 and "用户说" not in BUILTIN_GENERIC_TERMS


def test_quality_branch_defaults():
    assert QualityFeatureConfig().gate.enabled is True


def test_threshold_cross_validation_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    thresholds={  # type: ignore[arg-type]
                        "min_deterministic_score": 0.1,
                        "min_judge_score": 0.2,
                    },
                )
            ]
        )


def test_bad_regex_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": {"op": "regex", "field": "content", "pattern": "["},
                            "action": {
                                "kind": "force_disposition",
                                "value": "discard",
                            },
                        }
                    ],
                )
            ]
        )


def test_rule_tree_depth_capped():
    node = {"op": "exists", "field": "content"}
    for _ in range(5):
        node = {"op": "not", "child": node}
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": node,
                            "action": {"kind": "importance_delta", "delta": 0.1},
                        }
                    ],
                )
            ]
        )


def test_unknown_binding_and_default_profile_rejected():
    with pytest.raises(ValidationError):
        GateConfig(bindings=[{"profile": "missing", "chat_type": "group"}])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GateConfig(default_profile="missing")


def test_unknown_reason_code_override_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[  # type: ignore[arg-type]
                GateProfile(name="x", disposition_overrides={"bogus_code": "discard"})
            ]
        )


def test_judge_template_requires_placeholders():
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    judge={"enabled": True, "prompt_template": "无占位符"},  # type: ignore[arg-type]
                )
            ]
        )


def test_rule_id_unique_and_pattern():
    rule = {
        "id": "r1",
        "when": {"op": "exists", "field": "content"},
        "action": {"kind": "drop_atoms", "value": True},
    }
    with pytest.raises(ValidationError):
        GateConfig(profiles=[GateProfile(name="x", rules=[rule, rule])])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GateConfig(profiles=[GateProfile(name="x", rules=[{**rule, "id": "非法ID"}])])  # type: ignore[arg-type]


def test_rule_tree_boundary_accepts_depth4_nodes32():
    """深度恰好 4、节点数恰好 32 的合法树必须被接受。"""
    leaf = {"op": "exists", "field": "content"}
    tree = {
        "op": "not",
        "child": {"op": "not", "child": {"op": "and", "children": [leaf] * 29}},
    }
    cfg = GateConfig(
        default_profile="x",
        bindings=[],  # type: ignore[arg-type]
        profiles=[  # type: ignore[arg-type]
            GateProfile(
                name="x",
                rules=[  # type: ignore[arg-type]
                    {
                        "id": "r1",
                        "when": tree,
                        "action": {"kind": "drop_atoms", "value": True},
                    }
                ],
            )
        ],
    )
    assert len(cfg.profiles[0].rules) == 1


def test_rule_tree_nodes_33_rejected():
    leaf = {"op": "exists", "field": "content"}
    tree = {
        "op": "not",
        "child": {"op": "not", "child": {"op": "and", "children": [leaf] * 30}},
    }
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": tree,
                            "action": {"kind": "drop_atoms", "value": True},
                        }
                    ],
                )
            ],
        )


def test_regex_length_boundary():
    def with_pattern(pattern: str) -> None:
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": {
                                "op": "regex",
                                "field": "content",
                                "pattern": pattern,
                            },
                            "action": {"kind": "drop_atoms", "value": True},
                        }
                    ],
                )
            ],
        )

    with_pattern("a" * 500)  # 恰好 500 字符，合法
    with pytest.raises(ValidationError):
        with_pattern("a" * 501)


def test_summary_quality_low_override_accepted():
    GateConfig(
        default_profile="x",
        bindings=[],  # type: ignore[arg-type]
        profiles=[  # type: ignore[arg-type]
            GateProfile(
                name="x", disposition_overrides={"summary_quality_low": "discard"}
            )
        ],
    )


def test_non_whitelisted_reason_codes_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            profiles=[  # type: ignore[arg-type]
                GateProfile(name="x", disposition_overrides={"quality_low": "discard"})
            ],
        )
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    disposition_overrides={"grounding_any_new_typo": "discard"},
                )
            ],
        )


def test_custom_rule_override_cross_check():
    rule = {
        "id": "r1",
        "when": {"op": "exists", "field": "content"},
        "action": {"kind": "drop_atoms", "value": True},
    }
    # r1 存在时 custom_rule_r1 合法
    GateConfig(
        default_profile="x",
        bindings=[],  # type: ignore[arg-type]
        profiles=[  # type: ignore[arg-type]
            GateProfile(
                name="x",
                rules=[rule],  # type: ignore[arg-type]
                disposition_overrides={"custom_rule_r1": "discard"},
            )
        ],
    )
    # r1 不存在时拒绝
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x", disposition_overrides={"custom_rule_r1": "discard"}
                )
            ],
        )
    # 非法字符拒绝（大写、非 ASCII）
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[rule],  # type: ignore[arg-type]
                    disposition_overrides={"custom_rule_R1": "discard"},
                )
            ],
        )
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[rule],  # type: ignore[arg-type]
                    disposition_overrides={"custom_rule_非法": "discard"},
                )
            ],
        )


def test_judge_template_unknown_placeholder_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    judge={  # type: ignore[arg-type]
                        "enabled": True,
                        "prompt_template": "{claim_text} {source_text} {secret}",
                    },
                )
            ],
        )


def test_judge_template_unclosed_brace_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    judge={  # type: ignore[arg-type]
                        "enabled": True,
                        "prompt_template": "{claim_text} {source_text} {{oops",
                    },
                )
            ],
        )


def test_profiles_and_rules_are_tuples():
    cfg = GateConfig()
    assert isinstance(cfg.profiles, tuple)
    assert isinstance(cfg.profiles[0].rules, tuple)
    with pytest.raises(AttributeError):
        cfg.profiles.append(GateProfile(name="y"))  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        cfg.profiles[0].rules.append(None)  # type: ignore[attr-defined]


def test_disposition_overrides_immutable_and_serializable():
    cfg = GateConfig(
        default_profile="x",
        bindings=[],  # type: ignore[arg-type]
        profiles=[  # type: ignore[arg-type]
            GateProfile(
                name="x", disposition_overrides={"summary_quality_low": "discard"}
            )
        ],
    )
    profile = cfg.profiles[0]
    with pytest.raises(TypeError):
        profile.disposition_overrides["x"] = "y"  # type: ignore[index]
    dumped = cfg.model_dump(mode="json")
    assert dumped["profiles"][0]["disposition_overrides"] == {
        "summary_quality_low": "discard"
    }


def test_extra_forbid():
    with pytest.raises(ValidationError):
        GateConfig(enabled=True, undefined_field="x")  # type: ignore[call-arg]


def test_predicate_incompatible_payload_rejected():
    """op 与结构性字段（field/child/children）不兼容的组合必须被拒绝。"""
    leaf = {"op": "exists", "field": "content"}
    bad_whens = [
        # 叶谓词带 child
        {**leaf, "child": leaf},
        # 叶谓词带 children
        {**leaf, "children": [leaf]},
        # and 带 field
        {"op": "and", "children": [leaf], "field": "content"},
        # and 带 child
        {"op": "and", "children": [leaf], "child": leaf},
        # not 同时带 child 与 children
        {"op": "not", "child": leaf, "children": [leaf]},
        # not 带 field
        {"op": "not", "child": leaf, "field": "content"},
    ]
    for when in bad_whens:
        with pytest.raises(ValidationError):
            GateConfig(
                default_profile="x",
                bindings=[],  # type: ignore[arg-type]
                profiles=[  # type: ignore[arg-type]
                    GateProfile(
                        name="x",
                        rules=[  # type: ignore[arg-type]
                            {
                                "id": "r1",
                                "when": when,
                                "action": {"kind": "drop_atoms", "value": True},
                            }
                        ],
                    )
                ],
            )


@pytest.mark.parametrize(
    "when",
    [
        # 叶谓词带无关负载字段
        {"op": "exists", "field": "content", "pattern": "a"},
        {"op": "exists", "field": "content", "values": ["a"]},
        {"op": "regex", "field": "content", "pattern": "a", "values": ["a"]},
        {"op": "regex", "field": "content", "pattern": "a", "cmp": "eq", "value": 1},
        {"op": "contains", "field": "content", "values": ["a"], "pattern": "a"},
        {
            "op": "length_cmp",
            "field": "content",
            "cmp": "gt",
            "value": 1,
            "pattern": "a",
        },
        {
            "op": "numeric_cmp",
            "field": "importance",
            "cmp": "gt",
            "value": 0.5,
            "values": ["a"],
        },
        # 组合谓词带无关负载字段
        {
            "op": "and",
            "children": [{"op": "exists", "field": "content"}],
            "pattern": "a",
        },
        {"op": "or", "children": [{"op": "exists", "field": "content"}], "value": 1},
        {"op": "not", "child": {"op": "exists", "field": "content"}, "value": 1},
        {"op": "not", "child": {"op": "exists", "field": "content"}, "values": ["a"]},
    ],
)
def test_predicate_irrelevant_payload_rejected(when):
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": when,
                            "action": {"kind": "drop_atoms", "value": True},
                        }
                    ],
                )
            ],
        )


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "force_disposition", "value": "discard", "delta": 0.5},
        {"kind": "importance_delta", "delta": 0.5, "value": "discard"},
        {"kind": "set_importance", "value": 0.5, "values": ["t"]},
        {"kind": "add_topics", "values": ["t"], "value": "x"},
        {"kind": "set_privacy", "value": "public", "delta": 0.1},
        {"kind": "drop_atoms", "value": True, "values": ["t"]},
    ],
)
def test_action_irrelevant_payload_rejected(action):
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": {"op": "exists", "field": "content"},
                            "action": action,
                        }
                    ],
                )
            ],
        )


@pytest.mark.parametrize(
    "when",
    [
        {"op": "regex", "field": "importance", "pattern": "a"},
        {"op": "contains", "field": "importance", "values": ["a"]},
        {"op": "exists", "field": "importance"},
        {"op": "length_cmp", "field": "importance", "cmp": "gt", "value": 1},
    ],
)
def test_predicate_text_ops_reject_importance_field(when):
    """文本/列表类谓词（regex/contains/exists/length_cmp）拒绝 importance 字段。"""

    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": when,
                            "action": {"kind": "drop_atoms", "value": True},
                        }
                    ],
                )
            ],
        )


@pytest.mark.parametrize(
    "when",
    [
        {"op": "length_cmp", "field": "content", "cmp": "gt", "value": True},
        {"op": "numeric_cmp", "field": "importance", "cmp": "gt", "value": True},
    ],
)
def test_predicate_numeric_value_rejects_bool(when):
    """数值负载不接受 bool（True 不得被当 1）。"""
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": when,
                            "action": {"kind": "drop_atoms", "value": True},
                        }
                    ],
                )
            ],
        )


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "set_importance", "value": True},
        {"kind": "importance_delta", "delta": True},
    ],
)
def test_action_numeric_payload_rejects_bool(action):
    """动作数值负载不接受 bool。"""
    with pytest.raises(ValidationError):
        GateConfig(
            default_profile="x",
            bindings=[],  # type: ignore[arg-type]
            profiles=[  # type: ignore[arg-type]
                GateProfile(
                    name="x",
                    rules=[  # type: ignore[arg-type]
                        {
                            "id": "r1",
                            "when": {"op": "exists", "field": "content"},
                            "action": action,
                        }
                    ],
                )
            ],
        )
