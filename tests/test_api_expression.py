"""core/api/expression_api.py — ExpressionApiMixin 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.expression_api import ExpressionApiMixin
from core.base.list_sorting import SortQuery


def _make_mock_request(**args):
    mock = MagicMock()
    mock.args = args
    return mock


def _make_pattern(
    *,
    pattern_id: int = 1,
    situation: str = "hello there",
    expression: str = "hi human",
    group_id: str = "g1",
    persona_id: str = "default",
):
    pattern = MagicMock()
    pattern.pattern_id = pattern_id
    pattern.situation = situation
    pattern.expression = expression
    pattern.group_id = group_id
    pattern.persona_id = persona_id
    pattern.user_id = None
    pattern.weight = 1.5
    pattern.usage_count = 2
    pattern.created_at = 1700000000.0
    pattern.last_used_at = 1700000100.0
    return pattern


class TestExpressionPatterns:
    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_for_learner(self) -> None:
        learner = MagicMock()
        learner.get_patterns_for_injection = AsyncMock(
            return_value=[_make_pattern(pattern_id=11)]
        )

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=learner,
            _expression_store=None,
            initializer=None,
        )

        mock_req = _make_mock_request(group_id="room-1", limit="-1")
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        learner.get_patterns_for_injection.assert_awaited_once_with(
            group_id="room-1",
            persona_id="default",
            user_id=None,
            limit=20,
        )

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_for_store(self) -> None:
        store = MagicMock()
        store.get_top_by_weight = AsyncMock(return_value=[_make_pattern(pattern_id=22)])
        store.count_by_scope = AsyncMock(return_value=1)

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = MagicMock()
        stub.plugin._expression_store = store

        mock_req = _make_mock_request(group_id="room-2", persona_id="p1", limit="-7")
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        store.get_top_by_weight.assert_awaited_once()
        _, kwargs = store.get_top_by_weight.await_args
        assert kwargs == {"limit": 20, "sort": SortQuery("weight", "desc")}
        assert result["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_forwards_explicit_sort_to_store_before_limit(self) -> None:
        store = MagicMock()
        store.get_top_by_weight = AsyncMock(return_value=[_make_pattern(pattern_id=23)])
        store.count_by_scope = AsyncMock(return_value=1)
        learner = MagicMock()
        learner.get_patterns_for_injection = AsyncMock(
            return_value=[_make_pattern(pattern_id=99)]
        )

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=learner,
            _expression_store=store,
            initializer=None,
        )

        mock_req = _make_mock_request(
            group_id="room-2",
            sort_by="usage_count",
            sort_order="desc",
        )
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        _, kwargs = store.get_top_by_weight.await_args
        assert kwargs == {
            "limit": 20,
            "sort": SortQuery("usage_count", "desc"),
        }
        learner.get_patterns_for_injection.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sort_by", "sort_order", "field"),
        [
            ("missing", "asc", "sort_by"),
            ("weight", "DESC", "sort_order"),
        ],
    )
    async def test_rejects_invalid_sort_before_domain_read(
        self,
        sort_by: str,
        sort_order: str,
        field: str,
    ) -> None:
        store = MagicMock()
        store.get_top_by_weight = AsyncMock()
        learner = MagicMock()
        learner.get_patterns_for_injection = AsyncMock()

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=learner,
            _expression_store=store,
            initializer=None,
        )

        mock_req = _make_mock_request(
            group_id="room-2",
            sort_by=sort_by,
            sort_order=sort_order,
        )
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["code"] == "invalid_query"
        assert field in result["field_errors"]
        store.get_top_by_weight.assert_not_awaited()
        learner.get_patterns_for_injection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_learner_skips_malformed_pattern_items(self) -> None:
        learner = MagicMock()
        broken = MagicMock()
        type(broken).pattern_id = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("broken pattern"))
        )
        learner.get_patterns_for_injection = AsyncMock(
            return_value=[
                _make_pattern(pattern_id=11),
                broken,
                _make_pattern(pattern_id=12),
            ]
        )

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=learner,
            _expression_store=None,
            initializer=None,
        )

        mock_req = _make_mock_request(group_id="room-1")
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["pattern_id"] for item in result["data"]["patterns"]] == [11, 12]

    @pytest.mark.asyncio
    async def test_store_skips_malformed_pattern_items(self) -> None:
        store = MagicMock()
        broken = MagicMock()
        type(broken).pattern_id = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("broken pattern"))
        )
        store.get_top_by_weight = AsyncMock(
            return_value=[
                _make_pattern(pattern_id=21),
                broken,
                _make_pattern(pattern_id=22),
            ]
        )
        store.count_by_scope = AsyncMock(return_value=3)

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=None,
            _expression_store=store,
            initializer=None,
        )

        mock_req = _make_mock_request(group_id="room-2", persona_id="p1")
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["pattern_id"] for item in result["data"]["patterns"]] == [21, 22]

    @pytest.mark.asyncio
    async def test_store_tolerates_non_iterable_pattern_container(self) -> None:
        store = MagicMock()

        class BrokenPatterns:
            def __iter__(self):
                raise RuntimeError("broken pattern container")

            def __len__(self):
                raise RuntimeError("broken pattern length")

            def __bool__(self):
                return True

        store.get_top_by_weight = AsyncMock(return_value=BrokenPatterns())
        store.count_by_scope = AsyncMock(return_value=0)

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=None,
            _expression_store=store,
            initializer=None,
        )

        mock_req = _make_mock_request(group_id="room-3", persona_id="p2")
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        assert result["data"]["patterns"] == []
        assert result["data"]["group_patterns"] == 0
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_store_coerces_malformed_total_count(self) -> None:
        store = MagicMock()
        store.get_top_by_weight = AsyncMock(
            return_value=[_make_pattern(pattern_id=31), _make_pattern(pattern_id=32)]
        )
        store.count_by_scope = AsyncMock(return_value="bad-total")

        class Stub:
            get_expression_patterns = ExpressionApiMixin.get_expression_patterns
            _get_expression_learner = ExpressionApiMixin._get_expression_learner
            _get_expression_store = ExpressionApiMixin._get_expression_store

        stub = Stub()
        stub.plugin = SimpleNamespace(
            _expression_learner=None,
            _expression_store=store,
            initializer=None,
        )

        mock_req = _make_mock_request(group_id="room-4", persona_id="p3")
        with patch("core.api.expression_api.request", mock_req):
            result = await stub.get_expression_patterns()

        assert result["status"] == "ok"
        assert [item["pattern_id"] for item in result["data"]["patterns"]] == [31, 32]
        assert result["data"]["group_patterns"] == 2
        assert result["data"]["total"] == 0
