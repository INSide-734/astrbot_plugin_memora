"""受限 JSON 规则树的确定性解释执行。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..domain.gate_config import DISPOSITION_SAFETY_ORDER, GateProfile, RulePredicate

_STR_FIELDS = ("content", "summary")
_LIST_FIELDS = ("key_facts", "topics", "participants")


@dataclass(frozen=True, slots=True)
class CandidateView:
    """规则评估的脱敏候选视图，不含身份/会话/revision。"""

    content: str = ""
    summary: str = ""
    key_facts: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()
    importance: float = 0.5
    chat_type: str = "private"


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """一条候选的规则评估结果。"""

    importance_delta: float = 0.0
    forced_disposition: str | None = None
    matched_rule_ids: tuple[str, ...] = ()
    add_topics: tuple[str, ...] = ()
    set_importance: float | None = None
    set_privacy: str | None = None
    drop_atoms: bool = False


def _field_value(view: CandidateView, field_name: str) -> Any:
    value = getattr(view, field_name)
    if field_name in _STR_FIELDS:
        return value
    if field_name in _LIST_FIELDS:
        return tuple(value)
    return value


def _matches(node: RulePredicate, view: CandidateView) -> bool:
    """递归求值单个谓词节点；未知 op 防御性失败。"""
    op = node.op
    if op == "and":
        return all(_matches(child, view) for child in (node.children or []))
    if op == "or":
        return any(_matches(child, view) for child in (node.children or []))
    if op == "not":
        return not _matches(node.child, view)  # type: ignore[arg-type]
    value = _field_value(view, node.field or "")
    if op == "regex":
        return re.search(node.pattern or "", str(value)) is not None
    if op == "contains":
        text = " ".join(value) if isinstance(value, tuple) else str(value)
        return any(item in text for item in (node.values or []))
    if op == "exists":
        return bool(value)
    if op == "length_cmp":
        length = len(value)
        target = int(node.value or 0)
        return {
            "gt": length > target,
            "gte": length >= target,
            "lt": length < target,
            "lte": length <= target,
            "eq": length == target,
        }.get(node.cmp or "", False)
    if op == "numeric_cmp":
        target = float(node.value or 0.0)
        return {
            "gt": view.importance > target,
            "gte": view.importance >= target,
            "lt": view.importance < target,
            "lte": view.importance <= target,
            "eq": view.importance == target,
        }.get(node.cmp or "", False)
    raise ValueError(f"gate_unknown_op:{op}")


def evaluate_rules(candidate: CandidateView, profile: GateProfile) -> RuleOutcome:
    """按序评估全部启用规则；delta 累加、首个 force 生效。"""
    delta = 0.0
    forced: str | None = None
    matched: list[str] = []
    topics: list[str] = []
    set_importance: float | None = None
    set_privacy: str | None = None
    drop_atoms = False
    for rule in profile.rules:
        if not rule.enabled:
            continue
        if not _matches(rule.when, candidate):
            continue
        matched.append(rule.id)
        action = rule.action
        if action.kind == "importance_delta" and action.delta is not None:
            delta += action.delta
        elif action.kind == "force_disposition" and forced is None:
            forced = str(action.value)
        elif action.kind == "set_importance" and set_importance is None:
            value = action.value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                set_importance = float(value)
        elif action.kind == "add_topics":
            for topic in action.values or []:
                if topic not in topics:
                    topics.append(topic)
        elif action.kind == "set_privacy" and set_privacy is None:
            set_privacy = str(action.value)
        elif action.kind == "drop_atoms":
            drop_atoms = True
    return RuleOutcome(
        importance_delta=max(-1.0, min(1.0, delta)),
        forced_disposition=forced,
        matched_rule_ids=tuple(matched),
        add_topics=tuple(topics)[:5],
        set_importance=set_importance,
        set_privacy=set_privacy,
        drop_atoms=drop_atoms,
    )


def evaluate_disposition(
    reason_codes: tuple[str, ...],
    outcome: RuleOutcome,
    profile: GateProfile,
) -> str:
    """处置优先级：规则 force > 原因码映射（安全序取最保守）> 默认。"""
    if outcome.forced_disposition is not None:
        return outcome.forced_disposition
    mapped = [
        profile.disposition_overrides[code]
        for code in reason_codes
        if code in profile.disposition_overrides
    ]
    if mapped:
        for disposition in DISPOSITION_SAFETY_ORDER:
            if disposition in mapped:
                return disposition
    return profile.disposition
