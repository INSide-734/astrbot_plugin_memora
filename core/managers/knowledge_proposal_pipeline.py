"""从 canonical memory 生成带来源证据的自动知识 proposal。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from ..base.cost_control import CostControl
from ..base.extra_llm_budget import budgeted_extra_llm_call
from ..features.knowledge.application.knowledge_manager import KnowledgeManager
from ..features.knowledge.domain.models import KnowledgeEntry, KnowledgeType
from ..models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ..models.memory_evolution import MemorySourceRef
from ..processors.knowledge_extractor import KnowledgeExtractor

_MAX_TITLE_CHARS = 100
_MAX_CONTENT_CHARS = 2_000
_MAX_TAG_CHARS = 64
_MAX_TAGS = 16
_MIN_IMPORTANCE = 0.6
_MIN_CONFIDENCE = 0.65
_MIN_STABILITY = 0.6
_ACTIVE_STATUSES = frozenset({"active", "current", "stable"})


class CanonicalSourceStore(Protocol):
    """声明自动知识管线读取 canonical source 的最小接口。"""

    async def load_sources(
        self,
        memory_ids: Sequence[int],
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """按 ID 返回带 revision、作用域、隐私和有限正文的 source。"""


class KnowledgeProposalPipeline:
    """编排质量门、预算门、抽取和 provenance 约束写入。"""

    def __init__(
        self,
        *,
        knowledge_manager: KnowledgeManager,
        source_store: CanonicalSourceStore,
        get_memory: Callable[[int], Awaitable[dict[str, Any] | None]],
        extractor: KnowledgeExtractor,
        cost_control: CostControl,
        expire_days: int = 365,
    ) -> None:
        """保存知识 proposal 依赖，并冻结本次生命周期的运行时参数。"""

        self._knowledge_manager = knowledge_manager
        self._source_store = source_store
        self._get_memory = get_memory
        self._extractor = extractor
        self._cost_control = cost_control
        self._expire_days = max(0, int(expire_days))

    async def apply_for_memory(self, memory_id: int) -> bool:
        """为单条 canonical memory 生成并写入自动知识。

        只有质量门、请求级额外 LLM 预算和两次 source revision 校验均通过时
        才会调用 Provider 和 KnowledgeManager。普通抽取失败返回 ``False``；
        ``asyncio.CancelledError`` 始终继续向上传播。
        """

        normalized_id = int(memory_id)
        memory = await self._get_memory(normalized_id)
        if not _eligible_memory(memory):
            return False
        sources = await self._source_store.load_sources(
            (normalized_id,), max_content_chars=_MAX_CONTENT_CHARS
        )
        if len(sources) != 1 or sources[0].memory_id != normalized_id:
            return False
        source = sources[0]
        content = str(source.content or "").strip()[:_MAX_CONTENT_CHARS]
        if not content:
            return False

        async with budgeted_extra_llm_call(
            self._cost_control,
            "knowledge_extraction",
        ) as allowed:
            if not allowed:
                return False
            try:
                extracted = await self._extractor.extract(
                    content,
                    str(_metadata_dict(memory.get("metadata")).get("memory_type", "")),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return False

        fresh_sources = await self._source_store.load_sources(
            (normalized_id,), max_content_chars=_MAX_CONTENT_CHARS
        )
        if not _same_source(source, fresh_sources):
            return False
        sanitized = _sanitize_entry(extracted)
        if sanitized is None:
            return False
        provenance = DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (replace(source, content=None, source_role="primary"),),
        )
        expires_at = (
            time.time() + self._expire_days * 86400.0 if self._expire_days else 0.0
        )
        entry = replace(
            sanitized,
            source_ids=[normalized_id],
            expires_at=expires_at,
            origin=DomainObjectOrigin.DERIVED,
            provenance=provenance,
        )
        await self._knowledge_manager.add_derived_entry(entry, provenance)
        return True


def _eligible_memory(memory: Mapping[str, Any] | None) -> bool:
    """按重要性、置信度、稳定性和状态筛选 canonical memory。"""

    if not isinstance(memory, Mapping):
        return False
    metadata = _metadata_dict(memory.get("metadata"))
    importance = _number(metadata.get("importance", memory.get("importance")))
    confidence = max(
        _number(metadata.get("confidence")),
        _number(metadata.get("atom_confidence")),
        _number(metadata.get("extraction_quality")),
    )
    explicit_stability = metadata.get("stability", metadata.get("stability_score"))
    stability = _number(explicit_stability)
    if explicit_stability is None:
        status = str(metadata.get("status", "")).strip().casefold()
        stability = 1.0 if status in _ACTIVE_STATUSES else 0.0
    status = str(metadata.get("status", "active")).strip().casefold()
    return (
        importance >= _MIN_IMPORTANCE
        and confidence >= _MIN_CONFIDENCE
        and stability >= _MIN_STABILITY
        and status in _ACTIVE_STATUSES
    )


def _sanitize_entry(entry: Any) -> KnowledgeEntry | None:
    """验证抽取结果的结构、枚举和长度，拒绝不可信字段。"""

    if not isinstance(entry, KnowledgeEntry):
        return None
    title = entry.title.strip() if isinstance(entry.title, str) else ""
    content = entry.content.strip() if isinstance(entry.content, str) else ""
    if not title or len(title) > _MAX_TITLE_CHARS:
        return None
    if not content or len(content) > _MAX_CONTENT_CHARS:
        return None
    if not isinstance(entry.category, KnowledgeType):
        return None
    confidence = _number(entry.confidence)
    if confidence < 0.0 or confidence > 1.0:
        return None
    if not isinstance(entry.tags, (list, tuple)):
        return None
    tags: list[str] = []
    for raw_tag in entry.tags:
        if not isinstance(raw_tag, str):
            return None
        tag = raw_tag.strip()
        if not tag or len(tag) > _MAX_TAG_CHARS:
            return None
        if tag not in tags:
            tags.append(tag)
        if len(tags) > _MAX_TAGS:
            return None
    return replace(
        entry,
        title=title,
        content=content,
        confidence=confidence,
        tags=tags,
    )


def _same_source(
    source: MemorySourceRef, fresh_sources: Sequence[MemorySourceRef]
) -> bool:
    """确认抽取前后的 source revision、scope 和 privacy 完全一致。"""

    if len(fresh_sources) != 1 or fresh_sources[0].memory_id != source.memory_id:
        return False
    fresh = fresh_sources[0]
    return (
        fresh.revision_token == source.revision_token
        and fresh.scope_key == source.scope_key
        and fresh.privacy_level == source.privacy_level
    )


def _metadata_dict(value: Any) -> dict[str, Any]:
    """把 canonical metadata 安全转换为映射。"""

    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _number(value: Any) -> float:
    """把不可信评分转换为有限浮点数，非法值回退为零。"""

    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


__all__ = ["KnowledgeProposalPipeline"]
