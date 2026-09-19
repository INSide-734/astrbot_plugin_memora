"""普通召回候选在去重、排序与 top-K 截断前的用户证据资格契约。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.injection.application.selection import metadata_has_user_evidence
from core.features.recall.application.recall_handler import RecallHandler
from core.features.retrieval.rrf_fusion import HybridResult
from tests.fact_evidence_helpers import candidate_evidence_metadata
from tests.injection_executor_support import resolved_reference

_USER_FACT = "用户明确喜欢低糖饮料"
_ASSISTANT_FACT = "ASSISTANT_UNCONFIRMED_PLAN"


def _handler() -> RecallHandler:
    return RecallHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        conversation_manager=MagicMock(),
        injection_adapter=MagicMock(),
        enforce_limit_cb=AsyncMock(),
    )


def _candidate(doc_id: int, content: str, score: float, metadata: Any) -> HybridResult:
    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata=metadata,
    )


def _mixed_metadata() -> dict[str, Any]:
    """一半用户事实、一半助手事实，并带原聚合分数才有的 facet 命中。"""

    return {
        "key_facts": [_USER_FACT, _ASSISTANT_FACT],
        "fact_source_evidence": [
            [resolved_reference(_USER_FACT)],
            [resolved_reference(_ASSISTANT_FACT, role="assistant")],
        ],
        "source_evidence": [resolved_reference(_USER_FACT)],
        "_matched_facets": {"event": 1.0},
    }


def _assistant_only_metadata() -> dict[str, Any]:
    reference = resolved_reference(_ASSISTANT_FACT, role="assistant")
    return {
        "key_facts": [_ASSISTANT_FACT],
        "fact_source_evidence": [[reference]],
        "source_evidence": [reference],
    }


def test_assistant_only_high_score_cannot_hold_top_k_slot() -> None:
    unsupported = _candidate(11, _ASSISTANT_FACT, 0.99, _assistant_only_metadata())
    supported = _candidate(
        12, _USER_FACT, 0.05, candidate_evidence_metadata(_USER_FACT)
    )

    finalized = _handler()._finalize_recall_candidates(
        [unsupported, supported], top_k=1
    )

    assert [item.doc_id for item in finalized] == [12]


def test_mixed_high_score_cannot_hold_top_k_slot() -> None:
    """混合候选的原聚合分数不算资格，重建只发生在选择门内。"""

    mixed = _candidate(
        21, f"{_USER_FACT}；{_ASSISTANT_FACT}", 10_000.0, _mixed_metadata()
    )
    supported = _candidate(
        22, "低分完整用户事实", 0.02, candidate_evidence_metadata("低分完整用户事实")
    )

    finalized = _handler()._finalize_recall_candidates([mixed, supported], top_k=1)

    assert [item.doc_id for item in finalized] == [22]
    assert finalized[0].content == "低分完整用户事实"


def test_finalize_keeps_original_candidates_without_rebuilding() -> None:
    """资格判定只做筛选，候选对象原样进入后续选择门。"""

    supported = _candidate(31, _USER_FACT, 0.4, candidate_evidence_metadata(_USER_FACT))

    finalized = _handler()._finalize_recall_candidates([supported], top_k=3)

    assert finalized == [supported]


@pytest.mark.parametrize(
    "case, expected",
    [
        ("complete", True),
        ("mixed_facts", False),
        ("summary_without_user_evidence", False),
        ("legacy_reference", False),
        ("missing_metadata", False),
    ],
)
def test_eligibility_requires_complete_user_attribution(
    case: str, expected: bool
) -> None:
    metadata = {
        "complete": candidate_evidence_metadata(_USER_FACT),
        "mixed_facts": _mixed_metadata(),
        "summary_without_user_evidence": {
            "key_facts": [_USER_FACT],
            "fact_source_evidence": [[resolved_reference(_USER_FACT)]],
            "source_evidence": [resolved_reference(_ASSISTANT_FACT, role="assistant")],
        },
        "legacy_reference": {
            "key_facts": [_USER_FACT],
            "fact_source_evidence": [[{"message_index": 0, "start": 0, "end": 1}]],
            "source_evidence": [resolved_reference(_USER_FACT)],
        },
        "missing_metadata": None,
    }[case]

    assert metadata_has_user_evidence(metadata) is expected
