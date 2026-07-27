"""协议身份目录 Store 的 SQLite 契约测试。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from core.storage.protocol_identity_store import ProtocolIdentityStore


@pytest.mark.asyncio
async def test_initialize_bootstraps_three_identity_tables_idempotently(
    tmp_db_path: str,
) -> None:
    """初始化应只幂等创建三张身份表。"""

    store = ProtocolIdentityStore(tmp_db_path)
    await store.initialize()
    await store.initialize()

    async with aiosqlite.connect(tmp_db_path) as connection:
        cursor = await connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name IN (?, ?, ?)
            ORDER BY name
            """,
            ("identity_aliases", "identity_scope_members", "identity_users"),
        )
        names = [row[0] for row in await cursor.fetchall()]

    assert names == ["identity_aliases", "identity_scope_members", "identity_users"]
    await store.close()


@pytest.mark.asyncio
async def test_initialize_does_not_scan_or_rewrite_existing_unrelated_data(
    tmp_db_path: str,
) -> None:
    """初始化不得迁移、扫描或改写既有业务表。"""

    async with aiosqlite.connect(tmp_db_path) as connection:
        await connection.execute("CREATE TABLE legacy_values (value TEXT NOT NULL)")
        await connection.execute(
            "INSERT INTO legacy_values(value) VALUES (?)", ("保留",)
        )
        await connection.commit()

    store = ProtocolIdentityStore(tmp_db_path)
    await store.initialize()

    async with aiosqlite.connect(tmp_db_path) as connection:
        cursor = await connection.execute("SELECT value FROM legacy_values")
        rows = await cursor.fetchall()

    assert rows == [("保留",)]
    await store.close()


@pytest.mark.asyncio
async def test_identity_lookup_returns_none_before_observation(
    tmp_db_path: str,
) -> None:
    """未观察过的稳定身份查询应返回空结果。"""

    store = ProtocolIdentityStore(tmp_db_path)
    await store.initialize()

    assert await store.get_identity("qq", "10001", "group", "20001") is None

    await store.close()


@pytest.mark.asyncio
async def test_aliases_are_scoped_and_parameterized(tmp_db_path: str) -> None:
    """别名应按身份和作用域隔离，并保留重复插入幂等性。"""

    store = ProtocolIdentityStore(tmp_db_path)
    await store.initialize()

    aliases = (
        ("global", "", "全局旧名"),
        ("group", "20001", "群内旧名"),
        ("group", "20002", "另一个群旧名"),
    )
    await store.record_aliases("qq", "10001", aliases)
    await store.record_aliases("qq", "10001", aliases)
    await store.record_aliases("qq", "10002", (("global", "", "全局旧名"),))

    assert await store.find_aliases("qq", "10001", "global", "") == ["全局旧名"]
    assert await store.find_aliases("qq", "10001", "group", "20001") == ["群内旧名"]
    assert await store.find_aliases("qq", "10001", "group", "20002") == ["另一个群旧名"]
    assert await store.find_aliases("qq", "10002", "global", "") == ["全局旧名"]

    await store.close()


@pytest.mark.asyncio
async def test_store_requires_initialize(tmp_db_path: str) -> None:
    """未初始化写入应抛出稳定运行时错误。"""

    store = ProtocolIdentityStore(tmp_db_path)
    with pytest.raises(RuntimeError):
        await store.record_aliases("qq", "10001", ())


@pytest.mark.asyncio
async def test_store_creates_parent_directory(tmp_path: Path) -> None:
    """Store 应在初始化前创建不存在的数据库父目录。"""

    db_path = tmp_path / "nested" / "identity.db"
    store = ProtocolIdentityStore(str(db_path))
    await store.initialize()

    assert db_path.parent.exists()
    await store.close()
