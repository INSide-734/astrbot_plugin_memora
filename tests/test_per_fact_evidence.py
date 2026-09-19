"""Per-fact evidence boundaries from extraction through canonical admission."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.quality.application.gate_runtime import (
    GateRuntime,
    build_gate_snapshot,
)
from core.features.quality.application.memory_quality_gate import MemoryQualityGate
from core.features.quality.domain.gate_config import GateConfig, GateProfile
from core.features.quality.infrastructure.quarantine_store import MemoryQuarantineStore
from core.features.recall.processors.json_parser import JsonParser, SummaryParseError
from core.features.recall.processors.memory_grounding import MemoryGroundingValidator
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.shared.contracts.conversation import Message
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope


def _message(content: str, *, index: int = 0, role: str = "user") -> Message:
    return Message(
        id=index + 11,
        session_id="session-facts",
        role=role,
        content=content,
        sender_id="source-user" if role == "user" else "source-assistant",
        sender_name="Reader" if role == "user" else "Assistant",
        timestamp=100.0 + index,
    )


def _refs(index: int, content: str) -> list[dict[str, int]]:
    return [{"message_index": index, "start": 0, "end": len(content)}]


def _candidate(facts: list[str]) -> dict:
    return {
        "summary": "；".join(facts),
        "key_facts": facts,
        "fact_source_refs": [_refs(index, fact) for index, fact in enumerate(facts)],
        "source_refs": [
            ref for index, fact in enumerate(facts) for ref in _refs(index, fact)
        ],
        "importance": 0.8,
        "topics": ["绿茶", "潜水"],
    }


def _processor(
    candidate: dict,
    *,
    profile: GateProfile | None = None,
    judge=None,
    gate_enabled: bool = True,
    gate_runtime: GateRuntime | None = None,
    topic_strategy: str | None = None,
    topic_embed_fn=None,
) -> MemoryProcessor:
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=MagicMock(completion_text=json.dumps({"memories": [candidate]}))
    )
    runtime = gate_runtime or _gate_runtime(
        profile or GateProfile(name="private"), enabled=gate_enabled
    )
    return MemoryProcessor(
        llm_provider=provider,
        gate_runtime=runtime,
        grounding_judge=judge,
        topic_embed_fn=topic_embed_fn,
        config={
            "topic_segmentation.enabled": topic_strategy is not None,
            "topic_segmentation.strategy": topic_strategy or "a_b_hybrid",
            "atom_quality_filter_enabled": False,
        },
    )


def _gate_runtime(profile: GateProfile, *, enabled: bool = True) -> GateRuntime:
    """按单个 profile 构造门禁运行时，供处理器与质量门共用同一判定配置。"""

    return GateRuntime(
        build_gate_snapshot(
            GateConfig(enabled=enabled, profiles=(profile,), bindings=())
        )
    )


@pytest.mark.parametrize(
    "groups",
    [None, [], [[{"message_index": 0, "start": 0, "end": 1}]], [[], "invalid"]],
)
def test_schema_and_validator_reject_misaligned_fact_groups(groups) -> None:
    facts = ["用户喜欢绿茶。", "用户喜欢潜水。"]
    candidate = {**_candidate(facts), "fact_source_refs": groups}
    with pytest.raises(SummaryParseError) as error:
        JsonParser().parse_summary_response(json.dumps({"memories": [candidate]}))
    assert error.value.reason == "grounding_fact_evidence_mismatch"
    results = MemoryGroundingValidator().validate_facts(
        candidate, [_message(facts[0])], is_group_chat=False, message_seqs=(1,)
    )
    assert [result.reason_codes for result in results] == [
        ("grounding_fact_evidence_mismatch",)
    ] * 2


@pytest.mark.parametrize(
    "bad_ref",
    [
        {"message_index": 1, "start": 0, "end": 2},
        {"message_index": 0, "start": -1, "end": 2},
        {"message_index": 0, "start": 2, "end": 2},
        {"message_index": 0, "start": 0, "end": 1000},
        {"message_index": True, "start": 0, "end": 2},
    ],
)
def test_one_bad_ref_cannot_borrow_another_valid_ref(bad_ref) -> None:
    fact = "用户每天早晨喜欢喝绿茶。"
    candidate = _candidate([fact])
    candidate["fact_source_refs"][0].append(bad_ref)
    result = MemoryGroundingValidator().validate_facts(
        candidate, [_message(fact)], is_group_chat=False, message_seqs=(1,)
    )[0]
    assert not result.allowed
    assert result.reason_codes == ("grounding_reference_invalid",)


@pytest.mark.parametrize(
    "sequences", [(1,), (1, 1), (2, 1), (True, 2), (None, 2), (-1, 2)]
)
def test_fact_validation_rejects_invalid_window_sequences(sequences) -> None:
    facts = ["用户每天早晨喜欢喝绿茶。", "用户每周周末都会游泳。"]
    results = MemoryGroundingValidator().validate_facts(
        _candidate(facts),
        [_message(fact, index=index) for index, fact in enumerate(facts)],
        is_group_chat=False,
        message_seqs=sequences,
    )
    assert all(
        not result.allowed
        and result.reason_codes == ("grounding_message_sequence_invalid",)
        for result in results
    )


def test_legacy_fact_cannot_infer_missing_anonymous_refs() -> None:
    fact = "用户每天早晨喜欢喝绿茶。"
    candidate = _candidate([fact])
    candidate.pop("fact_source_refs")
    result = MemoryGroundingValidator().validate_facts(
        candidate, [_message(fact)], is_group_chat=False, message_seqs=(1,)
    )[0]
    assert result.reason_codes == ("grounding_fact_evidence_mismatch",)


def test_revalidation_relocates_same_message_not_same_text() -> None:
    fact = "用户每天早晨喜欢喝绿茶。"
    message = _message(fact)
    validator = MemoryGroundingValidator()
    original = validator.validate_facts(
        _candidate([fact]), [message], is_group_chat=False, message_seqs=(8,)
    )[0]
    stored = {"key_facts": [fact], "fact_source_evidence": [original.evidence]}
    relocated = validator.revalidate_facts(
        stored,
        [_message("另一条消息", index=1), message],
        is_group_chat=False,
        message_seqs=(7, 8),
    )[0]
    assert relocated.allowed
    assert relocated.evidence[0]["message_index"] == 1
    assert {
        key: relocated.evidence[0][key]
        for key in (
            "message_id",
            "message_seq",
            "role",
            "start",
            "end",
            "message_fingerprint",
        )
    } == {
        key: original.evidence[0][key]
        for key in (
            "message_id",
            "message_seq",
            "role",
            "start",
            "end",
            "message_fingerprint",
        )
    }
    for changed, sequences in (
        (replace(message, id=12), (8,)),
        (replace(message, role="assistant"), (8,)),
        (message, (9,)),
    ):
        result = validator.revalidate_facts(
            stored, [changed], is_group_chat=False, message_seqs=sequences
        )[0]
        assert result.reason_codes == ("grounding_source_changed",)


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_enabled", [True, False])
async def test_mixed_candidate_only_admits_user_fact(tmp_path, gate_enabled) -> None:
    facts = ["用户每天早晨喜欢喝绿茶。", "用户已经获得高级潜水认证。"]
    candidate = _candidate(facts)
    candidate["causal_relations"] = [{"cause": facts[0], "effect": facts[1]}]
    processor = _processor(candidate, gate_enabled=gate_enabled)
    candidates = await processor.process_conversation(
        [_message(facts[0]), _message(facts[1], index=1, role="assistant")],
        message_seqs=(41, 42),
    )
    store = MemoryQuarantineStore(tmp_path / "quarantine.sqlite3")
    await store.initialize()
    gate = MemoryQualityGate(
        store,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
    )
    admitted = []
    quarantined = []
    for item in candidates:
        verdict = await gate.route_candidate(
            item,
            session_id="session-facts",
            persona_id=None,
            source_window={"start_seq": 40, "end_seq": 42},
            is_group_chat=False,
        )
        if verdict.action == "allow":
            admitted.append(item)
        else:
            quarantined.append(await store.get_candidate(verdict.candidate_id))
    assert [item["metadata"]["key_facts"] for item in admitted] == [[facts[0]]]
    accepted = admitted[0]
    assert accepted["content"] == facts[0]
    from core.features.injection.application.selection import metadata_has_user_evidence

    assert metadata_has_user_evidence(accepted["metadata"])
    assert all(ref["role"] == "user" for ref in accepted["metadata"]["source_evidence"])
    assert [atom.content for atom in accepted["atoms"]] == [facts[0]]
    assert (
        accepted["atoms"][0].source_evidence
        == accepted["metadata"]["fact_source_evidence"][0]
    )
    assert facts[1] not in json.dumps(accepted["metadata"], ensure_ascii=False)
    assert "causal_relations" not in accepted["metadata"]
    assert accepted["metadata"]["topics"] == ["绿茶"]
    assert quarantined[0]["metadata"]["key_facts"] == [facts[1]]
    assert "grounding_user_source_missing" in quarantined[0]["reason_codes"]


@pytest.mark.asyncio
async def test_judge_verdicts_are_per_fact_and_payloads_are_anonymous() -> None:
    facts = ["用户每天早晨喜欢喝绿茶。", "用户每周周末都会游泳。"]
    sources = ["用户早晨总会泡绿茶，喜欢香气。", "用户周末安排游泳来锻炼身体。"]
    candidate = _candidate(facts)
    candidate["fact_source_refs"] = [
        _refs(index, source) for index, source in enumerate(sources)
    ]
    judge = AsyncMock(side_effect=[True, False])
    profile = GateProfile.model_validate(
        {
            "name": "private",
            "judge": {"enabled": True},
            "thresholds": {"min_deterministic_score": 1.0, "min_judge_score": 0.0},
            "scoring": {"token_weight": 0.0, "sequence_enabled": False},
        }
    )
    processor = _processor(candidate, profile=profile, judge=judge)
    with extra_llm_budget_scope(ExtraLlmBudget(2)):
        candidates = await processor.process_conversation(
            [_message(source, index=index) for index, source in enumerate(sources)],
            message_seqs=(41, 42),
        )
    assert [call.args[0] for call in judge.await_args_list] == [
        {"claim_text": fact, "source_text": source}
        for fact, source in zip(facts, sources, strict=True)
    ]
    assert candidates[0]["metadata"]["key_facts"] == [facts[0]]
    assert [atom.content for atom in candidates[0]["atoms"]] == [facts[0]]
    assert candidates[1]["metadata"]["grounding_reason_codes"] == [
        "grounding_judge_rejected"
    ]


@pytest.mark.asyncio
async def test_fact_judge_cancellation_stops_admission() -> None:
    fact = "用户每天早晨喜欢喝绿茶。"
    source = "用户早晨总会泡绿茶，喜欢香气。"
    candidate = _candidate([fact])
    candidate["fact_source_refs"] = [_refs(0, source)]
    profile = GateProfile.model_validate(
        {
            "name": "private",
            "judge": {"enabled": True},
            "thresholds": {"min_deterministic_score": 1.0, "min_judge_score": 0.0},
            "scoring": {"token_weight": 0.0, "sequence_enabled": False},
        }
    )
    processor = _processor(
        candidate,
        profile=profile,
        judge=AsyncMock(side_effect=asyncio.CancelledError),
    )
    with (
        extra_llm_budget_scope(ExtraLlmBudget(1)),
        pytest.raises(asyncio.CancelledError),
    ):
        await processor.process_conversation([_message(source)], message_seqs=(41,))


@pytest.mark.asyncio
async def test_duplicate_facts_bind_their_own_source_through_clustering() -> None:
    """重复 fact 的引用按下标跟随各自来源，不因文本相同共用首个证据。"""

    fact = "用户喜欢喝拿铁咖啡，每天早上都会喝一杯。"

    async def _orthogonal_embeddings(texts: list[str]) -> list[list[float]]:
        """给每条事实一个正交向量，强制重复事实落在不同话题簇。"""

        return [
            [1.0 if row == column else 0.0 for row in range(len(texts))]
            for column in range(len(texts))
        ]

    candidate = {
        "summary": fact,
        "key_facts": [fact, fact],
        "fact_source_refs": [_refs(0, fact), _refs(1, fact)],
        "source_refs": [_refs(0, fact), _refs(1, fact)],
        "importance": 0.8,
    }
    processor = _processor(
        candidate,
        topic_strategy="b",
        topic_embed_fn=_orthogonal_embeddings,
    )
    candidates = await processor.process_conversation(
        [_message(fact), _message(fact, index=1, role="assistant")],
        message_seqs=(41, 42),
    )

    assert len(candidates) == 2
    admitted, rejected = candidates
    assert admitted["metadata"]["key_facts"] == [fact]
    assert admitted["metadata"]["quality_gate_action"] == "allow"
    assert [
        (evidence["message_index"], evidence["role"])
        for group in admitted["metadata"]["fact_source_evidence"]
        for evidence in group
    ] == [(0, "user")]
    assert rejected["metadata"]["key_facts"] == [fact]
    assert rejected["metadata"]["quality_gate_action"] == "quarantine"
    assert rejected["metadata"]["grounding_reason_codes"] == [
        "grounding_user_source_missing"
    ]
    assert rejected["atoms"] == []


@pytest.mark.asyncio
async def test_all_rejected_facts_stay_unwritable_under_forced_allow(tmp_path) -> None:
    """summary 通过但 fact 全被拒绝时只能隔离：强制 allow 也不得进入 canonical。"""

    summary = "用户每天早晨喜欢喝绿茶。"
    rejected_fact = "用户已经获得高级潜水认证。"
    candidate = {
        "summary": summary,
        "key_facts": [rejected_fact],
        "fact_source_refs": [_refs(1, "今天的天气还不错。")],
        "source_refs": _refs(0, summary),
        "importance": 0.8,
    }
    profile = GateProfile.model_validate(
        {
            "name": "private",
            "rules": [
                {
                    "id": "force-allow",
                    "when": {"field": "content", "op": "exists"},
                    "action": {"kind": "force_disposition", "value": "allow"},
                }
            ],
        }
    )
    runtime = _gate_runtime(profile)
    processor = _processor(candidate, profile=profile, gate_runtime=runtime)
    candidates = await processor.process_conversation(
        [_message(summary), _message("今天的天气还不错。", index=1)],
        message_seqs=(41, 42),
    )

    assert len(candidates) == 1
    rejected = candidates[0]
    assert rejected["metadata"]["quality_gate_action"] == "quarantine"
    assert rejected["metadata"]["grounding_reason_codes"] == [
        "grounding_claim_unsupported"
    ]
    assert rejected["atoms"] == []

    store = MemoryQuarantineStore(tmp_path / "quarantine.sqlite3")
    await store.initialize()
    gate = MemoryQualityGate(
        store,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=runtime,
    )
    verdict = await gate.route_candidate(
        rejected,
        session_id="session-facts",
        persona_id=None,
        source_window={"start_seq": 40, "end_seq": 42},
        is_group_chat=False,
    )

    assert verdict.action == "quarantined"
    staged = await store.get_candidate(verdict.candidate_id)
    assert staged is not None
    assert staged["metadata"]["key_facts"] == [rejected_fact]


@pytest.mark.asyncio
async def test_partial_rejection_keeps_rejected_fact_out_of_admitted_candidate() -> (
    None
):
    """部分接受时准入候选只带被接受事实及其自身证据，被拒绝事实只进隔离诊断。"""

    accepted_fact = "用户每天早晨喜欢喝绿茶。"
    rejected_fact = "用户已经获得高级潜水认证。"
    candidate = {
        "summary": "；".join([accepted_fact, rejected_fact]),
        "key_facts": [accepted_fact, rejected_fact],
        "fact_source_refs": [_refs(0, accepted_fact), _refs(1, rejected_fact)],
        "source_refs": [_refs(0, accepted_fact), _refs(1, rejected_fact)],
        "importance": 0.8,
        "topics": ["绿茶", "潜水"],
    }
    processor = _processor(candidate)
    candidates = await processor.process_conversation(
        [_message(accepted_fact), _message(rejected_fact, index=1, role="assistant")],
        message_seqs=(41, 42),
    )

    assert len(candidates) == 2
    admitted, quarantined = candidates
    assert admitted["metadata"]["quality_gate_action"] == "allow"
    assert admitted["metadata"]["key_facts"] == [accepted_fact]
    assert admitted["content"] == accepted_fact
    assert rejected_fact not in admitted["content"]
    assert rejected_fact not in json.dumps(admitted["metadata"], ensure_ascii=False)
    assert [
        (evidence["message_id"], evidence["message_seq"], evidence["role"])
        for group in admitted["metadata"]["fact_source_evidence"]
        for evidence in group
    ] == [(11, 41, "user")]
    assert [atom.content for atom in admitted["atoms"]] == [accepted_fact]
    assert (
        admitted["atoms"][0].source_evidence
        == admitted["metadata"]["fact_source_evidence"][0]
    )
    assert admitted["metadata"]["topics"] == ["绿茶"]

    assert quarantined["metadata"]["key_facts"] == [rejected_fact]
    assert quarantined["metadata"]["grounding_status"] == "quarantine"
    assert quarantined["atoms"] == []


def test_legacy_text_cluster_guesses_no_fact_evidence() -> None:
    """旧式文本簇无法保持原始下标：不猜证据，只能隔离。"""

    from core.features.recall.processors.topic_splitter import (
        _build_segments_from_clusters,
    )

    data = {
        "summary": "s",
        "key_facts": ["相同事实", "相同事实"],
        "fact_source_refs": [_refs(0, "相同事实"), _refs(1, "相同事实")],
    }
    segments = _build_segments_from_clusters(data, [["相同事实"], ["相同事实"]])

    assert [segment.key_facts for segment in segments] == [["相同事实"], ["相同事实"]]
    assert all("fact_source_refs" not in segment.metadata for segment in segments)
    candidate = {"summary": segments[0].content, **segments[0].metadata}
    results = MemoryGroundingValidator().validate_facts(
        candidate, [_message("相同事实")], is_group_chat=False, message_seqs=(1,)
    )
    assert [result.reason_codes for result in results] == [
        ("grounding_fact_evidence_mismatch",)
    ]
    assert not results[0].allowed
