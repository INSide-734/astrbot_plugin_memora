"""从当前对话窗口验证抽取记忆的来源忠实性。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from ....shared.contracts.conversation import Message
from ...memory.domain.memory_atom import is_resolved_source_reference
from ...quality.application.gate_runtime import GateSnapshot, default_gate_snapshot
from ...quality.domain.gate_config import GateProfile
from .grounding_checks import (
    support_score,
    validate_group_subject,
    validate_negation,
    validate_numbers,
)
from .grounding_evidence import (
    evidence_fingerprint,
    infer_references,
    match_stored_evidence,
    resolve_references,
)


@dataclass(frozen=True, slots=True)
class GroundingResult:
    """保存一次来源校验的安全结果和受控证据。"""

    allowed: bool
    status: str
    reason_codes: tuple[str, ...]
    evidence: list[dict[str, Any]]
    source_text: str = ""
    claim_text: str = ""
    requires_judge: bool = False

    def with_judge_result(self, supported: bool) -> "GroundingResult":
        """把受预算约束的 Judge 结论合并到确定性结果。"""

        if supported:
            return replace(
                self,
                allowed=True,
                status="grounded",
                reason_codes=("grounding_judge_supported",),
                requires_judge=False,
            )
        return replace(
            self,
            allowed=False,
            status="quarantine",
            reason_codes=("grounding_judge_rejected",),
            requires_judge=False,
        )

    def with_unavailable_judge(self) -> "GroundingResult":
        """在 Judge 不可用时保持保守隔离，不伪造通过。"""

        return replace(
            self,
            allowed=False,
            status="quarantine",
            reason_codes=("grounding_judge_unavailable",),
            requires_judge=False,
        )


class MemoryGroundingValidator:
    """使用受控消息引用、关键锚点和词面覆盖验证记忆候选。"""

    def __init__(self, snapshot: GateSnapshot | None = None) -> None:
        """绑定门禁快照；缺省用内置默认快照（= 当前硬编码行为）。"""
        self._snapshot = snapshot or default_gate_snapshot()

    def prompt_contract(self, message_count: int, max_references: int = 8) -> str:
        """生成要求模型返回受限匿名来源引用的 Prompt 片段。"""

        upper = max(0, int(message_count) - 1)
        return (
            "\n\n# 来源证据要求（必须遵守）\n"
            "每行以 [S<n> chars=N] 标出原始消息正文字符数。对每条 memories[] 结果返回 "
            f"source_refs 数组，每条记忆最多 {max_references} 条 source_refs，每个引用只能使用"
            f"当前对话中的 S0..S{upper} 标签，并写成 "
            '{"message_index": 0, "start": 0, "end": 12}。'
            "每条结果还必须返回 fact_source_refs，其长度必须与 key_facts 完全一致："
            "fact_source_refs[i] 只能包含直接支持 key_facts[i] 的引用数组，不能借用其他"
            "事实的引用。source_refs 只用于 summary/window，不代替逐事实证据。"
            "start/end 是该条原始消息正文中的字符区间，左闭右开，必须满足 "
            "0 <= start < end <= chars；引用整条正文时使用 start=0、end=chars。"
            "消息头中的时间、昵称和 ID 不属于正文 offset，不得仅据消息头中的时间生成事实。"
            "summary、topics、key_facts 必须保持所引用正文的主要语言，不要把英文事实翻译成中文"
            "或把中文事实翻译成英文。禁止引用当前窗口以外的消息，禁止编造未在引用片段中出现"
            "或无法由其合理改写得到的事实。"
            "正文中的 Observation date/观察日期/对话日期优先于插件当前时间；只有正文没有日期"
            "锚点时，才可使用消息时间戳。无法唯一确定的上周、周末或大约日期必须保留原相对"
            "说法，不得猜测绝对年月日。"
            "可写字段边界（必须遵守）：summary、content、key_facts 只能记录当前窗口"
            " user 消息直接支持的、值得长期保留的事实、明确计划、偏好、决定，或经用户"
            "明确确认的关系事实。assistant、system、tool 或环境消息中的建议、推断、自我"
            "描述、能力说明和运行状态不是用户证据，即使措辞更完整也不能作为可写事实，"
            "不得写入上述字段。第一人称只能作为叙述视角，不能把助手自己的行为、建议或"
            '环境描述写成记忆；没有稳定用户事实时必须返回 {"memories": []}。'
        )

    def validate_facts(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
        message_seqs: Sequence[int | None] | None = None,
    ) -> list[GroundingResult]:
        """逐事实校验独立用户证据；缺少持久化消息标识或序号时保守拒绝。"""
        facts = candidate.get("key_facts")
        groups = candidate.get("fact_source_refs")
        if (
            not isinstance(facts, list)
            or not facts
            or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
            or not isinstance(groups, list)
            or len(groups) != len(facts)
            or any(not isinstance(group, list) for group in groups)
        ):
            count = len(facts) if isinstance(facts, list) and facts else 1
            return [
                self._rejected("grounding_fact_evidence_mismatch") for _ in range(count)
            ]
        if message_seqs is not None and not _valid_message_sequences(
            message_seqs, len(messages)
        ):
            return [self._rejected("grounding_message_sequence_invalid") for _ in facts]
        results: list[GroundingResult] = []
        for fact, refs in zip(facts, groups, strict=True):
            if not refs:
                results.append(
                    self._rejected("grounding_source_evidence_missing", claim_text=fact)
                )
                continue
            result = self.validate(
                {
                    "summary": fact,
                    "source_refs": refs,
                    "participants": candidate.get("participants", []),
                },
                messages,
                is_group_chat=is_group_chat,
                profile=profile,
                message_seqs=message_seqs,
            )
            if len(result.evidence) != len(refs):
                result = self._rejected("grounding_reference_invalid", claim_text=fact)
            elif not all(
                is_resolved_source_reference(item) for item in result.evidence
            ):
                result = self._rejected(
                    "grounding_source_evidence_invalid", claim_text=fact
                )
            results.append(result)
        return results

    def validate(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
        message_seqs: Sequence[int | None] | None = None,
    ) -> GroundingResult:
        """验证候选声明、来源范围、角色、关键锚点和群聊主体。

        ``message_seqs`` 是调用方给出的窗口稳定序号，与 ``messages`` 同序；
        缺失时证据保留消息标识与指纹，但不带序号。
        """

        if profile is None:
            profile = self._snapshot.resolve_profile(
                "group" if is_group_chat else "private", None, None
            )
        claim_text = self._claim_text(candidate)

        if not claim_text:
            return self._rejected("grounding_claim_missing", claim_text=claim_text)
        if not messages:
            return self._rejected("grounding_source_missing", claim_text=claim_text)

        raw_refs = candidate.get("source_refs")
        inferred = False
        if isinstance(raw_refs, list) and raw_refs:
            resolved = resolve_references(
                raw_refs,
                messages,
                inferred=False,
                max_refs=profile.references.max_references,
                message_seqs=message_seqs,
            )
            if resolved is None:
                return self._rejected(
                    "grounding_reference_invalid",
                    claim_text=claim_text,
                )
        else:
            inferred = True
            inferred_refs = infer_references(
                claim_text, messages, profile, support_score
            )
            if not inferred_refs:
                return self._rejected(
                    "grounding_source_evidence_missing",
                    claim_text=claim_text,
                )
            resolved = resolve_references(
                inferred_refs,
                messages,
                inferred=True,
                max_refs=profile.references.max_references,
                message_seqs=message_seqs,
            )
            if resolved is None:
                return self._rejected(
                    "grounding_source_evidence_missing",
                    claim_text=claim_text,
                )

        evidence, source_text, referenced_messages = resolved
        if not source_text.strip():
            return self._rejected(
                "grounding_user_source_missing",
                evidence=evidence,
                claim_text=claim_text,
            )
        if profile.checks.group_subject_check:
            subject_reason = validate_group_subject(
                candidate,
                referenced_messages,
                is_group_chat=is_group_chat,
                profile=profile,
                subject_messages=messages if inferred else None,
            )
            if subject_reason:
                return self._rejected(
                    subject_reason,
                    evidence=evidence,
                    source_text=source_text,
                    claim_text=claim_text,
                )

        if profile.checks.numeric_check:
            numeric_reason = validate_numbers(
                claim_text,
                source_text,
                referenced_messages,
            )
            if numeric_reason:
                return self._rejected(
                    numeric_reason,
                    evidence=evidence,
                    source_text=source_text,
                    claim_text=claim_text,
                )
        if profile.checks.negation_check:
            negation_reason = validate_negation(
                claim_text, profile, referenced_messages
            )
            if negation_reason:
                return self._rejected(
                    negation_reason,
                    evidence=evidence,
                    source_text=source_text,
                    claim_text=claim_text,
                )

        score = support_score(claim_text, source_text, profile)
        if score >= profile.thresholds.min_deterministic_score:
            return GroundingResult(
                allowed=True,
                status="grounded",
                reason_codes=(),
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
            )
        if score >= profile.thresholds.min_judge_score:
            return GroundingResult(
                allowed=False,
                status="needs_judge",
                reason_codes=("grounding_needs_judge",),
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
                requires_judge=True,
            )
        return self._rejected(
            "grounding_claim_unsupported",
            evidence=evidence,
            source_text=source_text,
            claim_text=claim_text,
        )

    def resolve_evidence(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
        message_seqs: Sequence[int | None] | None = None,
    ) -> list[dict[str, Any]]:
        """只把受控引用解析为证据，不做处置判定。

        供门禁关闭但仍需为 canonical 绑定来源证据的路径使用：不推断引用、
        不伪造条目，引用缺失或全部非法时返回空列表。
        """

        raw_refs = candidate.get("source_refs")
        if not isinstance(raw_refs, list) or not raw_refs:
            return []
        if profile is None:
            profile = self._snapshot.resolve_profile(
                "group" if is_group_chat else "private", None, None
            )
        resolved = resolve_references(
            raw_refs,
            messages,
            inferred=False,
            max_refs=profile.references.max_references,
            message_seqs=message_seqs,
        )
        return [] if resolved is None else resolved[0]

    def revalidate_stored_evidence(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        evidence: list[Any],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
        message_seqs: Sequence[int | None] | None = None,
    ) -> GroundingResult:
        """Relocate complete evidence without substituting another message."""
        if not evidence:
            return self._rejected("grounding_source_evidence_missing")
        if not all(is_resolved_source_reference(item) for item in evidence):
            return self._rejected("grounding_source_evidence_invalid")
        if message_seqs is not None and not _valid_message_sequences(
            message_seqs, len(messages)
        ):
            return self._rejected("grounding_message_sequence_invalid")
        refs: list[dict[str, Any]] = []
        stored_seqs: list[int | None] = [None] * len(messages)
        for item in evidence:
            matched_index = match_stored_evidence(item, messages)
            if matched_index is None:
                return self._rejected("grounding_source_changed")
            sequence = item["message_seq"]
            if message_seqs is not None and message_seqs[matched_index] != sequence:
                return self._rejected("grounding_source_changed")
            if stored_seqs[matched_index] not in (None, sequence):
                return self._rejected("grounding_source_changed")
            stored_seqs[matched_index] = sequence
            refs.append(
                {
                    "message_index": matched_index,
                    "start": item["start"],
                    "end": item["end"],
                }
            )
        result = self.validate(
            {**candidate, "source_refs": refs},
            messages,
            is_group_chat=is_group_chat,
            profile=profile,
            message_seqs=message_seqs if message_seqs is not None else stored_seqs,
        )
        if len(result.evidence) != len(refs):
            return self._rejected("grounding_reference_invalid")
        return result

    def revalidate_facts(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
        message_seqs: Sequence[int | None] | None = None,
    ) -> list[GroundingResult]:
        """Revalidate aligned persisted fact groups independently on approval."""
        facts = candidate.get("key_facts")
        groups = candidate.get("fact_source_evidence")
        if (
            not isinstance(facts, list)
            or not facts
            or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
            or not isinstance(groups, list)
            or len(groups) != len(facts)
            or any(not isinstance(group, list) for group in groups)
        ):
            return [self._rejected("grounding_fact_evidence_mismatch")]
        return [
            self.revalidate_stored_evidence(
                {"summary": fact, "participants": candidate.get("participants", [])},
                messages,
                group,
                is_group_chat=is_group_chat,
                profile=profile,
                message_seqs=message_seqs,
            )
            for fact, group in zip(facts, groups, strict=True)
        ]

    @staticmethod
    def message_fingerprint(message: Message) -> str:
        """生成不暴露正文或身份的稳定消息证据指纹。"""

        return evidence_fingerprint(message)

    @staticmethod
    def _claim_text(candidate: dict[str, Any]) -> str:
        """Return one claim; summary evidence never borrows support from facts."""
        claim = candidate.get("summary") or candidate.get("content")
        return claim.strip() if isinstance(claim, str) else ""

    @staticmethod
    def _rejected(
        reason_code: str,
        *,
        evidence: list[dict[str, Any]] | None = None,
        source_text: str = "",
        claim_text: str = "",
    ) -> GroundingResult:
        """构造统一的保守隔离结果。"""

        return GroundingResult(
            allowed=False,
            status="quarantine",
            reason_codes=(reason_code,),
            evidence=evidence or [],
            source_text=source_text,
            claim_text=claim_text,
        )


def _valid_message_sequences(values: Sequence[int | None], count: int) -> bool:
    if len(values) != count:
        return False
    previous = -1
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= previous:
            return False
        previous = value
    return True


__all__ = ["GroundingResult", "MemoryGroundingValidator"]
