"""测试 ProfileStore — user profiles and tags CRUD."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    compute_entity_revision,
)
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


class TestProfileStoreAtomicAdminCRUD:
    """管理员画像写入必须严格、原子并执行修订版本检查。"""

    @pytest.mark.asyncio
    async def test_create_profile_strict_returns_complete_profile(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        preferences = UserPreferences(
            reply_style="formal",
            preferred_topics=["graphs"],
            active_hours=[9, 10],
        )
        tag = UserTag(
            category=TagCategory.INTEREST,
            value="Python",
            confidence=0.9,
            source="client",
            created_at=1.0,
            last_seen_at=2.0,
            occurrence_count=99,
        )

        profile = await store.create_profile_strict(
            "strict-user",
            display_name="Strict",
            preferences=preferences,
            tags=[tag],
        )

        assert profile.user_id == "strict-user"
        assert profile.display_name == "Strict"
        assert profile.preferences.to_dict() == preferences.to_dict()
        assert len(profile.tags) == 1
        assert profile.tags[0].source == "manual"
        assert profile.tags[0].occurrence_count == 1
        assert profile.tags[0].created_at == profile.tags[0].last_seen_at
        assert profile.tags[0].created_at > 2.0
        assert profile.first_seen_at == profile.last_seen_at
        assert profile.created_at == profile.updated_at

    @pytest.mark.asyncio
    async def test_create_profile_strict_duplicate_preserves_existing(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "duplicate-user",
            display_name="Remote",
            tags=[_make_tag(value="remote-tag")],
        )
        before = await store.get_profile("duplicate-user")
        assert before is not None

        with pytest.raises(EntityAlreadyExistsError, match="用户画像已存在"):
            await store.create_profile_strict(
                "duplicate-user",
                display_name="Local",
                tags=[_make_tag(value="local-tag")],
            )

        after = await store.get_profile("duplicate-user")
        assert after is not None
        assert after.to_dict() == before.to_dict()

    @pytest.mark.asyncio
    async def test_create_profile_strict_rolls_back_tag_write_failure(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        with patch.object(
            store,
            "_replace_tags_with_db",
            new=AsyncMock(side_effect=RuntimeError("tag write failed")),
            create=True,
        ):
            with pytest.raises(RuntimeError, match="tag write failed"):
                await store.create_profile_strict(
                    "atomic-create", tags=[_make_tag(value="tag")]
                )

        assert await store.get_profile("atomic-create") is None
        created = await store.create_profile_strict("atomic-create")
        assert created.user_id == "atomic-create"

    @pytest.mark.asyncio
    async def test_create_profile_strict_rolls_back_cancelled_tag_write(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        with patch.object(
            store,
            "_replace_tags_with_db",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
            create=True,
        ):
            with pytest.raises(asyncio.CancelledError):
                await store.create_profile_strict(
                    "cancelled-create", tags=[_make_tag(value="tag")]
                )

        assert await store.get_profile("cancelled-create") is None
        created = await store.create_profile_strict("cancelled-create")
        assert created.user_id == "cancelled-create"

    @pytest.mark.asyncio
    async def test_replace_editable_fields_forces_manual_tag_metadata(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "replace-user",
            display_name="Old",
            tags=[_make_tag(value="old")],
        )
        current = await store.get_profile("replace-user")
        assert current is not None

        updated = await store.replace_editable_fields(
            "replace-user",
            display_name="New",
            preferences=UserPreferences(
                reply_style="formal",
                preferred_topics=["databases"],
                avoided_topics=["spoilers"],
                active_hours=[8, 20],
            ),
            tags=[
                UserTag(
                    category=TagCategory.KNOWLEDGE,
                    value="SQLite",
                    confidence=0.95,
                    source="untrusted",
                    created_at=1.0,
                    last_seen_at=2.0,
                    occurrence_count=42,
                )
            ],
            expected_revision=compute_entity_revision(current.to_dict()),
        )

        assert updated.display_name == "New"
        assert updated.preferences.reply_style == "formal"
        assert [tag.value for tag in updated.tags] == ["SQLite"]
        assert updated.tags[0].source == "manual"
        assert updated.tags[0].occurrence_count == 1
        assert updated.tags[0].created_at == updated.tags[0].last_seen_at
        assert updated.tags[0].created_at > 2.0

    @pytest.mark.asyncio
    async def test_replace_editable_fields_preserves_read_only_profile_state(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        profile = await store.create_profile_strict("preserved-user")
        profile.total_messages = 17
        profile.total_sessions = 4
        profile.last_seen_at = profile.last_seen_at + 50
        await store.update_profile(profile)
        current = await store.get_profile("preserved-user")
        assert current is not None
        read_only = (
            current.total_messages,
            current.total_sessions,
            current.first_seen_at,
            current.last_seen_at,
            current.created_at,
        )

        updated = await store.replace_editable_fields(
            "preserved-user",
            display_name="Editable",
            preferences=UserPreferences(reply_style="concise"),
            tags=[],
            expected_revision=compute_entity_revision(current.to_dict()),
        )

        assert (
            updated.total_messages,
            updated.total_sessions,
            updated.first_seen_at,
            updated.last_seen_at,
            updated.created_at,
        ) == read_only
        assert updated.updated_at >= current.updated_at

    @pytest.mark.asyncio
    async def test_replace_stale_revision_preserves_remote_entity_and_tags(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "stale-replace",
            display_name="Remote",
            tags=[_make_tag(value="remote")],
        )
        remote = await store.get_profile("stale-replace")
        assert remote is not None

        with pytest.raises(EditConflictError) as raised:
            await store.replace_editable_fields(
                "stale-replace",
                display_name="Local",
                preferences=UserPreferences(reply_style="local"),
                tags=[_make_tag(value="local")],
                expected_revision="stale",
            )

        after = await store.get_profile("stale-replace")
        assert after is not None
        assert after.to_dict() == remote.to_dict()
        assert raised.value.current_entity == remote.to_dict()
        assert raised.value.current_revision == compute_entity_revision(remote.to_dict())

    @pytest.mark.asyncio
    async def test_replace_missing_profile_raises_not_found(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        with pytest.raises(EntityNotFoundError, match="画像不存在"):
            await store.replace_editable_fields(
                "missing",
                display_name="Missing",
                preferences=UserPreferences(),
                tags=[],
                expected_revision="revision",
            )

    @pytest.mark.asyncio
    async def test_replace_tag_failure_rolls_back_profile_and_tags(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "replace-failure",
            display_name="Remote",
            tags=[_make_tag(value="remote")],
        )
        before = await store.get_profile("replace-failure")
        assert before is not None

        with patch.object(
            store,
            "_replace_tags_with_db",
            new=AsyncMock(side_effect=RuntimeError("replacement failed")),
        ):
            with pytest.raises(RuntimeError, match="replacement failed"):
                await store.replace_editable_fields(
                    "replace-failure",
                    display_name="Local",
                    preferences=UserPreferences(reply_style="local"),
                    tags=[_make_tag(value="local")],
                    expected_revision=compute_entity_revision(before.to_dict()),
                )

        after = await store.get_profile("replace-failure")
        assert after is not None
        assert after.to_dict() == before.to_dict()

    @pytest.mark.asyncio
    async def test_delete_profile_if_revision_succeeds(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "delete-user", tags=[_make_tag(value="delete-me")]
        )
        current = await store.get_profile("delete-user")
        assert current is not None

        deleted = await store.delete_profile_if_revision(
            "delete-user",
            expected_revision=compute_entity_revision(current.to_dict()),
        )

        assert deleted is True
        assert await store.get_profile("delete-user") is None

    @pytest.mark.asyncio
    async def test_delete_profile_if_revision_missing_raises_not_found(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        with pytest.raises(EntityNotFoundError, match="画像不存在"):
            await store.delete_profile_if_revision(
                "missing", expected_revision="revision"
            )

    @pytest.mark.asyncio
    async def test_delete_stale_revision_preserves_profile_and_tags(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "stale-delete",
            display_name="Remote",
            tags=[_make_tag(value="remote")],
        )
        remote = await store.get_profile("stale-delete")
        assert remote is not None

        with pytest.raises(EditConflictError) as raised:
            await store.delete_profile_if_revision(
                "stale-delete", expected_revision="stale"
            )

        after = await store.get_profile("stale-delete")
        assert after is not None
        assert after.to_dict() == remote.to_dict()
        assert raised.value.current_entity == remote.to_dict()
        assert raised.value.current_revision == compute_entity_revision(remote.to_dict())
