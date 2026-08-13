"""验证自动画像写入保存 canonical 来源而不覆盖人工权威。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from core.features.profiles.application.profile_manager import ProfileManager
from core.features.profiles.domain.models import TagCategory, UserTag
from core.features.profiles.infrastructure.profile_store import ProfileStore
from core.shared.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.shared.contracts import MemorySourceRef


def _derived_provenance(
    memory_id: int = 17,
    *,
    revision: str | None = None,
    scope: str = "private:user-a",
    privacy: str = "confidential",
) -> DomainProvenance:
    """构造单来源派生画像证据。"""

    return DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            MemorySourceRef(
                memory_id=memory_id,
                revision_token=revision or f"rev-{memory_id}",
                scope_key=scope,
                privacy_level=privacy,
                occurred_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
                content="该正文不得复制进画像表",
            ),
        ),
    )


async def _create_canonical_source(
    db_path,
    *,
    memory_id: int = 17,
    revision: str = "rev-17",
    scope: str = "private:user-a",
    privacy: str = "confidential",
) -> None:
    """在领域 Store 使用的同一 SQLite 中写入匿名 canonical source。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT INTO documents(id,text,metadata,created_at,updated_at)
               VALUES(?,?,?,?,?)""",
            (
                memory_id,
                "匿名 canonical 正文",
                json.dumps(
                    {"scope_key": scope, "privacy_level": privacy},
                    ensure_ascii=False,
                ),
                "2026-07-21T00:00:00+00:00",
                revision,
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_automatic_tags_require_and_round_trip_provenance(tmp_path) -> None:
    """自动标签缺来源时拒绝，有来源时只持久化引用。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")
    tag = UserTag(
        category=TagCategory.INTEREST,
        value="数据库",
        confidence=0.8,
        source="llm",
    )

    with pytest.raises(ValueError, match="source_provenance_required"):
        await manager.ingest_tags("user-a", [tag])

    await manager.ingest_tags(
        "user-a",
        [tag],
        provenance=_derived_provenance(),
    )
    profile = await manager.get_profile("user-a")

    assert profile is not None
    stored = profile.tags[0]
    assert stored.provenance is not None
    assert stored.provenance.origin is DomainObjectOrigin.DERIVED
    assert stored.provenance.sources[0].revision_token == "rev-17"
    assert stored.provenance.sources[0].content is None


@pytest.mark.asyncio
async def test_derived_tag_does_not_replace_manual_authority(tmp_path) -> None:
    """同名派生标签只能强化人工标签，不能改变其来源权威。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")
    await manager.add_tag(
        "user-a",
        UserTag(
            category=TagCategory.PREFERENCE,
            value="简洁回复",
            confidence=0.6,
            source="manual",
        ),
    )

    await manager.ingest_tags(
        "user-a",
        [
            UserTag(
                category=TagCategory.PREFERENCE,
                value="简洁回复",
                confidence=0.9,
                source="llm",
            )
        ],
        provenance=_derived_provenance(),
    )
    profile = await manager.get_profile("user-a")

    assert profile is not None
    assert profile.tags[0].confidence == 0.9
    assert profile.tags[0].source == "manual"
    assert profile.tags[0].provenance == DomainProvenance(DomainObjectOrigin.MANUAL)


@pytest.mark.asyncio
async def test_automatic_preferences_require_and_round_trip_provenance(
    tmp_path,
) -> None:
    """自动偏好合并必须携带来源，并在 JSON 字段中安全往返。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")

    with pytest.raises(ValueError, match="source_provenance_required"):
        await manager.update_preferences("user-a", {"reply_style": "formal"})

    await manager.update_preferences(
        "user-a",
        {"reply_style": "formal", "preferred_topics": ["数据库"]},
        provenance=_derived_provenance(),
    )
    profile = await manager.get_profile("user-a")

    assert profile is not None
    assert profile.preferences.reply_style == "formal"
    assert profile.preferences.provenance is not None
    assert profile.preferences.provenance.sources[0].content is None


