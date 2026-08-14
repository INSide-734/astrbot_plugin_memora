"""Sorting contracts for the persisted jargon meaning list."""

from __future__ import annotations

import pytest

from core.features.cognition.jargon.jargon_store import (
    JARGON_MEANING_SORT_COLUMNS,
    JargonStore,
)
from core.features.cognition.jargon.models import JargonMeaning
from core.shared.list_sorting import SortQuery


@pytest.mark.asyncio
async def test_list_by_group_uses_allowlisted_stable_sorting(tmp_path) -> None:
    store = JargonStore(str(tmp_path / "jargon-sort.db"))
    await store.initialize()
    try:
        for term, count in (
            ("beta", 5),
            ("alpha", 5),
            ("Alpha", 5),
            ("gamma", 1),
        ):
            await store.upsert(
                JargonMeaning(
                    term=term,
                    group_id="g1",
                    meaning=f"{term} meaning",
                    count=count,
                    is_confirmed=True,
                )
            )

        meanings = await store.list_by_group(
            "g1",
            confirmed_only=False,
            sort=SortQuery("count", "desc"),
        )

        assert [meaning.term for meaning in meanings] == [
            "Alpha",
            "alpha",
            "beta",
            "gamma",
        ]
    finally:
        await store.close()


def test_meaning_sort_columns_are_fixed() -> None:
    assert JARGON_MEANING_SORT_COLUMNS == {
        "term": "term COLLATE NOCASE",
        "confidence": "confidence",
        "count": "count",
        "created_at": "created_at",
        "updated_at": "updated_at",
    }
