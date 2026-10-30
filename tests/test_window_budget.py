"""来源窗口预算：去重、同事件合并、单条与窗口裁剪的消费者可见契约。"""

from __future__ import annotations

import copy

from core.features.memory.application.representation_migration_contracts import (
    WRITE_KIND_CONTENT,
)
from core.features.memory.application.representation_migration_target import (
    compute_representation_target,
)
from core.features.recall.processors.storage_builder import StorageBuilder
from core.features.recall.processors.window_budget import (
    MEMORY_MAX_FACTS,
    WINDOW_MAX_CHARS,
    WINDOW_MAX_FACTS,
    WINDOW_MAX_TOKENS,
    apply_window_budget,
)
from core.features.reflection.domain.topic_label_renderer import estimate_token_count


def _ref(index: int, end: int = 4) -> dict[str, int]:
    return {"message_index": index, "start": 0, "end": end}


def _candidate(
    facts: list[tuple[str, int]],
    *,
    topics: list[str],
    importance: float = 0.5,
    bindings: bool = False,
) -> dict:
    """每个事实附带一条指向给定消息下标的逐事实引用。"""

    key_facts = [text for text, _ in facts]
    candidate = {
        "summary": "；".join(key_facts),
        "key_facts": key_facts,
        "topics": topics,
        "importance": importance,
        "source_refs": [_ref(index) for _, index in facts],
        "fact_source_refs": [[_ref(index)] for _, index in facts],
    }
    if bindings:
        candidate["fact_bindings"] = [
            {"fact_index": i, "fact": text, "target": topics[0], "target_type": "topic"}
            for i, text in enumerate(key_facts)
        ]
    return candidate


def _assert_aligned(candidates: list[dict]) -> None:
    for candidate in candidates:
        facts = candidate["key_facts"]
        assert len(candidate["fact_source_refs"]) == len(facts)
        for binding in candidate.get("fact_bindings") or []:
            assert 0 <= binding["fact_index"] < len(facts)
            assert binding["fact"] == facts[binding["fact_index"]]


def _window_text(candidates: list[dict]) -> list[str]:
    return ["；".join(candidate["key_facts"]) for candidate in candidates]


def test_independent_topics_stay_separate_and_unchanged() -> None:
    coffee = _candidate([("用户喜欢手冲咖啡", 0)], topics=["咖啡"])
    trip = _candidate([("用户下周去杭州出差", 3)], topics=["出差"])

    result = apply_window_budget([coffee, trip])

    assert result.candidates == [coffee, trip]
    assert result.counts.fact_count == 2
    assert result.counts.merged_count == 0
    assert result.counts.dedup_dropped_count == 0


def test_same_event_round_trips_merge_in_evidence_order() -> None:
    """同一事件被按往返拆成两条：合并为一条，事实按证据消息升序。"""

    outcome = _candidate(
        [("用户最终改约到周五", 4)], topics=["牙医预约"], importance=0.7
    )
    cause = _candidate(
        [("用户周三的牙医预约冲突", 0), ("用户打电话询问改期", 2)],
        topics=["牙医预约"],
        importance=0.5,
        bindings=True,
    )
    outcome["source_refs"] = [_ref(2), _ref(4)]  # 与原因区间相交

    result = apply_window_budget([outcome, cause])

    assert len(result.candidates) == 1
    merged = result.candidates[0]
    assert merged["key_facts"] == [
        "用户周三的牙医预约冲突",
        "用户打电话询问改期",
        "用户最终改约到周五",
    ]
    assert [refs[0]["message_index"] for refs in merged["fact_source_refs"]] == [
        0,
        2,
        4,
    ]
    assert merged["summary"] == "；".join(merged["key_facts"])
    assert merged["importance"] == 0.7
    assert [b["fact_index"] for b in merged["fact_bindings"]] == [0, 1]
    assert result.counts.merged_count == 1
    _assert_aligned(result.candidates)


def test_disjoint_sources_with_shared_topic_are_not_merged() -> None:
    first = _candidate([("用户周一跑了五公里", 0)], topics=["跑步"])
    second = _candidate([("用户周日报名了半马", 6)], topics=["跑步"])

    result = apply_window_budget([first, second])

    assert len(result.candidates) == 2


