"""core/api/social_api.py — SocialApiMixin 测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.social_api import SocialApiMixin
from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock()
    return mock


def _make_relation(
    *,
    from_user: str = "u1",
    to_user: str = "u2",
    relation_type: str = "colleague",
    strength: float = 0.6,
    frequency: int = 3,
    last_interaction: float = 1700000000.0,
    group_id: str = "g1",
    tags: list[str] | None = None,
):
    relation = MagicMock()
    relation.from_user = from_user
    relation.to_user = to_user
    relation.relation_type = relation_type
    relation.strength = strength
    relation.frequency = frequency
    relation.last_interaction = last_interaction
    relation.group_id = group_id
    relation.tags = tags or []
    return relation


def _identity(**overrides):
    identity = {
        "from_user": "alice",
        "to_user": "bob",
        "group_id": "g1",
        "relation_type": "colleague",
    }
    identity.update(overrides)
    return identity


def _create_payload(**overrides):
    payload = {
        **_identity(),
        "strength": 0.4,
        "tags": ["work"],
    }
    payload.update(overrides)
    return payload


def _update_payload(**overrides):
    payload = {
        "identity": _identity(),
        "changes": {"strength": 0.8},
        "expected_revision": "rev-old",
    }
    payload.update(overrides)
    return payload


def _delete_payload(**overrides):
    payload = {
        "identity": _identity(),
        "expected_revision": "rev-old",
    }
    payload.update(overrides)
    return payload


def _batch_payload(action="delete", *, items=None, params=None):
    return {
        "action": action,
        "items": items
        or [{"identity": _identity(), "expected_revision": "rev-old"}],
        "params": params or {},
    }


def _make_stub(*, group_relations=None, all_relations=None, has_manager=True):
    class Stub:
        get_social_relations = SocialApiMixin.get_social_relations
        _get_relation_manager = SocialApiMixin._get_relation_manager

    stub = Stub()
    if has_manager:
        manager = MagicMock()
        manager.get_relations_by_group = MagicMock(return_value=group_relations or [])
        manager.list_all = MagicMock(return_value=all_relations or [])
        manager.revision_for = MagicMock(return_value="rev-list")
        stub.plugin = SimpleNamespace(
            _relation_manager=manager,
            relation_manager=None,
            initializer=None,
        )
    else:
        stub.plugin = SimpleNamespace(
            _relation_manager=None,
            relation_manager=None,
            initializer=None,
        )
    return stub


def _make_write_stub(*, current=None):
    class Stub:
        create_social_relation = SocialApiMixin.create_social_relation
        update_social_relation = SocialApiMixin.update_social_relation
        delete_social_relation = SocialApiMixin.delete_social_relation
        batch_social_relations = SocialApiMixin.batch_social_relations
        _get_relation_manager = SocialApiMixin._get_relation_manager
        _maintenance_write_guard = MagicMock(return_value=None)

    current = current or _make_relation(
        from_user="alice",
        to_user="bob",
        relation_type="colleague",
        strength=0.4,
        frequency=3,
        group_id="g1",
        tags=["work", "trusted"],
    )
    created = _make_relation(
        from_user="alice",
        to_user="bob",
        relation_type="colleague",
        strength=0.4,
        frequency=0,
        last_interaction=0.0,
        group_id="g1",
        tags=["work"],
    )
    manager = MagicMock()
    manager.list_all = AsyncMock(return_value=[current])
    manager.create_manual_relation = AsyncMock(return_value=created)
    manager.update_manual_relation = AsyncMock(return_value=current)
    manager.delete_manual_relation = AsyncMock(return_value=True)
    manager.revision_for = MagicMock(return_value="rev-result")

    stub = Stub()
    stub.plugin = SimpleNamespace(
        _relation_manager=manager,
        relation_manager=None,
        initializer=None,
    )
    return stub


class TestSocialRelations:
    @pytest.mark.asyncio
    async def test_no_manager_returns_stable_component_error(self) -> None:
        stub = _make_stub(has_manager=False)
        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()
        assert result["status"] == "error"
        assert result["code"] == "component_unavailable"

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
    async def test_list_includes_revision_for_every_relation(self) -> None:
        relations = [_make_relation(), _make_relation(from_user="u3", to_user="u4")]
        stub = _make_stub(all_relations=relations)
        stub.plugin._relation_manager.revision_for.side_effect = ["rev-1", "rev-2"]

        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()

        assert [item["revision"] for item in result["data"]["relations"]] == [
            "rev-1",
            "rev-2",
        ]

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
        type(broken).from_user = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("broken relation"))
        )
        relations = [
            _make_relation(from_user="u1", to_user="u2"),
            broken,
            _make_relation(from_user="u3", to_user="u4", relation_type="lover"),
        ]
        stub = _make_stub(all_relations=relations)

        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
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
        stub.plugin._relation_manager.list_all = MagicMock(
            return_value=BrokenRelations()
        )

        with patch("core.api.social_api.request", _mock_request()):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["relations"] == []
        assert result["data"]["total"] == 0


class TestSocialRelationWrites:
    @pytest.mark.asyncio
    async def test_create_returns_complete_entity_and_revision(self) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload(group_id="  ")

        with patch("core.api.social_api.request", request_mock):
            result = await stub.create_social_relation()

        assert result["status"] == "ok"
        assert result["data"]["entity"]["frequency"] == 0
        assert result["data"]["entity"]["last_interaction"] == 0.0
        assert result["data"]["revision"] == "rev-result"
        assert stub.plugin._relation_manager.create_manual_relation.await_args.kwargs[
            "group_id"
        ] == ""

    @pytest.mark.asyncio
    async def test_update_stale_revision_maps_to_edit_conflict(self) -> None:
        stub = _make_write_stub()
        stub.plugin._relation_manager.update_manual_relation.side_effect = (
            EditConflictError(
                {
                    **_identity(),
                    "strength": 0.6,
                    "frequency": 3,
                    "last_interaction": 1700000000.0,
                    "tags": [],
                    "category": "career",
                },
                "rev-current",
            )
        )
        request_mock = _mock_request()
        request_mock.get_json.return_value = _update_payload()

        with patch("core.api.social_api.request", request_mock):
            result = await stub.update_social_relation()

        assert result["code"] == "edit_conflict"
        assert result["data"]["current_revision"] == "rev-current"

    @pytest.mark.asyncio
    async def test_partial_update_preserves_omitted_editable_values(self) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _update_payload(
            changes={"strength": 0.8}
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.update_social_relation()

        assert result["status"] == "ok"
        kwargs = stub.plugin._relation_manager.update_manual_relation.await_args.kwargs
        assert kwargs["relation_type"] == "colleague"
        assert kwargs["strength"] == 0.8
        assert kwargs["tags"] == ["work", "trusted"]
        assert kwargs["expected_revision"] == "rev-old"

    @pytest.mark.asyncio
    async def test_delete_returns_normalized_identity_envelope(self) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _delete_payload(
            identity=_identity(from_user=" alice ", group_id=" ")
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.delete_social_relation()

        assert result == {
            "status": "ok",
            "data": {
                "deleted": True,
                "identity": _identity(group_id=""),
            },
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exception", "expected_code"),
        [
            (EntityValidationError({"strength": "bad"}), "validation_error"),
            (EntityAlreadyExistsError("duplicate"), "already_exists"),
            (EntityNotFoundError("missing"), "not_found"),
        ],
    )
    async def test_domain_exceptions_map_to_stable_codes(
        self, exception, expected_code
    ) -> None:
        stub = _make_write_stub()
        stub.plugin._relation_manager.create_manual_relation.side_effect = exception
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload()

        with patch("core.api.social_api.request", request_mock):
            result = await stub.create_social_relation()

        assert result["code"] == expected_code
        if expected_code == "validation_error":
            assert result["field_errors"] == {"strength": "bad"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "field"),
        [
            ("create_social_relation", _create_payload(frequency=1), "frequency"),
            ("update_social_relation", _update_payload(extra=True), "extra"),
            (
                "update_social_relation",
                _update_payload(identity=_identity(category="career")),
                "category",
            ),
            (
                "update_social_relation",
                _update_payload(changes={"from_user": "mallory"}),
                "from_user",
            ),
        ],
    )
    async def test_unknown_or_read_only_fields_are_rejected(
        self, method_name, payload, field
    ) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = payload

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        assert result["code"] == "validation_error"
        assert any(name.endswith(field) for name in result["field_errors"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "field"),
        [
            ("create_social_relation", _create_payload(from_user=True), "from_user"),
            (
                "update_social_relation",
                _update_payload(identity=_identity(to_user=True)),
                "identity.to_user",
            ),
            ("create_social_relation", _create_payload(strength=float("inf")), "strength"),
            (
                "update_social_relation",
                _update_payload(changes={"strength": float("nan")}),
                "changes.strength",
            ),
        ],
    )
    async def test_boolean_identifiers_and_nonfinite_strength_are_structured_validation(
        self, method_name, payload, field
    ) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = payload

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        assert result["code"] == "validation_error"
        assert field in result["field_errors"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [None, [], "not-an-object"])
    async def test_malformed_json_body_is_invalid_request(self, payload) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = payload

        with patch("core.api.social_api.request", request_mock):
            result = await stub.create_social_relation()

        assert result["code"] == "invalid_request"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload"),
        [
            ("create_social_relation", _create_payload()),
            ("update_social_relation", _update_payload()),
            ("delete_social_relation", _delete_payload()),
            ("batch_social_relations", _batch_payload()),
        ],
    )
    async def test_unavailable_manager_is_stable(self, method_name, payload) -> None:
        stub = _make_write_stub()
        stub.plugin._relation_manager = None
        request_mock = _mock_request()
        request_mock.get_json.return_value = payload

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        assert result["code"] == "component_unavailable"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name",
        [
            "create_social_relation",
            "update_social_relation",
            "delete_social_relation",
            "batch_social_relations",
        ],
    )
    async def test_maintenance_guard_runs_before_json_read(self, method_name) -> None:
        stub = _make_write_stub()
        guarded = {"status": "error", "code": "maintenance_pending"}
        stub._maintenance_write_guard = MagicMock(return_value=guarded)
        request_mock = _mock_request()

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        assert result is guarded
        request_mock.get_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_error_response_and_log_omit_payload_values(self) -> None:
        stub = _make_write_stub()
        secret = "private-tag-and-text"
        stub.plugin._relation_manager.create_manual_relation.side_effect = RuntimeError(
            secret
        )
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload(tags=[secret])

        with (
            patch("core.api.social_api.request", request_mock),
            patch("core.api.social_api.logger.error") as log_error,
        ):
            result = await stub.create_social_relation()

        assert result["code"] == "internal_error"
        assert secret not in json.dumps(result, ensure_ascii=False)
        assert secret not in repr(log_error.call_args_list)


class TestSocialRelationBatch:
    @pytest.mark.asyncio
    async def test_batch_delete_uses_revision_checked_manager(self) -> None:
        stub = _make_write_stub()
        items = [
            {"identity": _identity(), "expected_revision": "rev-1"},
            {
                "identity": _identity(to_user="carol"),
                "expected_revision": "rev-2",
            },
        ]
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload(items=items)

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert len(result["data"]["succeeded_ids"]) == 2
        assert stub.plugin._relation_manager.delete_manual_relation.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action", "params_tags", "expected_tags"),
        [
            ("add_tags", [" new ", "work"], ["work", "trusted", "new"]),
            ("remove_tags", ["trusted"], ["work"]),
        ],
    )
    async def test_batch_tag_actions_preserve_other_business_values(
        self, action, params_tags, expected_tags
    ) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload(
            action, params={"tags": params_tags}
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        assert result["status"] == "ok"
        kwargs = stub.plugin._relation_manager.update_manual_relation.await_args.kwargs
        assert kwargs["relation_type"] == "colleague"
        assert kwargs["strength"] == 0.4
        assert kwargs["tags"] == expected_tags
        assert kwargs["expected_revision"] == "rev-old"
        assert not stub.plugin._relation_manager.update_tags.called

    @pytest.mark.asyncio
    async def test_batch_continues_after_partial_failure(self) -> None:
        stub = _make_write_stub()
        stub.plugin._relation_manager.delete_manual_relation.side_effect = [
            EntityNotFoundError("missing"),
            True,
        ]
        items = [
            {"identity": _identity(), "expected_revision": "rev-1"},
            {
                "identity": _identity(to_user="carol"),
                "expected_revision": "rev-2",
            },
        ]
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload(items=items)

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        assert result["status"] == "ok"
        assert len(result["data"]["failures"]) == 1
        assert result["data"]["failures"][0]["code"] == "not_found"
        assert result["data"]["succeeded_ids"] == [_identity(to_user="carol")]

    @pytest.mark.asyncio
    async def test_batch_conflict_preserves_current_entity_and_revision(self) -> None:
        stub = _make_write_stub()
        current_entity = {
            **_identity(),
            "strength": 0.6,
            "frequency": 3,
            "last_interaction": 1700000000.0,
            "tags": ["current"],
            "category": "career",
        }
        stub.plugin._relation_manager.delete_manual_relation.side_effect = (
            EditConflictError(current_entity, "rev-current")
        )
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload()

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        failure = result["data"]["failures"][0]
        assert failure["code"] == "edit_conflict"
        assert failure["current_entity"] == current_entity
        assert failure["current_revision"] == "rev-current"

    @pytest.mark.asyncio
    async def test_batch_rejects_more_than_100_before_mutation(self) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload(
            items=[
                {
                    "identity": _identity(to_user=f"user-{index}"),
                    "expected_revision": f"rev-{index}",
                }
                for index in range(101)
            ]
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        assert result["code"] == "validation_error"
        stub.plugin._relation_manager.delete_manual_relation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_reports_malformed_items_without_aborting_valid_items(self) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload(
            items=[
                {"identity": ["bad"], "expected_revision": "rev-bad"},
                {"identity": _identity(), "expected_revision": "rev-good"},
            ]
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert result["data"]["failures"][0]["code"] in {
            "invalid_request",
            "validation_error",
        }
        assert result["data"]["failures"][0]["identity"] == {"item_index": 0}
        assert result["data"]["succeeded_ids"] == [_identity()]
