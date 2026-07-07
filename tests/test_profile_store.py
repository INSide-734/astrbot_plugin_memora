"""测试 ProfileStore — user profiles and tags CRUD."""

import time

import pytest

from core.models.user_profile import TagCategory, UserPreferences, UserProfile, UserTag
from core.storage.profile_store import ProfileStore


def _make_tag(category=TagCategory.INTEREST, value="编程", confidence=0.8):
    return UserTag(
        category=category,
        value=value,
        confidence=confidence,
        source="auto",
        created_at=time.time(),
        last_seen_at=time.time(),
        occurrence_count=1,
    )


class TestProfileStoreCRUD:
    """Profile CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_and_get_profile(self, tmp_db_path):
        """创建 a profile and retrieve it."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        profile = await store.create_profile("user-1", display_name="张三")
        assert profile.user_id == "user-1"
        assert profile.display_name == "张三"

        fetched = await store.get_profile("user-1")
        assert fetched is not None
        assert fetched.user_id == "user-1"
        assert fetched.display_name == "张三"

    @pytest.mark.asyncio
    async def test_get_profile_missing_returns_none(self, tmp_db_path):
        """get_profile for unknown user returns None."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        assert await store.get_profile("no-such-user") is None

    @pytest.mark.asyncio
    async def test_get_or_create_profile_new(self, tmp_db_path):
        """get_or_create_profile creates a new profile when missing."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        profile = await store.get_or_create_profile("new-user")
        assert profile is not None
        assert profile.user_id == "new-user"

    @pytest.mark.asyncio
    async def test_get_or_create_profile_existing(self, tmp_db_path):
        """get_or_create_profile returns existing profile if present."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("existing-user", "Existing")
        profile = await store.get_or_create_profile("existing-user")
        assert profile.display_name == "Existing"

    @pytest.mark.asyncio
    async def test_update_profile(self, tmp_db_path):
        """update_profile persists changes to a profile."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        profile = await store.create_profile("user-upd", "Old Name")
        profile.display_name = "New Name"
        profile.total_messages = 42
        profile.total_sessions = 3
        profile.preferences.reply_style = "formal"
        await store.update_profile(profile)

        fetched = await store.get_profile("user-upd")
        assert fetched is not None
        assert fetched.display_name == "New Name"
        assert fetched.total_messages == 42
        assert fetched.total_sessions == 3
        assert fetched.preferences.reply_style == "formal"

    @pytest.mark.asyncio
    async def test_touch_updates_last_seen(self, tmp_db_path):
        """touch bumps last_seen_at."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-touch")
        original = await store.get_profile("user-touch")
        assert original is not None

        import asyncio
        await asyncio.sleep(0.02)
        await store.touch("user-touch")
        updated = await store.get_profile("user-touch")
        assert updated is not None
        assert updated.last_seen_at > original.last_seen_at

    @pytest.mark.asyncio
    async def test_list_profiles(self, tmp_db_path):
        """list_profiles returns paginated results."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        for i in range(5):
            await store.create_profile(f"user-list-{i}")

        profiles, total = await store.list_profiles(limit=3, offset=0)
        assert len(profiles) == 3
        assert total == 5

    @pytest.mark.asyncio
    async def test_delete_profile(self, tmp_db_path):
        """delete_profile removes profile and returns True."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-del")
        assert await store.delete_profile("user-del")
        assert await store.get_profile("user-del") is None

    @pytest.mark.asyncio
    async def test_delete_profile_missing(self, tmp_db_path):
        """delete_profile for unknown user returns False."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        assert not await store.delete_profile("no-such-user")