def test_merge_disabled_keeps_split_segments() -> None:
    """策略 B 片段共享来源与话题，关闭合并时仍保持拆分。"""

    left = _candidate([("用户喜欢猫", 0)], topics=["宠物"])
    right = _candidate([("用户养了一只柯基", 0)], topics=["宠物"])

    assert len(apply_window_budget([left, right]).candidates) == 1
    kept = apply_window_budget([left, right], merge_events=False).candidates
    assert kept == [left, right]


def test_same_source_duplicates_collapse_once() -> None:
    first = _candidate([("用户喜欢 手冲咖啡", 0), ("用户住在上海", 1)], topics=["a"])
    repeat = _candidate([("用户喜欢手冲咖啡", 0)], topics=["b"])
    repeat["key_facts"] = ["用户喜欢　手冲咖啡"]  # 全角空格，NFKC 后同文
    overlap = _candidate([("用户住在上海", 1), ("用户养猫", 5)], topics=["c"])

    result = apply_window_budget([first, repeat, overlap], merge_events=False)

    facts = [fact for c in result.candidates for fact in c["key_facts"]]
    assert facts == ["用户喜欢 手冲咖啡", "用户住在上海", "用户养猫"]
    assert result.counts.dedup_dropped_count == 2
    _assert_aligned(result.candidates)


def test_same_text_with_different_evidence_is_kept_for_grounding() -> None:
    """同文但证据不同不是同源重复，必须各自交给 grounding 判定。"""

    first = _candidate([("用户喜欢拿铁", 0)], topics=["咖啡"])
    second = _candidate([("用户喜欢拿铁", 1)], topics=["饮品"])

    result = apply_window_budget([first, second], merge_events=False)

    assert result.candidates == [first, second]


def test_single_memory_cap_keeps_latest_facts() -> None:
    facts = [(f"用户第{i}次调整计划", i) for i in range(MEMORY_MAX_FACTS + 2)]

    result = apply_window_budget([_candidate(facts, topics=["计划"])])

    kept = result.candidates[0]["key_facts"]
    assert kept == [text for text, _ in facts[-MEMORY_MAX_FACTS:]]
    assert result.counts.budget_dropped_fact_count == 2
    _assert_aligned(result.candidates)


def test_window_fact_budget_trims_earliest_then_drops_lowest_importance() -> None:
    candidates = [
        _candidate(
            [(f"话题{t}事实{i}", t * 10 + i) for i in range(MEMORY_MAX_FACTS)],
            topics=[f"话题{t}"],
            importance=0.4 + t * 0.1,
        )
        for t in range(4)
    ]

    result = apply_window_budget(candidates)

    assert result.counts.fact_count <= WINDOW_MAX_FACTS
    assert result.counts.budget_dropped_fact_count == 4 * MEMORY_MAX_FACTS - 12
    for candidate in result.candidates:
        # 每条都保留最晚证据的事实（最终状态）
        assert candidate["key_facts"][-1].endswith(f"事实{MEMORY_MAX_FACTS - 1}")
    _assert_aligned(result.candidates)


def test_char_and_token_budgets_hold() -> None:
    long_fact = "长" * 900
    candidates = [
        _candidate([(f"{long_fact}{i}", i)], topics=[f"t{i}"], importance=i / 10)
        for i in range(8)
    ]

    result = apply_window_budget(candidates)

    texts = _window_text(result.candidates)
    assert sum(len(text) for text in texts) <= WINDOW_MAX_CHARS
    assert sum(estimate_token_count(text) for text in texts) <= WINDOW_MAX_TOKENS
    assert result.counts.char_count == sum(len(text) for text in texts)
    # 单事实候选只能整条丢弃，最低重要性的先走
    kept_importance = [candidate["importance"] for candidate in result.candidates]
    assert kept_importance == sorted(kept_importance)
    assert min(kept_importance) > 0
    assert result.counts.budget_dropped_candidate_count == 8 - len(result.candidates)


