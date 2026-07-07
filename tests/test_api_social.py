"""core/api/social_api.py — SocialApiMixin 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.social_api import SocialApiMixin


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    return mock


def _make_relation(
    *,
    from_user: str = "u1",
    to_user: str = "u2",
    relation_type: str = "colleague",
    strength: float = 0.6,
    frequency: int = 3,
    group_id: str = "g1",
    tags: list[str] | None = None,
):
    relation = MagicMock()
    relation.from_user = from_user
    relation.to_user = to_user
    relation.relation_type = relation_type
    relation.strength = strength
    relation.frequency = frequency
    relation.last_interaction = 1700000000.0
    relation.group_id = group_id
    relation.tags = tags or []
    return relation


def _make_stub(*, group_relations=None, all_relations=None, has_manager=True):
    class Stub:
        get_social_relations = SocialApiMixin.get_social_relations
        _get_relation_manager = SocialApiMixin._get_relation_manager

    stub = Stub()
    if has_manager:
        stub.plugin = MagicMock()
        manager = MagicMock()
        manager.get_relations_by_group = MagicMock(return_value=group_relations or [])
        manager.list_all = MagicMock(return_value=all_relations or [])
        stub.plugin._relation_manager = manager
    return stub


class TestSocialRelations:
    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self) -> None:
        stub = _make_stub(has_manager=False)
        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_group_filter_awaits_async_manager_call(self) -> None:
        relations = [_make_relation(group_id="group-1", relation_type="best_friend")]
        stub = _make_stub(group_relations=relations)
        stub.plugin._relation_manager.get_relations_by_group = AsyncMock(
            return_value=relations
        )

        with patch("core.api.social_api.request", _mock_request(group_id="group-1")):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        stub.plugin._relation_manager.get_relations_by_group.assert_awaited_once_with(
            "group-1"
        )
        assert result["data"]["relations"][0]["group_id"] == "group-1"

    @pytest.mark.asyncio
    async def test_category_filter_reduces_results(self) -> None:
        relations = [
            _make_relation(relation_type="colleague"),
            _make_relation(from_user="u3", to_user="u4", relation_type="lover"),
        ]
        stub = _make_stub(all_relations=relations)

        with patch("core.api.social_api.request", _mock_request(category="emotional")):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert result["data"]["relations"][0]["relation_type"] == "lover"

    @pytest.mark.asyncio
    async def test_unknown_relation_type_keeps_unknown_category(self) -> None:
        relations = [_make_relation(relation_type="mystery_bond")]
        stub = _make_stub(all_relations=relations)

        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["relations"][0]["category"] == "unknown"

    @pytest.mark.asyncio
    async def test_skips_malformed_relation_items(self) -> None:
        broken = MagicMock()
        type(broken).from_user = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken relation")))
        relations = [
            _make_relation(from_user="u1", to_user="u2", relation_type="colleague"),
            broken,
            _make_relation(from_user="u3", to_user="u4", relation_type="lover"),
        ]
        stub = _make_stub(all_relations=relations)

        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert [(item["from_user"], item["to_user"]) for item in result["data"]["relations"]] == [
            ("u1", "u2"),
            ("u3", "u4"),
        ]

    @pytest.mark.asyncio
    async def test_tolerates_malformed_relation_container(self) -> None:
        class BrokenRelations:
            def __iter__(self):
                raise RuntimeError("broken relations")

            def __bool__(self):
                return True

        stub = _make_stub(all_relations=[])
        stub.plugin._relation_manager.list_all = MagicMock(return_value=BrokenRelations())

        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["relations"] == []
        assert result["data"]["total"] == 0
