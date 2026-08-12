"""ProjectionReader 的读取边界和预算测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from core.features.evolution.domain import (
    DerivedState,
    ProjectionBundle,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
)
from core.retrieval.projection_reader import (
    ProjectionBudget,
    ProjectionReader,
    ProjectionScope,
)
from core.retrieval.rrf_fusion import HybridResult
from core.shared.contracts import MemorySourceRef

UTC = timezone.utc
NOW = datetime(2026, 7, 19, tzinfo=UTC)


def candidate(memory_id: int, score: float = 0.9) -> HybridResult:
    return HybridResult(
        doc_id=memory_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=f"canonical-{memory_id}",
        metadata={"scope_key": "private:user-a", "privacy_level": "shared"},
    )


def source(
    memory_id: int, *, revision: str | None = None, privacy: str = "shared"
) -> MemorySourceRef:
    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision or f"rev-{memory_id}",
        scope_key="private:user-a",
        privacy_level=privacy,
        occurred_at=NOW,
        content=f"source-{memory_id}",
    )


def bundle(
    primary: int = 17,
    supporting: int = 18,
    *,
    confidence: float = 0.86,
    state: DerivedState = DerivedState.ACTIVE,
    projection_type: ProjectionType = ProjectionType.EPISODE_SUMMARY,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    roles: tuple[str, str] = ("primary", "supporting"),
) -> ProjectionBundle:
    projection_id = f"p:{primary}:{supporting}:{confidence}"
    projection = ProjectionView(
        projection_id=projection_id,
        projection_type=projection_type,
        summary="先完成迁移，再进行灰度发布。",
        source_memory_ids=(primary, supporting),
        scope_key="private:user-a",
        privacy_level="shared",
        confidence=confidence,
        state=state,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return ProjectionBundle(
        projection=projection,
        sources=(
            ProjectionSourceView(projection_id, primary, f"rev-{primary}", roles[0], 0),
            ProjectionSourceView(
                projection_id, supporting, f"rev-{supporting}", roles[1], 1
            ),
        ),
    )


class FakeStore:
    def __init__(
        self, bundles: list[ProjectionBundle], sources: dict[int, MemorySourceRef]
    ):
        self.bundles = bundles
        self.sources = sources
        self.bundle_calls: list[tuple[tuple[int, ...], str, int]] = []
        self.source_calls: list[tuple[tuple[int, ...], int]] = []

    async def active_projection_bundles_for_seeds(self, seed_ids, *, scope_key, limit):
        self.bundle_calls.append((tuple(seed_ids), scope_key, limit))
        return self.bundles[:limit]

    async def load_sources(self, memory_ids, *, max_content_chars):
        self.source_calls.append((tuple(memory_ids), max_content_chars))
        return [self.sources[item] for item in memory_ids if item in self.sources]


def make_reader(*items: ProjectionBundle) -> ProjectionReader:
    store = FakeStore(
        list(items),
        {17: source(17), 18: source(18), 19: source(19), 20: source(20)},
    )
    return ProjectionReader(store)


def read_scope(**overrides) -> ProjectionScope:
    values = {"scope_key": "private:user-a", "privacy_level": "shared", "now": NOW}
    values.update(overrides)
    return ProjectionScope(**values)


def read_budget(**overrides) -> ProjectionBudget:
    values = {
        "max_chars": 2_000,
        "max_items": 16,
        "max_per_candidate": 4,
        "max_summary_chars": 600,
    }
    values.update(overrides)
    return ProjectionBudget(**values)


@pytest.mark.asyncio
async def test_reader_attaches_only_to_primary_candidate() -> None:
    reader = make_reader(bundle())
    result = await reader.attach(
        [candidate(17), candidate(18)], scope=read_scope(), budget=read_budget()
    )

    assert [item.doc_id for item in result] == [17, 18]
    assert result[0].metadata["derived_projections"] == [
        {
            "type": "episode_summary",
            "summary": "先完成迁移，再进行灰度发布。",
            "confidence": 0.86,
        }
    ]
    assert "derived_projections" not in result[1].metadata
    assert result[0].content == "canonical-17"
    assert result[0].final_score == 0.9


@pytest.mark.asyncio
async def test_reader_drops_when_only_supporting_candidate_is_present() -> None:
    reader = make_reader(bundle())
    result = await reader.attach(
        [candidate(18)], scope=read_scope(), budget=read_budget()
    )
    assert result[0].metadata.get("derived_projections") is None


@pytest.mark.asyncio
async def test_reader_drops_stale_source_and_invalid_scope_or_time() -> None:
    reader = make_reader(bundle())
    reader.store.sources[18] = source(18, revision="new-revision")
    result = await reader.attach(
        [candidate(17)], scope=read_scope(), budget=read_budget()
    )
    assert result[0].metadata.get("derived_projections") is None

    reader = make_reader(bundle(valid_to=NOW - timedelta(seconds=1)))
    result = await reader.attach(
        [candidate(17)], scope=read_scope(), budget=read_budget()
    )
    assert result[0].metadata.get("derived_projections") is None


@pytest.mark.asyncio
async def test_reader_requires_both_conflict_roles() -> None:
    invalid = bundle(
        projection_type=ProjectionType.CONFLICT_SET,
        roles=("conflict_left", "supporting"),
    )
    reader = make_reader(invalid)
    result = await reader.attach(
        [candidate(17)], scope=read_scope(), budget=read_budget()
    )
    assert result[0].metadata.get("derived_projections") is None


@pytest.mark.asyncio
async def test_reader_orders_deduplicates_and_obeys_budgets() -> None:
    first = bundle(confidence=0.9)
    second = bundle(primary=17, supporting=19, confidence=0.8)
    reader = make_reader(first, second, first)
    result = await reader.attach(
        [candidate(17)],
        scope=read_scope(),
        budget=read_budget(max_chars=120, max_items=1, max_per_candidate=4),
    )
    assert len(result[0].metadata["derived_projections"]) == 1
    assert result[0].metadata["derived_projections"][0]["confidence"] == 0.9


@pytest.mark.asyncio
async def test_reader_returns_baseline_on_store_error() -> None:
    reader = make_reader(bundle())
    reader.store.active_projection_bundles_for_seeds = AsyncMock(
        side_effect=RuntimeError("内部错误")
    )
    seeds = [candidate(17)]
    result = await reader.attach(seeds, scope=read_scope(), budget=read_budget())
    assert result[0].doc_id == 17
    assert result[0].metadata.get("derived_projections") is None
    assert seeds[0].metadata.get("derived_projections") is None


@pytest.mark.asyncio
async def test_reader_propagates_cancelled_error() -> None:
    reader = make_reader(bundle())
    reader.store.active_projection_bundles_for_seeds = AsyncMock(
        side_effect=asyncio.CancelledError()
    )
    with pytest.raises(asyncio.CancelledError):
        await reader.attach([candidate(17)], scope=read_scope(), budget=read_budget())


@pytest.mark.asyncio
async def test_reader_uses_one_bundle_read_and_one_source_batch() -> None:
    reader = make_reader(bundle())
    await reader.attach([candidate(17)], scope=read_scope(), budget=read_budget())
    assert len(reader.store.bundle_calls) == 1
    assert len(reader.store.source_calls) == 1
    assert reader.store.source_calls[0][0] == (17, 18)
