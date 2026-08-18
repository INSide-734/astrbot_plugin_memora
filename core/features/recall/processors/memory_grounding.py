"""从当前对话窗口验证抽取记忆的来源忠实性。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any

from ....shared.contracts.conversation import Message
from ...quality.application.gate_runtime import GateSnapshot, default_gate_snapshot
from ...quality.domain.gate_config import (
    BUILTIN_NEGATION_MARKERS,
    BUILTIN_NEGATION_WHITELIST,
    GateProfile,
)
from .grounding_dates import _CJK_NUM_RE, _cjk_to_int, supported_claim_date_numbers

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK_CHUNK_RE = re.compile(r"[\u3400-\u9fff]+")
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
            "start/end 是该条原始消息正文中的字符区间，左闭右开，必须满足 "
            "0 <= start < end <= chars；引用整条正文时使用 start=0、end=chars。"
            "消息头中的时间、昵称和 ID 不属于正文 offset，不得仅据消息头中的时间生成事实。"
            "summary、topics、key_facts 必须保持所引用正文的主要语言，不要把英文事实翻译成中文"
            "或把中文事实翻译成英文。禁止引用当前窗口以外的消息，禁止编造未在引用片段中出现"
            "或无法由其合理改写得到的事实。"
            "正文中的 Observation date/观察日期/对话日期优先于插件当前时间；只有正文没有日期"
            "锚点时，才可使用消息时间戳。无法唯一确定的上周、周末或大约日期必须保留原相对"
            "说法，不得猜测绝对年月日。"
        )

    def validate(
        self,
        candidate: dict[str, Any],
        messages: list[Message],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
    ) -> GroundingResult:
        """验证候选声明、来源范围、关键锚点和群聊主体。"""

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
        if isinstance(raw_refs, list) and raw_refs:
            resolved = self._resolve_references(
                raw_refs,
                messages,
                inferred=False,
                max_refs=profile.references.max_references,
            )
            if resolved is None:
                return self._rejected(
                    "grounding_reference_invalid",
                    claim_text=claim_text,
                )
        else:
            inferred_refs = self._infer_references(claim_text, messages, profile)
            if not inferred_refs:
                return self._rejected(
                    "grounding_source_evidence_missing",
                    claim_text=claim_text,
                )
            resolved = self._resolve_references(
                inferred_refs,
                messages,
                inferred=True,
                max_refs=profile.references.max_references,
            )
            if resolved is None:
                return self._rejected(
                    "grounding_source_evidence_missing",
                    claim_text=claim_text,
                )

        evidence, source_text, referenced_messages = resolved
        if profile.checks.group_subject_check:
            subject_reason = self._validate_group_subject(
                candidate,
                referenced_messages,
                is_group_chat=is_group_chat,
                profile=profile,
            )
            if subject_reason:
                return self._rejected(
                    subject_reason,
                    evidence=evidence,
                    source_text=source_text,
                    claim_text=claim_text,
                )

        if profile.checks.numeric_check:
            numeric_reason = self._validate_numbers(
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
            negation_reason = self._validate_negation(claim_text, source_text, profile)
            if negation_reason:
                return self._rejected(
                    negation_reason,
                    evidence=evidence,
                    source_text=source_text,
                    claim_text=claim_text,
                )

        support_score = self._support_score(claim_text, source_text, profile)
        if support_score >= profile.thresholds.min_deterministic_score:
            return GroundingResult(
                allowed=True,
                status="grounded",
                reason_codes=(),
                evidence=evidence,
                source_text=source_text,
                claim_text=claim_text,
            )
        if support_score >= profile.thresholds.min_judge_score:
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
        evidence: list[Any],
        *,
        is_group_chat: bool,
        profile: GateProfile | None = None,
    ) -> GroundingResult:
        """按消息指纹重新定位持久化证据并复用完整校验。"""

        if profile is None:
            profile = self._snapshot.resolve_profile(
                "group" if is_group_chat else "private", None, None
            )
        if not evidence:
            return self._rejected("grounding_source_evidence_missing")
        examined = evidence[: profile.references.max_references]
        refs: list[dict[str, Any]] = []
        well_formed = 0
        for item in examined:
            if not isinstance(item, dict):
                continue  # 坏证据过滤化：单条畸形不再毁整条复核
            well_formed += 1
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
                continue  # 单项无法匹配时跳过，零条可复用才整体拒绝
            refs.append(
                {
                    "message_index": matched_index,
                    "start": item.get("start"),
                    "end": item.get("end"),
                }
            )
        if not refs:
            if well_formed == 0:
                return self._rejected("grounding_source_evidence_invalid")
            return self._rejected("grounding_source_changed")
        replay = dict(candidate)
        replay["source_refs"] = refs
        return self.validate(
            replay, messages, is_group_chat=is_group_chat, profile=profile
        )

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
        max_refs: int,
    ) -> tuple[list[dict[str, Any]], str, list[Message]] | None:
        """校验引用边界并构造内部证据，不接受布尔值冒充整数。"""

        evidence: list[dict[str, Any]] = []
        snippets: list[str] = []
        referenced_messages: list[Message] = []
        for raw_ref in raw_refs[:max_refs]:
            if not isinstance(raw_ref, dict):
                continue  # 坏引用过滤化：单条非法不再毁整条候选
            message_index = raw_ref.get("message_index")
            start = raw_ref.get("start")
            end = raw_ref.get("end")
            if (
                isinstance(message_index, bool)
                or isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(message_index, int)
                or not isinstance(start, int)
                or not isinstance(end, int)
            ):
                continue
            if message_index < 0 or message_index >= len(messages):
                continue
            message = messages[message_index]
            content = Message.content_to_text(message.content)
            if start < 0 or end <= start or end > len(content):
                continue
            snippet = content[start:end].strip()
            if not snippet:
                continue
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
        profile: GateProfile,
    ) -> list[dict[str, int]]:
        """仅在模型缺少引用时从当前窗口推断高相关受控引用。"""

        min_score = profile.thresholds.min_inference_score
        scored: list[tuple[float, int, str]] = []
        for index, message in enumerate(messages):
            content = Message.content_to_text(message.content)
            if not content.strip():
                continue
            score = self._support_score(claim_text, content, profile)
            scored.append((score, index, content))
        if not scored:
            return []
        scored.sort(reverse=True)
        best_score = scored[0][0]
        if best_score < min_score:
            return []
        selected = [
            item for item in scored if item[0] >= max(min_score, best_score - 0.08)
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
        profile: GateProfile,
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
                self._normalize_text(value, profile)
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
            self._normalize_text(item, profile)
            for item in (candidate.get("participants") or [])
            if isinstance(item, str) and item.strip()
        }
        if not participants:
            return "grounding_subject_ambiguous"
        if any(not labels.intersection(participants) for labels in users.values()):
            return "grounding_subject_mismatch"
        return None

    @classmethod
    def _validate_numbers(
        cls,
        claim_text: str,
        source_text: str,
        referenced_messages: list[Message],
    ) -> str | None:
        """严格匹配普通数值，仅放行可信身份标签与可靠日期规范化。"""

        claim_without_identity_labels = claim_text
        for label in cls._trusted_identity_labels(referenced_messages):
            claim_without_identity_labels = claim_without_identity_labels.replace(
                label, ""
            )
        claim_numbers = cls._canonical_numbers(claim_without_identity_labels)
        source_numbers = cls._canonical_numbers(source_text)
        supported_date_numbers = supported_claim_date_numbers(
            claim_text,
            source_text,
            referenced_messages,
        )
        if claim_numbers - source_numbers - supported_date_numbers:
            return "grounding_numeric_conflict"
        return None

    @staticmethod
    def _trusted_identity_labels(messages: list[Message]) -> set[str]:
        """返回引用消息中由运行时确认的稳定身份标签。"""

        labels: set[str] = set()
        for message in messages:
            metadata = message.metadata if isinstance(message.metadata, dict) else {}
            label = metadata.get("identity_label")
            if metadata.get("identity_trusted") is True and isinstance(label, str):
                normalized = label.strip()
                if normalized:
                    labels.add(normalized)
        return labels

    @staticmethod
    def _canonical_numbers(text: str) -> set[str]:
        """规范前导零和小数尾零，并把中文数字归一为阿拉伯数字。"""

        converted = _CJK_NUM_RE.sub(
            lambda match: str(_cjk_to_int(match.group(0))), text
        )
        canonical: set[str] = set()
        for raw_value in _NUMBER_RE.findall(converted):
            integer, separator, fraction = raw_value.partition(".")
            integer = integer.lstrip("0") or "0"
            if separator:
                fraction = fraction.rstrip("0")
            canonical.add(f"{integer}.{fraction}" if fraction else integer)
        return canonical

    @staticmethod
    def _validate_negation(
        claim_text: str, source_text: str, profile: GateProfile
    ) -> str | None:
        """阻止候选与紧邻来源片段出现相反否定极性（白名单短语先剔除）。"""

        whitelist = {
            phrase.casefold()
            for phrase in (
                *BUILTIN_NEGATION_WHITELIST,
                *profile.word_lists.negation_whitelist,
            )
        }
        claim_clean = claim_text.casefold()
        source_clean = source_text.casefold()
        for phrase in sorted(whitelist, key=len, reverse=True):
            claim_clean = claim_clean.replace(phrase, "")
            source_clean = source_clean.replace(phrase, "")
        marker_cfg = profile.word_lists.negation_markers
        if marker_cfg.mode == "replace":
            markers = tuple(item.casefold() for item in marker_cfg.items)
        else:
            markers = tuple(BUILTIN_NEGATION_MARKERS) + tuple(
                item.casefold() for item in marker_cfg.items
            )
        claim_negative = any(marker in claim_clean for marker in markers)
        source_negative = any(marker in source_clean for marker in markers)
        if claim_negative != source_negative:
            return "grounding_negation_conflict"
        return None

    def _support_score(
        self, claim_text: str, source_text: str, profile: GateProfile
    ) -> float:
        """组合词元覆盖与字符序列相似度，权重由 profile 控制。"""

        claim_normalized = self._normalize_text(claim_text, profile)
        source_normalized = self._normalize_text(source_text, profile)
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
        ) * profile.scoring.token_weight
        if not profile.scoring.sequence_enabled:
            return token_score
        sequence_score = SequenceMatcher(
            None, claim_normalized, source_normalized
        ).ratio()
        return max(token_score, sequence_score * profile.scoring.sequence_weight)

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
    def _normalize_text(value: str, profile: GateProfile | None = None) -> str:
        """统一大小写、兼容字符和同义表达，并保留英文词元边界。"""

        normalized = unicodedata.normalize("NFKC", str(value)).casefold()
        replacements = _SYNONYM_REPLACEMENTS
        if profile is not None:
            replacements = replacements + tuple(
                (pair.source.casefold(), pair.target.casefold())
                for pair in profile.word_lists.synonym_pairs
            )
        for source, target in replacements:
            normalized = normalized.replace(source, target)
        return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", normalized).strip()

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