class TestProfileStoreTags:
    """Tag CRUD operations."""

    @pytest.mark.asyncio
    async def test_add_tag_new(self, tmp_db_path):
        """add_tag inserts a new tag and returns True."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-tag")
        tag = _make_tag(value="Python", confidence=0.9)
        result = await store.add_tag("user-tag", tag)
        assert result is True

        profile = await store.get_profile("user-tag")
        assert profile is not None
        assert len(profile.tags) >= 1
        assert any(t.value == "Python" for t in profile.tags)

    @pytest.mark.asyncio
    async def test_add_tag_duplicate_merges(self, tmp_db_path):
        """Adding a duplicate tag increases occurrence_count and returns False."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-dup")
        tag1 = _make_tag(value="Go", confidence=0.7)
        assert await store.add_tag("user-dup", tag1) is True

        tag2 = _make_tag(value="Go", confidence=0.85)
        assert await store.add_tag("user-dup", tag2) is False

        profile = await store.get_profile("user-dup")
        assert profile is not None
        go_tags = [t for t in profile.tags if t.value == "Go"]
        assert len(go_tags) == 1
        assert go_tags[0].confidence >= 0.85  # max confidence kept
        assert go_tags[0].occurrence_count == 2

    @pytest.mark.asyncio
    async def test_remove_tag(self, tmp_db_path):
        """remove_tag deletes a tag and returns True."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-rem")
        await store.add_tag("user-rem", _make_tag(value="Java"))

        assert await store.remove_tag("user-rem", "interest", "Java")
        profile = await store.get_profile("user-rem")
        assert profile is not None
        assert not any(t.value == "Java" for t in profile.tags)

    @pytest.mark.asyncio
    async def test_remove_tag_missing(self, tmp_db_path):
        """remove_tag for non-existent tag returns False."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-rem2")
        assert not await store.remove_tag("user-rem2", "interest", "missing")

    @pytest.mark.asyncio
    async def test_profile_includes_tags(self, tmp_db_path):
        """get_profile returns user profile with associated tags."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-full")
        await store.add_tag("user-full", _make_tag(category=TagCategory.INTEREST, value="AI"))
        await store.add_tag("user-full", _make_tag(category=TagCategory.PERSONALITY, value="curious"))
        await store.add_tag("user-full", _make_tag(category=TagCategory.HABIT, value="early_bird"))

        profile = await store.get_profile("user-full")
        assert profile is not None
        categories = {t.category for t in profile.tags}
        assert TagCategory.INTEREST in categories
        assert TagCategory.PERSONALITY in categories
        assert TagCategory.HABIT in categories

    @pytest.mark.asyncio
    async def test_delete_profile_cascades_tags(self, tmp_db_path):
        """Deleting a profile also removes its tags."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-cascade")
        await store.add_tag("user-cascade", _make_tag(value="test"))
        await store.delete_profile("user-cascade")

        # Re-create and verify tags are gone
        await store.create_profile("user-cascade")
        profile = await store.get_profile("user-cascade")
        assert profile is not None
        assert profile.tags == []

    @pytest.mark.asyncio
    async def test_tags_sorted_by_confidence(self, tmp_db_path):
        """Tags are returned sorted by confidence descending."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-sorted")
        await store.add_tag("user-sorted", _make_tag(value="low", confidence=0.3))
        await store.add_tag("user-sorted", _make_tag(value="high", confidence=0.95))
        await store.add_tag("user-sorted", _make_tag(value="mid", confidence=0.6))

        profile = await store.get_profile("user-sorted")
        assert profile is not None
        confidences = [t.confidence for t in profile.tags]
        assert confidences == sorted(confidences, reverse=True)


class TestProfileStoreEdgeCases:
    """边 cases for profile store."""

    @pytest.mark.asyncio
    async def test_create_profile_with_defaults(self, tmp_db_path):
        """create_profile fills in sensible defaults."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        profile = await store.create_profile("user-default")
        assert profile.display_name == ""
        assert profile.total_messages == 0
        assert profile.total_sessions == 0
        assert profile.preferences.reply_style == "casual"
        assert profile.preferences.preferred_topics == []

    @pytest.mark.asyncio
    async def test_update_profile_preserves_tags(self, tmp_db_path):
        """Updating a profile does not affect its tags."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        profile = await store.create_profile("user-preserve")
        await store.add_tag("user-preserve", _make_tag(value="reading"))
        await store.add_tag("user-preserve", _make_tag(value="music"))

        profile.display_name = "Updated"
        await store.update_profile(profile)

        fetched = await store.get_profile("user-preserve")
        assert fetched is not None
        assert fetched.display_name == "Updated"
        assert len(fetched.tags) == 2

    @pytest.mark.asyncio
    async def test_list_profiles_offset(self, tmp_db_path):
        """list_profiles with offset works for pagination."""
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        for i in range(5):
            await store.create_profile(f"user-page-{i}")

        page1, total = await store.list_profiles(limit=2, offset=0)
        assert len(page1) == 2
        assert total == 5

        page3, total = await store.list_profiles(limit=2, offset=4)
        assert len(page3) == 1
