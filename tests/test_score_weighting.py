"""测试 ScoreWeighting — importance + temporal decay weighting."""

from __future__ import annotations

import time
from typing import Any

import pytest


def _make_fused(
    doc_id: int, rrf_score: float, content: str = "", metadata: dict | None = None
) -> Any:
    from core.retrieval.rrf_fusion import FusedResult

    return FusedResult(
        doc_id=doc_id,
        rrf_score=rrf_score,
        bm25_score=0.7,
        vector_score=0.6,
        content=content or f"content_{doc_id}",
        metadata=metadata or {},
    )


class TestScoreWeighting:
    @pytest.fixture
    def weighting(self) -> Any:
        from core.retrieval.score_weighting import ScoreWeighting

        return ScoreWeighting(decay_rate=0.01, importance_weight=1.0)

    def test_empty_results(self, weighting: Any) -> None:
        """空 input returns empty output."""
        result = weighting.apply_weighting([], time.time())
        assert result == []

    def test_basic_weighting(self, weighting: Any) -> None:
        """Results are scored with RRF normalized + importance + recency."""
        now = time.time()
        results = [
            _make_fused(
                1,
                0.8,
                "test content",
                {
                    "importance": 0.7,
                    "create_time": now - 3600,  # 1 hour ago
                    "last_access_time": now - 600,
                },
            ),
        ]
        output = weighting.apply_weighting(results, now)
        assert len(output) == 1
        assert output[0].doc_id == 1
        assert output[0].final_score > 0
        assert "final_score" in output[0].score_breakdown

    def test_sorting_by_final_score(self, weighting: Any) -> None:
        """Results are sorted descending by final_score."""
        now = time.time()
        results = [
            _make_fused(
                1,
                0.3,
                "low relevance",
                {
                    "importance": 0.5,
                    "create_time": now,
                    "last_access_time": now,
                },
            ),
            _make_fused(
                2,
                0.9,
                "high relevance",
                {
                    "importance": 0.9,
                    "create_time": now,
                    "last_access_time": now,
                },
            ),
        ]
        output = weighting.apply_weighting(results, now)
        assert output[0].doc_id == 2  # higher RRF + higher importance

    def test_temporal_decay(self, weighting: Any) -> None:
        """Older memories get lower recency_weight."""
        now = time.time()
        old_time = now - 86400 * 30  # 30 days ago
        recent_result = _make_fused(
            1,
            0.5,
            "recent",
            {
                "importance": 0.5,
                "create_time": now,
                "last_access_time": now,
            },
        )
        old_result = _make_fused(
            2,
            0.5,
            "old",
            {
                "importance": 0.5,
                "create_time": old_time,
                "last_access_time": old_time,
            },
        )
        output = weighting.apply_weighting(
            [recent_result, old_result],
            now,
        )
        assert output[0].doc_id == 1  # recent should rank higher

    def test_metadata_string_parsed(self, weighting: Any) -> None:
        """String metadata is JSON-parsed."""
        import json

        now = time.time()
        metadata_str = json.dumps(
            {
                "importance": 0.8,
                "create_time": now,
                "last_access_time": now,
            }
        )
        results = [_make_fused(1, 0.7, "content", metadata_str)]  # type: ignore[arg-type]
        output = weighting.apply_weighting(results, now)
        assert len(output) == 1

    def test_metadata_none_handled(self, weighting: Any) -> None:
        """None metadata is safely handled."""
        now = time.time()
        results = [_make_fused(1, 0.7, "content", None)]  # type: ignore[arg-type]
        output = weighting.apply_weighting(results, now)
        assert len(output) == 1

    def test_single_rrf_value(self, weighting: Any) -> None:
        """单个 result with zero RRF still normalizes correctly (max=1.0 fallback)."""
        now = time.time()
        results = [
            _make_fused(
                1,
                0.0,
                "content",
                {
                    "importance": 0.5,
                    "create_time": now,
                    "last_access_time": now,
                },
            )
        ]
        output = weighting.apply_weighting(results, now)
        assert len(output) == 1
        assert output[0].final_score >= 0

    def test_recency_bump_score(self) -> None:
        """静态 _recency_bump_score returns correct ranges."""
        from core.retrieval.score_weighting import ScoreWeighting

        assert ScoreWeighting._recency_bump_score(3) == 1.5  # type: ignore[attr-defined]
        assert ScoreWeighting._recency_bump_score(15) == 1.2  # type: ignore[attr-defined]
        assert ScoreWeighting._recency_bump_score(60) == 1.0  # type: ignore[attr-defined]
        assert ScoreWeighting._recency_bump_score(None) == 1.0  # type: ignore[attr-defined]
        assert ScoreWeighting._recency_bump_score(-1) == 1.0  # type: ignore[attr-defined]
