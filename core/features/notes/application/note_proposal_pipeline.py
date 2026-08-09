"""从 canonical memory 生成带来源证据的自动笔记 proposal。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ....base.cost_control import CostControl
from ....base.extra_llm_budget import budgeted_extra_llm_call
from ....models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ....shared.contracts import MemorySourceRef
from ..contracts import NoteGeneratorPort, NoteSourceReaderPort
from .note_manager import NoteManager

_MAX_SOURCE_CHARS = 4_000
_MAX_TITLE_CHARS = 80
_MAX_CONTENT_CHARS = 2_000
_MAX_TAG_CHARS = 64


class NoteProposalPipeline:
    """编排预算、生成、来源复核和自动笔记持久化。"""

    def __init__(
        self,
        *,
        note_manager: NoteManager,
        source_store: NoteSourceReaderPort,
        generator: NoteGeneratorPort,
        cost_control: CostControl,
        auto_create_min_length: int = 50,
        max_tags: int = 10,
    ) -> None:
        """保存 proposal 依赖，并冻结自动长度和标签限制。"""

        self._note_manager = note_manager
        self._source_store = source_store
        self._generator = generator
        self._cost_control = cost_control
        self._auto_create_min_length = max(1, int(auto_create_min_length))
        self._max_tags = max(0, int(max_tags))

    async def apply_for_memory(
        self,
        memory_id: int,
        *,
        allow_provider: bool = True,
    ) -> bool:
        """为单条 canonical memory 生成并写入自动笔记。

        请求级预算允许时使用生成器端口；预算缺失、功能门关闭或
        生成结果不可用时使用确定性来源摘要。写入前会重新读取 source，只有
        revision、scope 和 privacy 均未变化时才创建 derived note。

        Args:
            memory_id: canonical memory 的整数 ID。
            allow_provider: 是否允许尝试请求级额外 LLM 调用；重建路径传入
                ``False``，确保重建不产生 Provider 成本。

        Returns:
            成功应用或幂等命中已有自动笔记时返回 ``True``，来源不可用、
            长度不足或来源变化时返回 ``False``。

        Raises:
            asyncio.CancelledError: 当前任务或 Provider 调用被取消。
            Exception: canonical 读取或笔记持久化失败，由调用方隔离。
        """

        normalized_id = int(memory_id)
        sources = await self._source_store.load_sources(
            (normalized_id,),
            max_content_chars=_MAX_SOURCE_CHARS,
        )
        if len(sources) != 1 or sources[0].memory_id != normalized_id:
            return False
        source = sources[0]
        content = str(source.content or "").strip()[:_MAX_SOURCE_CHARS]
        if len(content) < self._auto_create_min_length:
            return False

        generated: Any = None
        if allow_provider:
            try:
                async with budgeted_extra_llm_call(
                    self._cost_control,
                    "note_generation",
                ) as allowed:
                    if allowed:
                        generated = await self._generator.generate(content)
            except asyncio.CancelledError:
                raise
            except Exception:
                generated = None

        fresh_sources = await self._source_store.load_sources(
            (normalized_id,),
            max_content_chars=_MAX_SOURCE_CHARS,
        )
        if not _same_source(source, fresh_sources):
            return False

        title, note_content, tags = _sanitize_proposal(
            generated,
            content,
            max_tags=self._max_tags,
        )
        provenance = DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (replace(source, content=None, source_role="primary"),),
        )
        note_id = await self._note_manager.auto_create_from_memory(
            content,
            source_memory_ids=[normalized_id],
            provenance=provenance,
            title=title,
            note_content=note_content,
            tags=tags,
        )
        return note_id is not None

    async def rebuild_from_canonical(self) -> dict[str, Any]:
        """从全部当前 canonical source 幂等重建自动笔记。

        重建按 source ID 串行执行，避免无界后台任务和 SQLite 写竞争；每条
        source 都复用 ``apply_for_memory`` 的二次 revision 校验，并强制关闭
        Provider 调用。普通单条失败计入 ``errors`` 后继续，取消始终传播。
        """

        try:
            sources = await self._source_store.load_all_sources(
                max_content_chars=_MAX_SOURCE_CHARS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return {
                "status": "failed",
                "success": False,
                "created": 0,
                "skipped": 0,
                "errors": 1,
                "reason_code": "note_source_load_failed",
            }

        created = 0
        skipped = 0
        errors = 0
        for memory_id in dict.fromkeys(source.memory_id for source in sources):
            try:
                applied = await self.apply_for_memory(
                    memory_id,
                    allow_provider=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                errors += 1
                continue
            if applied:
                created += 1
            else:
                skipped += 1
        return {
            "status": "completed" if errors == 0 else "failed",
            "success": errors == 0,
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "reason_code": (
                "note_rebuild_completed" if errors == 0 else "note_rebuild_failed"
            ),
        }


def _sanitize_proposal(
    proposal: Any,
    source_content: str,
    *,
    max_tags: int,
) -> tuple[str, str, list[str]]:
    """把不可信生成结果收敛到 Note 领域的有限字段。"""

    fallback_title, fallback_content = _fallback_note(source_content)
    if not isinstance(proposal, dict):
        return fallback_title, fallback_content, []

    raw_title = proposal.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title:
        title = fallback_title
    title = title[:_MAX_TITLE_CHARS]

    raw_content = proposal.get("content")
    note_content = raw_content.strip() if isinstance(raw_content, str) else ""
    if not note_content:
        note_content = fallback_content
    note_content = note_content[:_MAX_CONTENT_CHARS]
    tags = _sanitize_tags(proposal.get("tags"), max_tags=max_tags)
    return title, note_content, tags


def _fallback_note(source_content: str) -> tuple[str, str]:
    """从 canonical 正文生成稳定标题和正文 fallback。"""

    normalized = str(source_content or "").strip()
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return "自动笔记", ""
    title = lines[0][:_MAX_TITLE_CHARS]
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else normalized
    return title, (body or normalized)[:_MAX_CONTENT_CHARS]


def _sanitize_tags(value: Any, *, max_tags: int) -> list[str]:
    """过滤非法标签，并按配置上限保持顺序去重。"""

    if max_tags <= 0 or not isinstance(value, (list, tuple)):
        return []
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if not tag or len(tag) > _MAX_TAG_CHARS or tag in tags:
            continue
        tags.append(tag)
        if len(tags) >= max_tags:
            break
    return tags


def _same_source(
    source: MemorySourceRef,
    fresh_sources: Sequence[MemorySourceRef],
) -> bool:
    """确认生成前后的 source revision、scope 和 privacy 完全一致。"""

    if len(fresh_sources) != 1 or fresh_sources[0].memory_id != source.memory_id:
        return False
    fresh = fresh_sources[0]
    return (
        fresh.revision_token == source.revision_token
        and fresh.scope_key == source.scope_key
        and fresh.privacy_level == source.privacy_level
    )


__all__ = ["NoteProposalPipeline"]
