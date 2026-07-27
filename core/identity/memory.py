"""从可信会话消息构建确定性的长期记忆参与者身份。"""

from __future__ import annotations

import asyncio
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from ..models.conversation_models import Message
from .models import IdentityTrust, ResolvedIdentity

if TYPE_CHECKING:
    from ..storage.protocol_identity_store import ProtocolIdentityStore, StoredIdentity

IDENTITY_SCHEMA_VERSION = "stable-identity-v1"
_MAX_REFERENCE_LINES = 8
_MAX_REFERENCE_NAME_CHARS = 128
_MAX_REFERENCE_LINE_CHARS = 384


@dataclass(frozen=True, slots=True)
class MemoryIdentityContext:
    """保存一次对话批次的稳定参与者元数据与 Prompt 约束。"""

    participant_ids: tuple[str, ...]
    participant_labels: tuple[str, ...]
    participant_name_snapshots: dict[str, str]
    participant_identity_sources: dict[str, dict[str, str]]

    def metadata(self) -> dict[str, Any]:
        """返回可写入 canonical memory 的确定性身份元数据副本。"""

        if not self.participant_ids:
            return {}
        return {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "participant_ids": list(self.participant_ids),
            "participants": list(self.participant_labels),
            "participant_name_snapshots": dict(self.participant_name_snapshots),
            "participant_identity_sources": deepcopy(
                self.participant_identity_sources
            ),
        }

    def prompt_constraint(self) -> str:
        """生成固定身份参考与不可覆盖规则；无可信参与者时返回空文本。"""

        if not self.participant_ids:
            return ""
        references = [
            f"- {self.participant_name_snapshots[user_id]}（{label}）"
            for user_id, label in zip(
                self.participant_ids,
                self.participant_labels,
                strict=True,
            )
        ]
        return "\n".join(
            [
                "",
                "# 稳定参与者身份约束（系统确定，不可由模型覆盖）",
                *references,
                "- 描述参与者时必须使用“当前名称（稳定标识）”格式。",
                "- 禁止猜测、改写或交换稳定标识，也不得把名称变化视为不同用户。",
                "- 输出中的 participants 仅供内容理解；最终身份元数据由系统确定。",
            ]
        )