@pytest.mark.asyncio
async def test_automatic_preferences_recheck_current_canonical_source(
    tmp_path,
) -> None:
    """自动偏好写入必须在事务内拒绝陈旧的 canonical revision。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")

    with pytest.raises(ValueError, match="source_revision_mismatch"):
        await manager.update_preferences(
            "user-a",
            {"reply_style": "formal"},
            provenance=_derived_provenance(revision="stale-revision"),
        )


@pytest.mark.asyncio
async def test_derived_preferences_do_not_override_manual_authority(tmp_path) -> None:
    """自动偏好 proposal 不得覆盖管理员维护的偏好快照。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")
    await manager.update_profile_fields(
        "user-a",
        preferences={"reply_style": "concise"},
    )

    await manager.update_preferences(
        "user-a",
        {"reply_style": "formal", "preferred_topics": ["数据库"]},
        provenance=_derived_provenance(),
    )

    profile = await manager.get_profile("user-a")
    assert profile is not None
    assert profile.preferences.reply_style == "concise"
    assert profile.preferences.preferred_topics == []
    assert profile.preferences.provenance == DomainProvenance(DomainObjectOrigin.MANUAL)


@pytest.mark.asyncio
async def test_manual_profile_fields_receive_manual_origin(tmp_path) -> None:
    """管理员创建的画像字段保持领域内人工权威。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    manager = ProfileManager(store)

    profile = await manager.create_profile_manual(
        "user-a",
        display_name="匿名用户",
        preferences={"reply_style": "concise"},
        tags=[
            {
                "category": "interest",
                "value": "数据库",
                "confidence": 0.7,
            }
        ],
    )

    assert profile.preferences.provenance == DomainProvenance(DomainObjectOrigin.MANUAL)
    assert profile.tags[0].provenance == DomainProvenance(DomainObjectOrigin.MANUAL)


@pytest.mark.asyncio
async def test_profile_store_migrates_tag_provenance_column(tmp_path) -> None:
    """旧画像表初始化时可重复增加来源列，不改写旧标签。"""

    db_path = tmp_path / "legacy-profile.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE NOT NULL,
                display_name TEXT DEFAULT '',
                preferences_json TEXT DEFAULT '{}',
                total_messages INTEGER DEFAULT 0,
                total_sessions INTEGER DEFAULT 0,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE user_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'custom',
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'auto',
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                occurrence_count INTEGER DEFAULT 1,
                UNIQUE(user_id, category, value)
            );
            """
        )
        await db.commit()

    store = ProfileStore(str(db_path))
    await store.init_table()
    await store.init_table()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(user_tags)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
    assert "provenance_json" in columns


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_kwargs", "reason_code"),
    [
        ({"memory_id": 404}, "source_not_found"),
        ({"revision": "stale-revision"}, "source_revision_mismatch"),
        ({"scope": "group:other"}, "source_scope_mismatch"),
        ({"privacy": "shared"}, "source_privacy_mismatch"),
    ],
)
async def test_profile_derived_write_rechecks_current_canonical_source(
    tmp_path,
    source_kwargs,
    reason_code,
) -> None:
    """持久化前必须重新核对 canonical source，而不是信任调用方快照。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")

    with pytest.raises(ValueError, match=reason_code):
        await manager.ingest_tags(
            "user-a",
            [UserTag(value="不可信来源", source="llm")],
            provenance=_derived_provenance(**source_kwargs),
        )


@pytest.mark.asyncio
async def test_profile_read_drops_stale_derived_fields_only(tmp_path) -> None:
    """读取画像时只丢弃 stale 自动字段，不影响人工画像聚合。"""

    store = ProfileStore(str(tmp_path / "profile.db"))
    await store.init_table()
    await _create_canonical_source(store.db_path)
    manager = ProfileManager(store)
    await manager.ensure_profile("user-a")
    await manager.add_tag(
        "user-a",
        UserTag(category=TagCategory.PREFERENCE, value="人工偏好", source="manual"),
    )
    await manager.ingest_tags(
        "user-a",
        [UserTag(category=TagCategory.INTEREST, value="自动兴趣", source="llm")],
        provenance=_derived_provenance(),
    )
    await manager.update_preferences(
        "user-a",
        {"reply_style": "formal"},
        provenance=_derived_provenance(),
    )
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "UPDATE documents SET updated_at = ? WHERE id = 17",
            ("rev-18",),
        )
        await db.commit()

    profile = await manager.get_profile("user-a")

    assert profile is not None
    assert [tag.value for tag in profile.tags] == ["人工偏好"]
    assert profile.preferences.reply_style == "casual"
