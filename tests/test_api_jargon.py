"""core/api/jargon_api.py — JargonApiMixin 测试。

Validates endpoint responses, parameter validation, and error handling.
Uses unittest.mock to mock jargon filter, store, miner, and quart.request.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.jargon_api import JargonApiMixin


def _make_mock_request(**args):
    """Create a mock quart.request with args dict and async get_json."""
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_jargon_candidate(term="破防", group_id="g1", score=0.72):
    """Create a mock JargonCandidate."""
    from core.jargon.models import JargonCandidate

    return JargonCandidate(
        term=term,
        group_id=group_id,
        score=score,
        frequency=12,
        unique_users=3,
        idf_score=1.5,
        burst_score=2.1,
        concentration_score=0.33,
        first_seen=1700000000.0,
        context_examples=["我今天真的破防了", "破防了破防了"],
    )


def _make_jargon_meaning(term="破防", group_id="g1", confirmed=True):
    """Create a mock JargonMeaning."""
    from core.jargon.models import JargonMeaning

    return JargonMeaning(
        term=term,
        group_id=group_id,
        meaning="心理防线被突破，情绪失控",
        confidence=0.85,
        is_jargon=True,
        is_confirmed=confirmed,
        is_global=False,
        is_complete=True,
        count=120,
        last_inference_count=100,
        context_examples=["我今天真的破防了"],
        created_at=1700000000.0,
        updated_at=1700000001.0,
    )


def _make_stub(*, has_filter=True, has_store=True, has_miner=False,
               candidates=None, meanings=None, store_count=5, store_confirmed=3):
    """Create a JargonApiMixin stub with mocked dependencies."""

    class Stub:
        get_jargon_candidates = JargonApiMixin.get_jargon_candidates
        get_jargon_meanings = JargonApiMixin.get_jargon_meanings
        get_jargon_stats = JargonApiMixin.get_jargon_stats
        confirm_jargon = JargonApiMixin.confirm_jargon
        mine_jargon = JargonApiMixin.mine_jargon
        _get_jargon_filter = JargonApiMixin._get_jargon_filter
        _get_jargon_store = JargonApiMixin._get_jargon_store
        _get_jargon_miner = JargonApiMixin._get_jargon_miner
        _get_feature_delegation = JargonApiMixin._get_feature_delegation
        _require_group_id = staticmethod(JargonApiMixin._require_group_id)

    stub = Stub()
    stub.plugin = None  # default: no plugin (test will set if needed)

    if has_filter or has_store or has_miner:
        stub.plugin = MagicMock()
        # Prevent lazy-creation from accidentally succeeding through
        # MagicMock auto-created attributes.
        stub.plugin.initializer = None
        stub.plugin.data_dir = None
        # No feature_delegation by default → delegation gate is skipped
        stub.plugin.feature_delegation = None

    if has_filter:
        jf = MagicMock()
        jf.get_candidates = MagicMock(return_value=candidates or [])
        jf.get_stats = MagicMock(return_value=MagicMock(
            group_id="g1", total_terms=50, candidate_count=8,
            top_candidates=candidates or [],
        ))
        stub.plugin._jargon_filter = jf

    if has_store:
        store = MagicMock()
        store.list_by_group = AsyncMock(return_value=meanings or [])
        store.confirm = AsyncMock(return_value=None)
        store.count_by_group = AsyncMock(return_value=store_count)
        store.count_confirmed = AsyncMock(return_value=store_confirmed)
        stub.plugin._jargon_store = store

    if has_miner:
        miner = MagicMock()
        miner.run_once = AsyncMock(return_value=[])
        stub.plugin._jargon_miner = miner

    return stub


# ---------------------------------------------------------------------------
# Jargon candidates
# ---------------------------------------------------------------------------


class TestJargonCandidates:
    @pytest.mark.asyncio
    async def test_requires_group_id(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "error"
        assert "group_id" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_returns_candidates(self) -> None:
        cands = [_make_jargon_candidate("破防", "g1"), _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands)
        mock_req = _make_mock_request(group_id="g1", limit="10")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        assert len(result["data"]["candidates"]) == 2
        assert result["data"]["candidates"][0]["term"] == "破防"

    @pytest.mark.asyncio
    async def test_no_filter_returns_error(self) -> None:
        stub = _make_stub(has_filter=False, has_store=False)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_window(self) -> None:
        cands = [_make_jargon_candidate("破防", "g1"), _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands)
        mock_req = _make_mock_request(group_id="g1", limit="-1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        stub.plugin._jargon_filter.get_candidates.assert_called_once_with("g1", limit=20)
        assert len(result["data"]["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_skips_malformed_candidate_items(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken candidate")))
        cands = [_make_jargon_candidate("破防", "g1"), broken, _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands)
        mock_req = _make_mock_request(group_id="g1", limit="10")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["term"] for item in result["data"]["candidates"]] == ["破防", "躺平"]

    @pytest.mark.asyncio
    async def test_tolerates_malformed_candidate_container(self) -> None:
        class BrokenCandidates:
            def __iter__(self):
                raise RuntimeError("broken candidates")

            def __len__(self):
                raise RuntimeError("broken candidate length")

            def __bool__(self):
                return True

        stub = _make_stub(candidates=[])
        stub.plugin._jargon_filter.get_candidates = MagicMock(
            return_value=BrokenCandidates()
        )
        mock_req = _make_mock_request(group_id="g1", limit="10")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        assert result["data"]["candidates"] == []
        assert result["data"]["total"] == 0


# ---------------------------------------------------------------------------
# Jargon meanings
# ---------------------------------------------------------------------------


class TestJargonMeanings:
    @pytest.mark.asyncio
    async def test_requires_group_id(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_returns_meanings(self) -> None:
        meanings = [_make_jargon_meaning("破防", "g1"), _make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(meanings=meanings)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "ok"
        assert len(result["data"]["meanings"]) == 2

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self) -> None:
        stub = _make_stub(has_store=False)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_skips_malformed_meaning_items(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken meaning")))
        meanings = [_make_jargon_meaning("破防", "g1"), broken, _make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(meanings=meanings)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["term"] for item in result["data"]["meanings"]] == ["破防", "躺平"]


# ---------------------------------------------------------------------------
# Jargon stats
# ---------------------------------------------------------------------------


class TestJargonStats:
    @pytest.mark.asyncio
    async def test_returns_stats_with_store_counts(self) -> None:
        cands = [_make_jargon_candidate("破防", "g1")]
        stub = _make_stub(candidates=cands, store_count=5, store_confirmed=3)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "ok"
        assert result["data"]["store_total"] == 5
        assert result["data"]["store_confirmed"] == 3

    @pytest.mark.asyncio
    async def test_requires_group_id(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_stats_skips_malformed_top_candidates(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken top candidate")))
        cands = [_make_jargon_candidate("破防", "g1"), broken, _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands, store_count=5, store_confirmed=3)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "ok"
        assert [item["term"] for item in result["data"]["top_candidates"]] == ["破防", "躺平"]

    @pytest.mark.asyncio
    async def test_stats_tolerates_malformed_top_candidate_container(self) -> None:
        class BrokenCandidates:
            def __iter__(self):
                raise RuntimeError("broken stats candidates")

            def __bool__(self):
                return True

        broken_stats = MagicMock()
        broken_stats.group_id = "g1"
        broken_stats.total_terms = 50
        broken_stats.candidate_count = 8
        broken_stats.top_candidates = BrokenCandidates()

        stub = _make_stub(candidates=[], store_count=5, store_confirmed=3)
        stub.plugin._jargon_filter.get_stats = MagicMock(return_value=broken_stats)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "ok"
        assert result["data"]["top_candidates"] == []
        assert result["data"]["candidate_count"] == 8

    @pytest.mark.asyncio
    async def test_stats_returns_error_for_malformed_stats_payload(self) -> None:
        broken_stats = MagicMock()
        type(broken_stats).group_id = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("broken stats"))
        )
        broken_stats.top_candidates = [_make_jargon_candidate("破防", "g1")]
        stub = _make_stub(candidates=[], store_count=5, store_confirmed=3)
        stub.plugin._jargon_filter.get_stats = MagicMock(return_value=broken_stats)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "error"
        assert "获取黑话统计失败" in result["message"]


# ---------------------------------------------------------------------------
# Jargon confirm
# ---------------------------------------------------------------------------


class TestJargonConfirm:
    @pytest.mark.asyncio
    async def test_confirm_success(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "term": "破防", "group_id": "g1", "confirmed": True,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "ok"
        assert result["data"]["action"] == "confirmed"

    @pytest.mark.asyncio
    async def test_reject_success(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "term": "破防", "group_id": "g1", "confirmed": False,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "ok"
        assert result["data"]["action"] == "rejected"

    @pytest.mark.asyncio
    async def test_missing_term_returns_error(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "confirmed": True,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "error"
        assert "term" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(side_effect=ValueError("bad json"))
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Jargon mine
# ---------------------------------------------------------------------------


class TestJargonMine:
    @pytest.mark.asyncio
    async def test_mine_requires_group_id(self) -> None:
        stub = _make_stub(has_miner=True, has_store=True)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"limit": 5})
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "error"
        assert "group_id" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_mine_success(self) -> None:
        meanings = [_make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=meanings)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": 5,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        assert result["data"]["inferred_count"] == 1

    @pytest.mark.asyncio
    async def test_no_miner_returns_error(self) -> None:
        stub = _make_stub(has_miner=False)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"group_id": "g1"})
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_window(self) -> None:
        meanings = [_make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=meanings)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": -3,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        stub.plugin._jargon_miner.run_once.assert_awaited_once_with("g1", limit=5)
        assert result["data"]["inferred_count"] == 1

    @pytest.mark.asyncio
    async def test_mine_skips_malformed_result_items(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken mined meaning")))
        meanings = [_make_jargon_meaning("躺平", "g1"), broken, _make_jargon_meaning("破防", "g1")]
        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=meanings)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": 5,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        assert result["data"]["inferred_count"] == 3
        assert [item["term"] for item in result["data"]["results"]] == ["躺平", "破防"]

    @pytest.mark.asyncio
    async def test_mine_tolerates_malformed_result_container(self) -> None:
        class BrokenResults:
            def __iter__(self):
                raise RuntimeError("broken mined results")

            def __len__(self):
                raise RuntimeError("broken mined result length")

            def __bool__(self):
                return True

        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=BrokenResults())
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": 5,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        assert result["data"]["inferred_count"] == 0
        assert result["data"]["results"] == []
