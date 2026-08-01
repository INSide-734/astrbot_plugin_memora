"""core/api/learning_api.py — 自主学习 shadow 候选 API 测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.learning_api import LearningApiMixin


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    return mock


def _auto_learning_mock():
    auto_learning = MagicMock()
    auto_learning.safe_summary.return_value = {
        "available": True,
        "candidate_count": 1,
        "ready_count": 1,
        "rejected_count": 0,
        "published_count": 0,
        "reasons": ["candidate"],
    }
    auto_learning.get_candidates.return_value = [
        {
            "scope_domain": "private:u",
            "proposed_document_weight": 0.72,
            "proposed_graph_weight": 0.28,
            "delta_from_baseline": 0.02,
            "accepted_count": 4,
            "independent_window_count": 2,
            "decayed_support": 0.8,
            "status": "ready_for_review",
            "reason_code": "candidate",
        }
    ]
    auto_learning.reset = AsyncMock()
    return auto_learning


def _make_mixin(*, auto_learning_available: bool = True, plugin_ready: bool = True):
    class Stub:
        get_learning_status = LearningApiMixin.get_learning_status
        get_learning_history = LearningApiMixin.get_learning_history
        reset_learning = LearningApiMixin.reset_learning

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "plugin not ready"}
            policy = MagicMock()
            policy.baseline_document_weight = 0.7
            policy.baseline_graph_weight = 0.3
            feedback = MagicMock()
            feedback.policy = policy
            engine = MagicMock(spec=[])
            engine.config = {
                "document_route_weight": 0.61,
                "graph_route_weight": 0.39,
            }
            if auto_learning_available:
                engine.auto_learning = _auto_learning_mock()
                engine.feedback_signal_manager = feedback
            return {"memory_engine": engine}, None

    return Stub()


class TestLearningValidation:
    @pytest.mark.asyncio
    async def test_get_learning_status_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin(plugin_ready=False).get_learning_status()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_learning_history_rejects_non_numeric_limit(self) -> None:
        req = _mock_request(limit="abc")
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin().get_learning_history()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_learning_history_requires_auto_learning(self) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin(
                auto_learning_available=False
            ).get_learning_history()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_learning_history_rejects_excessive_limit(self) -> None:
        """候选历史限制必须受固定上限约束。"""

        req = _mock_request(limit="101")
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin().get_learning_history()
        assert result["status"] == "error"


class TestLearningHappyPath:
    @pytest.mark.asyncio
    async def test_get_learning_status_exposes_candidates_and_baseline(self) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin().get_learning_status()
        assert result["status"] == "ok"
        assert result["data"]["candidate_count"] == 1
        assert result["data"]["ready_count"] == 1
        assert result["data"]["baseline"] == {
            "document_route_weight": 0.7,
            "graph_route_weight": 0.3,
        }
        assert result["data"]["current"] == {
            "document_route_weight": 0.61,
            "graph_route_weight": 0.39,
        }
        candidate = result["data"]["candidates"][0]
        assert candidate["status"] == "ready_for_review"
        assert candidate["proposed_document_weight"] == 0.72
        assert "scope_domain" not in candidate
        assert "private:u" not in json.dumps(result, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_get_learning_status_constrains_candidate_and_reason_types(
        self,
    ) -> None:
        """任意状态文件对象不能通过候选字段或摘要 reason 泄漏。"""

        mixin = _make_mixin()
        auto = _auto_learning_mock()
        auto.safe_summary.return_value = {
            "available": True,
            "candidate_count": True,
            "ready_count": -1,
            "rejected_count": 0,
            "published_count": 0,
            "reasons": [{"leak": "BODY-CANARY"}],
        }
        auto.get_candidates.return_value = [
            {
                "proposed_document_weight": {"leak": "BODY-CANARY"},
                "proposed_graph_weight": 0.3,
                "delta_from_baseline": float("inf"),
                "accepted_count": True,
                "independent_window_count": -1,
                "decayed_support": {"leak": "BODY-CANARY"},
                "status": {"leak": "BODY-CANARY"},
                "reason_code": {"leak": "BODY-CANARY"},
            }
        ]
        feedback = MagicMock()
        feedback.policy = MagicMock(
            baseline_document_weight=0.7,
            baseline_graph_weight=0.3,
        )
        engine = MagicMock(
            auto_learning=auto,
            feedback_signal_manager=feedback,
            config={"document_route_weight": 0.61, "graph_route_weight": 0.39},
        )
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": engine}, None)
        )

        with patch("core.api.learning_api.request", _mock_request()):
            result = await mixin.get_learning_status()

        serialized = json.dumps(result, ensure_ascii=False)
        assert "BODY-CANARY" not in serialized
        assert result["data"]["candidate_count"] == 0
        assert result["data"]["reasons"] == ["invalid_state"]
        assert result["data"]["candidates"][0]["status"] == "invalid_state"

    @pytest.mark.asyncio
    async def test_get_learning_history_returns_candidate_views(self) -> None:
        req = _mock_request(limit="1")
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin().get_learning_history()
        assert result["status"] == "ok"
        assert len(result["data"]["history"]) == 1
        assert result["data"]["history"][0]["reason_code"] == "candidate"

    @pytest.mark.asyncio
    async def test_reset_learning_clears_shadow_state(self) -> None:
        req = _mock_request()
        mixin = _make_mixin()
        auto = _auto_learning_mock()
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": MagicMock(auto_learning=auto)}, None)
        )
        with patch("core.api.learning_api.request", req):
            result = await mixin.reset_learning()
        assert result["status"] == "ok"
        auto.reset.assert_awaited_once()
