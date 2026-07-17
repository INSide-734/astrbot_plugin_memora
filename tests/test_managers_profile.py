"""测试 ProfileManager — 基于 Mock ProfileStore 的用户画像 CRUD。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.base.entity_editing import (
    EntityNotFoundError,
    EntityValidationError,
    compute_entity_revision,
)
from core.managers.profile_manager import ProfileManager
from core.models.user_profile import (
    TagCategory,
    UserPreferences,
    UserProfile,
    UserTag,
)
from core.storage.profile_store import ProfileStore


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestProfileManagerInit:
    """构造方法测试。"""

    def test_init_with_store(self) -> None:
        store = MagicMock()
        mgr = ProfileManager(profile_store=store)
        assert mgr._store is store


# ---------------------------------------------------------------------------
# ensure_profile
# ---------------------------------------------------------------------------

class TestEnsureProfile:
    """ensure_profile 方法测试。"""

    @pytest.mark.asyncio
    async def test_ensure_profile_delegates(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        result = await mgr.ensure_profile("user1")
        assert result is profile
        store.get_or_create_profile.assert_called_once_with("user1")


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------

class TestGetProfile:
    """get_profile 方法测试。"""

    @pytest.mark.asyncio
    async def test_get_profile_delegates(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        result = await mgr.get_profile("user1")
        assert result is profile

    @pytest.mark.asyncio
    async def test_get_profile_none(self) -> None:
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=None)
        mgr = ProfileManager(profile_store=store)
        assert await mgr.get_profile("unknown") is None


# ---------------------------------------------------------------------------
# touch
# ---------------------------------------------------------------------------

class TestTouch:
    """touch 方法测试。"""

    @pytest.mark.asyncio
    async def test_touch_delegates(self) -> None:
        store = MagicMock()
        store.touch = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.touch("user1")
        store.touch.assert_called_once_with("user1")


# ---------------------------------------------------------------------------
# public write helpers
# ---------------------------------------------------------------------------

class TestProfileWriteHelpers:
    @pytest.mark.asyncio
    async def test_update_profile_fields_converts_preferences_dict(self) -> None:
        profile = UserProfile(
            user_id="user1",
            display_name="New",
            preferences=UserPreferences(
                reply_style="formal",
                preferred_topics=["ai"],
            ),
        )
        store = MagicMock()
        store.update_profile_fields_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)

        result = await mgr.update_profile_fields(
            "user1",
            display_name="New",
            preferences={"reply_style": "formal", "preferred_topics": ["ai"]},
        )

        assert result is profile
        assert profile.display_name == "New"
        assert isinstance(profile.preferences, UserPreferences)
        assert profile.preferences.reply_style == "formal"
        assert profile.preferences.preferred_topics == ["ai"]
        store.update_profile_fields_atomic.assert_awaited_once()
        args = store.update_profile_fields_atomic.await_args
        assert args.args == ("user1",)
        assert args.kwargs["display_name"] == "New"
        assert args.kwargs["preferences"].reply_style == "formal"
        assert args.kwargs["preferences"].preferred_topics == ["ai"]

    @pytest.mark.asyncio
    async def test_delete_profile_delegates(self) -> None:
        store = MagicMock()
        store.delete_profile = AsyncMock(return_value=True)
        mgr = ProfileManager(profile_store=store)

        assert await mgr.delete_profile("user1") is True
        store.delete_profile.assert_called_once_with("user1")

    @pytest.mark.asyncio
    async def test_add_tag_uses_model_tag(self) -> None:
        profile = UserProfile(user_id="user1")
        tag = UserTag(category=TagCategory.INTEREST, value="coffee")
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=profile)
        store.add_tag = AsyncMock(return_value=True)
        mgr = ProfileManager(profile_store=store)

        result = await mgr.add_tag("user1", tag)

        assert result is profile
        store.add_tag.assert_called_once_with("user1", tag)

    @pytest.mark.asyncio
    async def test_add_tag_missing_profile_returns_none(self) -> None:
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=None)
        store.add_tag = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        result = await mgr.add_tag("missing", UserTag(value="coffee"))

        assert result is None
        store.add_tag.assert_not_called()


class TestProfileManualAdminCRUD:
    """管理员画像 CRUD 的校验、规范化和存储委托。"""

    @pytest.mark.asyncio
    async def test_create_profile_manual_accepts_all_editable_preferences(
        self,
    ) -> None:
        profile = UserProfile(user_id="user-1", display_name="Alice")
        store = MagicMock()
        store.create_profile_strict = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)

        result = await mgr.create_profile_manual(
            user_id=" user-1 ",
            display_name=" Alice ",
            preferences={
                "reply_style": " formal ",
                "preferred_topics": [" graphs ", "graphs", "databases"],
                "avoided_topics": [" spoilers "],
                "active_hours": [9, 9, 20],
            },
            tags=[
                {
                    "category": " interest ",
                    "value": " Python ",
                    "confidence": 0.9,
                    "source": "client",
                    "occurrence_count": 99,
                }
            ],
        )

        assert result is profile
        store.create_profile_strict.assert_awaited_once()
        kwargs = store.create_profile_strict.await_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert kwargs["display_name"] == "Alice"
        assert kwargs["preferences"].to_dict() == {
            "reply_style": "formal",
            "preferred_topics": ["graphs", "databases"],
            "avoided_topics": ["spoilers"],
            "active_hours": [9, 20],
            "avg_reply_length": 0,
            "interaction_frequency": 0.0,
        }
        assert len(kwargs["tags"]) == 1
        assert isinstance(kwargs["tags"][0], UserTag)
        assert kwargs["tags"][0].category is TagCategory.INTEREST
        assert kwargs["tags"][0].value == "Python"
        assert kwargs["tags"][0].confidence == 0.9
        assert kwargs["tags"][0].source == "auto"
        assert kwargs["tags"][0].occurrence_count == 1

    @pytest.mark.asyncio
    async def test_update_profile_manual_delegates_normalized_data(self) -> None:
        profile = UserProfile(user_id="user-1", display_name="New")
        store = MagicMock()
        store.replace_editable_fields = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)

        result = await mgr.update_profile_manual(
            user_id=" user-1 ",
            display_name=" New ",
            preferences={"reply_style": " concise "},
            tags=[{"category": "habit", "value": " walking ", "confidence": 1}],
            expected_revision=" revision ",
        )

        assert result is profile
        store.replace_editable_fields.assert_awaited_once()
        args = store.replace_editable_fields.await_args
        assert args.args == ("user-1",)
        assert args.kwargs["display_name"] == "New"
        assert args.kwargs["preferences"].reply_style == "concise"
        assert args.kwargs["tags"][0].category is TagCategory.HABIT
        assert args.kwargs["tags"][0].value == "walking"
        assert args.kwargs["tags"][0].confidence == 1.0
        assert args.kwargs["expected_revision"] == "revision"

    @pytest.mark.asyncio
    async def test_delete_profile_manual_delegates_normalized_identity(self) -> None:
        store = MagicMock()
        store.delete_profile_if_revision = AsyncMock(return_value=True)
        mgr = ProfileManager(profile_store=store)

        assert (
            await mgr.delete_profile_manual(
                user_id=" user-1 ", expected_revision=" revision "
            )
            is True
        )
        store.delete_profile_if_revision.assert_awaited_once_with(
            "user-1", expected_revision="revision"
        )

    def test_revision_for_changes_with_editable_profile_and_tag_state(self) -> None:
        mgr = ProfileManager(profile_store=MagicMock())
        profile = UserProfile(user_id="user-1", display_name="Before")
        before = mgr.revision_for(profile)

        profile.display_name = "After"
        after_name = mgr.revision_for(profile)
        profile.tags.append(_manager_tag(value="Python"))
        after_tag = mgr.revision_for(profile)

        assert len(before) == 64
        assert len({before, after_name, after_tag}) == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "preference_key",
        ["avg_reply_length", "interaction_frequency", "unknown"],
    )
    async def test_manual_profile_rejects_read_only_and_unknown_preferences(
        self, preference_key
    ) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError) as raised:
            await mgr.create_profile_manual(
                user_id="user-1",
                preferences={preference_key: 1},
                tags=[],
            )

        assert "preferences." + preference_key in raised.value.field_errors
        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preference_key", [1, None])
    async def test_create_profile_manual_rejects_non_string_preference_keys(
        self, preference_key
    ) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError) as raised:
            await mgr.create_profile_manual(
                user_id="user-1",
                preferences={preference_key: "invalid"},
                tags=[],
            )

        assert raised.value.field_errors == {
            "preferences": "字段名称必须为字符串"
        }
        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preferences", [[], "formal", 1, True])
    async def test_manual_profile_rejects_malformed_preferences(
        self, preferences
    ) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.create_profile_manual(
                user_id="user-1", preferences=preferences, tags=[]
            )

        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "preferences",
        [
            {"reply_style": 1},
            {"preferred_topics": "graphs"},
            {"preferred_topics": [1]},
            {"avoided_topics": ["x" * 65]},
            {"active_hours": "9"},
            {"active_hours": [True]},
            {"active_hours": [-1]},
            {"active_hours": [24]},
        ],
    )
    async def test_manual_profile_rejects_invalid_preference_values(
        self, preferences
    ) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.create_profile_manual(
                user_id="user-1", preferences=preferences, tags=[]
            )

        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tags", [{}, "tag", 1, True, [None], ["tag"]])
    async def test_manual_profile_rejects_malformed_tags(self, tags) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.create_profile_manual(
                user_id="user-1", preferences={}, tags=tags
            )

        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tag",
        [
            {"category": "invalid", "value": "tag", "confidence": 0.5},
            {"category": 1, "value": "tag", "confidence": 0.5},
            {"category": "interest", "value": "", "confidence": 0.5},
            {"category": "interest", "value": "   ", "confidence": 0.5},
            {"category": "interest", "value": 1, "confidence": 0.5},
            {"category": "interest", "value": "x" * 129, "confidence": 0.5},
            {"category": "interest", "value": "tag", "confidence": True},
            {"category": "interest", "value": "tag", "confidence": float("nan")},
            {"category": "interest", "value": "tag", "confidence": float("inf")},
            {"category": "interest", "value": "tag", "confidence": -0.1},
            {"category": "interest", "value": "tag", "confidence": 1.1},
            {"category": "interest", "value": "tag", "confidence": "0.5"},
        ],
    )
    async def test_manual_profile_rejects_invalid_tag_fields(self, tag) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.create_profile_manual(
                user_id="user-1", preferences={}, tags=[tag]
            )

        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manual_profile_rejects_normalized_duplicate_tags(self) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError) as raised:
            await mgr.create_profile_manual(
                user_id="user-1",
                preferences={},
                tags=[
                    {
                        "category": " interest ",
                        "value": " Python ",
                        "confidence": 0.8,
                    },
                    {
                        "category": "interest",
                        "value": "Python",
                        "confidence": 0.9,
                    },
                ],
            )

        assert raised.value.field_errors == {"tags.1.value": "标签重复"}
        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_id", [None, 1, True, "", "   ", "x" * 129])
    async def test_create_profile_manual_rejects_invalid_user_id(self, user_id) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.create_profile_manual(
                user_id=user_id, preferences={}, tags=[]
            )

        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("display_name", [None, 1, True, "x" * 129])
    async def test_create_profile_manual_rejects_invalid_display_name(
        self, display_name
    ) -> None:
        store = MagicMock()
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.create_profile_manual(
                user_id="user-1",
                display_name=display_name,
                preferences={},
                tags=[],
            )

        store.create_profile_strict.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "expected_revision", [None, 1, True, "", "   ", "x" * 257]
    )
    async def test_update_profile_manual_rejects_invalid_revision(
        self, expected_revision
    ) -> None:
        store = MagicMock()
        store.replace_editable_fields = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        with pytest.raises(EntityValidationError):
            await mgr.update_profile_manual(
                user_id="user-1",
                display_name="Name",
                preferences={},
                tags=[],
                expected_revision=expected_revision,
            )

        store.replace_editable_fields.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_automatic_ensure_profile_keeps_non_strict_path(self) -> None:
        profile = UserProfile(user_id="automatic")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.create_profile_strict = AsyncMock()
        mgr = ProfileManager(profile_store=store)

        assert await mgr.ensure_profile("automatic") is profile
        store.get_or_create_profile.assert_awaited_once_with("automatic")
        store.create_profile_strict.assert_not_awaited()


def _manager_tag(
    *,
    category: TagCategory = TagCategory.INTEREST,
    value: str,
    confidence: float = 0.8,
) -> UserTag:
    return UserTag(category=category, value=value, confidence=confidence)


# ---------------------------------------------------------------------------
# ingest_tags
# ---------------------------------------------------------------------------

class TestIngestTags:
    """ingest_tags 方法测试。"""

    @pytest.mark.asyncio
    async def test_ingest_new_tags(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.upsert_tags_atomic = AsyncMock(return_value=(profile, 2))
        mgr = ProfileManager(profile_store=store)
        tags = [
            UserTag(category=TagCategory.INTEREST, value="coffee", confidence=0.8),
            UserTag(category=TagCategory.HABIT, value="morning_run", confidence=0.6),
        ]
        result = await mgr.ingest_tags("user1", tags)
        assert result is profile
        store.upsert_tags_atomic.assert_awaited_once_with("user1", tags)

    @pytest.mark.asyncio
    async def test_ingest_existing_tag_updates_confidence(self) -> None:
        initial = UserProfile(user_id="user1")
        initial.tags.append(UserTag(
            category=TagCategory.INTEREST, value="coffee", confidence=0.5
        ))
        profile = UserProfile(user_id="user1")
        profile.tags.append(UserTag(
            category=TagCategory.INTEREST,
            value="coffee",
            confidence=0.9,
            occurrence_count=2,
        ))
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=initial)
        store.upsert_tags_atomic = AsyncMock(return_value=(profile, 0))
        mgr = ProfileManager(profile_store=store)
        new_tag = UserTag(
            category=TagCategory.INTEREST, value="coffee", confidence=0.9
        )
        result = await mgr.ingest_tags("user1", [new_tag])
        # Existing tag confidence should be updated to 0.9
        assert result.tags[0].confidence == 0.9
        assert result.tags[0].occurrence_count == 2

    @pytest.mark.asyncio
    async def test_ingest_empty_tags(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.upsert_tags_atomic = AsyncMock(return_value=(profile, 0))
        mgr = ProfileManager(profile_store=store)
        await mgr.ingest_tags("user1", [])
        store.upsert_tags_atomic.assert_awaited_once_with("user1", [])

    @pytest.mark.asyncio
    async def test_ingest_tags_does_not_restore_stale_admin_fields(
        self, tmp_db_path
    ) -> None:
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "interleaved-user",
            display_name="Before admin",
            preferences=UserPreferences(reply_style="casual"),
        )
        mgr = ProfileManager(profile_store=store)
        original_ensure = mgr.ensure_profile

        async def ensure_then_admin_update(user_id):
            stale_snapshot = await original_ensure(user_id)
            current = await store.get_profile(user_id)
            assert current is not None
            await store.replace_editable_fields(
                user_id,
                display_name="Admin name",
                preferences=UserPreferences(
                    reply_style="formal",
                    preferred_topics=["admin-topic"],
                ),
                tags=current.tags,
                expected_revision=compute_entity_revision(current.to_dict()),
            )
            return stale_snapshot

        with patch.object(mgr, "ensure_profile", new=ensure_then_admin_update):
            result = await mgr.ingest_tags(
                "interleaved-user",
                [
                    UserTag(
                        category=TagCategory.INTEREST,
                        value="automatic-tag",
                        confidence=0.8,
                    )
                ],
            )

        persisted = await store.get_profile("interleaved-user")
        assert persisted is not None
        assert result.display_name == "Admin name"
        assert persisted.display_name == "Admin name"
        assert persisted.preferences.reply_style == "formal"
        assert persisted.preferences.preferred_topics == ["admin-topic"]
        assert "automatic-tag" in [tag.value for tag in persisted.tags]

    @pytest.mark.asyncio
    async def test_ingest_tags_does_not_return_profile_deleted_after_ensure(
        self, tmp_db_path
    ) -> None:
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict("deleted-during-ingest")
        mgr = ProfileManager(profile_store=store)
        original_ensure = mgr.ensure_profile

        async def ensure_then_delete(user_id):
            stale_snapshot = await original_ensure(user_id)
            assert await store.delete_profile(user_id) is True
            return stale_snapshot

        with patch.object(mgr, "ensure_profile", new=ensure_then_delete):
            with pytest.raises(EntityNotFoundError, match="画像不存在"):
                await mgr.ingest_tags(
                    "deleted-during-ingest",
                    [_manager_tag(value="must-not-persist")],
                )

        assert await store.get_profile("deleted-during-ingest") is None
        assert await store._get_tags("deleted-during-ingest") == []


# ---------------------------------------------------------------------------
# get_tag_weights
# ---------------------------------------------------------------------------

class TestGetTagWeights:
    """get_tag_weights 方法测试。"""

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_profile(self) -> None:
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=None)
        mgr = ProfileManager(profile_store=store)
        result = await mgr.get_tag_weights("unknown")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_weight_vector(self) -> None:
        profile = UserProfile(user_id="user1")
        profile.tags = [
            UserTag(category=TagCategory.INTEREST, value="coffee", confidence=0.8, occurrence_count=10),
            UserTag(category=TagCategory.HABIT, value="running", confidence=0.3, occurrence_count=2),
            UserTag(category=TagCategory.PERSONALITY, value="shy", confidence=0.1, occurrence_count=1),
        ]
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        weights = await mgr.get_tag_weights("user1")
        # shy has confidence 0.1 < 0.2, excluded
        # coffee: 0.8 * min(1.0, 10/10) = 0.8
        # running: 0.3 * min(1.0, 2/10) = 0.3 * 0.2 = 0.06
        assert "coffee" in weights
        assert "running" in weights
        assert "shy" not in weights


# ---------------------------------------------------------------------------
# decay_and_clean
# ---------------------------------------------------------------------------

class TestDecayAndClean:
    """decay_and_clean 方法测试。"""

    @pytest.mark.asyncio
    async def test_decay_removes_stale_tags(self) -> None:
        store = MagicMock()
        store.decay_and_clean_tags_atomic = AsyncMock(return_value=1)
        mgr = ProfileManager(profile_store=store)
        removed = await mgr.decay_and_clean("user1")
        assert removed == 1
        store.decay_and_clean_tags_atomic.assert_awaited_once_with("user1")

    @pytest.mark.asyncio
    async def test_decay_no_profile(self) -> None:
        store = MagicMock()
        store.decay_and_clean_tags_atomic = AsyncMock(return_value=0)
        mgr = ProfileManager(profile_store=store)
        assert await mgr.decay_and_clean("unknown") == 0

    @pytest.mark.asyncio
    async def test_decay_no_removals(self) -> None:
        store = MagicMock()
        store.decay_and_clean_tags_atomic = AsyncMock(return_value=0)
        mgr = ProfileManager(profile_store=store)
        removed = await mgr.decay_and_clean("user1")
        assert removed == 0
        store.decay_and_clean_tags_atomic.assert_awaited_once_with("user1")


# ---------------------------------------------------------------------------
# record_message
# ---------------------------------------------------------------------------

class TestRecordMessage:
    """record_message 方法测试。"""

    @pytest.mark.asyncio
    async def test_record_message_increments_counter(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.record_message_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.record_message("user1", message_length=50)
        store.record_message_atomic.assert_awaited_once_with(
            "user1", message_length=50
        )

    @pytest.mark.asyncio
    async def test_record_message_ema_avg_length(self) -> None:
        profile = UserProfile(user_id="user1")
        profile.preferences.avg_reply_length = 100
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.record_message_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.record_message("user1", message_length=200)
        store.record_message_atomic.assert_awaited_once_with(
            "user1", message_length=200
        )

    @pytest.mark.asyncio
    async def test_record_message_first_time_sets_length(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.record_message_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.record_message("user1", message_length=80)
        store.record_message_atomic.assert_awaited_once_with(
            "user1", message_length=80
        )


# ---------------------------------------------------------------------------
# update_preferences
# ---------------------------------------------------------------------------

class TestUpdatePreferences:
    """update_preferences 方法测试。"""

    @pytest.mark.asyncio
    async def test_update_reply_style(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.merge_preferences_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences("user1", {"reply_style": "formal"})
        store.merge_preferences_atomic.assert_awaited_once_with(
            "user1", {"reply_style": "formal"}
        )

    @pytest.mark.asyncio
    async def test_update_preferred_topics_dedup(self) -> None:
        profile = UserProfile(user_id="user1")
        profile.preferences.preferred_topics = ["coffee"]
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.merge_preferences_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences(
            "user1", {"preferred_topics": ["coffee", "tea"]}
        )
        store.merge_preferences_atomic.assert_awaited_once_with(
            "user1", {"preferred_topics": ["coffee", "tea"]}
        )

    @pytest.mark.asyncio
    async def test_update_avoided_topics(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.merge_preferences_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences(
            "user1", {"avoided_topics": ["politics"]}
        )
        store.merge_preferences_atomic.assert_awaited_once_with(
            "user1", {"avoided_topics": ["politics"]}
        )

    @pytest.mark.asyncio
    async def test_update_empty_preferences(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.merge_preferences_atomic = AsyncMock(return_value=profile)
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences("user1", {})
        store.merge_preferences_atomic.assert_awaited_once_with("user1", {})


# ---------------------------------------------------------------------------
# get_profile_count / list_profiles
# ---------------------------------------------------------------------------

class TestProfileQuery:
    """get_profile_count / list_profiles 方法测试。"""

    @pytest.mark.asyncio
    async def test_get_profile_count(self) -> None:
        store = MagicMock()
        store.list_profiles = AsyncMock(return_value=([], 5))
        mgr = ProfileManager(profile_store=store)
        assert await mgr.get_profile_count() == 5

    @pytest.mark.asyncio
    async def test_list_profiles(self) -> None:
        profiles = [UserProfile(user_id="u1"), UserProfile(user_id="u2")]
        store = MagicMock()
        store.list_profiles = AsyncMock(return_value=(profiles, 2))
        mgr = ProfileManager(profile_store=store)
        result, total = await mgr.list_profiles(limit=10, offset=0)
        assert len(result) == 2
        assert total == 2
        store.list_profiles.assert_called_once_with(limit=10, offset=0)
