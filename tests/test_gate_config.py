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
    assert private.disposition == "quarantine" and private.rules == []
    assert len(BUILTIN_GENERIC_TERMS) == 5 and "用户说" not in BUILTIN_GENERIC_TERMS


def test_quality_branch_defaults():
    assert QualityFeatureConfig().gate.enabled is True


def test_threshold_cross_validation_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[
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
            profiles=[
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
            profiles=[
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
            profiles=[
                GateProfile(name="x", disposition_overrides={"bogus_code": "discard"})
            ]
        )


def test_judge_template_requires_placeholders():
    with pytest.raises(ValidationError):
        GateConfig(
            profiles=[
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
