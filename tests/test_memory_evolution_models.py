from datetime import datetime, timezone
from typing import cast

import pytest

from core.features.evolution.domain import (
    DerivedState,
    GateDecision,
    JobSpec,
    JobState,
    MemoryRelationProposal,
    ProjectionSourceView,
    ProjectionType,
    RelationType,
    RelationView,
)
from core.shared.contracts import MemorySourceRef


def test_relation_and_projection_allowlists_are_stable() -> None:
    assert RelationType.SAME_EPISODE.value == "same_episode"
    assert RelationType.PREFERENCE_CHANGE.value == "preference_change"
    assert ProjectionType.CONFLICT_SET.value == "conflict_set"
    assert JobState.PROCESSING.value == "processing"
    assert DerivedState.INVALIDATED.value == "invalidated"


def test_source_ref_keeps_revision_scope_privacy_and_time() -> None:
    occurred_at = datetime(2026, 7, 18, tzinfo=timezone.utc)
    source = MemorySourceRef(
        17,
        "2026-07-18T00:00:00+00:00",
        "private:user-a",
        "shared",
        occurred_at,
    )
    assert source.memory_id == 17
    assert source.revision_token == "2026-07-18T00:00:00+00:00"
    assert source.privacy_level == "shared"
    assert source.occurred_at == occurred_at


def test_proposal_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError):
        MemoryRelationProposal(
            source_alias="M1",
            target_alias="M2",
            relation_type=RelationType.RELATED,
            confidence=1.1,
            rationale=None,
            valid_from=None,
            valid_to=None,
        )


def test_proposal_rejects_reversed_time_interval() -> None:
    start = datetime(2026, 7, 18, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        MemoryRelationProposal(
            "M1", "M2", RelationType.RELATED, 0.5, None, start, start.replace(day=17)
        )


def test_local_constraints_cover_gate_job_source_and_projection_roles() -> None:
    with pytest.raises(ValueError, match="reason_code"):
        GateDecision(False, None, "")
    with pytest.raises(ValueError, match="source_ids must not be empty"):
        JobSpec(
            "scope", "bucket", (), "key", datetime(2026, 7, 18, tzinfo=timezone.utc)
        )
    with pytest.raises(ValueError, match="unique"):
        JobSpec(
            "scope",
            "bucket",
            (17, 17),
            "key",
            datetime(2026, 7, 18, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="non-negative integers"):
        JobSpec(
            "scope",
            "bucket",
            (True,),
            "bool-id",
            datetime(2026, 7, 18, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="reference source_ids"):
        JobSpec(
            "scope",
            "bucket",
            (17,),
            "key-with-foreign-revision",
            datetime(2026, 7, 18, tzinfo=timezone.utc),
            source_revisions={18: "r18"},
        )
    with pytest.raises(ValueError, match="evidence length"):
        MemorySourceRef(
            17,
            "r1",
            "scope",
            "shared",
            datetime(2026, 7, 18, tzinfo=timezone.utc),
            "x" * 4001,
        )
    with pytest.raises(ValueError, match="source role"):
        ProjectionSourceView("p1", 17, "r1", role="unknown")


def test_views_reject_unknown_types_and_reversed_intervals() -> None:
    with pytest.raises(ValueError, match="relation_type"):
        RelationView(
            "r1",
            17,
            18,
            cast(RelationType, "unknown"),
            0.5,
            "scope",
            "shared",
        )
