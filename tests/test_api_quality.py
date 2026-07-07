"""core/api/quality_api.py — QualityApiMixin 测试。

Validates endpoint responses, parameter validation, and error handling.
Uses unittest.mock to mock scorer and quart.request.
"""

from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from core.api.quality_api import QualityApiMixin


def _make_mock_request(**args):
    """Create a mock quart.request with args dict."""
    mock = MagicMock()
    mock.args = args
    mock.get_json = MagicMock()
    return mock


def _make_stub_scorer(*, score_history=None, alert_history=None, has_scorer=True):
    """Create a QualityApiMixin stub with a mocked scorer."""

    class Stub:
        get_quality_stats = QualityApiMixin.get_quality_stats
        get_quality_recent = QualityApiMixin.get_quality_recent
        get_quality_alerts = QualityApiMixin.get_quality_alerts
        reset_quality = QualityApiMixin.reset_quality
        _get_quality_scorer = QualityApiMixin._get_quality_scorer

    stub = Stub()
    if has_scorer:
        scorer = MagicMock()
        scorer._score_history = deque(score_history or [], maxlen=100)
        scorer._alert_history = deque(alert_history or [], maxlen=200)
        scorer._paused = False
        scorer._pause_reason = ""

        def mock_get_stats():
            n = len(scorer._score_history)
            if n == 0:
                return {
                    "total_scored": 0,
                    "paused": scorer._paused,
                    "pause_reason": scorer._pause_reason,
                    "alert_counts": {},
                    "recent_scores": [],
                }
            return {
                "avg_overall": 0.72,
                "avg_consistency": 0.80,
                "avg_coherence": 0.65,
                "avg_relevance": 0.70,
                "avg_freshness": 0.55,
                "avg_accuracy": 0.90,
                "total_scored": n,
                "paused": scorer._paused,
                "pause_reason": scorer._pause_reason,
                "alert_counts": {"medium": 1},
                "recent_scores": [],
            }

        scorer.get_stats = mock_get_stats
        stub.plugin = MagicMock()
        stub.plugin._quality_scorer = scorer

    return stub


def _make_quality_score(atom_id="a1", overall=0.72):
    """Create a mock QualityScore."""
    score = MagicMock()
    score.atom_id = atom_id
    score.overall = overall
    score.consistency = 0.80
    score.coherence = 0.65
    score.relevance = 0.70
    score.freshness = 0.55
    score.accuracy = 0.90
    score.timestamp = 1700000000.0
    return score


def _make_quality_alert(level="high", dimension="consistency", score=0.42):
    """Create a mock QualityAlert."""
    alert = MagicMock()
    alert.level = MagicMock()
    alert.level.value = level
    alert.dimension = dimension
    alert.score = score
    alert.threshold = 0.45
    alert.message = f"{dimension} score {score} below threshold"
    alert.suggestion = "Review extraction quality"
    alert.timestamp = 1700000000.0
    return alert


# ---------------------------------------------------------------------------
# Quality stats
# ---------------------------------------------------------------------------


class TestQualityStats:
    @pytest.mark.asyncio
    async def test_returns_stats_with_scores(self) -> None:
        s1 = _make_quality_score("a1", 0.72)
        s2 = _make_quality_score("a2", 0.65)
        stub = _make_stub_scorer(score_history=[s1, s2])
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_stats()
        assert result["status"] == "ok"
        assert result["data"]["total_scored"] == 2
        assert "avg_overall" in result["data"]

    @pytest.mark.asyncio
    async def test_returns_stats_without_scores(self) -> None:
        stub = _make_stub_scorer(score_history=[])
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_stats()
        assert result["status"] == "ok"
        assert result["data"]["total_scored"] == 0
        assert result["data"]["status"] == "no_samples"

    @pytest.mark.asyncio
    async def test_tolerates_non_mapping_stats_payload(self) -> None:
        stub = _make_stub_scorer(score_history=[])
        stub.plugin._quality_scorer.get_stats = MagicMock(return_value="bad-stats")
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_stats()
        assert result["status"] == "ok"
        assert result["data"] == {}

    @pytest.mark.asyncio
    async def test_no_scorer_returns_error(self) -> None:
        class Stub:
            get_quality_stats = QualityApiMixin.get_quality_stats
            _get_quality_scorer = QualityApiMixin._get_quality_scorer

        stub = Stub()
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_stats()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Quality recent
# ---------------------------------------------------------------------------


class TestQualityRecent:
    @pytest.mark.asyncio
    async def test_returns_recent_scores(self) -> None:
        scores = [_make_quality_score(f"a{i}", 0.5 + i * 0.01) for i in range(25)]
        stub = _make_stub_scorer(score_history=scores)
        mock_req = _make_mock_request(limit="20")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_recent()
        assert result["status"] == "ok"
        assert len(result["data"]["scores"]) == 20
        assert result["data"]["total_scores"] == 25

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(self) -> None:
        scores = [_make_quality_score(f"a{i}", 0.5) for i in range(50)]
        stub = _make_stub_scorer(score_history=scores)
        mock_req = _make_mock_request(limit="5")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_recent()
        assert result["status"] == "ok"
        assert len(result["data"]["scores"]) == 5

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_window(self) -> None:
        scores = [_make_quality_score(f"a{i}", 0.5 + i * 0.01) for i in range(5)]
        stub = _make_stub_scorer(score_history=scores)
        mock_req = _make_mock_request(limit="-1")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_recent()
        assert result["status"] == "ok"
        assert len(result["data"]["scores"]) == 5
        assert result["data"]["scores"][0]["atom_id"] == "a4"

    @pytest.mark.asyncio
    async def test_recent_skips_malformed_score_history_items(self) -> None:
        broken = MagicMock()
        type(broken).atom_id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken score")))
        scores = [_make_quality_score("good-1", 0.61), broken, _make_quality_score("good-2", 0.72)]
        stub = _make_stub_scorer(score_history=scores)
        mock_req = _make_mock_request(limit="10")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_recent()
        assert result["status"] == "ok"
        assert result["data"]["total_scores"] == 3
        assert [item["atom_id"] for item in result["data"]["scores"]] == ["good-2", "good-1"]

    @pytest.mark.asyncio
    async def test_recent_tolerates_non_iterable_score_history_container(self) -> None:
        stub = _make_stub_scorer(score_history=[])

        class BrokenHistory:
            def __iter__(self):
                raise RuntimeError("broken score history container")

        stub.plugin._quality_scorer._score_history = BrokenHistory()
        mock_req = _make_mock_request(limit="10")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_recent()
        assert result["status"] == "ok"
        assert result["data"]["scores"] == []
        assert result["data"]["total_scores"] == 0


