"""core/api/affection_api.py 测试 — AffectionApiMixin。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.api.affection_api import AffectionApiMixin
from core.base.entity_editing import EditConflictError


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    return mock


def _request_json(payload):
    mock = _mock_request()
    mock.get_json = AsyncMock(return_value=payload)
    return mock


def _make_user(user_id: str = "u1", group_id: str = "g1", level_name: str = "温暖"):
    user = MagicMock()
    user.user_id = user_id
    user.group_id = group_id
    user.affection_score = 42
    user.level = MagicMock()
    user.level.name = "WARM"
    user.level_name = level_name
    user.interaction_count = 5
    user.last_interaction = 1700000000.0
    return user


def _make_mood():
    mood = MagicMock()
    mood.mood_type = MagicMock()
    mood.mood_type.value = "happy"
    mood.intensity = 0.7
    mood.description = "feeling good"
    mood.is_active = MagicMock(return_value=True)
    return mood


def _make_stub(*, has_manager=True, has_store=True, status=None, groups=None, mood=None):
    class Stub:
        get_affection_status = AffectionApiMixin.get_affection_status
        _get_affection_manager = AffectionApiMixin._get_affection_manager
        _get_affection_store = AffectionApiMixin._get_affection_store

    stub = Stub()
    if has_manager or has_store:
        stub.plugin = MagicMock()
        stub.plugin.initializer = None

    if has_manager:
        manager = MagicMock()
        manager.get_group_affection_status = AsyncMock(return_value=status)
        manager.get_mood = AsyncMock(return_value=mood)
        stub.plugin._affection_manager = manager

    if has_store:
        store = MagicMock()
        store.list_groups = AsyncMock(return_value=groups or [])
        stub.plugin._affection_store = store

    return stub


class TestAffectionStatus:
    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self) -> None:
        stub = _make_stub(has_manager=False, has_store=False)
        with patch("core.api.affection_api.request", _mock_request()):
            result = await stub.get_affection_status()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_group_id_falls_back_to_first_store_group(self) -> None:
        status = {
            "group_id": "group-1",
            "total_affection": 42,
            "max_total_affection": 100,
            "user_count": 1,
            "top_users": [_make_user(group_id="group-1")],
            "current_mood": {"mood_type": "happy", "intensity": 0.5},
        }
        stub = _make_stub(status=status, groups=["group-1"])

        with patch("core.api.affection_api.request", _mock_request()):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        stub.plugin._affection_manager.get_group_affection_status.assert_awaited_once_with(
            "group-1"
        )
        assert result["data"]["group_id"] == "group-1"
        assert result["data"]["top_users"][0]["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_fetches_mood_directly_when_status_has_none(self) -> None:
        status = {
            "group_id": "default",
            "total_affection": 0,
            "max_total_affection": 0,
            "user_count": 0,
            "top_users": [],
            "current_mood": None,
        }
        stub = _make_stub(status=status, groups=[], mood=_make_mood())

        with patch("core.api.affection_api.request", _mock_request()):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        assert result["data"]["current_mood"]["mood_type"] == "happy"
        assert result["data"]["current_mood"]["is_active"] is True


def _make_editing_stub():
    class Stub:
        _get_affection_manager = AffectionApiMixin._get_affection_manager

    stub = Stub()
    stub.plugin = MagicMock()
    stub.plugin.initializer = None
    stub.plugin._affection_manager = MagicMock()
    stub._maintenance_write_guard = MagicMock(return_value=None)
    return stub


class TestAffectionEditing:
    @pytest.mark.asyncio
    async def test_batch_affection_users_rejects_set_score(self):
        stub = _make_editing_stub()
        stub.batch_affection_users = AffectionApiMixin.batch_affection_users.__get__(stub)
        manager = stub.plugin._affection_manager
        manager.update_user_affection_manual = AsyncMock()
        payload = {
            "action": "set_score",
            "items": [
                {
                    "identity": {"group_id": "g1", "user_id": "alice"},
                    "expected_revision": "rev-1",
                }
            ],
            "params": {"score": 42},
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.batch_affection_users()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {"action": "仅支持 delete"}
        manager.update_user_affection_manual.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_forbidden_score_params_without_mutating(self):
        stub = _make_editing_stub()
        stub.batch_affection_users = AffectionApiMixin.batch_affection_users.__get__(stub)
        manager = stub.plugin._affection_manager
        manager.delete_user_affection_manual = AsyncMock()
        manager.update_user_affection_manual = AsyncMock()
        payload = {
            "action": "delete",
            "items": [
                {
                    "identity": {"group_id": "g1", "user_id": "alice"},
                    "expected_revision": "rev-1",
                }
            ],
            "params": {"score": 42},
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.batch_affection_users()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {"score": "字段不可写"}
        manager.delete_user_affection_manual.assert_not_awaited()
        manager.update_user_affection_manual.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_delete_continues_after_conflict_and_returns_partial_result(self):
        stub = _make_editing_stub()
        stub.batch_affection_users = AffectionApiMixin.batch_affection_users.__get__(stub)
        manager = stub.plugin._affection_manager
        current = {"group_id": "g1", "user_id": "bob", "affection_score": 5}
        manager.delete_user_affection_manual = AsyncMock(
            side_effect=[True, EditConflictError(current, "rev-current"), True]
        )
        payload = {
            "action": "delete",
            "items": [
                {"identity": {"group_id": "g1", "user_id": "alice"}, "expected_revision": "rev-alice"},
                {"identity": {"group_id": "g1", "user_id": "bob"}, "expected_revision": "rev-bob"},
                {"identity": {"group_id": "g1", "user_id": "carol"}, "expected_revision": "rev-carol"},
            ],
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.batch_affection_users()

        assert result["data"] == {
            "total": 3,
            "succeeded_count": 2,
            "failed_count": 1,
            "succeeded_ids": [
                {"group_id": "g1", "user_id": "alice"},
                {"group_id": "g1", "user_id": "carol"},
            ],
            "failures": [
                {
                    "identity": {"group_id": "g1", "user_id": "bob"},
                    "code": "edit_conflict",
                    "message": "记录已被后台更新，请检查最新数据",
                    "current_entity": current,
                    "current_revision": "rev-current",
                }
            ],
        }
        manager.delete_user_affection_manual.assert_has_awaits(
            [
                call("g1", "alice", expected_revision="rev-alice"),
                call("g1", "bob", expected_revision="rev-bob"),
                call("g1", "carol", expected_revision="rev-carol"),
            ]
        )

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_invalid_items_and_continues_to_later_valid_item(self):
        stub = _make_editing_stub()
        stub.batch_affection_users = AffectionApiMixin.batch_affection_users.__get__(stub)
        manager = stub.plugin._affection_manager
        manager.delete_user_affection_manual = AsyncMock(return_value=True)
        payload = {
            "action": "delete",
            "items": [
                {
                    "identity": {"group_id": "g1", "user_id": "unknown-field"},
                    "expected_revision": "rev-unknown",
                    "unexpected": "forbidden",
                },
                "not-an-object",
                {"identity": "not-an-object", "expected_revision": "rev-malformed"},
                {
                    "identity": {"group_id": "g1", "user_id": "valid"},
                    "expected_revision": "rev-valid",
                },
            ],
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.batch_affection_users()

        assert result["data"] == {
            "total": 4,
            "succeeded_count": 1,
            "failed_count": 3,
            "succeeded_ids": [{"group_id": "g1", "user_id": "valid"}],
            "failures": [
                {
                    "identity": {"item_index": 0},
                    "code": "validation_error",
                    "message": "请求包含不支持的字段",
                    "field_errors": {"unexpected": "字段不可写"},
                },
                {
                    "identity": {"item_index": 1},
                    "code": "invalid_request",
                    "message": "请求体必须是 JSON 对象",
                },
                {
                    "identity": {"item_index": 2},
                    "code": "invalid_request",
                    "message": "请求体必须是 JSON 对象",
                },
            ],
        }
        manager.delete_user_affection_manual.assert_awaited_once_with(
            "g1", "valid", expected_revision="rev-valid"
        )

    @pytest.mark.asyncio
    async def test_batch_delete_enforces_100_item_cap(self):
        stub = _make_editing_stub()
        stub.batch_affection_users = AffectionApiMixin.batch_affection_users.__get__(stub)
        payload = {
            "action": "delete",
            "items": [
                {
                    "identity": {"group_id": "g1", "user_id": f"user-{index}"},
                    "expected_revision": "rev-1",
                }
                for index in range(101)
            ],
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.batch_affection_users()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {"items": "项目数量必须在 1 到 100 之间"}
        stub.plugin._affection_manager.delete_user_affection_manual.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_runs_guard_before_parsing_and_redacts_revisions_from_logs(self):
        stub = _make_editing_stub()
        stub.batch_affection_users = AffectionApiMixin.batch_affection_users.__get__(stub)
        blocked = {"status": "error", "code": "maintenance_in_progress"}
        stub._maintenance_write_guard.return_value = blocked
        request_mock = _request_json({"action": "delete", "items": []})

        with patch("core.api.affection_api.request", request_mock):
            result = await stub.batch_affection_users()

        assert result is blocked
        request_mock.get_json.assert_not_awaited()

        stub._maintenance_write_guard.return_value = None
        stub.plugin._affection_manager.delete_user_affection_manual = AsyncMock(return_value=True)
        payload = {
            "action": "delete",
            "items": [
                {
                    "identity": {"group_id": "g1", "user_id": "alice"},
                    "expected_revision": "revision-secret",
                }
            ],
        }
        with patch("core.api.affection_api.logger.info") as logged, patch(
            "core.api.affection_api.request", _request_json(payload)
        ):
            result = await stub.batch_affection_users()

        assert result["status"] == "ok"
        rendered = str(logged.call_args_list)
        assert "batch_delete" in rendered and "g1" in rendered and "alice" in rendered
        assert "revision-secret" not in rendered

    @pytest.mark.asyncio
    async def test_list_affection_users_requires_group_and_returns_real_pagination(self):
        stub = _make_editing_stub()
        stub.list_affection_users = AffectionApiMixin.list_affection_users.__get__(stub)
        manager = stub.plugin._affection_manager
        manager.list_user_affections = AsyncMock(return_value=([_make_user("alice", "g1")], 12))
        manager.revision_for_affection = MagicMock(return_value="rev-1")

        with patch("core.api.affection_api.request", _mock_request(group_id="g1", limit="10", offset="10")):
            result = await stub.list_affection_users()

        assert result["data"]["total"] == 12
        assert result["data"]["limit"] == 10
        assert result["data"]["offset"] == 10
        assert result["data"]["users"][0]["revision"] == "rev-1"
        manager.list_user_affections.assert_awaited_once_with("g1", 10, 10)

        with patch("core.api.affection_api.request", _mock_request(limit="10", offset="0")):
            missing = await stub.list_affection_users()
        assert missing["code"] == "validation_error"
        assert "group_id" in missing["field_errors"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", [{"limit": "0", "offset": "0"}, {"limit": "1", "offset": "-1"}])
    async def test_list_affection_users_validates_limit_and_offset(self, args):
        stub = _make_editing_stub()
        stub.list_affection_users = AffectionApiMixin.list_affection_users.__get__(stub)

        with patch("core.api.affection_api.request", _mock_request(group_id="g1", **args)):
            result = await stub.list_affection_users()

        assert result["code"] == "validation_error"
        assert result["field_errors"]

    @pytest.mark.asyncio
    async def test_create_update_delete_use_manager_and_manager_revision(self):
        stub = _make_editing_stub()
        for name in ("create_affection_user", "update_affection_user", "delete_affection_user"):
            setattr(stub, name, getattr(AffectionApiMixin, name).__get__(stub))
        manager = stub.plugin._affection_manager
        user = _make_user("alice", "g1")
        manager.create_user_affection_manual = AsyncMock(return_value=user)
        manager.update_user_affection_manual = AsyncMock(return_value=user)
        manager.delete_user_affection_manual = AsyncMock(return_value=True)
        manager.revision_for_affection = MagicMock(return_value="rev-current")

        with patch("core.api.affection_api.request", _request_json({"group_id": "g1", "user_id": "alice", "affection_score": 42})):
            created = await stub.create_affection_user()
        assert created["data"]["entity"]["interaction_count"] == 5
        assert created["data"]["revision"] == "rev-current"
        manager.create_user_affection_manual.assert_awaited_once_with("g1", "alice", 42)

        with patch("core.api.affection_api.request", _request_json({"identity": {"group_id": "g1", "user_id": "alice"}, "changes": {"affection_score": 55}, "expected_revision": "rev-current"})):
            updated = await stub.update_affection_user()
        assert updated["data"]["revision"] == "rev-current"
        manager.update_user_affection_manual.assert_awaited_once_with("g1", "alice", 55, expected_revision="rev-current")

        with patch("core.api.affection_api.request", _request_json({"identity": {"group_id": "g1", "user_id": "alice"}, "expected_revision": "rev-current"})):
            deleted = await stub.delete_affection_user()
        assert deleted["data"] == {"deleted": True, "identity": {"group_id": "g1", "user_id": "alice"}}
        manager.delete_user_affection_manual.assert_awaited_once_with("g1", "alice", expected_revision="rev-current")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("affection_score", [True, 1.5, -101, 101])
    async def test_affection_score_rejects_non_integer_or_out_of_range_values(self, affection_score):
        stub = _make_editing_stub()
        stub.create_affection_user = AffectionApiMixin.create_affection_user.__get__(stub)

        with patch("core.api.affection_api.request", _request_json({"group_id": "g1", "user_id": "alice", "affection_score": affection_score})):
            result = await stub.create_affection_user()

        assert result["code"] == "validation_error"
        assert "affection_score" in result["field_errors"]
        stub.plugin._affection_manager.create_user_affection_manual.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler_name", ["create_affection_user", "update_affection_user"])
    async def test_legacy_score_is_rejected_as_non_contract_field(self, handler_name):
        stub = _make_editing_stub()
        setattr(stub, handler_name, getattr(AffectionApiMixin, handler_name).__get__(stub))
        payload = {"group_id": "g1", "user_id": "alice", "score": 42}
        if handler_name.startswith("update"):
            payload = {
                "identity": {"group_id": "g1", "user_id": "alice"},
                "changes": {"score": 42},
                "expected_revision": "rev-current",
            }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await getattr(stub, handler_name)()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {"score": "字段不可写"}
        if handler_name.startswith("update"):
            stub.plugin._affection_manager.update_user_affection_manual.assert_not_called()
        else:
            stub.plugin._affection_manager.create_user_affection_manual.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read_only", ["interaction_count", "last_interaction"])
    async def test_user_read_only_fields_are_rejected(self, read_only):
        stub = _make_editing_stub()
        stub.create_affection_user = AffectionApiMixin.create_affection_user.__get__(stub)

        with patch("core.api.affection_api.request", _request_json({"group_id": "g1", "user_id": "alice", "affection_score": 1, read_only: 0})):
            result = await stub.create_affection_user()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {read_only: "字段不可写"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler_name", ["update_affection_user", "delete_affection_user"])
    async def test_stale_user_mutations_return_edit_conflict(self, handler_name):
        stub = _make_editing_stub()
        setattr(stub, handler_name, getattr(AffectionApiMixin, handler_name).__get__(stub))
        current = {"group_id": "g1", "user_id": "alice", "affection_score": 10}
        manager = stub.plugin._affection_manager
        manager.update_user_affection_manual = AsyncMock(side_effect=EditConflictError(current, "rev-new"))
        manager.delete_user_affection_manual = AsyncMock(side_effect=EditConflictError(current, "rev-new"))
        payload = {"identity": {"group_id": "g1", "user_id": "alice"}, "expected_revision": "rev-old"}
        if handler_name.startswith("update"):
            payload["changes"] = {"affection_score": 11}

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await getattr(stub, handler_name)()

        assert result["code"] == "edit_conflict"
        assert result["data"] == {"current_entity": current, "current_revision": "rev-new"}

    @pytest.mark.asyncio
    async def test_mood_set_reset_and_history_validate_and_serialize_edit_values(self):
        stub = _make_editing_stub()
        for name in ("set_affection_mood", "reset_affection_mood", "get_affection_mood_history"):
            setattr(stub, name, getattr(AffectionApiMixin, name).__get__(stub))
        manager = stub.plugin._affection_manager
        mood = _make_mood()
        mood.duration_hours = 2.5
        mood.start_time = 1700000000.0
        manager.set_mood = AsyncMock(return_value=mood)
        manager.reset_mood = AsyncMock(return_value=mood)
        manager.get_mood_history = AsyncMock(return_value=[mood])

        with patch("core.api.affection_api.request", _request_json({"group_id": "g1", "mood_type": "happy", "intensity": 0.7, "duration_hours": 2.5})):
            set_result = await stub.set_affection_mood()
        assert set_result["data"]["duration_hours"] == 2.5
        assert set_result["data"]["start_time"] == 1700000000.0

        with patch("core.api.affection_api.request", _request_json({"group_id": "g1"})):
            reset_result = await stub.reset_affection_mood()
        assert reset_result["data"]["mood_type"] == "happy"
        manager.reset_mood.assert_awaited_once_with("g1")

        with patch("core.api.affection_api.request", _mock_request(group_id="g1", limit="3")):
            history_result = await stub.get_affection_mood_history()
        assert history_result["data"]["history"][0]["duration_hours"] == 2.5
        manager.get_mood_history.assert_awaited_once_with("g1", 3)

        with patch("core.api.affection_api.request", _request_json({"group_id": "g1", "mood_type": "unknown", "intensity": float("inf"), "duration_hours": 1})):
            invalid = await stub.set_affection_mood()
        assert invalid["code"] == "validation_error"
        assert {"mood_type", "intensity"} <= set(invalid["field_errors"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "intensity,duration_hours",
        [(0.1, 0.25), (1.0, 168.0)],
    )
    async def test_mood_set_accepts_page_api_boundaries(self, intensity, duration_hours):
        stub = _make_editing_stub()
        stub.set_affection_mood = AffectionApiMixin.set_affection_mood.__get__(stub)
        manager = stub.plugin._affection_manager
        manager.set_mood = AsyncMock(return_value=_make_mood())
        payload = {
            "group_id": "g1",
            "mood_type": "happy",
            "intensity": intensity,
            "duration_hours": duration_hours,
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.set_affection_mood()

        assert result["status"] == "ok"
        manager.set_mood.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "intensity,duration_hours,error_field",
        [
            (0.09, 1.0, "intensity"),
            (0.0, 1.0, "intensity"),
            (-0.1, 1.0, "intensity"),
            (1.01, 1.0, "intensity"),
            (0.5, 0.24, "duration_hours"),
            (0.5, 0.0, "duration_hours"),
            (0.5, -1.0, "duration_hours"),
            (0.5, 168.01, "duration_hours"),
        ],
    )
    async def test_mood_set_rejects_out_of_range_page_api_values(
        self, intensity, duration_hours, error_field
    ):
        stub = _make_editing_stub()
        stub.set_affection_mood = AffectionApiMixin.set_affection_mood.__get__(stub)
        manager = stub.plugin._affection_manager
        manager.set_mood = AsyncMock()
        payload = {
            "group_id": "g1",
            "mood_type": "happy",
            "intensity": intensity,
            "duration_hours": duration_hours,
        }

        with patch("core.api.affection_api.request", _request_json(payload)):
            result = await stub.set_affection_mood()

        assert result["code"] == "validation_error"
        assert error_field in result["field_errors"]
        manager.set_mood.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_write_guard_runs_before_json_parsing_and_logs_identity_without_content(self):
        stub = _make_editing_stub()
        stub.create_affection_user = AffectionApiMixin.create_affection_user.__get__(stub)
        blocked = {"status": "error", "code": "maintenance_in_progress"}
        stub._maintenance_write_guard.return_value = blocked
        request_mock = _request_json({"group_id": "g1", "user_id": "secret-user", "affection_score": 1})
        with patch("core.api.affection_api.request", request_mock):
            result = await stub.create_affection_user()
        assert result is blocked
        request_mock.get_json.assert_not_awaited()

        stub._maintenance_write_guard.return_value = None
        stub.plugin._affection_manager.create_user_affection_manual = AsyncMock(return_value=_make_user("alice", "g1"))
        stub.plugin._affection_manager.revision_for_affection = MagicMock(return_value="rev-1")
        with patch("core.api.affection_api.logger.info") as logged, patch("core.api.affection_api.request", _request_json({"group_id": "g1", "user_id": "alice", "affection_score": 1})):
            await stub.create_affection_user()
        rendered = str(logged.call_args_list)
        assert "create" in rendered and "g1" in rendered and "alice" in rendered
        assert "description" not in rendered

    @pytest.mark.asyncio
    async def test_missing_group_data_returns_error(self) -> None:
        stub = _make_stub(status=None, groups=["group-x"])

        with patch("core.api.affection_api.request", _mock_request(group_id="group-x")):
            result = await stub.get_affection_status()

        assert result["status"] == "error"
        assert "group-x" in result["message"]

    @pytest.mark.asyncio
    async def test_skips_malformed_top_users(self) -> None:
        broken = MagicMock()
        type(broken).user_id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken affection user")))
        status = {
            "group_id": "group-1",
            "total_affection": 42,
            "max_total_affection": 100,
            "user_count": 3,
            "top_users": [_make_user(user_id="u1"), broken, _make_user(user_id="u2")],
            "current_mood": {"mood_type": "happy", "intensity": 0.5},
        }
        stub = _make_stub(status=status, groups=["group-1"])

        with patch("core.api.affection_api.request", _mock_request(group_id="group-1")):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        assert [item["user_id"] for item in result["data"]["top_users"]] == ["u1", "u2"]

    @pytest.mark.asyncio
    async def test_bad_status_mood_falls_back_to_none(self) -> None:
        broken_mood = MagicMock()
        type(broken_mood).mood_type = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken mood")))
        status = {
            "group_id": "group-1",
            "total_affection": 42,
            "max_total_affection": 100,
            "user_count": 1,
            "top_users": [_make_user(group_id="group-1")],
            "current_mood": broken_mood,
        }
        stub = _make_stub(status=status, groups=["group-1"], mood=None)

        with patch("core.api.affection_api.request", _mock_request(group_id="group-1")):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        assert result["data"]["current_mood"] is None
