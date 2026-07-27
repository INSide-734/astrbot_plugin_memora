"""core/api/social_api.py — SocialApiMixin 测试。"""

from __future__ import annotations

import asyncio
import json
import logging
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
from core.base.list_sorting import SortQuery


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
        "items": items or [{"identity": _identity(), "expected_revision": "rev-old"}],
        "params": params or {},
    }


def _audit_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "[社交关系 AUDIT]" in record.getMessage()
    ]


def _individual_audit(
    action: str,
    identity,
    *,
    result: str,
    error_code: str = "none",
    error_class: str = "none",
) -> str:
    return (
        f"[社交关系 AUDIT] action={action} entity=social_relation "
        f"identity={identity} result={result} error_code={error_code} "
        f"error_class={error_class} count=1"
    )


def _batch_audit(
    action: str,
    *,
    result: str,
    error_code: str,
    succeeded_count: int,
    failed_count: int,
) -> str:
    return (
        f"[社交关系 AUDIT] action={action} entity=social_relation identity=batch "
        f"result={result} error_code={error_code} error_class=none "
        f"succeeded_count={succeeded_count} failed_count={failed_count}"
    )


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

        with patch(
            "core.api.social_api.request",
            _mock_request(
                group_id="group-1",
                sort_by="frequency",
                sort_order="desc",
            ),
        ):
            result = await stub.get_social_relations()

        assert result["status"] == "ok"
        stub.plugin._relation_manager.get_relations_by_group.assert_awaited_once_with(
            "group-1",
            sort=SortQuery("frequency", "desc"),
        )
        assert result["data"]["relations"][0]["group_id"] == "group-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sort_by", "sort_order", "field"),
        [
            ("missing", "asc", "sort_by"),
            ("frequency", "DESC", "sort_order"),
        ],
    )
    async def test_rejects_invalid_sort_values(
        self, sort_by: str, sort_order: str, field: str
    ) -> None:
        stub = _make_stub()

        with patch(
            "core.api.social_api.request",
            _mock_request(sort_by=sort_by, sort_order=sort_order),
        ):
            result = await stub.get_social_relations()

        assert result["status"] == "error"
        assert result["code"] == "invalid_query"
        assert field in result["field_errors"]
        stub.plugin._relation_manager.list_all.assert_not_called()
        stub.plugin._relation_manager.get_relations_by_group.assert_not_called()

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
        stub.plugin._relation_manager.list_all.assert_called_once_with(
            sort=SortQuery("last_interaction", "desc")
        )

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
        assert [
            (item["from_user"], item["to_user"]) for item in result["data"]["relations"]
        ] == [
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

    @pytest.mark.asyncio
    async def test_audit_contract_read_failure_does_not_audit_or_leak_into_mutation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        read_stub = _make_stub()
        read_stub.plugin._relation_manager.list_all = AsyncMock(
            side_effect=RuntimeError("read-backend-secret-514c")
        )

        with patch("core.api.social_api.request", _mock_request()):
            read_result = await read_stub.get_social_relations()

        assert read_result["status"] == "error"
        assert read_result["code"] == "internal_error"
        assert _audit_messages(caplog) == []

        write_stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload()
        with patch("core.api.social_api.request", request_mock):
            write_result = await write_stub.create_social_relation()

        assert write_result["status"] == "ok"
        assert _audit_messages(caplog) == [
            _individual_audit("create", _identity(), result="success")
        ]
        assert "read-backend-secret-514c" not in caplog.text

    @pytest.mark.asyncio
    async def test_audit_contract_read_cancellation_propagates_without_audit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_stub()
        stub.plugin._relation_manager.list_all = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with (
            patch("core.api.social_api.request", _mock_request()),
            pytest.raises(asyncio.CancelledError),
        ):
            await stub.get_social_relations()

        assert _audit_messages(caplog) == []


class TestSocialRelationWrites:
    @pytest.mark.asyncio
    async def test_create_returns_complete_entity_and_revision(self) -> None:
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload(group_id="  ")

        with (
            patch("core.api.social_api.request", request_mock),
            patch("core.api.social_api.logger.info") as audit,
        ):
            result = await stub.create_social_relation()

        assert result["status"] == "ok"
        assert result["data"]["entity"]["frequency"] == 0
        assert result["data"]["entity"]["last_interaction"] == 0.0
        assert result["data"]["revision"] == "rev-result"
        assert (
            stub.plugin._relation_manager.create_manual_relation.await_args.kwargs[
                "group_id"
            ]
            == ""
        )
        rendered_audit = repr(audit.call_args_list)
        assert (
            "action=%s entity=social_relation identity=%s result=%s error_code=%s"
            in rendered_audit
        )
        assert "'success', 'none'" in rendered_audit
        assert "work" not in rendered_audit

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
        request_mock.get_json.return_value = _update_payload(changes={"strength": 0.8})

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
            (
                "create_social_relation",
                _create_payload(strength=float("inf")),
                "strength",
            ),
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
    async def test_mutation_audit_covers_early_validation_without_payload_leak(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        secret = "social-payload-secret-42d8"
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload(unknown=secret)

        with patch("core.api.social_api.request", request_mock):
            result = await stub.create_social_relation()

        audits = [
            record.getMessage()
            for record in caplog.records
            if "[社交关系 AUDIT]" in record.getMessage()
        ]
        assert result["code"] == "validation_error"
        assert len(audits) == 1
        assert "action=create" in audits[0]
        assert "entity=social_relation" in audits[0]
        assert "identity=unavailable" in audits[0]
        assert "result=failure" in audits[0]
        assert "error_code=validation_error" in audits[0]
        assert secret not in caplog.text
        request_mock.get_json.assert_awaited_once_with(silent=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "expected_code", "exception_secret"),
        [
            (
                EntityNotFoundError("social-domain-secret-3fc1"),
                "not_found",
                "social-domain-secret-3fc1",
            ),
            (
                RuntimeError("social-generic-secret-98a6"),
                "internal_error",
                "social-generic-secret-98a6",
            ),
        ],
    )
    async def test_mutation_audit_mapper_failure_is_single_and_redacted(
        self,
        caplog: pytest.LogCaptureFixture,
        failure: Exception,
        expected_code: str,
        exception_secret: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        payload_secret = "social-payload-secret-16bc"
        stub.plugin._relation_manager.create_manual_relation.side_effect = failure
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload(tags=[payload_secret])

        with patch("core.api.social_api.request", request_mock):
            result = await stub.create_social_relation()

        audits = [
            record.getMessage()
            for record in caplog.records
            if "[社交关系 AUDIT]" in record.getMessage()
        ]
        assert result["code"] == expected_code
        assert len(audits) == 1
        assert "action=create" in audits[0]
        assert "entity=social_relation" in audits[0]
        assert "identity=unavailable" in audits[0]
        assert "result=failure" in audits[0]
        assert f"error_code={expected_code}" in audits[0]
        rendered = caplog.text + repr(result)
        assert payload_secret not in rendered
        assert exception_secret not in rendered

    @pytest.mark.asyncio
    async def test_mutation_audit_preserves_cancellation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        stub.plugin._relation_manager.create_manual_relation.side_effect = (
            asyncio.CancelledError()
        )
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload()

        with (
            patch("core.api.social_api.request", request_mock),
            pytest.raises(asyncio.CancelledError),
        ):
            await stub.create_social_relation()

        assert not [
            record
            for record in caplog.records
            if "[社交关系 AUDIT]" in record.getMessage()
        ]

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "expected_audit"),
        [
            (
                "create_social_relation",
                _create_payload(),
                _individual_audit("create", _identity(), result="success"),
            ),
            (
                "update_social_relation",
                _update_payload(),
                _individual_audit("update", _identity(), result="success"),
            ),
            (
                "delete_social_relation",
                _delete_payload(),
                _individual_audit("delete", _identity(), result="success"),
            ),
            (
                "batch_social_relations",
                _batch_payload(),
                _batch_audit(
                    "batch_delete",
                    result="success",
                    error_code="none",
                    succeeded_count=1,
                    failed_count=0,
                ),
            ),
        ],
    )
    async def test_audit_contract_mutation_success_emits_exactly_once(
        self, caplog: pytest.LogCaptureFixture, method_name, payload, expected_audit
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = payload

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        assert result["status"] == "ok"
        assert _audit_messages(caplog) == [expected_audit]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "action"),
        [
            ("create_social_relation", "create"),
            ("update_social_relation", "update"),
            ("delete_social_relation", "delete"),
            ("batch_social_relations", "batch"),
        ],
    )
    async def test_audit_contract_mutation_early_failure_emits_exactly_once(
        self, caplog: pytest.LogCaptureFixture, method_name, action
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        request_mock = _mock_request()
        request_mock.get_json.return_value = None

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        assert result["code"] == "invalid_request"
        assert _audit_messages(caplog) == [
            _individual_audit(
                action,
                "unavailable",
                result="failure",
                error_code="invalid_request",
            )
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "manager_method", "failure", "expected_code", "error_class"),
        [
            (
                "update_social_relation",
                "update_manual_relation",
                EditConflictError({**_identity(), "strength": 0.6}, "rev-current"),
                "edit_conflict",
                "EditConflictError",
            ),
            (
                "delete_social_relation",
                "delete_manual_relation",
                EntityNotFoundError("delete-domain-secret-2d91"),
                "not_found",
                "EntityNotFoundError",
            ),
        ],
    )
    async def test_audit_contract_domain_failure_uses_parsed_canonical_identity(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name,
        manager_method,
        failure,
        expected_code,
        error_class,
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        getattr(stub.plugin._relation_manager, manager_method).side_effect = failure
        canonical_identity = _identity()
        payload = (
            _update_payload(identity=_identity(from_user=" alice ", group_id=" g1 "))
            if method_name == "update_social_relation"
            else _delete_payload(
                identity=_identity(from_user=" alice ", group_id=" g1 ")
            )
        )
        request_mock = _mock_request()
        request_mock.get_json.return_value = payload

        with patch("core.api.social_api.request", request_mock):
            result = await getattr(stub, method_name)()

        action = method_name.removesuffix("_social_relation")
        assert result["code"] == expected_code
        assert _audit_messages(caplog) == [
            _individual_audit(
                action,
                canonical_identity,
                result="failure",
                error_code=expected_code,
                error_class=error_class,
            )
        ]
        assert "delete-domain-secret-2d91" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_point", ["revision", "serialization"])
    async def test_audit_contract_create_post_return_failure_uses_entity_identity(
        self, caplog: pytest.LogCaptureFixture, failure_point: str
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        returned_identity = _identity(
            from_user="canonical-alice", to_user="canonical-bob", group_id="canonical-g"
        )
        failure_secret = f"create-{failure_point}-secret-a81f"

        if failure_point == "revision":
            returned = _make_relation(**returned_identity, tags=["returned"])
            stub.plugin._relation_manager.revision_for.side_effect = RuntimeError(
                failure_secret
            )
        else:

            class BrokenSerializedRelation:
                from_user = returned_identity["from_user"]
                to_user = returned_identity["to_user"]
                group_id = returned_identity["group_id"]
                relation_type = returned_identity["relation_type"]
                frequency = 0
                last_interaction = 0.0
                tags = ["returned"]

                @property
                def strength(self):
                    raise RuntimeError(failure_secret)

            returned = BrokenSerializedRelation()

        stub.plugin._relation_manager.create_manual_relation.return_value = returned
        request_mock = _mock_request()
        request_mock.get_json.return_value = _create_payload(
            tags=["create-payload-secret-c573"]
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.create_social_relation()

        assert result["code"] == "internal_error"
        assert _audit_messages(caplog) == [
            _individual_audit(
                "create",
                returned_identity,
                result="failure",
                error_code="internal_error",
                error_class="RuntimeError",
            )
        ]
        rendered = caplog.text + repr(result)
        assert failure_secret not in rendered
        assert "create-payload-secret-c573" not in rendered


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
        assert result["data"]["succeeded_count"] == 2
        assert result["data"]["failed_count"] == 0
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
        assert result["data"]["succeeded_count"] == 1
        assert result["data"]["failed_count"] == 1

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
    async def test_batch_reports_malformed_items_without_aborting_valid_items(
        self,
    ) -> None:
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
        assert result["data"]["succeeded_count"] == 1
        assert result["data"]["failed_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("side_effect", "expected_result", "succeeded_count", "failed_count"),
        [
            (
                [EntityNotFoundError("batch-item-secret-41e0"), True],
                "partial",
                1,
                1,
            ),
            (
                [
                    EntityNotFoundError("batch-item-secret-41e0"),
                    EntityNotFoundError("batch-item-secret-41e0"),
                ],
                "failure",
                0,
                2,
            ),
        ],
    )
    async def test_audit_contract_batch_failure_emits_one_aggregate_event(
        self,
        caplog: pytest.LogCaptureFixture,
        side_effect,
        expected_result,
        succeeded_count,
        failed_count,
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        stub.plugin._relation_manager.delete_manual_relation.side_effect = side_effect
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload(
            items=[
                {
                    "identity": _identity(),
                    "expected_revision": "batch-payload-secret-f921",
                },
                {
                    "identity": _identity(to_user="carol"),
                    "expected_revision": "rev-2",
                },
            ]
        )

        with patch("core.api.social_api.request", request_mock):
            result = await stub.batch_social_relations()

        assert result["status"] == "ok"
        assert result["data"]["succeeded_count"] == succeeded_count
        assert result["data"]["failed_count"] == failed_count
        assert _audit_messages(caplog) == [
            _batch_audit(
                "batch_delete",
                result=expected_result,
                error_code="item_failure",
                succeeded_count=succeeded_count,
                failed_count=failed_count,
            )
        ]
        assert "batch-item-secret-41e0" not in caplog.text
        assert "batch-payload-secret-f921" not in caplog.text

    @pytest.mark.asyncio
    async def test_audit_contract_batch_cancellation_has_no_completed_audit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        stub = _make_write_stub()
        stub.plugin._relation_manager.delete_manual_relation.side_effect = (
            asyncio.CancelledError()
        )
        request_mock = _mock_request()
        request_mock.get_json.return_value = _batch_payload()

        with (
            patch("core.api.social_api.request", request_mock),
            pytest.raises(asyncio.CancelledError),
        ):
            await stub.batch_social_relations()

        assert _audit_messages(caplog) == []
