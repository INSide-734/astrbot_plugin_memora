"""从可信 canonical memory 生成并应用用户画像 proposal。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from ..base.cost_control import CostControl
from ..base.extra_llm_budget import budgeted_extra_llm_call
from ..features.profiles.application import ProfileManager
from ..features.profiles.domain.models import UserTag
from ..identity.memory import IDENTITY_SCHEMA_VERSION
from ..models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ..models.memory_evolution import MemorySourceRef
from ..processors.profile_extractor import ProfileExtractor

_MAX_EVIDENCE_CHARS = 2_000
_MAX_TOPIC_CHARS = 64
_MAX_TOPICS = 16
_REPLY_STYLES = frozenset({"casual", "formal", "concise", "detailed"})


class CanonicalSourceStore(Protocol):
    """声明画像管线所需的 canonical source 读取能力。"""

    async def load_sources(
        self,
        memory_ids: Sequence[int],
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """按 ID 返回带 revision、作用域和隐私的 canonical 快照。"""


class ProfileProposalPipeline:
    """编排可信主体解析、画像抽取和 provenance 约束写入。"""

    def __init__(
        self,
        *,
        profile_manager: ProfileManager,
        source_store: CanonicalSourceStore,
        get_memory: Callable[[int], Awaitable[dict[str, Any] | None]],
        extractor: ProfileExtractor,
        cost_control: CostControl,
        min_tag_confidence: float = 0.1,
    ) -> None:
        """保存生产依赖，并规范标签置信度门槛。"""

        self._profile_manager = profile_manager
        self._source_store = source_store
        self._get_memory = get_memory
        self._extractor = extractor
        self._cost_control = cost_control
        self._min_tag_confidence = max(0.0, min(1.0, float(min_tag_confidence)))

    async def apply_for_memory(self, memory_id: int) -> bool:
        """为单条 canonical memory 生成并应用来源约束画像。

        返回:
            至少写入一个自动标签或偏好时返回 ``True``；身份或来源不满足
            封闭条件、没有可用 proposal 时返回 ``False``。

        异常:
            asyncio.CancelledError: 任务或 Provider 调用被取消。
            Exception: canonical 读取或画像持久化失败，由写后钩子隔离。
        """

        normalized_id = int(memory_id)
        memory = await self._get_memory(normalized_id)
        user_id = trusted_profile_subject_id(memory)
        if user_id is None:
            return False

        sources = await self._source_store.load_sources(
            (normalized_id,),
            max_content_chars=_MAX_EVIDENCE_CHARS,
        )
        if len(sources) != 1 or sources[0].memory_id != normalized_id:
            return False
        source = sources[0]
        content = str(source.content or "").strip()[:_MAX_EVIDENCE_CHARS]
        if not content:
            return False

        tags: list[UserTag] = []
        preferences: dict[str, Any] = {}
        async with budgeted_extra_llm_call(
            self._cost_control,
            "profile_extraction",
        ) as allowed:
            if allowed:
                extracted_tags, extracted_preferences = await self._extractor.extract(
                    content
                )
                tags = list(extracted_tags or [])
                preferences = _normalize_preferences(extracted_preferences)
        if not tags:
            tags = list(self._extractor.extract_keywords_fallback(content))

        tags = [
            tag
            for tag in tags
            if isinstance(tag, UserTag)
            and float(tag.confidence) >= self._min_tag_confidence
        ]
        if not tags and not preferences:
            return False

        provenance = DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (replace(source, content=None, source_role="primary"),),
        )
        if tags:
            await self._profile_manager.ingest_tags(
                user_id,
                tags,
                provenance=provenance,
            )
        if preferences:
            await self._profile_manager.update_preferences(
                user_id,
                preferences,
                provenance=provenance,
            )
        return True


def trusted_profile_subject_id(memory: Mapping[str, Any] | None) -> str | None:
    """从内部稳定身份证据中提取唯一 canonical user ID。

    显示名、模型生成的 ``participants`` 和多主体结果均不能成为画像主键。
    """

    if not isinstance(memory, Mapping):
        return None
    metadata = _metadata_dict(memory.get("metadata"))
    if metadata.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
        return None

    subject_ids = metadata.get("subject_ids")
    participant_ids = metadata.get("participant_ids")
    participant_labels = metadata.get("participants")
    snapshots = metadata.get("participant_name_snapshots")
    sources = metadata.get("participant_identity_sources")
    if (
        not isinstance(subject_ids, list)
        or len(subject_ids) != 1
        or not isinstance(participant_ids, list)
        or not isinstance(participant_labels, list)
        or len(participant_ids) != len(participant_labels)
        or not isinstance(snapshots, Mapping)
        or not isinstance(sources, Mapping)
    ):
        return None

    subject_id = _plain_identifier(subject_ids[0])
    normalized_participants = [_plain_identifier(item) for item in participant_ids]
    if subject_id is None or normalized_participants.count(subject_id) != 1:
        return None
    participant_index = normalized_participants.index(subject_id)
    identity_label = _plain_text(participant_labels[participant_index])
    snapshot = _plain_text(snapshots.get(subject_id))
    source = sources.get(subject_id)
    if identity_label is None or snapshot is None or not isinstance(source, Mapping):
        return None

    protocol = _plain_identifier(source.get("protocol"))
    namespace = _plain_identifier(source.get("identity_namespace"))
    stable_user_id = _plain_identifier(source.get("stable_user_id"))
    source_label = _plain_text(source.get("identity_label"))
    if None in {protocol, namespace, stable_user_id, source_label}:
        return None
    if source_label != identity_label:
        return None
    if namespace == "qq" and (
        stable_user_id != subject_id or identity_label != f"QQ:{subject_id}"
    ):
        return None
    return subject_id


def _metadata_dict(value: Any) -> dict[str, Any]:
    """把 canonical metadata 安全转换为映射，非法 JSON 返回空映射。"""

    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _plain_identifier(value: Any) -> str | None:
    """规范内部标识符，并拒绝空值、控制字符和异常长度。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return None
    return normalized


def _plain_text(value: Any) -> str | None:
    """规范内部短文本证据，并拒绝空值和控制字符。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return None
    return normalized


def _normalize_preferences(value: Any) -> dict[str, Any]:
    """把不可信 LLM 偏好收敛到画像 Store 支持的有限字段。"""

    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, Any] = {}
    reply_style = value.get("reply_style")
    if isinstance(reply_style, str) and reply_style.strip() in _REPLY_STYLES:
        normalized["reply_style"] = reply_style.strip()
    for field in ("preferred_topics", "avoided_topics"):
        topics = _normalize_topics(value.get(field))
        if topics:
            normalized[field] = topics
    return normalized


def _normalize_topics(value: Any) -> list[str]:
    """规范主题列表，执行类型、长度、数量和顺序去重限制。"""

    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    topics: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        topic = item.strip()
        if not topic or len(topic) > _MAX_TOPIC_CHARS or topic in topics:
            continue
        topics.append(topic)
        if len(topics) >= _MAX_TOPICS:
            break
    return topics


__all__ = ["ProfileProposalPipeline", "trusted_profile_subject_id"]
