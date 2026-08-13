"""自动画像 proposal 生产闭环测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.shared.cost_control import CostControl
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.profiles.application.profile_proposal_pipeline import (
    ProfileProposalPipeline,
)
from core.features.profiles.domain.models import TagCategory, UserTag
from core.shared.domain_provenance import DomainObjectOrigin
from core.shared.contracts import MemorySourceRef
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope


def _trusted_memory(*, subject_ids: list[str] | None = None) -> dict:
    """构造只含内部稳定身份元数据的 canonical 读取结果。"""

    canonical_id = "canonical-alpha"
    subjects = [canonical_id] if subject_ids is None else subject_ids
    return {
        "id": 17,
        "text": "我喜欢喝咖啡",
        "metadata": {
            "identity_schema_version": "stable-identity-v1",
            "participant_ids": [canonical_id],
            "participants": ["TEST:stable-alpha"],
            "participant_name_snapshots": {canonical_id: "Alpha"},
            "participant_identity_sources": {
                canonical_id: {
                    "protocol": "test",
                    "identity_namespace": "test-instance",
                    "stable_user_id": "stable-alpha",
                    "identity_label": "TEST:stable-alpha",
                }
            },
            "subject_ids": subjects,
        },
    }


def _source() -> MemorySourceRef:
    """构造与 canonical revision、作用域和隐私绑定的来源快照。"""

    return MemorySourceRef(
        memory_id=17,
        revision_token="revision-17",
        scope_key="session:test",
        privacy_level="shared",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        content="我喜欢喝咖啡",
    )


def _pipeline(
    *,
    memory: dict | None = None,
    source: MemorySourceRef | None = None,
    extractor: MagicMock | None = None,
    manager: MagicMock | None = None,
    cost_control: CostControl | None = None,
) -> tuple[ProfileProposalPipeline, MagicMock, MagicMock]:
    """以可控依赖构造画像 proposal 管线。"""

    if extractor is None:
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=([], {}))
        extractor.extract_keywords_fallback = MagicMock(return_value=[])
    manager = manager or MagicMock()
    manager.ingest_tags = AsyncMock()
    manager.update_preferences = AsyncMock()
    source_store = MagicMock()
    source_store.load_sources = AsyncMock(return_value=[source or _source()])
    get_memory = AsyncMock(return_value=memory or _trusted_memory())
    pipeline = ProfileProposalPipeline(
        profile_manager=manager,
        source_store=source_store,
        get_memory=get_memory,
        extractor=extractor,
        cost_control=cost_control or CostControl(mode="quality"),
        min_tag_confidence=0.1,
    )
    return pipeline, extractor, manager


@pytest.mark.asyncio
async def test_profile_proposal_persists_unique_trusted_subject_with_provenance() -> (
    None
):
    """唯一可信主体的标签和偏好必须携带同一 canonical 来源。"""

    extractor = MagicMock()
    tag = UserTag(
        category=TagCategory.INTEREST,
        value="咖啡",
        confidence=0.9,
        source="llm",
    )
    extractor.extract = AsyncMock(return_value=([tag], {"reply_style": "concise"}))
    extractor.extract_keywords_fallback = MagicMock(return_value=[])
    pipeline, _extractor, manager = _pipeline(extractor=extractor)

    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is True
    manager.ingest_tags.assert_awaited_once()
    tag_call = manager.ingest_tags.await_args
    assert tag_call.args[:2] == ("canonical-alpha", [tag])
    provenance = tag_call.kwargs["provenance"]
    assert provenance.origin is DomainObjectOrigin.DERIVED
    assert len(provenance.sources) == 1
    assert provenance.sources[0].memory_id == 17
    assert provenance.sources[0].revision_token == "revision-17"
    assert provenance.sources[0].content is None
    manager.update_preferences.assert_awaited_once_with(
        "canonical-alpha",
        {"reply_style": "concise"},
        provenance=provenance,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memory",
    [
        _trusted_memory(subject_ids=[]),
        _trusted_memory(subject_ids=["canonical-alpha", "canonical-beta"]),
        {
            **_trusted_memory(),
            "metadata": {
                **_trusted_memory()["metadata"],
                "identity_schema_version": "untrusted-import",
            },
        },
    ],
)
async def test_profile_proposal_skips_anonymous_ambiguous_or_untrusted_identity(
    memory: dict,
) -> None:
    """匿名、多主体和非法身份 metadata 都不得触发画像写入。"""

    pipeline, extractor, manager = _pipeline(memory=memory)

    applied = await pipeline.apply_for_memory(17)

    assert applied is False
    extractor.extract.assert_not_awaited()
    manager.ingest_tags.assert_not_awaited()
    manager.update_preferences.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_proposal_uses_keyword_fallback_without_extra_llm_budget() -> (
    None
):
    """额外 LLM 额度不可用时仍应执行无 Provider 的保守关键词 proposal。"""

    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=([], {}))
    fallback_tag = UserTag(
        category=TagCategory.PREFERENCE,
        value="喜欢咖啡",
        confidence=0.4,
        source="keyword",
    )
    extractor.extract_keywords_fallback = MagicMock(return_value=[fallback_tag])
    pipeline, _extractor, manager = _pipeline(extractor=extractor)

    applied = await pipeline.apply_for_memory(17)

    assert applied is True
    extractor.extract.assert_not_awaited()
    extractor.extract_keywords_fallback.assert_called_once_with("我喜欢喝咖啡")
    manager.ingest_tags.assert_awaited_once()
    manager.update_preferences.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_proposal_propagates_cancellation_and_releases_budget() -> None:
    """Provider 取消必须穿透管线，并释放尚未提交的额外 LLM reservation。"""

    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=asyncio.CancelledError)
    extractor.extract_keywords_fallback = MagicMock(return_value=[])
    budget = ExtraLlmBudget(1)
    pipeline, _extractor, manager = _pipeline(extractor=extractor)

    with extra_llm_budget_scope(budget):
        with pytest.raises(asyncio.CancelledError):
            await pipeline.apply_for_memory(17)

    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    manager.ingest_tags.assert_not_awaited()
    manager.update_preferences.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_add_schedules_profile_proposal_without_breaking_main_write() -> (
    None
):
    """canonical 成功后应由受跟踪任务触发画像，普通派生失败不能回滚主写。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    hybrid_retriever = MagicMock()
    hybrid_retriever.add_memory = AsyncMock(return_value=123)
    setattr(engine, "hybrid_retriever", hybrid_retriever)
    engine.graph_memory_manager = None
    engine.atom_store = None
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._retrieval.apply_interference = AsyncMock()
    engine._retrieval.extract_triggers = AsyncMock()
    pipeline = MagicMock()
    pipeline.apply_for_memory = AsyncMock(side_effect=RuntimeError("derived failed"))
    setattr(engine, "profile_proposal_pipeline", pipeline)
    tasks: list[asyncio.Task] = []

    def create_task(coroutine) -> None:
        """记录引擎派生任务，便于等待确定性完成。"""

        tasks.append(asyncio.create_task(coroutine))

    setattr(engine, "_create_tracked_task", create_task)

    doc_id = await engine.add_memory(
        "我喜欢喝咖啡",
        metadata=_trusted_memory()["metadata"],
    )
    await asyncio.gather(*tasks)

    assert doc_id == 123
    pipeline.apply_for_memory.assert_awaited_once_with(123)


@pytest.mark.asyncio
async def test_profile_hook_tracks_post_write_task() -> None:
    """画像写后 hook 必须把 proposal 任务交给 MemoryEngine 生命周期。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    pipeline = MagicMock()
    pipeline.apply_for_memory = AsyncMock()
    setattr(engine, "profile_proposal_pipeline", pipeline)
    tasks: list[asyncio.Task] = []

    def create_task(coroutine) -> None:
        """记录生命周期任务，便于测试等待其完成。"""

        tasks.append(asyncio.create_task(coroutine))

    setattr(engine, "_create_tracked_task", create_task)
    engine._schedule_profile_proposal_after_write(29)
    await asyncio.gather(*tasks)

    pipeline.apply_for_memory.assert_awaited_once_with(29)
