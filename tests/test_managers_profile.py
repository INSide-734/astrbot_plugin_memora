"""测试 ProfileManager — 基于 Mock ProfileStore 的用户画像 CRUD。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.managers.profile_manager import ProfileManager
from core.models.user_profile import (
    TagCategory,
    UserPreferences,
    UserProfile,
    UserTag,
)


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
        profile = UserProfile(user_id="user1", display_name="Old")
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
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
        store.update_profile.assert_called_once_with(profile)

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
        store.add_tag = AsyncMock()
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        tags = [
            UserTag(category=TagCategory.INTEREST, value="coffee", confidence=0.8),
            UserTag(category=TagCategory.HABIT, value="morning_run", confidence=0.6),
        ]
        result = await mgr.ingest_tags("user1", tags)
        assert result is profile
        assert store.add_tag.call_count == 2
        store.update_profile.assert_called_once_with(profile)

    @pytest.mark.asyncio
    async def test_ingest_existing_tag_updates_confidence(self) -> None:
        profile = UserProfile(user_id="user1")
        existing_tag = UserTag(
            category=TagCategory.INTEREST, value="coffee", confidence=0.5
        )
        profile.tags.append(existing_tag)
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.add_tag = AsyncMock()
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        new_tag = UserTag(
            category=TagCategory.INTEREST, value="coffee", confidence=0.9
        )
        await mgr.ingest_tags("user1", [new_tag])
        # Existing tag confidence should be updated to 0.9
        assert profile.tags[0].confidence == 0.9
        assert profile.tags[0].occurrence_count == 2

    @pytest.mark.asyncio
    async def test_ingest_empty_tags(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.ingest_tags("user1", [])
        store.add_tag.assert_not_called()
        store.update_profile.assert_called_once()


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
        profile = UserProfile(user_id="user1")
        profile.tags = [
            UserTag(
                category=TagCategory.INTEREST,
                value="old_topic",
                confidence=0.05,  # below 0.1 min
            ),
            UserTag(
                category=TagCategory.HABIT,
                value="strong",
                confidence=0.9,
            ),
        ]
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        removed = await mgr.decay_and_clean("user1")
        assert removed == 1
        assert len(profile.tags) == 1
        assert profile.tags[0].value == "strong"

    @pytest.mark.asyncio
    async def test_decay_no_profile(self) -> None:
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=None)
        mgr = ProfileManager(profile_store=store)
        assert await mgr.decay_and_clean("unknown") == 0

    @pytest.mark.asyncio
    async def test_decay_no_removals(self) -> None:
        profile = UserProfile(user_id="user1")
        profile.tags = [
            UserTag(category=TagCategory.INTEREST, value="strong", confidence=0.5),
        ]
        store = MagicMock()
        store.get_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        removed = await mgr.decay_and_clean("user1")
        assert removed == 0
        store.update_profile.assert_not_called()  # no change → no update


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
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.record_message("user1", message_length=50)
        assert profile.total_messages == 1
        assert profile.last_seen_at > 0

    @pytest.mark.asyncio
    async def test_record_message_ema_avg_length(self) -> None:
        profile = UserProfile(user_id="user1")
        profile.preferences.avg_reply_length = 100
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.record_message("user1", message_length=200)
        # EMA: 0.9 * 100 + 0.1 * 200 = 90 + 20 = 110
        assert profile.preferences.avg_reply_length == 110

    @pytest.mark.asyncio
    async def test_record_message_first_time_sets_length(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.record_message("user1", message_length=80)
        assert profile.preferences.avg_reply_length == 80  # direct set (no EMA)


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
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences("user1", {"reply_style": "formal"})
        assert profile.preferences.reply_style == "formal"

    @pytest.mark.asyncio
    async def test_update_preferred_topics_dedup(self) -> None:
        profile = UserProfile(user_id="user1")
        profile.preferences.preferred_topics = ["coffee"]
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences(
            "user1", {"preferred_topics": ["coffee", "tea"]}
        )
        assert profile.preferences.preferred_topics == ["coffee", "tea"]

    @pytest.mark.asyncio
    async def test_update_avoided_topics(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences(
            "user1", {"avoided_topics": ["politics"]}
        )
        assert "politics" in profile.preferences.avoided_topics

    @pytest.mark.asyncio
    async def test_update_empty_preferences(self) -> None:
        profile = UserProfile(user_id="user1")
        store = MagicMock()
        store.get_or_create_profile = AsyncMock(return_value=profile)
        store.update_profile = AsyncMock()
        mgr = ProfileManager(profile_store=store)
        await mgr.update_preferences("user1", {})
        store.update_profile.assert_called_once()


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
