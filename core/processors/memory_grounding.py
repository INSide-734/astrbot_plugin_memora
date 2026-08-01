"""从当前对话窗口验证抽取记忆的来源忠实性。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any

from ..models.conversation_models import Message

_MAX_REFERENCES = 8
_MIN_INFERENCE_SCORE = 0.2
_MIN_DETERMINISTIC_SCORE = 0.42
_MIN_JUDGE_SCORE = 0.08
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK_CHUNK_RE = re.compile(r"[\u3400-\u9fff]+")
_NEGATION_MARKERS = ("不", "没", "无", "未", "否", "never", "not", "no")
_GENERIC_TOKENS = {
    "用户",
    "对方",
    "成员",
    "群成员",
    "表示",
    "说道",
    "提到",
    "assistant",
    "user",
}
_SYNONYM_REPLACEMENTS = (
    ("星期五", "周五"),
    ("礼拜五", "周五"),
    ("准备", "计划"),
    ("打算", "计划"),
    ("前往", "去"),
    ("喜爱", "喜欢"),
    ("偏爱", "喜欢"),
    ("更换", "换"),
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

    def prompt_contract(self, message_count: int) -> str:
        """生成要求模型返回匿名来源引用的固定 Prompt 片段。"""

        upper = max(0, int(message_count) - 1)
        return (
            "\n\n# 来源证据要求（必须遵守）\n"
            "对每条 memories[] 结果返回 source_refs 数组。每个引用只能使用当前对话中的 "
            f"S0..S{upper} 标签，并写成 "
            '{"message_index": 0, "start": 0, "end": 12}。'
            "start/end 是该条原始消息正文中的字符区间，左闭右开；禁止引用当前窗口以外的消息，"
            "禁止编造未在引用片段中出现或无法由其合理改写得到的事实。"
        )

    def validate(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        *,
        is_group_chat: bool,
    ) -> GroundingResult:
        """验证候选声明、来源范围、关键锚点和群聊主体。"""

        claim_text = self._claim_text(candidate)
        if not claim_text:
            return self._rejected("grounding_claim_missing", claim_text=claim_text)
        if not messages:
            return self._rejected("grounding_source_missing", claim_text=claim_text)

        raw_refs = candidate.get("source_refs")
        explicit_refs = isinstance(raw_refs, list) and bool(raw_refs)
        if explicit_refs:
            resolved = self._resolve_references(raw_refs, messages, inferred=False)
            if resolved is None:
                return self._rejected(
                    "grounding_reference_invalid",
                    claim_text=claim_text,
                )
        else:
            inferred_refs = self._infer_references(claim_text, messages)
            if not inferred_refs:
                return self._rejected(
                    "grounding_source_evidence_missing",
                    claim_text=claim_text,
                )
            resolved = self._resolve_references(inferred_refs, messages, inferred=True)
            if resolved is None:
                return self._rejected(
                    "grounding_source_evidence_missing",
                    claim_text=claim_text,
                )

        evidence, source_text, referenced_messages = resolved
        subject_reason = self._validate_group_subject(
            candidate,
            referenced_messages,
            is_group_chat=is_group_chat,
        )
        if subject_reason:
            return self._rejected(
                subject_reason,
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
            )

        numeric_reason = self._validate_numbers(claim_text, source_text)
        if numeric_reason:
            return self._rejected(
                numeric_reason,
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
            )
        negation_reason = self._validate_negation(claim_text, source_text)
        if negation_reason:
            return self._rejected(
                negation_reason,
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
            )

        support_score = self._support_score(claim_text, source_text)
        if support_score >= _MIN_DETERMINISTIC_SCORE:
            return GroundingResult(
                allowed=True,
                status="grounded",
                reason_codes=(),
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
            )
        if support_score >= _MIN_JUDGE_SCORE:
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

    def revalidate_stored_evidence(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        evidence: list[dict[str, Any]],
        *,
        is_group_chat: bool,
    ) -> GroundingResult:
        """按消息指纹重新定位持久化证据并复用完整校验。"""

        if not evidence:
            return self._rejected("grounding_source_evidence_missing")
        refs: list[dict[str, int]] = []
        for item in evidence[:_MAX_REFERENCES]:
            if not isinstance(item, dict):
                return self._rejected("grounding_source_evidence_invalid")
            fingerprint = str(item.get("message_fingerprint") or "")
            matched_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if self.message_fingerprint(message) == fingerprint
                ),
                None,
            )
            if matched_index is None:
                return self._rejected("grounding_source_changed")
            refs.append(
                {
                    "message_index": matched_index,
                    "start": item.get("start"),
                    "end": item.get("end"),
                }
            )
        replay = dict(candidate)
        replay["source_refs"] = refs
        return self.validate(replay, messages, is_group_chat=is_group_chat)

    @staticmethod
    def message_fingerprint(message: Message) -> str:
        """生成不暴露正文或身份的稳定消息证据指纹。"""

        content = Message.content_to_text(message.content)
        payload = f"{message.role}\0{content}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _resolve_references(
        self,
        raw_refs: list[Any],
        messages: list[Message],
        *,
        inferred: bool,
    ) -> tuple[list[dict[str, Any]], str, list[Message]] | None:
        """校验引用边界并构造内部证据，不接受布尔值冒充整数。"""

        evidence: list[dict[str, Any]] = []
        snippets: list[str] = []
        referenced_messages: list[Message] = []
        for raw_ref in raw_refs[:_MAX_REFERENCES]:
            if not isinstance(raw_ref, dict):
                return None
            message_index = raw_ref.get("message_index")
            start = raw_ref.get("start")
            end = raw_ref.get("end")
            if any(isinstance(value, bool) for value in (message_index, start, end)):
                return None
            if not all(isinstance(value, int) for value in (message_index, start, end)):
                return None
            if message_index < 0 or message_index >= len(messages):
                return None
            message = messages[message_index]
            content = Message.content_to_text(message.content)
            if start < 0 or end <= start or end > len(content):
                return None
            snippet = content[start:end].strip()
            if not snippet:
                return None
            evidence.append(
                {
                    "message_index": message_index,
                    "start": start,
                    "end": end,
                    "message_fingerprint": self.message_fingerprint(message),
                    "inferred": inferred,
                }
            )
            snippets.append(snippet)
            referenced_messages.append(message)
        if not evidence:
            return None
        return evidence, "\n".join(snippets), referenced_messages

    def _infer_references(
        self,
        claim_text: str,
        messages: list[Message],
    ) -> list[dict[str, int]]:
        """仅在模型缺少引用时从当前窗口推断高相关受控引用。"""

        scored: list[tuple[float, int, str]] = []
        for index, message in enumerate(messages):
            content = Message.content_to_text(message.content)
            if not content.strip():
                continue
            score = self._support_score(claim_text, content)
            scored.append((score, index, content))
        if not scored:
            return []
        scored.sort(reverse=True)
        best_score = scored[0][0]
        if best_score < _MIN_INFERENCE_SCORE:
            return []
        selected = [
            item
            for item in scored
            if item[0] >= max(_MIN_INFERENCE_SCORE, best_score - 0.08)
        ]
        return [
            {"message_index": index, "start": 0, "end": len(content)}
            for _, index, content in selected[:3]
        ]

    def _validate_group_subject(
        self,
        candidate: dict[str, Any],
        referenced_messages: list[Message],
        *,
        is_group_chat: bool,
    ) -> str | None:
        """用真实引用消息验证群聊主体，禁止模型自行交换参与者。"""

        if not is_group_chat:
            return None
        users: dict[str, set[str]] = {}
        for message in referenced_messages:
            if message.role != "user":
                continue
            sender_key = str(message.sender_id or message.sender_name or "").strip()
            if not sender_key:
                continue
            labels = {
                self._normalize_text(value)
                for value in (
                    message.sender_id,
                    message.sender_name,
                    message.metadata.get("identity_label")
                    if isinstance(message.metadata, dict)
                    else None,
                )
                if isinstance(value, str) and value.strip()
            }
            users.setdefault(sender_key, set()).update(labels)
        if len(users) <= 1:
            return None
        participants = {
            self._normalize_text(item)
            for item in (candidate.get("participants") or [])
            if isinstance(item, str) and item.strip()
        }
        if not participants:
            return "grounding_subject_ambiguous"
        if any(not labels.intersection(participants) for labels in users.values()):
            return "grounding_subject_mismatch"
        return None

    @staticmethod
    def _validate_numbers(claim_text: str, source_text: str) -> str | None:
        """要求候选中的数值锚点全部能在引用片段中找到。"""

        claim_numbers = set(_NUMBER_RE.findall(claim_text))
        source_numbers = set(_NUMBER_RE.findall(source_text))
        if claim_numbers - source_numbers:
            return "grounding_numeric_conflict"
        return None

    @staticmethod
    def _validate_negation(claim_text: str, source_text: str) -> str | None:
        """阻止候选与紧邻来源片段出现相反否定极性。"""

        claim_negative = any(
            marker in claim_text.casefold() for marker in _NEGATION_MARKERS
        )
        source_negative = any(
            marker in source_text.casefold() for marker in _NEGATION_MARKERS
        )
        if claim_negative != source_negative:
            return "grounding_negation_conflict"
        return None

    def _support_score(self, claim_text: str, source_text: str) -> float:
        """组合词元覆盖和字符序列相似度，允许有限同义改写。"""

        claim_normalized = self._normalize_text(claim_text)
        source_normalized = self._normalize_text(source_text)
        if not claim_normalized or not source_normalized:
            return 0.0
        if (
            claim_normalized in source_normalized
            or source_normalized in claim_normalized
        ):
            return 1.0
        claim_tokens = self._tokens(claim_normalized)
        source_tokens = self._tokens(source_normalized)
        token_score = (
            len(claim_tokens.intersection(source_tokens)) / len(claim_tokens)
            if claim_tokens
            else 0.0
        )
        sequence_score = SequenceMatcher(
            None, claim_normalized, source_normalized
        ).ratio()
        return max(token_score, sequence_score * 0.7)

    @staticmethod
    def _claim_text(candidate: dict[str, Any]) -> str:
        """合并摘要与去重事实，形成待验证声明。"""

        parts: list[str] = []
        summary = candidate.get("summary") or candidate.get("content")
        if isinstance(summary, str) and summary.strip():
            parts.append(summary.strip())
        for fact in candidate.get("key_facts") or []:
            if isinstance(fact, str) and fact.strip() and fact.strip() not in parts:
                parts.append(fact.strip())
        return " ".join(parts)

    @staticmethod
    def _normalize_text(value: str) -> str:
        """统一大小写、兼容字符和少量稳定同义表达。"""

        normalized = unicodedata.normalize("NFKC", str(value)).casefold()
        for source, target in _SYNONYM_REPLACEMENTS:
            normalized = normalized.replace(source, target)
        return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", normalized)

    @staticmethod
    def _tokens(normalized: str) -> set[str]:
        """提取英文词元与中文二元片段，过滤无信息泛称。"""

        tokens = set(_LATIN_TOKEN_RE.findall(normalized))
        for chunk in _CJK_CHUNK_RE.findall(normalized):
            if len(chunk) == 1:
                tokens.add(chunk)
                continue
            tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        return {token for token in tokens if token not in _GENERIC_TOKENS}

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


__all__ = ["GroundingResult", "MemoryGroundingValidator"]
