"""门禁可配置化配置模型：开关、阈值、词表、处置、规则树与 profile 绑定。"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

GateDisposition = Literal["quarantine", "discard", "mark_write"]
RuleActionForce = Literal["quarantine", "discard", "mark_write", "allow"]
BUILTIN_GATE_REASON_CODES: frozenset[str] = frozenset(
    {
        "grounding_claim_missing",
        "grounding_source_missing",
        "grounding_reference_invalid",
        "grounding_source_evidence_missing",
        "grounding_source_evidence_invalid",
        "grounding_source_changed",
        "grounding_subject_ambiguous",
        "grounding_subject_mismatch",
        "grounding_numeric_conflict",
        "grounding_negation_conflict",
        "grounding_claim_unsupported",
        "grounding_needs_judge",
        "grounding_judge_supported",
        "grounding_judge_rejected",
        "grounding_judge_unavailable",
        "grounding_not_verified",
        "summary_quality_low",
    }
)
_CUSTOM_RULE_RE = re.compile(r"^custom_rule_([a-z0-9_-]{1,64})$")
_PLACEHOLDER_CLAIM, _PLACEHOLDER_SOURCE = "{claim_text}", "{source_text}"
_PLACEHOLDER_RE = re.compile(r"\{([a-z0-9_]+)\}")
_ALLOWED_PLACEHOLDERS = frozenset({"claim_text", "source_text"})
DISPOSITION_SAFETY_ORDER: tuple[str, ...] = ("quarantine", "discard", "mark_write")
BUILTIN_NEGATION_WHITELIST: tuple[str, ...] = ("不错", "没问题", "没准")
BUILTIN_NEGATION_MARKERS: tuple[str, ...] = (
    "不",
    "没",
    "无",
    "未",
    "否",
    "never",
    "not",
    "no",
)
BUILTIN_GENERIC_TERMS: tuple[str, ...] = (
    "某用户",
    "有人",
    "某人",
    "群成员",
    "某群成员",
)
SYNONYM_DEFAULTS: tuple[tuple[str, str], ...] = ()


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class GateChecks(_Frozen):
    numeric_check: bool = True
    negation_check: bool = True
    group_subject_check: bool = True
    quality_low_check: bool = True


class GateThresholds(_Frozen):
    min_deterministic_score: float = Field(default=0.42, ge=0.0, le=1.0)
    min_judge_score: float = Field(default=0.08, ge=0.0, le=1.0)
    min_inference_score: float = Field(default=0.20, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _judge_le_deterministic(self) -> "GateThresholds":
        if self.min_judge_score > self.min_deterministic_score:
            raise ValueError("min_judge_score 必须不大于 min_deterministic_score")
        return self


class GateScoring(_Frozen):
    token_weight: float = Field(default=1.0, ge=0.0, le=2.0)
    sequence_enabled: bool = True
    sequence_weight: float = Field(default=0.7, ge=0.0, le=2.0)


class GateReferences(_Frozen):
    max_references: int = Field(default=8, ge=1, le=16)


class GateQualityParams(_Frozen):
    min_summary_chars: int = Field(default=10, ge=1, le=100)


class WordListConfig(_Frozen):
    mode: Literal["append", "replace"] = "append"
    items: tuple[str, ...] = Field(default_factory=tuple, max_length=50)

    @field_validator("items")
    @classmethod
    def _item_bounds(cls, items: tuple[str, ...]) -> tuple[str, ...]:
        for item in items:
            if not item or len(item) > 32:
                raise ValueError("词表项必须非空且不超过 32 字符")
        return items


class SynonymPair(_Frozen):
    source: str = Field(min_length=1, max_length=16)
    target: str = Field(min_length=1, max_length=16)


class GateWordLists(_Frozen):
    negation_whitelist: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    negation_markers: WordListConfig = Field(default_factory=WordListConfig)
    generic_terms: WordListConfig = Field(default_factory=WordListConfig)
    synonym_pairs: tuple[SynonymPair, ...] = Field(default_factory=tuple, max_length=20)

    @field_validator("negation_whitelist")
    @classmethod
    def _whitelist_bounds(cls, items: tuple[str, ...]) -> tuple[str, ...]:
        for item in items:
            if not item or len(item) > 32:
                raise ValueError("否定白名单项必须非空且不超过 32 字符")
        return items


class GateJudge(_Frozen):
    enabled: bool = False
    prompt_template: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _template_placeholders(self) -> "GateJudge":
        template = self.prompt_template
        if not template:
            return self
        if _PLACEHOLDER_CLAIM not in template or _PLACEHOLDER_SOURCE not in template:
            raise ValueError("Judge 模板必须包含 {claim_text} 与 {source_text}")
        placeholders = set(_PLACEHOLDER_RE.findall(template))
        if not placeholders <= _ALLOWED_PLACEHOLDERS:
            raise ValueError(
                "Judge 模板包含未知占位符: "
                + ", ".join(sorted(placeholders - _ALLOWED_PLACEHOLDERS))
            )
        residual = _PLACEHOLDER_RE.sub("", template)
        if "{" in residual or "}" in residual:
            raise ValueError("Judge 模板包含未闭合或多余的 {} 花括号")
        return self


_FIELDS = ("content", "summary", "key_facts", "topics", "participants", "importance")
_OPS_LEAF = ("regex", "contains", "exists", "length_cmp", "numeric_cmp")
_PAYLOAD_FIELDS = ("pattern", "values", "cmp", "value")
_LEAF_PAYLOAD_ALLOWED: dict[str, tuple[str, ...]] = {
    "regex": ("pattern",),
    "contains": ("values",),
    "exists": (),
    "length_cmp": ("cmp", "value"),
    "numeric_cmp": ("cmp", "value"),
}
_ACTION_PAYLOAD_ALLOWED: dict[str, tuple[str, ...]] = {
    "force_disposition": ("value",),
    "importance_delta": ("delta",),
    "set_importance": ("value",),
    "add_topics": ("values",),
    "set_privacy": ("value",),
    "drop_atoms": ("value",),
}


def _reject_irrelevant(n: RulePredicate, allowed: tuple[str, ...]) -> None:
    for name in _PAYLOAD_FIELDS:
        if name not in allowed and getattr(n, name) is not None:
            raise ValueError(f"{n.op} 不接受 {name}")


class RulePredicate(_Frozen):
    op: Literal[
        "regex", "contains", "exists", "length_cmp", "numeric_cmp", "and", "or", "not"
    ]
    field: (
        Literal[
            "content", "summary", "key_facts", "topics", "participants", "importance"
        ]
        | None
    ) = None
    pattern: str | None = None
    values: tuple[str, ...] | None = None
    cmp: Literal["gt", "gte", "lt", "lte", "eq"] | None = None
    value: int | float | None = None
    children: tuple["RulePredicate", ...] | None = None
    child: "RulePredicate | None" = None

    @field_validator("value", mode="before")
    @classmethod
    def _value_not_bool(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("value 不接受 bool")
        return v


def _validate_predicate(node: RulePredicate) -> None:
    """校验谓词形状：op 专属字段、正则编译、树深度 ≤4、节点数 ≤32。"""

    def walk(n: RulePredicate, depth: int, counter: list[int]) -> None:
        counter[0] += 1
        if depth > 4 or counter[0] > 32:
            raise ValueError("规则树深度不能超过 4 且节点数不能超过 32")
        if n.op in _OPS_LEAF:
            if n.child is not None or n.children is not None:
                raise ValueError(f"{n.op} 不接受 child/children")
            if n.field is None:
                raise ValueError(f"{n.op} 需要 field")
            _reject_irrelevant(n, _LEAF_PAYLOAD_ALLOWED[n.op])
            if n.op in ("regex", "contains", "exists", "length_cmp") and (
                n.field == "importance"
            ):
                raise ValueError(f"{n.op} 不支持 importance 字段（仅限文本/列表字段）")
            if n.op == "regex":
                if not n.pattern or len(n.pattern) > 500:
                    raise ValueError("regex pattern 必须非空且不超过 500 字符")
                try:
                    re.compile(n.pattern)
                except re.error as exc:
                    raise ValueError(f"regex 无法编译: {exc}") from exc
            if n.op == "contains" and not n.values:
                raise ValueError("contains 需要 values")
            if n.op == "length_cmp" and (n.cmp is None or not isinstance(n.value, int)):
                raise ValueError("length_cmp 需要 cmp 与整数 value")
            if n.op == "numeric_cmp" and (
                n.field != "importance"
                or n.cmp is None
                or not isinstance(n.value, (int, float))
            ):
                raise ValueError("numeric_cmp 仅支持 importance 且需要 cmp/value")
        elif n.op in ("and", "or"):
            if n.field is not None or n.child is not None:
                raise ValueError(f"{n.op} 不接受 field/child")
            _reject_irrelevant(n, ())
            if not n.children:
                raise ValueError(f"{n.op} 需要 children")
            for child in n.children:
                walk(child, depth + 1, counter)
        else:  # not
            if n.field is not None or n.children is not None:
                raise ValueError("not 不接受 field/children")
            _reject_irrelevant(n, ())
            if n.child is None:
                raise ValueError("not 需要单个 child")
            walk(n.child, depth + 1, counter)

    walk(node, 1, [0])


class RuleAction(_Frozen):
    kind: Literal[
        "force_disposition",
        "importance_delta",
        "set_importance",
        "add_topics",
        "set_privacy",
        "drop_atoms",
    ]
    value: str | float | bool | None = None
    delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    values: tuple[str, ...] | None = Field(default=None, max_length=5)

    @field_validator("delta", mode="before")
    @classmethod
    def _delta_not_bool(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("delta 不接受 bool")
        return v

    @model_validator(mode="after")
    def _kind_payload(self) -> "RuleAction":
        kind, value, delta, values = self.kind, self.value, self.delta, self.values
        for name, provided in (("value", value), ("delta", delta), ("values", values)):
            if name not in _ACTION_PAYLOAD_ALLOWED[kind] and provided is not None:
                raise ValueError(f"{kind} 不接受 {name}")
        if kind == "force_disposition":
            if value not in ("quarantine", "discard", "mark_write", "allow"):
                raise ValueError("force_disposition 值非法")
        elif kind == "importance_delta":
            if delta is None:
                raise ValueError("importance_delta 需要 delta")
        elif kind == "set_importance":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError("set_importance 需要 value ∈ [0,1] 且不接受 bool")
        elif kind == "add_topics":
            if not values or any(not v or len(v) > 32 for v in values):
                raise ValueError("add_topics 需要 1-5 个非空 ≤32 字符主题")
        elif kind == "set_privacy":
            if value not in ("public", "confidential"):
                raise ValueError("set_privacy 值非法")
        elif kind == "drop_atoms":
            if value is not True:
                raise ValueError("drop_atoms 需要 value=true")
        return self


class GateRuleConfig(_Frozen):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    enabled: bool = True
    description: str = Field(default="", max_length=200)
    when: RulePredicate
    action: RuleAction

    @field_validator("when")
    @classmethod
    def _predicate_shape(cls, node: RulePredicate) -> RulePredicate:
        _validate_predicate(node)
        return node


class GateProfile(_Frozen):
    name: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    checks: GateChecks = Field(default_factory=GateChecks)
    thresholds: GateThresholds = Field(default_factory=GateThresholds)
    scoring: GateScoring = Field(default_factory=GateScoring)
    references: GateReferences = Field(default_factory=GateReferences)
    quality: GateQualityParams = Field(default_factory=GateQualityParams)
    word_lists: GateWordLists = Field(default_factory=GateWordLists)
    judge: GateJudge = Field(default_factory=GateJudge)
    disposition: GateDisposition = "quarantine"
    disposition_overrides: dict[str, GateDisposition] = Field(
        default_factory=dict, max_length=20
    )
    rules: tuple[GateRuleConfig, ...] = Field(default_factory=tuple, max_length=50)

    @field_validator("disposition_overrides")
    @classmethod
    def _freeze_overrides(
        cls, overrides: dict[str, GateDisposition]
    ) -> Mapping[str, GateDisposition]:
        return MappingProxyType(dict(overrides or {}))

    @field_serializer("disposition_overrides")
    def _serialize_overrides(
        self, overrides: Mapping[str, GateDisposition]
    ) -> dict[str, GateDisposition]:
        return dict(overrides)

    @field_validator("rules")
    @classmethod
    def _unique_ids(
        cls, rules: tuple[GateRuleConfig, ...]
    ) -> tuple[GateRuleConfig, ...]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("规则 id 必须唯一")
        return rules

    @model_validator(mode="after")
    def _override_codes(self) -> "GateProfile":
        rule_ids = {rule.id for rule in self.rules}
        for code in self.disposition_overrides:
            if code in BUILTIN_GATE_REASON_CODES:
                continue
            match = _CUSTOM_RULE_RE.fullmatch(code)
            if match and match.group(1) in rule_ids:
                continue
            raise ValueError(f"未知原因码: {code}")
        return self


class GateBinding(_Frozen):
    profile: str
    chat_type: Literal["private", "group"] | None = None
    group_id: str | None = Field(default=None, max_length=64)
    persona_id: str | None = Field(default=None, max_length=64)


def _default_profiles() -> tuple[GateProfile, ...]:
    return (GateProfile(name="private"), GateProfile(name="group"))


def _default_bindings() -> tuple[GateBinding, ...]:
    return (
        GateBinding(profile="private", chat_type="private"),
        GateBinding(profile="group", chat_type="group"),
    )


class GateConfig(_Frozen):
    enabled: bool = True
    default_profile: str = "private"
    bindings: tuple[GateBinding, ...] = Field(
        default_factory=_default_bindings, max_length=50
    )
    profiles: tuple[GateProfile, ...] = Field(
        default_factory=_default_profiles, max_length=20
    )

    @model_validator(mode="after")
    def _refs(self) -> "GateConfig":
        names = [profile.name for profile in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("profile 名必须唯一")
        if self.default_profile not in names:
            raise ValueError("default_profile 必须存在于 profiles")
        for binding in self.bindings:
            if binding.profile not in names:
                raise ValueError(f"绑定引用了不存在的 profile: {binding.profile}")
        return self


class QualityFeatureConfig(_Frozen):
    gate: GateConfig = Field(default_factory=GateConfig)
