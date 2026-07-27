"""core/api/learning_api.py — LearningApiMixin 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.learning_api import LearningApiMixin


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(*, auto_learning_available: bool = True, plugin_ready: bool = True):
    class Stub:
        get_learning_status = LearningApiMixin.get_learning_status
        get_learning_history = LearningApiMixin.get_learning_history
        reset_learning = LearningApiMixin.reset_learning

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "plugin not ready"}
            engine = MagicMock(spec=[])
            if auto_learning_available:
                optimizer = MagicMock()
                optimizer.get_history.return_value = [
                    {"param": "alpha", "old": 0.1, "new": 0.2}
                ]
                auto_learning = MagicMock()
                auto_learning.get_stats.return_value = {
                    "enabled": True,
                    "feedback": {
                        "total_hits": 3,
                        "total_recalls": 6,
                        "avg_quality": 0.75,
                        "total_corrections": 1,
                    },
                    "params": {"alpha": 0.2},
                    "history": [
                        {"timestamp": "t1", "param": "alpha", "old": 0.1, "new": 0.2}
                    ],
                }
                auto_learning._optimizer = optimizer
                auto_learning.reset = AsyncMock()
                engine.auto_learning = auto_learning
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


class TestLearningHappyPath:
    @pytest.mark.asyncio
    async def test_get_learning_status_flattens_stats(self) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            result = await _make_mixin().get_learning_status()
        assert result["status"] == "ok"
        assert result["data"]["hit_rate"] == 0.5
        assert result["data"]["avg_quality"] == 0.75
        assert result["data"]["parameters"] == {"alpha": 0.2}

    @pytest.mark.asyncio
    async def test_get_learning_status_tolerates_malformed_stats_payload(self) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=(
                    {
                        "memory_engine": MagicMock(
                            auto_learning=MagicMock(
                                get_stats=MagicMock(
                                    return_value={
                                        "enabled": "yes",
                                        "feedback": {
                                            "total_hits": "bad",
                                            "total_recalls": None,
                                            "avg_quality": None,
                                            "total_corrections": "oops",
                                        },
                                        "params": "not-a-dict",
                                        "history": [
                                            {
                                                "timestamp": None,
                                                "reason": None,
                                                "param": "alpha",
                                                "old": 0.1,
                                                "new": 0.2,
                                            },
                                            "bad-entry",
                                        ],
                                    }
                                )
                            )
                        )
                    },
                    None,
                )
            )
            result = await mixin.get_learning_status()
        assert result["status"] == "ok"
        assert result["data"]["hit_rate"] == 0.0
        assert result["data"]["avg_quality"] == 0.5
        assert result["data"]["total_trials"] == 1
        assert result["data"]["total_corrections"] == 0
        assert result["data"]["parameters"] == {}
        assert result["data"]["history"] == [
            {
                "timestamp": "None",
                "action": None,
                "detail": "alpha: 0.1 → 0.2",
            },
            {"timestamp": "", "action": "", "detail": ""},
        ]
        assert result["data"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_learning_status_tolerates_non_mapping_stats_payload(
        self,
    ) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=(
                    {
                        "memory_engine": MagicMock(
                            auto_learning=MagicMock(
                                get_stats=MagicMock(return_value="bad-stats")
                            )
                        )
                    },
                    None,
                )
            )
            result = await mixin.get_learning_status()
        assert result["status"] == "ok"
        assert result["data"]["hit_rate"] == 0.0
        assert result["data"]["avg_quality"] == 0.5
        assert result["data"]["total_trials"] == 1
        assert result["data"]["total_corrections"] == 0
        assert result["data"]["parameters"] == {}
        assert result["data"]["history"] == []
        assert result["data"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_learning_status_accepts_iterable_history_payload(self) -> None:
        req = _mock_request()
        history = (
            {"timestamp": "t1", "param": "alpha", "old": 0.1, "new": 0.2},
            {"timestamp": "t2", "reason": "adjusted"},
        )
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=(
                    {
                        "memory_engine": MagicMock(
                            auto_learning=MagicMock(
                                get_stats=MagicMock(
                                    return_value={
                                        "enabled": True,
                                        "feedback": {
                                            "total_hits": 2,
                                            "total_recalls": 4,
                                            "avg_quality": 0.8,
                                            "total_corrections": 1,
                                        },
                                        "params": {"alpha": 0.2},
                                        "history": history,
                                    }
                                )
                            )
                        )
                    },
                    None,
                )
            )
            result = await mixin.get_learning_status()
        assert result["status"] == "ok"
        assert result["data"]["history"] == [
            {
                "timestamp": "t1",
                "action": "alpha",
                "detail": "alpha: 0.1 → 0.2",
            },
            {
                "timestamp": "t2",
                "action": "adjusted",
                "detail": "adjusted",
            },
        ]

    @pytest.mark.asyncio
    async def test_get_learning_history_returns_history(self) -> None:
        req = _mock_request(limit="1")
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            result = await mixin.get_learning_history()
        assert result["status"] == "ok"
        assert result["data"]["history"] == [{"param": "alpha", "old": 0.1, "new": 0.2}]

    @pytest.mark.asyncio
    async def test_get_learning_history_tolerates_non_list_history_payload(
        self,
    ) -> None:
        req = _mock_request(limit="5")
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=(
                    {
                        "memory_engine": MagicMock(
                            auto_learning=MagicMock(
                                _optimizer=MagicMock(
                                    get_history=MagicMock(return_value="bad-history")
                                )
                            )
                        )
                    },
                    None,
                )
            )
            result = await mixin.get_learning_history()
        assert result["status"] == "ok"
        assert result["data"]["history"] == []

    @pytest.mark.asyncio
    async def test_get_learning_history_accepts_iterable_history_payload(self) -> None:
        req = _mock_request(limit="5")
        expected = (
            {"param": "alpha", "old": 0.1, "new": 0.2},
            {"param": "beta", "old": 0.2, "new": 0.3},
        )
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=(
                    {
                        "memory_engine": MagicMock(
                            auto_learning=MagicMock(
                                _optimizer=MagicMock(
                                    get_history=MagicMock(return_value=expected)
                                )
                            )
                        )
                    },
                    None,
                )
            )
            result = await mixin.get_learning_history()
        assert result["status"] == "ok"
        assert result["data"]["history"] == list(expected)

    @pytest.mark.asyncio
    async def test_reset_learning_calls_manager(self) -> None:
        req = _mock_request()
        with patch("core.api.learning_api.request", req):
            mixin = _make_mixin()
            result = await mixin.reset_learning()
        assert result["status"] == "ok"