def test_trimmed_candidate_does_not_trigger_content_rewrite() -> None:
    facts = [(f"用户第{i}次调整计划", i) for i in range(MEMORY_MAX_FACTS + 1)]
    candidate = apply_window_budget([_candidate(facts, topics=["计划"])]).candidates[0]
    candidate["fact_source_evidence"] = [
        [{"message_index": refs[0]["message_index"], "role": "user"}]
        for refs in candidate["fact_source_refs"]
    ]

    content, metadata = StorageBuilder.build_storage_format("", candidate, False)
    target = compute_representation_target(content, metadata)

    assert target.write_kind != WRITE_KIND_CONTENT
    assert content == target.content


def test_empty_window_yields_no_candidates() -> None:
    for window in ([], [{"summary": "", "key_facts": []}], ["闲聊"]):
        result = apply_window_budget(window)
        assert result.candidates == []
        assert result.counts.fact_count == 0


def test_misaligned_evidence_is_not_treated_as_coverage() -> None:
    candidate = _candidate([("用户喜欢猫", 0), ("用户喜欢狗", 1)], topics=["宠物"])
    candidate["fact_source_refs"] = [[_ref(0)]]  # 与 key_facts 不等长
    duplicate = _candidate([("用户喜欢猫", 0)], topics=["猫"])

    result = apply_window_budget([duplicate, candidate], merge_events=False)

    # 无法证明对齐的事实不能被当作已覆盖而删除，交给 grounding 单独判定。
    assert result.candidates == [duplicate, candidate]
    assert result.counts.dedup_dropped_count == 0


def test_absorbed_candidate_keeps_importance_and_metadata() -> None:
    kept = _candidate([("用户喜欢手冲咖啡", 0)], topics=["咖啡"], importance=0.2)
    covered = _candidate([("用户喜欢手冲咖啡", 0)], topics=["饮品"], importance=0.9)
    covered["entities"] = ["手冲"]

    result = apply_window_budget([kept, covered], merge_events=False)

    assert len(result.candidates) == 1
    merged = result.candidates[0]
    assert merged["importance"] == 0.9
    assert merged["topics"] == ["咖啡", "饮品"]
    assert merged["entities"] == ["手冲"]


def test_trimmed_candidate_drops_causal_relations() -> None:
    facts = [(f"用户第{i}次调整计划", i) for i in range(MEMORY_MAX_FACTS + 1)]
    candidate = _candidate(facts, topics=["计划"])
    candidate["causal_relations"] = [{"cause": "用户第0次调整计划", "effect": "x"}]

    result = apply_window_budget([candidate])

    assert result.candidates[0]["causal_relations"] == []


def test_merge_never_truncates_summary_source_refs() -> None:
    left = _candidate([(f"左事实{i}", i) for i in range(10)], topics=["旅行"])
    right = _candidate([(f"右事实{i}", i + 5) for i in range(10)], topics=["旅行"])
    left["source_refs"] = [_ref(i, 4 + i) for i in range(10)]
    right["source_refs"] = [_ref(i + 5, 4 + i) for i in range(10)]

    result = apply_window_budget([left, right])

    for candidate in result.candidates:
        assert len(candidate["source_refs"]) <= 16
    assert len(result.candidates) == 2  # 并集 >16 条引用：不合并，保住证据


def test_retry_is_deterministic_and_input_untouched() -> None:
    window = [
        _candidate([("用户周三牙医冲突", 0)], topics=["牙医"], bindings=True),
        _candidate([("用户改约到周五", 1)], topics=["牙医"]),
        _candidate([(f"用户第{i}次调整", i) for i in range(7)], topics=["计划"]),
    ]
    snapshot = copy.deepcopy(window)

    first = apply_window_budget(copy.deepcopy(window))
    second = apply_window_budget(copy.deepcopy(window))

    assert first == second
    apply_window_budget(window)
    assert window == snapshot


def test_counts_are_integers_only() -> None:
    fields = apply_window_budget(
        [_candidate([("用户喜欢猫", 0)], topics=["猫"])]
    ).counts.as_fields()

    assert all(type(value) is int for value in fields.values())
    assert all(name.startswith("window_") for name in fields)
