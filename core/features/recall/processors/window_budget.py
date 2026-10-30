"""约束一个来源窗口的候选总事实量：去重 → 同事件合并 → 单条与窗口裁剪。

话题分段只能拆分中心，不能绕过窗口预算。本模块是确定性纯函数：同一输入
总得到逐字段相同的输出，且在 slot 分配与幂等键计算之前执行。只产出计数，
不记录正文、ID 或 query；未被改动的候选原样返回。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ...reflection.domain.topic_label_renderer import estimate_token_count
from .quality_validator import QualityValidator
from .topic_fact_index import remap_fact_bindings

WINDOW_MAX_FACTS = 12
WINDOW_MAX_CHARS = 4000
WINDOW_MAX_TOKENS = 2000
MEMORY_MAX_FACTS = 5
_MAX_SOURCE_REFS = 16
_FACT_JOINER = "；"
_UNION_FIELDS = ("topics", "entities", "participants", "emotion_tags")


@dataclass(frozen=True, slots=True)
class WindowBudgetCounts:
    """窗口预算的低敏计数；只含整数。"""

    fact_count: int = 0
    char_count: int = 0
    estimated_tokens: int = 0
    dedup_dropped_count: int = 0
    merged_count: int = 0
    budget_dropped_fact_count: int = 0
    budget_dropped_candidate_count: int = 0

    def as_fields(self) -> dict[str, int]:
        """返回诊断事件 allowlist 中的固定字段名。"""

        return {
            "window_fact_count": self.fact_count,
            "window_char_count": self.char_count,
            "window_estimated_tokens": self.estimated_tokens,
            "window_dedup_dropped_count": self.dedup_dropped_count,
            "window_merged_count": self.merged_count,
            "window_budget_dropped_fact_count": self.budget_dropped_fact_count,
            "window_budget_dropped_candidate_count": (
                self.budget_dropped_candidate_count
            ),
        }


@dataclass(frozen=True, slots=True)
class WindowBudgetResult:
    """预算后的候选与窗口计数。"""

    candidates: list[dict[str, Any]]
    counts: WindowBudgetCounts


@dataclass(slots=True)
class _Fact:
    text: str  # 原始字符串，供 remap_fact_bindings 逐条核对
    key: str
    refs: list[Any] | None  # None：无法证明与该事实对齐
    source: int
    index: int  # 在来源候选 key_facts 中的原始下标
    evidence: int  # 首个证据消息下标；无证据记 -1，裁剪时最先丢弃


@dataclass(slots=True)
class _Work:
    sources: list[int]  # 并入的输入下标，首个为基底
    facts: list[_Fact]
    importance: float
    span: tuple[int, int] | None
    topic_keys: frozenset[str]
    order: int
    refs: list[Any]  # 摘要级 source_refs 并集；合并不得使其超过 _MAX_SOURCE_REFS
    changed: bool = False
    trimmed: bool = False  # 预算裁剪删过事实：因果关系无法再证明，需清空
    # 事实被同源覆盖而整条并入本候选的输入下标：只贡献元数据与重要性。
    extra: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _Tally:
    dedup: int = 0
    merged: int = 0
    dropped_facts: int = 0
    dropped_candidates: int = 0


def apply_window_budget(
    candidates: list[Any], *, merge_events: bool = True
) -> WindowBudgetResult:
    """对一个来源窗口的候选执行去重、同事件合并与预算裁剪。

    ``merge_events=False`` 用于策略 B 由单条 memory 拆出的片段：片段继承同一
    ``source_refs`` 与 ``topics``，按同事件合并会撤销话题拆分。
    """

    # 无事实且无摘要的候选与处理器的跳过口径一致，直接剔除（R6：不生成占位）。
    originals = [
        item
        for item in candidates
        if isinstance(item, dict)
        and (item.get("key_facts") or str(item.get("summary", "") or "").strip())
    ]
    tally = _Tally()
    works = [_to_work(order, item) for order, item in enumerate(originals)]
    works = _absorb_subsets(works, tally)
    works = _drop_duplicate_facts(works, tally)
    if merge_events:
        works = _merge_same_events(works, tally)
    for work in works:
        while len(work.facts) > MEMORY_MAX_FACTS:
            _drop_earliest_fact(work, tally)
    works = _fit_window(works, originals, tally)
    contents = [_content(work, originals) for work in works]
    counts = WindowBudgetCounts(
        fact_count=sum(len(work.facts) for work in works),
        char_count=sum(len(text) for text in contents),
        estimated_tokens=sum(estimate_token_count(text) for text in contents),
        dedup_dropped_count=tally.dedup,
        merged_count=tally.merged,
        budget_dropped_fact_count=tally.dropped_facts,
        budget_dropped_candidate_count=tally.dropped_candidates,
    )
    return WindowBudgetResult(
        candidates=[_to_candidate(work, originals) for work in works],
        counts=counts,
    )


def _normalize(text: str) -> str:
    """NFKC + 空白折叠 + casefold，只用于判等，不改写落库文本。"""

    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _message_indices(refs: Any) -> list[int]:
    """提取引用中的合法消息下标；拒绝布尔值冒充整数。"""

    if not isinstance(refs, list):
        return []
    return [
        ref["message_index"]
        for ref in refs
        if isinstance(ref, dict)
        and isinstance(ref.get("message_index"), int)
        and not isinstance(ref.get("message_index"), bool)
    ]


def _to_work(order: int, item: dict[str, Any]) -> _Work:
    """把候选投影为内部工作态；事实保留原始下标以便绑定核对。"""

    raw_facts = item.get("key_facts")
    raw_facts = raw_facts if isinstance(raw_facts, list) else []
    raw_refs = item.get("fact_source_refs")
    if not isinstance(raw_refs, list) or len(raw_refs) != len(raw_facts):
        raw_refs = None  # 无法证明逐事实对齐：全部事实不携带证据（fail-closed）
    facts: list[_Fact] = []
    for index, text in enumerate(raw_facts):
        if not isinstance(text, str) or not text.strip():
            continue
        refs = raw_refs[index] if raw_refs is not None else None
        refs = refs if isinstance(refs, list) else None
        indices = _message_indices(refs)
        facts.append(
            _Fact(
                text=text,
                key=_normalize(text),
                refs=refs,
                source=order,
                index=index,
                evidence=min(indices) if indices else -1,
            )
        )
    spread = _message_indices(item.get("source_refs"))
    spread += [index for fact in facts for index in _message_indices(fact.refs)]
    topics = item.get("topics")
    return _Work(
        sources=[order],
        facts=facts,
        importance=QualityValidator.validate_importance(item.get("importance", 0.5)),
        span=(min(spread), max(spread)) if spread else None,
        topic_keys=frozenset(
            _normalize(topic)
            for topic in (topics if isinstance(topics, list) else [])
            if isinstance(topic, str) and topic.strip()
        ),
        order=order,
        refs=_union(_list(item.get("source_refs"))),
    )


def _supported(fact: _Fact) -> bool:
    """证据存在、非空且每项都是合法消息引用；否则不能证明事实被支持。"""

    return bool(fact.refs) and len(_message_indices(fact.refs)) == len(fact.refs)


def _covers(kept: _Fact, duplicate: _Fact) -> bool:
    """同源重复：文本规范化相同，且双方证据都有效，重复项证据被保留项包含。

    证据缺失、为空、畸形或未对齐的事实无法证明同源，不能被当作已覆盖而删除，
    必须各自交给 grounding 判定；证据不同的同文事实（例如一条来自 user、一条来自
    assistant）同样不能互相吞并。
    """

    if kept.key != duplicate.key:
        return False
    if not _supported(kept) or not _supported(duplicate):
        return False
    return all(ref in (kept.refs or []) for ref in duplicate.refs or [])


def _covered(work: _Work, other: _Work) -> bool:
    return all(any(_covers(big, fact) for big in other.facts) for fact in work.facts)


def _absorb_subsets(works: list[_Work], tally: _Tally) -> list[_Work]:
    """事实集合被另一候选同源覆盖（相互覆盖时保留更早者）的候选整条并入对方。

    被并入候选的元数据（话题、实体、参与者、因果关系）与重要性转交给最终保留者，
    链式并入时直达链尾，避免写进一个随后也被丢弃的候选。
    """

    target_of: dict[int, int] = {}
    for i, work in enumerate(works):
        if not work.facts:
            continue
        for j, other in enumerate(works):
            if (
                j != i
                and _covered(work, other)
                and (j < i or not _covered(other, work))
            ):
                target_of[i] = j
                break
    kept: list[_Work] = []
    for i, work in enumerate(works):
        if i not in target_of:
            kept.append(work)
            continue
        tally.dedup += len(work.facts)
        root = target_of[i]
        for _ in range(len(works)):
            if root not in target_of:
                break
            root = target_of[root]
        retained = works[root]
        retained.extra.extend([*work.sources, *work.extra])
        retained.importance = max(retained.importance, work.importance)
    return kept


def _drop_duplicate_facts(works: list[_Work], tally: _Tally) -> list[_Work]:
    """同源重复事实在窗口内只保留首次出现；被删空的候选整条丢弃。

    重复项的重要性并入保留者，被删空候选的元数据转交给保留者，避免预算裁剪时
    丢掉本应存活的高重要性事实。
    """

    seen: list[tuple[_Fact, _Work]] = []
    kept: list[_Work] = []
    for work in works:
        had_facts = bool(work.facts)
        unique: list[_Fact] = []
        first_owner: _Work | None = None
        for fact in work.facts:
            owner = next((o for prior, o in seen if _covers(prior, fact)), None)
            if owner is not None:
                tally.dedup += 1
                work.changed = True
                owner.importance = max(owner.importance, work.importance)
                first_owner = first_owner or owner
                continue
            seen.append((fact, work))
            unique.append(fact)
        work.facts = unique
        if unique or not had_facts:
            kept.append(work)
        elif first_owner is not None:
            first_owner.extra.extend([*work.sources, *work.extra])
    return kept


def _same_event(left: _Work, right: _Work) -> bool:
    """来源消息区间相交且话题相交；无事实的纯摘要候选不参与合并。"""

    if not left.facts or not right.facts or left.span is None or right.span is None:
        return False
    overlaps = left.span[0] <= right.span[1] and right.span[0] <= left.span[1]
    return overlaps and bool(left.topic_keys & right.topic_keys)


def _merge_same_events(works: list[_Work], tally: _Tally) -> list[_Work]:
    """按输入顺序贪心并入首个同事件分组，事实按首个证据消息下标升序。

    摘要级 source_refs 并集超过上限时不合并：截断会让保留事实失去证据。
    """

    groups: list[_Work] = []
    for work in works:
        target = next(
            (
                group
                for group in groups
                if _same_event(group, work)
                and len(_union([*group.refs, *work.refs])) <= _MAX_SOURCE_REFS
            ),
            None,
        )
        if target is None:
            groups.append(work)
            continue
        tally.merged += 1
        target.sources.extend(work.sources)
        target.extra.extend(work.extra)
        target.refs = _union([*target.refs, *work.refs])
        # 稳定排序：同一证据位置保持原有先后，结果与重试无关。
        target.facts = sorted(target.facts + work.facts, key=lambda fact: fact.evidence)
        target.importance = max(target.importance, work.importance)
        if target.span is not None and work.span is not None:  # _same_event 已保证
            target.span = (
                min(target.span[0], work.span[0]),
                max(target.span[1], work.span[1]),
            )
        target.topic_keys = target.topic_keys | work.topic_keys
        target.changed = True
    return groups


def _drop_earliest_fact(work: _Work, tally: _Tally) -> None:
    """删除证据最早的事实，保留后续的最终状态；同位置时删靠前者。"""

    position = min(range(len(work.facts)), key=lambda i: work.facts[i].evidence)
    del work.facts[position]
    work.changed = True
    work.trimmed = True
    tally.dropped_facts += 1


def _fit_window(
    works: list[_Work], originals: list[dict[str, Any]], tally: _Tally
) -> list[_Work]:
    """先裁多事实候选里全窗口最早的证据事实，仍超出时按重要性整条丢弃。"""

    kept = list(works)
    while kept and _over_budget(kept, originals):
        trimmable = [work for work in kept if len(work.facts) > 1]
        if trimmable:
            work = min(
                trimmable,
                key=lambda item: min(fact.evidence for fact in item.facts),
            )
            _drop_earliest_fact(work, tally)
            continue
        # 重要性升序；同分时输入顺序靠后者先丢。
        victim = min(kept, key=lambda item: (item.importance, -item.order))
        kept.remove(victim)
        tally.dropped_candidates += 1
    return kept


def _over_budget(works: list[_Work], originals: list[dict[str, Any]]) -> bool:
    """任一窗口上限被突破即视为超预算。"""

    contents = [_content(work, originals) for work in works]
    return (
        sum(len(work.facts) for work in works) > WINDOW_MAX_FACTS
        or sum(len(text) for text in contents) > WINDOW_MAX_CHARS
        or sum(estimate_token_count(text) for text in contents) > WINDOW_MAX_TOKENS
    )


def _content(work: _Work, originals: list[dict[str, Any]]) -> str:
    """与 StorageBuilder 同口径的落库正文：事实拼接，无事实时取摘要。"""

    if work.facts:
        return _FACT_JOINER.join(fact.text.strip() for fact in work.facts)
    return str(originals[work.sources[0]].get("summary", "") or "")


def _to_candidate(work: _Work, originals: list[dict[str, Any]]) -> dict[str, Any]:
    """未改动候选原样返回；改动候选按保留事实确定性重建并重排证据与绑定。"""

    base = originals[work.sources[0]]
    items = [originals[index] for index in (*work.sources, *work.extra)]
    if not work.changed and len(work.sources) == 1:
        return _carry_absorbed(base, work, items)
    key_facts = [fact.text for fact in work.facts]
    candidate = dict(base)
    candidate["key_facts"] = key_facts
    # 摘要按保留事实确定性重建：不保留已删事实的改述，也不借用其证据。
    candidate["summary"] = _FACT_JOINER.join(text.strip() for text in key_facts)
    candidate["importance"] = work.importance
    if all(fact.refs is not None for fact in work.facts):
        candidate["fact_source_refs"] = [list(fact.refs or []) for fact in work.facts]
    else:
        candidate.pop("fact_source_refs", None)
    candidate["fact_bindings"] = _remapped_bindings(work, originals)
    candidate["source_refs"] = list(work.refs)
    _merge_metadata(candidate, items)
    if work.trimmed:
        # 因果双方引用的事实可能已被裁掉，无法再证明关系成立（与事实准入同口径）。
        candidate["causal_relations"] = []
    return candidate


def _carry_absorbed(
    base: dict[str, Any], work: _Work, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """事实未变的候选：仅在吸收了他人元数据或重要性时才复制，否则原样返回。"""

    raised = work.importance != QualityValidator.validate_importance(
        base.get("importance", 0.5)
    )
    if not work.extra and not raised:
        return base
    candidate = dict(base)
    candidate["importance"] = work.importance
    _merge_metadata(candidate, items)
    return candidate


def _merge_metadata(candidate: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """合并话题、实体、参与者、情感标签与因果关系；按首次出现保序去重。"""

    for name in _UNION_FIELDS:
        values = _union(
            value
            for item in items
            for value in _list(item.get(name))
            if isinstance(value, str) and value.strip()
        )
        if values or name in candidate:
            candidate[name] = values
    if len(items) > 1:
        candidate["causal_relations"] = _union(
            relation
            for item in items
            for relation in _list(item.get("causal_relations"))
        )


def _remapped_bindings(
    work: _Work, originals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """逐来源复用 remap_fact_bindings 核对原文，再换算为合并后的下标。"""

    bindings: list[dict[str, Any]] = []
    for source in work.sources:
        positions = [i for i, fact in enumerate(work.facts) if fact.source == source]
        if not positions:
            continue
        remapped = remap_fact_bindings(
            originals[source],
            [work.facts[i].text for i in positions],
            [work.facts[i].index for i in positions],
        )
        for binding in remapped:
            bindings.append({**binding, "fact_index": positions[binding["fact_index"]]})
    return sorted(bindings, key=lambda item: item["fact_index"])


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _union(values: Any) -> list[Any]:
    """按首次出现保序去重；支持 dict 等不可哈希值。"""

    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


__all__ = [
    "MEMORY_MAX_FACTS",
    "WINDOW_MAX_CHARS",
    "WINDOW_MAX_FACTS",
    "WINDOW_MAX_TOKENS",
    "WindowBudgetCounts",
    "WindowBudgetResult",
    "apply_window_budget",
]