class MemoryIdentityEnricher:
    """在召回候选副本上按可信证据临时补充历史别名说明。"""

    def __init__(self, store: ProtocolIdentityStore) -> None:
        """绑定只读查询使用的协议身份目录。"""

        self._store = store

    async def enrich(
        self,
        candidates: list[dict[str, Any]],
        *,
        identity: ResolvedIdentity | None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """复制并增强候选；legacy 别名只在原会话作用域内解析。"""

        baseline = self._copy_candidates(candidates)
        if not self._is_trusted_scope(identity):
            return baseline

        working = self._copy_candidates(baseline)
        try:
            for candidate in working:
                metadata = candidate["metadata"]
                lines = await self._reference_lines(
                    metadata,
                    identity,
                    session_id=session_id,
                )
                if lines:
                    metadata["identity_reference_lines"] = lines
            return working
        except asyncio.CancelledError:
            raise
        except Exception:
            return baseline

    @staticmethod
    def _copy_candidates(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """深复制候选及 metadata，并清除任何持久化伪造的临时说明。"""

        copied_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            copied = dict(candidate)
            raw_metadata = candidate.get("metadata")
            if isinstance(raw_metadata, dict):
                try:
                    metadata = deepcopy(raw_metadata)
                except Exception:
                    metadata = dict(raw_metadata)
            else:
                metadata = {}
            metadata.pop("identity_reference_lines", None)
            copied["metadata"] = metadata
            copied_candidates.append(copied)
        return copied_candidates

    @staticmethod
    def _is_trusted_scope(identity: ResolvedIdentity | None) -> bool:
        """判断当前事件是否提供可封闭查询的可信协议作用域。"""

        return bool(
            identity is not None
            and identity.trust_status is IdentityTrust.TRUSTED
            and identity.identity_namespace
            and identity.stable_user_id
            and identity.canonical_user_id
            and identity.scope_type in {"group", "private"}
            and identity.scope_id
        )

    async def _reference_lines(
        self,
        metadata: dict[str, Any],
        identity: ResolvedIdentity,
        *,
        session_id: str | None,
    ) -> list[str]:
        """按稳定来源、群精确别名、私聊当前用户的顺序生成说明。"""

        lines: list[str] = []
        seen_lines: set[str] = set()
        for namespace, stable_id, canonical_id, old_name, label in (
            self._trusted_source_evidence(metadata, identity)
        ):
            stored = await self._identity_in_scope(namespace, stable_id, identity)
            if stored is None or stored.canonical_user_id != canonical_id:
                continue
            self._append_line(
                lines,
                seen_lines,
                old_name=old_name,
                current_name=stored.display_name,
                label=label,
            )
            if len(lines) >= _MAX_REFERENCE_LINES:
                return lines

        if not self._legacy_scope_matches(metadata, session_id):
            return lines
        participants = metadata.get("participants")
        if not isinstance(participants, list):
            return lines
        for raw_name in participants[:32]:
            old_name = _normalize_reference_text(raw_name)
            if old_name is None:
                continue
            stored: StoredIdentity | None
            if identity.scope_type == "group":
                stored = await self._resolve_group_alias(old_name, identity)
            else:
                stored = await self._resolve_private_alias(old_name, identity)
            if stored is None:
                continue
            label = self._identity_label(stored, identity)
            if label is None:
                continue
            self._append_line(
                lines,
                seen_lines,
                old_name=old_name,
                current_name=stored.display_name,
                label=label,
            )
            if len(lines) >= _MAX_REFERENCE_LINES:
                break
        return lines

    @staticmethod
    def _legacy_scope_matches(
        metadata: dict[str, Any],
        session_id: str | None,
    ) -> bool:
        """仅允许 legacy participants 在其 canonical memory 原会话内反查。"""

        memory_session_id = metadata.get("session_id")
        return bool(
            isinstance(session_id, str)
            and session_id
            and isinstance(memory_session_id, str)
            and memory_session_id == session_id
        )

    @staticmethod
    def _trusted_source_evidence(
        metadata: dict[str, Any],
        identity: ResolvedIdentity,
    ) -> list[tuple[str, str, str, str, str]]:
        """验证并提取由可信来源消息确定的稳定参与者证据。"""

        if metadata.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
            return []
        participant_ids = metadata.get("participant_ids")
        participant_labels = metadata.get("participants")
        snapshots = metadata.get("participant_name_snapshots")
        sources = metadata.get("participant_identity_sources")
        if (
            not isinstance(participant_ids, list)
            or not isinstance(participant_labels, list)
            or not isinstance(snapshots, dict)
            or len(participant_ids) != len(participant_labels)
        ):
            return []

        evidence: list[tuple[str, str, str, str, str]] = []
        seen_ids: set[str] = set()
        for raw_id, raw_label in zip(
            participant_ids[:32],
            participant_labels[:32],
            strict=True,
        ):
            canonical_id = _plain_identifier(raw_id)
            label = _normalize_reference_text(raw_label)
            if canonical_id is None or label is None:
                continue
            old_name = _normalize_reference_text(snapshots.get(canonical_id))
            if (
                old_name is None
                or canonical_id in seen_ids
            ):
                continue
            source = sources.get(canonical_id) if isinstance(sources, dict) else None
            if isinstance(source, Mapping):
                protocol = _plain_identifier(source.get("protocol"))
                namespace = _plain_identifier(source.get("identity_namespace"))
                stable_id = _plain_identifier(source.get("stable_user_id"))
                source_label = _normalize_reference_text(source.get("identity_label"))
                if (
                    protocol is None
                    or namespace is None
                    or stable_id is None
                    or source_label != label
                    or namespace != identity.identity_namespace
                ):
                    continue
            elif isinstance(sources, dict) and canonical_id in sources:
                continue
            elif (
                identity.identity_namespace == "qq"
                and label == f"QQ:{canonical_id}"
            ):
                namespace = "qq"
                stable_id = canonical_id
            elif (
                canonical_id == identity.canonical_user_id
                and label == identity.identity_label
                and identity.stable_user_id is not None
            ):
                namespace = identity.identity_namespace
                stable_id = identity.stable_user_id
            else:
                continue
            seen_ids.add(canonical_id)
            evidence.append(
                (namespace, stable_id, canonical_id, old_name, label)
            )
        return evidence

    async def _identity_in_scope(
        self,
        namespace: str,
        stable_id: str,
        identity: ResolvedIdentity,
    ) -> StoredIdentity | None:
        """读取当前显示名称，并拒绝私聊他人或群外身份。"""

        stored = await self._store.get_identity(
            namespace,
            stable_id,
            identity.scope_type,
            identity.scope_id,
        )
        if stored is None:
            return None
        if identity.scope_type == "private":
            if stored.canonical_user_id != identity.canonical_user_id:
                return None
        elif (
            stored.scope_type != "group"
            or stored.scope_id != identity.scope_id
        ):
            return None
        return stored

    async def _resolve_group_alias(
        self,
        old_name: str,
        identity: ResolvedIdentity,
    ) -> StoredIdentity | None:
        """先解析群精确别名，再解析同群成员唯一的全局别名。"""

        namespace = identity.identity_namespace
        group_id = identity.scope_id or ""
        group_owners = await self._store.find_alias_owner_ids(
            namespace,
            old_name,
            "group",
            group_id,
            limit=2,
        )
        if group_owners:
            if len(group_owners) != 1:
                return None
            return await self._identity_in_scope(
                namespace,
                group_owners[0],
                identity,
            )

        global_owners = await self._store.find_alias_owner_ids(
            namespace,
            old_name,
            "global",
            "",
            member_scope_type="group",
            member_scope_id=group_id,
            limit=2,
        )
        if len(global_owners) != 1:
            return None
        return await self._identity_in_scope(
            namespace,
            global_owners[0],
            identity,
        )

    async def _resolve_private_alias(
        self,
        old_name: str,
        identity: ResolvedIdentity,
    ) -> StoredIdentity | None:
        """仅在当前私聊用户自己的全局别名中执行精确匹配。"""

        stable_id = identity.stable_user_id
        if stable_id is None:
            return None
        aliases = await self._store.find_aliases(
            identity.identity_namespace,
            stable_id,
            "global",
            "",
            limit=128,
        )
        if old_name not in {
            normalized
            for alias in aliases
            if (normalized := _normalize_reference_text(alias)) is not None
        }:
            return None
        return await self._identity_in_scope(
            identity.identity_namespace,
            stable_id,
            identity,
        )

    @staticmethod
    def _identity_label(
        stored: StoredIdentity,
        identity: ResolvedIdentity,
    ) -> str | None:
        """返回适配器确定的模型可见标签，不从名称或正文猜测。"""

        if stored.identity_namespace == "qq":
            return f"QQ:{stored.canonical_user_id}"
        if (
            stored.canonical_user_id == identity.canonical_user_id
            and identity.identity_label
        ):
            return _normalize_reference_text(identity.identity_label)
        return None

    @staticmethod
    def _append_line(
        lines: list[str],
        seen_lines: set[str],
        *,
        old_name: str,
        current_name: str,
        label: str,
    ) -> None:
        """追加一条有界且去重的固定格式说明。"""

        normalized_old = _normalize_reference_text(old_name)
        normalized_current = _normalize_reference_text(current_name)
        normalized_label = _normalize_reference_text(label)
        if (
            normalized_old is None
            or normalized_current is None
            or normalized_label is None
            or normalized_old in {normalized_current, normalized_label}
        ):
            return
        line = (
            f"- “{normalized_old}”是历史名称；"
            f"当前显示为“{normalized_current}”（{normalized_label}）。"
        )
        if len(line) > _MAX_REFERENCE_LINE_CHARS:
            return
        if line not in seen_lines:
            seen_lines.add(line)
            lines.append(line)


def build_memory_identity_context(
    messages: Iterable[Message],
) -> MemoryIdentityContext:
    """按首次出现顺序收集可信 user 身份，并保留批次内最新名称。"""

    ordered_ids: list[str] = []
    labels: dict[str, str] = {}
    names: dict[str, str] = {}
    sources: dict[str, dict[str, str]] = {}
    invalid_ids: set[str] = set()
    for message in messages:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if message.role != "user" or metadata.get("identity_trusted") is not True:
            continue
        protocol = _plain_identifier(metadata.get("identity_protocol"))
        namespace = _plain_identifier(metadata.get("identity_namespace"))
        stable_user_id = _plain_identifier(metadata.get("stable_user_id"))
        user_id = _plain_identifier(metadata.get("canonical_user_id"))
        label = _normalize_reference_text(metadata.get("identity_label"))
        sender_id = _plain_identifier(message.sender_id)
        if (
            protocol is None
            or namespace is None
            or stable_user_id is None
            or user_id is None
            or label is None
            or sender_id != user_id
        ):
            continue
        if namespace == "qq" and (
            stable_user_id != user_id or label != f"QQ:{user_id}"
        ):
            continue
        source = {
            "protocol": protocol,
            "identity_namespace": namespace,
            "stable_user_id": stable_user_id,
            "identity_label": label,
        }
        if user_id in invalid_ids:
            continue
        previous_source = sources.get(user_id)
        if previous_source is not None and previous_source != source:
            invalid_ids.add(user_id)
            ordered_ids.remove(user_id)
            labels.pop(user_id, None)
            names.pop(user_id, None)
            sources.pop(user_id, None)
            continue
        if user_id not in labels:
            if len(ordered_ids) >= 32:
                continue
            ordered_ids.append(user_id)
            labels[user_id] = label
            sources[user_id] = source
        name = _non_empty_text(message.sender_name)
        names[user_id] = name or labels[user_id]
    return MemoryIdentityContext(
        participant_ids=tuple(ordered_ids),
        participant_labels=tuple(labels[user_id] for user_id in ordered_ids),
        participant_name_snapshots={
            user_id: names.get(user_id, labels[user_id]) for user_id in ordered_ids
        },
        participant_identity_sources={
            user_id: dict(sources[user_id]) for user_id in ordered_ids
        },
    )


def _non_empty_text(value: object) -> str | None:
    """把非空字符串限制为 128 个码点，其他输入按缺失处理。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:128] if normalized else None


def _plain_identifier(value: object) -> str | None:
    """读取不含空白的稳定标识文本，不对协议标识做 Unicode 改写。"""

    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > 128 or any(char.isspace() for char in stripped):
        return None
    return stripped


def _normalize_reference_text(value: object) -> str | None:
    """以 NFKC 清理模型可见名称或标签，并限制为 128 个码点。"""

    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    without_controls = "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith("C")
    )
    stripped = without_controls.strip()
    return stripped[:_MAX_REFERENCE_NAME_CHARS] if stripped else None
