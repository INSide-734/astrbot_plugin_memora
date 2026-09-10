"""测试 ProfileStore 的用户画像与标签增删改查。"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from core.features.profiles.domain.models import TagCategory, UserPreferences, UserTag
from core.features.profiles.infrastructure.profile_store import ProfileStore
from core.shared.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    compute_entity_revision,
)
from core.shared.list_sorting import SortQuery


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


class _FatalProfileStoreError(BaseException):
    """用于验证非取消 fatal 异常回滚的测试异常。"""


class _RollbackTrackingConnection:
    """委托给真实连接，同时记录回滚并可在画像删除前注入故障。"""

    def __init__(
        self,
        db,
        *,
        delete_failure=None,
        commit_failure=None,
        rollback_failures=None,
    ):
        self._db = db
        self._delete_failure = delete_failure
        self._commit_failure = commit_failure
        self._rollback_failures = list(rollback_failures or [])
        self.rollback_calls = 0

    async def execute(self, sql, *args, **kwargs):
        normalized_sql = " ".join(sql.split()).upper()
        if self._delete_failure is not None and normalized_sql.startswith(
            "DELETE FROM USER_PROFILES"
        ):
            raise self._delete_failure
        return await self._db.execute(sql, *args, **kwargs)

    async def rollback(self):
        self.rollback_calls += 1
        if self._rollback_failures:
            raise self._rollback_failures.pop(0)
        return await self._db.rollback()

    async def commit(self):
        if self._commit_failure is not None:
            raise self._commit_failure
        return await self._db.commit()

    def __getattr__(self, name):
        return getattr(self._db, name)


@asynccontextmanager
async def _tracked_connection(
    original_connect,
    trackers,
    *,
    delete_failure=None,
    commit_failure=None,
    rollback_failures=None,
):
    async with original_connect() as db:
        tracker = _RollbackTrackingConnection(
            db,
            delete_failure=delete_failure,
            commit_failure=commit_failure,
            rollback_failures=rollback_failures,
        )
        trackers.append(tracker)
        yield tracker


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
    async def test_list_profiles_sorts_display_names_before_pagination(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        await store.create_profile("user-gamma", "Gamma")
        await store.create_profile("user-alpha", "Alpha")
        await store.create_profile("user-beta", "Beta")

        profiles, total = await store.list_profiles(
            limit=2,
            sort=SortQuery("display_name", "asc"),
        )

        assert [profile.display_name for profile in profiles] == ["Alpha", "Beta"]
        assert total == 3

    @pytest.mark.asyncio
    async def test_list_profiles_sorts_message_totals_descending(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        for user_id, total_messages in (
            ("user-low", 2),
            ("user-high", 9),
            ("user-mid", 5),
        ):
            profile = await store.create_profile(user_id)
            profile.total_messages = total_messages
            await store.update_profile(profile)

        profiles, _ = await store.list_profiles(
            sort=SortQuery("total_messages", "desc"),
        )

        assert [profile.user_id for profile in profiles] == [
            "user-high",
            "user-mid",
            "user-low",
        ]

    @pytest.mark.asyncio
    async def test_list_profiles_sorts_last_seen_descending_with_stable_user_id_ties(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()

        for user_id in ("user-b", "user-a", "user-newest"):
            await store.create_profile(user_id)
        async with store._connect() as db:
            await db.execute(
                "UPDATE user_profiles SET last_seen_at = 100 WHERE user_id IN (?, ?)",
                ("user-a", "user-b"),
            )
            await db.execute(
                "UPDATE user_profiles SET last_seen_at = 200 WHERE user_id = ?",
                ("user-newest",),
            )
            await db.commit()

        profiles, _ = await store.list_profiles(
            sort=SortQuery("last_seen_at", "desc"),
        )

        assert [profile.user_id for profile in profiles] == [
            "user-newest",
            "user-a",
            "user-b",
        ]

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
        await store.add_tag(
            "user-full", _make_tag(category=TagCategory.INTEREST, value="AI")
        )
        await store.add_tag(
            "user-full", _make_tag(category=TagCategory.PERSONALITY, value="curious")
        )
        await store.add_tag(
            "user-full", _make_tag(category=TagCategory.HABIT, value="early_bird")
        )

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
    async def test_create_profile_strict_duplicate_preserves_existing(
        self, tmp_db_path
    ):
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
    async def test_create_profile_strict_preserves_non_unique_integrity_error(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        with pytest.raises(aiosqlite.IntegrityError) as exc_info:
            await store.create_profile_strict(cast(str, None))
        assert not isinstance(exc_info.value, EntityAlreadyExistsError)
        async with store._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM user_profiles")
            row = await cursor.fetchone()
            assert row is not None and row[0] == 0
            assert db.in_transaction is False

        created = await store.create_profile_strict("usable-after-integrity-error")
        assert created.user_id == "usable-after-integrity-error"

    @pytest.mark.asyncio
    async def test_create_profile_strict_tag_integrity_error_is_not_profile_duplicate(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        duplicate_tags = [
            _make_tag(value="duplicate-tag"),
            _make_tag(value="duplicate-tag"),
        ]

        with pytest.raises(aiosqlite.IntegrityError):
            await store.create_profile_strict(
                "tag-integrity",
                tags=duplicate_tags,
            )

        assert await store.get_profile("tag-integrity") is None

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
    async def test_equal_confidence_tags_have_stable_order_and_revision(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "stable-order",
            tags=[
                _make_tag(
                    category=TagCategory.INTEREST,
                    value="zeta",
                    confidence=0.8,
                ),
                _make_tag(
                    category=TagCategory.HABIT,
                    value="alpha",
                    confidence=0.8,
                ),
            ],
        )

        public_profile = await store.get_profile("stable-order")
        direct_tags = await store._get_tags("stable-order")
        async with store._connect() as db:
            bound_profile = await store._get_profile_with_db(db, "stable-order")

        assert public_profile is not None
        assert bound_profile is not None
        expected = [("habit", "alpha"), ("interest", "zeta")]
        assert [
            (tag.category.value, tag.value) for tag in public_profile.tags
        ] == expected
        assert [(tag.category.value, tag.value) for tag in direct_tags] == expected
        assert [
            (tag.category.value, tag.value) for tag in bound_profile.tags
        ] == expected
        assert compute_entity_revision(
            public_profile.to_dict()
        ) == compute_entity_revision(bound_profile.to_dict())

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
        assert raised.value.current_revision == compute_entity_revision(
            remote.to_dict()
        )

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
        assert raised.value.current_revision == compute_entity_revision(
            remote.to_dict()
        )

    @pytest.mark.asyncio
    async def test_replace_rolls_back_non_cancellation_base_exception(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "fatal-replace",
            display_name="Remote",
            tags=[_make_tag(value="remote-tag")],
        )
        before = await store.get_profile("fatal-replace")
        assert before is not None

        failure = _FatalProfileStoreError("fatal tag replacement")
        trackers = []
        original_connect = store._connect

        def tracked_connect():
            return _tracked_connection(original_connect, trackers)

        async def fail_during_tag_replacement(db, user_id, tags, now):
            await db.execute(
                "DELETE FROM user_tags WHERE user_id = ?",
                (user_id,),
            )
            raise failure

        with (
            patch.object(store, "_connect", new=tracked_connect),
            patch.object(
                store,
                "_replace_tags_with_db",
                new=fail_during_tag_replacement,
            ),
        ):
            with pytest.raises(_FatalProfileStoreError) as raised:
                await store.replace_editable_fields(
                    "fatal-replace",
                    display_name="Local",
                    preferences=UserPreferences(reply_style="formal"),
                    tags=[_make_tag(value="local-tag")],
                    expected_revision=compute_entity_revision(before.to_dict()),
                )

        assert raised.value is failure
        assert len(trackers) == 1
        assert trackers[0].rollback_calls == 1
        after = await store.get_profile("fatal-replace")
        assert after is not None
        assert after.to_dict() == before.to_dict()

        recovered = await store.replace_editable_fields(
            "fatal-replace",
            display_name="Recovered",
            preferences=UserPreferences(reply_style="concise"),
            tags=[_make_tag(value="recovered-tag")],
            expected_revision=compute_entity_revision(after.to_dict()),
        )
        assert recovered.display_name == "Recovered"
        assert [tag.value for tag in recovered.tags] == ["recovered-tag"]

    @pytest.mark.asyncio
    async def test_delete_rolls_back_non_cancellation_base_exception(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "fatal-delete",
            display_name="Remote",
            tags=[_make_tag(value="remote-tag")],
        )
        before = await store.get_profile("fatal-delete")
        assert before is not None

        failure = _FatalProfileStoreError("fatal profile deletion")
        trackers = []
        original_connect = store._connect

        def tracked_connect():
            return _tracked_connection(
                original_connect,
                trackers,
                delete_failure=failure,
            )

        with patch.object(store, "_connect", new=tracked_connect):
            with pytest.raises(_FatalProfileStoreError) as raised:
                await store.delete_profile_if_revision(
                    "fatal-delete",
                    expected_revision=compute_entity_revision(before.to_dict()),
                )

        assert raised.value is failure
        assert len(trackers) == 1
        assert trackers[0].rollback_calls == 1
        after = await store.get_profile("fatal-delete")
        assert after is not None
        assert after.to_dict() == before.to_dict()

        assert await store.delete_profile_if_revision(
            "fatal-delete",
            expected_revision=compute_entity_revision(after.to_dict()),
        )
        assert await store.get_profile("fatal-delete") is None

    @pytest.mark.asyncio
    async def test_original_fatal_error_survives_transient_rollback_failure(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "rollback-retry",
            display_name="Remote",
            tags=[_make_tag(value="remote-tag")],
        )
        before = await store.get_profile("rollback-retry")
        assert before is not None

        original_failure = _FatalProfileStoreError("original fatal failure")
        rollback_failure = RuntimeError("transient rollback failure")
        trackers = []
        original_connect = store._connect

        def tracked_connect():
            return _tracked_connection(
                original_connect,
                trackers,
                rollback_failures=[rollback_failure],
            )

        async def fail_during_tag_replacement(db, user_id, tags, now):
            await db.execute(
                "DELETE FROM user_tags WHERE user_id = ?",
                (user_id,),
            )
            raise original_failure

        with (
            patch.object(store, "_connect", new=tracked_connect),
            patch.object(
                store,
                "_replace_tags_with_db",
                new=fail_during_tag_replacement,
            ),
        ):
            with pytest.raises(_FatalProfileStoreError) as raised:
                await store.replace_editable_fields(
                    "rollback-retry",
                    display_name="Local",
                    preferences=UserPreferences(reply_style="formal"),
                    tags=[_make_tag(value="local-tag")],
                    expected_revision=compute_entity_revision(before.to_dict()),
                )

        assert raised.value is original_failure
        assert trackers[0].rollback_calls == 2
        after = await store.get_profile("rollback-retry")
        assert after is not None
        assert after.to_dict() == before.to_dict()
        await store.touch("rollback-retry")

    @pytest.mark.asyncio
    async def test_commit_error_survives_transient_rollback_failure(self, tmp_db_path):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "commit-rollback",
            display_name="Remote",
            tags=[_make_tag(value="remote-tag")],
        )
        before = await store.get_profile("commit-rollback")
        assert before is not None

        commit_failure = RuntimeError("commit failed")
        rollback_failure = RuntimeError("transient rollback failure")
        trackers = []
        original_connect = store._connect

        def tracked_connect():
            return _tracked_connection(
                original_connect,
                trackers,
                commit_failure=commit_failure,
                rollback_failures=[rollback_failure],
            )

        with patch.object(store, "_connect", new=tracked_connect):
            with pytest.raises(RuntimeError) as raised:
                await store.replace_editable_fields(
                    "commit-rollback",
                    display_name="Local",
                    preferences=UserPreferences(reply_style="formal"),
                    tags=[_make_tag(value="local-tag")],
                    expected_revision=compute_entity_revision(before.to_dict()),
                )

        assert raised.value is commit_failure
        assert trackers[0].rollback_calls == 2
        after = await store.get_profile("commit-rollback")
        assert after is not None
        assert after.to_dict() == before.to_dict()
        await store.touch("commit-rollback")


class TestProfileStoreAutomaticWrites:
    """自动学习写入只修改各自拥有的字段。"""

    @pytest.mark.asyncio
    async def test_update_profile_fields_atomic_preserves_unowned_state(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        profile = await store.create_profile_strict(
            "field-update",
            display_name="Old",
            preferences=UserPreferences(
                reply_style="formal",
                preferred_topics=["graphs"],
            ),
            tags=[_make_tag(value="stable-tag")],
        )
        profile.total_messages = 12
        profile.total_sessions = 4
        profile.last_seen_at += 100
        await store.update_profile(profile)
        before = await store.get_profile("field-update")
        assert before is not None

        updated = await store.update_profile_fields_atomic(
            "field-update",
            display_name="New",
        )

        assert updated is not None
        assert updated.display_name == "New"
        assert updated.preferences.to_dict() == before.preferences.to_dict()
        assert updated.total_messages == before.total_messages
        assert updated.total_sessions == before.total_sessions
        assert updated.first_seen_at == before.first_seen_at
        assert updated.last_seen_at == before.last_seen_at
        assert [tag.to_dict() for tag in updated.tags] == [
            tag.to_dict() for tag in before.tags
        ]

    @pytest.mark.asyncio
    async def test_record_message_atomic_uses_latest_counter_and_preferences(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        profile = await store.create_profile_strict(
            "record-atomic",
            display_name="Admin",
            preferences=UserPreferences(
                reply_style="formal",
                preferred_topics=["graphs"],
                avg_reply_length=100,
            ),
        )
        profile.total_messages = 10
        await store.update_profile(profile)

        updated = await store.record_message_atomic(
            "record-atomic",
            message_length=200,
        )

        assert updated is not None
        assert updated.total_messages == 11
        assert updated.display_name == "Admin"
        assert updated.preferences.reply_style == "formal"
        assert updated.preferences.preferred_topics == ["graphs"]
        assert updated.preferences.avg_reply_length == 110

    @pytest.mark.asyncio
    async def test_merge_preferences_atomic_preserves_unrelated_latest_fields(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        await store.create_profile_strict(
            "merge-preferences",
            display_name="Admin",
            preferences=UserPreferences(
                reply_style="formal",
                preferred_topics=["coffee"],
                avoided_topics=["spoilers"],
                active_hours=[9, 20],
                avg_reply_length=80,
                interaction_frequency=0.4,
            ),
        )

        updated = await store.merge_preferences_atomic(
            "merge-preferences",
            {"preferred_topics": ["coffee", "tea"]},
        )

        assert updated is not None
        assert updated.display_name == "Admin"
        assert updated.preferences.to_dict() == {
            "reply_style": "formal",
            "preferred_topics": ["coffee", "tea"],
            "avoided_topics": ["spoilers"],
            "active_hours": [9, 20],
            "avg_reply_length": 80,
            "interaction_frequency": 0.4,
        }

    @pytest.mark.asyncio
    async def test_decay_tags_atomic_preserves_profile_fields_and_tag_metadata(
        self, tmp_db_path
    ):
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        profile = await store.create_profile_strict(
            "decay-atomic",
            display_name="Admin",
            preferences=UserPreferences(reply_style="formal"),
        )
        profile.total_messages = 7
        profile.total_sessions = 2
        await store.update_profile(profile)
        reference_time = 1_000_000.0
        await store.add_tag(
            "decay-atomic",
            UserTag(
                category=TagCategory.INTEREST,
                value="remove-me",
                confidence=0.05,
                source="auto",
                created_at=reference_time,
                last_seen_at=reference_time,
                occurrence_count=3,
            ),
        )
        await store.add_tag(
            "decay-atomic",
            UserTag(
                category=TagCategory.HABIT,
                value="keep-me",
                confidence=0.9,
                source="manual",
                created_at=reference_time,
                last_seen_at=reference_time,
                occurrence_count=5,
            ),
        )
        before = await store.get_profile("decay-atomic")
        assert before is not None

        removed = await store.decay_and_clean_tags_atomic(
            "decay-atomic",
            reference_time=reference_time,
        )

        after = await store.get_profile("decay-atomic")
        assert after is not None
        assert removed == 1
        assert after.display_name == before.display_name
        assert after.preferences.to_dict() == before.preferences.to_dict()
        assert after.total_messages == before.total_messages
        assert after.total_sessions == before.total_sessions
        assert [tag.value for tag in after.tags] == ["keep-me"]
        assert after.tags[0].source == "manual"
        assert after.tags[0].occurrence_count == 5
