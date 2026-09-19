"""逐事实准入的纯投影：分区、证据继承、处置与隔离载荷。

模块只把「已校验的逐事实结果」映射为可准入事实、摘要证据、处置记录和隔离
载荷，不做 IO、不猜证据：被拒绝的事实的证据绝不进入可准入字段，摘要证据只
在被接受事实之间继承。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....platform.security.guardrails import MemoryExtractionResult
from ....shared.contracts.conversation import Message
from ...memory.domain.memory_atom import has_user_source_evidence
from ...quality.domain.gate_config import GateProfile
from .memory_grounding import GroundingResult

if TYPE_CHECKING:
    from .memory_grounding import MemoryGroundingValidator


def fact_dispositions(results: Sequence[GroundingResult]) -> list[dict[str, Any]]:
    """按原始 fact 下标输出逐事实处置（状态与原因码）。"""

    return [
        {
            "fact_index": index,
            "status": result.status,
            "reason_codes": list(result.reason_codes),
        }
        for index, result in enumerate(results)
    ]


def quarantine_fact_candidate(
    facts: list[str],
    results: list[GroundingResult],
    rejected: list[int],
    importance: float,
) -> dict[str, Any]:
    """Keep rejected facts in a separate quarantine payload, never canonical metadata."""
    rejected_facts = [facts[index] for index in rejected]
    reasons = list(
        dict.fromkeys(
            reason for index in rejected for reason in results[index].reason_codes
        )
    )
    return {
        "content": "；".join(rejected_facts),
        "importance": importance,
        "atoms": [],
        "metadata": {
            "key_facts": rejected_facts,
            "fact_source_evidence": [results[index].evidence for index in rejected],
            "fact_dispositions": [
                {
                    "fact_index": index,
                    "status": results[index].status,
                    "reason_codes": list(results[index].reason_codes),
                }
                for index in rejected
            ],
            "source_evidence": [],
            "grounding_status": "quarantine",
            "grounding_reason_codes": reasons,
            "quality_gate_action": "quarantine",
            "summary_quality": "normal",
            "schema_version": "v3",
        },
    }


@dataclass(frozen=True, slots=True)
class FactAdmission:
    """一次候选的逐事实准入投影（不补齐被拒事实，也不借用其来源）。"""

    key_facts: list[str]
    fact_source_evidence: list[list[dict[str, Any]]]
    dispositions: list[dict[str, Any]]
    rejected: list[int]
    summary_rebuilt: bool
    quarantine_candidate: dict[str, Any] | None
    subject_evidence: list[dict[str, Any]]
    grounding: GroundingResult


def project_fact_admission(
    *,
    facts: list[str],
    results: list[GroundingResult],
    summary_allowed: bool,
    summary_evidence: list[dict[str, Any]],
    importance: float,
) -> FactAdmission:
    """把逐事实校验结果投影为可准入事实、摘要证据与隔离载荷。

    摘要证据继承：只在摘要被确定性重建时改为被接受事实自己的证据集合，未重建
    时保持调用方给出的候选级证据；全部事实被拒时候选不携带任何可准入证据。
    """

    accepted = [index for index, result in enumerate(results) if result.allowed]
    rejected = [index for index, result in enumerate(results) if not result.allowed]
    summary_rebuilt = bool(rejected) or not summary_allowed
    quarantine_candidate = None
    if accepted and rejected:
        quarantine_candidate = quarantine_fact_candidate(
            facts, results, rejected, importance
        )
    if accepted:
        key_facts = [facts[index] for index in accepted]
        fact_source_evidence = [results[index].evidence for index in accepted]
        effective_evidence = summary_evidence
        if summary_rebuilt:
            # 确定性拼接的摘要继承保留事实证据，不借用被拒事实的来源。
            effective_evidence = [
                reference for index in accepted for reference in results[index].evidence
            ]
        grounding = GroundingResult(True, "grounded", (), effective_evidence)
    else:
        key_facts = []
        fact_source_evidence = []
        grounding = GroundingResult(
            False,
            "quarantine",
            tuple(
                dict.fromkeys(
                    reason for result in results for reason in result.reason_codes
                )
            ),
            summary_evidence,
        )
    subject_evidence = [
        item for result in results if result.allowed for item in result.evidence
    ]
    if not subject_evidence:
        subject_evidence = list(grounding.evidence)
    return FactAdmission(
        key_facts=key_facts,
        fact_source_evidence=fact_source_evidence,
        dispositions=fact_dispositions(results),
        rejected=rejected,
        summary_rebuilt=summary_rebuilt,
        quarantine_candidate=quarantine_candidate,
        subject_evidence=subject_evidence,
        grounding=grounding,
    )


def apply_fact_admission(
    mem: dict[str, Any],
    admission: FactAdmission,
    topics: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """把准入投影写回候选副本，返回 (候选, 生效 topics)。

    只有被接受的事实及其证据进入候选；全部被拒时清空可准入证据而保留事实文本
    供隔离复核，摘要本身不被改写。
    """

    applied = dict(mem)
    if admission.grounding.allowed:
        applied["key_facts"] = list(admission.key_facts)
        applied["fact_source_evidence"] = list(admission.fact_source_evidence)
        if admission.summary_rebuilt:
            applied["summary"] = "；".join(admission.key_facts)
        if admission.rejected:
            retained_text = " ".join(admission.key_facts).casefold()
            topics = [topic for topic in topics if topic.casefold() in retained_text]
            applied["topics"] = list(topics)
            applied["causal_relations"] = []
    else:
        applied["fact_source_evidence"] = []
    return applied, topics


async def admit_candidate_facts(
    *,
    validator: "MemoryGroundingValidator",
    resolve_judge: Callable[..., Awaitable[GroundingResult]],
    mem: dict[str, Any],
    facts: list[str],
    messages: list[Message],
    is_group_chat: bool,
    profile: GateProfile,
    message_seqs: Sequence[int | None] | None,
    gate_enabled: bool,
    topics: list[str],
    importance: float,
) -> FactAdmission:
    """校验逐事实与摘要、解析 Judge，并返回准入投影。

    门禁关闭时只跳过可配置判定，不跳过逐事实证据解析：候选级证据由
    ``resolve_evidence`` 给出，绝不推断。``CancelledError`` 继续穿透。
    """

    fact_results = validator.validate_facts(
        mem,
        messages,
        is_group_chat=is_group_chat,
        profile=profile,
        message_seqs=message_seqs,
    )
    for index, result in enumerate(fact_results):
        if result.requires_judge:
            fact_results[index] = await resolve_judge(
                result,
                is_group_chat=is_group_chat,
                profile=profile,
                topics=tuple(topics),
                importance=importance,
            )
    summary_result = validator.validate(
        mem,
        messages,
        is_group_chat=is_group_chat,
        profile=profile,
        message_seqs=message_seqs,
    )
    if gate_enabled:
        summary_evidence = summary_result.evidence
    else:
        # Disabling configurable checks never removes the evidence boundary.
        summary_evidence = validator.resolve_evidence(
            mem,
            messages,
            is_group_chat=is_group_chat,
            profile=profile,
            message_seqs=message_seqs,
        )
    return project_fact_admission(
        facts=facts,
        results=fact_results,
        summary_allowed=summary_result.allowed,
        summary_evidence=summary_evidence,
        importance=importance,
    )


def apply_admission_metadata(
    metadata: dict[str, Any],
    admission: FactAdmission,
    *,
    quality: str,
    messages: list[Message],
    identity_metadata: dict[str, Any],
) -> bool:
    """把准入处置、状态、原因、证据与主体写回 metadata，返回是否隔离。"""

    metadata["fact_dispositions"] = admission.dispositions
    grounding = admission.grounding
    metadata["grounding_status"] = grounding.status
    metadata["grounding_reason_codes"] = list(grounding.reason_codes)
    metadata["source_evidence"] = grounding.evidence
    subject_ids = referenced_subject_ids(
        admission.subject_evidence,
        messages,
        identity_metadata,
    )
    if subject_ids:
        metadata["subject_ids"] = list(subject_ids)
    should_quarantine = quality == "low" or not grounding.allowed
    metadata["quality_gate_action"] = "quarantine" if should_quarantine else "allow"
    return should_quarantine


def has_trusted_fact_evidence(facts: Any, evidence: Any) -> bool:
    """事实与逐事实证据逐条对齐且都带用户来源时才可信。"""

    return (
        isinstance(facts, list)
        and bool(facts)
        and all(isinstance(fact, str) and fact.strip() for fact in facts)
        and isinstance(evidence, list)
        and len(facts) == len(evidence)
        and all(has_user_source_evidence(group) for group in evidence)
    )


def referenced_subject_ids(
    evidence: list[dict[str, Any]],
    messages: list[Message],
    identity_metadata: dict[str, Any],
) -> tuple[str, ...]:
    """从候选实际引用的用户消息提取可信 canonical 参与者。"""
    raw_trusted = identity_metadata.get("participant_ids")
    if not isinstance(raw_trusted, list):
        return ()
    trusted = {
        item.strip() for item in raw_trusted if isinstance(item, str) and item.strip()
    }
    if not trusted:
        return ()
    subjects: list[str] = []
    for item in evidence:
        if item.get("role") != "user":
            continue
        index = item.get("message_index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if index < 0 or index >= len(messages):
            continue
        sender_id = messages[index].sender_id
        if sender_id in trusted and sender_id not in subjects:
            subjects.append(sender_id)
    return tuple(subjects)


def guarded_result_to_structured_data(
    guarded: MemoryExtractionResult,
) -> dict[str, Any]:
    """把通过护栏的结构转换为处理器既有字段契约。"""
    memories: list[dict[str, Any]] = []
    for atom in guarded.memories:
        content = atom.content.strip()
        if not content:
            continue
        key_facts = list(atom.key_facts)
        memories.append(
            {
                "summary": content,
                "key_facts": key_facts,
                "topics": list(atom.topics or atom.entities),
                "importance": atom.importance,
                "sentiment": atom.sentiment,
                "emotion_tags": list(atom.emotion_tags),
                "participants": list(atom.participants),
                "causal_relations": list(atom.causal_relations),
                "source_refs": [
                    reference.model_dump() for reference in atom.source_refs
                ],
                "fact_source_refs": [
                    [reference.model_dump() for reference in group]
                    for group in atom.fact_source_refs
                ],
                "confidence": atom.confidence,
                "atom_type": (
                    atom.atom_type if "atom_type" in atom.model_fields_set else None
                ),
            }
        )

    first = memories[0] if memories else {}
    return {
        "summary": first.get("summary", ""),
        "topics": first.get("topics", []),
        "key_facts": first.get("key_facts", []),
        "sentiment": first.get("sentiment", "neutral"),
        "importance": first.get("importance", 0.5),
        "memories": memories,
        "confidence": guarded.confidence,
        "extraction_quality": guarded.extraction_quality,
        "_guardrails_validated": True,
    }


__all__ = [
    "FactAdmission",
    "admit_candidate_facts",
    "apply_admission_metadata",
    "apply_fact_admission",
    "fact_dispositions",
    "guarded_result_to_structured_data",
    "has_trusted_fact_evidence",
    "project_fact_admission",
    "quarantine_fact_candidate",
    "referenced_subject_ids",
]