# ---------------------------------------------------------------------------
# Quality alerts
# ---------------------------------------------------------------------------


class TestQualityAlerts:
    @pytest.mark.asyncio
    async def test_returns_all_alerts(self) -> None:
        alerts = [
            _make_quality_alert("high", "consistency", 0.42),
            _make_quality_alert("critical", "overall", 0.28),
        ]
        stub = _make_stub_scorer(alert_history=alerts)
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_alerts()
        assert result["status"] == "ok"
        assert len(result["data"]["alerts"]) == 2
        assert result["data"]["total_alerts"] == 2

    @pytest.mark.asyncio
    async def test_filters_by_level(self) -> None:
        alerts = [
            _make_quality_alert("high", "consistency", 0.42),
            _make_quality_alert("critical", "overall", 0.28),
            _make_quality_alert("high", "coherence", 0.40),
        ]
        stub = _make_stub_scorer(alert_history=alerts)
        mock_req = _make_mock_request(level="critical")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_alerts()
        assert result["status"] == "ok"
        assert result["data"]["filtered_count"] == 1
        assert result["data"]["alerts"][0]["level"] == "critical"

    @pytest.mark.asyncio
    async def test_rejects_invalid_level(self) -> None:
        stub = _make_stub_scorer(alert_history=[])
        mock_req = _make_mock_request(level="invalid")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_alerts()
        assert result["status"] == "error"
        assert "invalid level" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_window(self) -> None:
        alerts = [
            _make_quality_alert("high", "consistency", 0.42),
            _make_quality_alert("critical", "overall", 0.28),
        ]
        stub = _make_stub_scorer(alert_history=alerts)
        mock_req = _make_mock_request(limit="-1")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_alerts()
        assert result["status"] == "ok"
        assert len(result["data"]["alerts"]) == 2
        assert result["data"]["alerts"][0]["level"] == "critical"

    @pytest.mark.asyncio
    async def test_alerts_skip_malformed_alert_history_items(self) -> None:
        broken = MagicMock()
        type(broken).level = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken alert")))
        alerts = [
            _make_quality_alert("high", "consistency", 0.42),
            broken,
            _make_quality_alert("critical", "overall", 0.28),
        ]
        stub = _make_stub_scorer(alert_history=alerts)
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_alerts()
        assert result["status"] == "ok"
        assert result["data"]["total_alerts"] == 3
        assert result["data"]["filtered_count"] == 3
        assert [item["level"] for item in result["data"]["alerts"]] == ["critical", "high"]

    @pytest.mark.asyncio
    async def test_alerts_filter_skips_malformed_alert_items(self) -> None:
        broken = MagicMock()
        type(broken).level = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("broken filtered alert"))
        )
        alerts = [
            _make_quality_alert("high", "consistency", 0.42),
            broken,
            _make_quality_alert("critical", "overall", 0.28),
        ]
        stub = _make_stub_scorer(alert_history=alerts)
        mock_req = _make_mock_request(level="critical")
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.get_quality_alerts()
        assert result["status"] == "ok"
        assert result["data"]["total_alerts"] == 3
        assert result["data"]["filtered_count"] == 1
        assert [item["level"] for item in result["data"]["alerts"]] == ["critical"]


# ---------------------------------------------------------------------------
# Quality reset
# ---------------------------------------------------------------------------


class TestQualityReset:
    @pytest.mark.asyncio
    async def test_reset_clears_history(self) -> None:
        s = _make_quality_score()
        a = _make_quality_alert()
        stub = _make_stub_scorer(score_history=[s], alert_history=[a])
        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.reset_quality()
        assert result["status"] == "ok"
        assert "reset" in result["data"]["message"].lower()
        scorer = stub.plugin._quality_scorer
        assert len(scorer._score_history) == 0
        assert len(scorer._alert_history) == 0
        assert scorer._paused is False

    @pytest.mark.asyncio
    async def test_reset_replaces_malformed_history_containers(self) -> None:
        stub = _make_stub_scorer(score_history=[], alert_history=[])

        class BrokenHistory:
            def clear(self):
                raise RuntimeError("broken history clear")

        scorer = stub.plugin._quality_scorer
        scorer._score_history = BrokenHistory()
        scorer._alert_history = BrokenHistory()
        scorer._paused = True
        scorer._pause_reason = "bad state"

        mock_req = _make_mock_request()
        with patch("core.api.quality_api.request", mock_req):
            result = await stub.reset_quality()

        assert result["status"] == "ok"
        assert isinstance(scorer._score_history, deque)
        assert isinstance(scorer._alert_history, deque)
        assert len(scorer._score_history) == 0
        assert len(scorer._alert_history) == 0
        assert scorer._paused is False
        assert scorer._pause_reason == ""
