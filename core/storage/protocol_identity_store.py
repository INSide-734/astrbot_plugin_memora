"""协议稳定身份目录的 SQLite 持久化实现。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import aiosqlite

from ..identity.models import ResolvedIdentity
from .base import apply_perf_pragmas


@dataclass(frozen=True, slots=True)
class StoredIdentity:
    """保存身份目录当前值及其作用域名称。"""

    identity_namespace: str
    stable_user_id: str
    canonical_user_id: str
    global_name: str | None
    scope_type: str | None
    scope_id: str | None
    scope_name: str | None
    display_name: str
    first_seen_at: float
    last_seen_at: float
    global_name_updated_at: float | None
    scope_name_updated_at: float | None
    admin_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationMutation:
    """描述 Service 对当前身份名称计算出的事务内变更。"""

    global_name_changed: bool
    global_name: str | None
    global_name_updated_at: float | None
    scope_name_changed: bool
    scope_name: str | None
    scope_name_updated_at: float | None
    aliases: tuple[tuple[str, str, str], ...] = ()


IdentityMerger = Callable[[StoredIdentity | None], ObservationMutation]


class ProtocolIdentityStore:
    """维护协议身份、作用域成员和历史别名三张表。"""

    def __init__(self, db_path: str) -> None:
        """初始化 Store，并创建数据库父目录。"""

        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """打开连接并幂等创建身份目录表，不执行历史迁移。"""

        if self._connection is not None:
            return
        connection = await aiosqlite.connect(self.db_path)
        self._connection = connection
        connection.row_factory = aiosqlite.Row
        await apply_perf_pragmas(connection)
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_users (
                identity_namespace TEXT NOT NULL,
                stable_user_id TEXT NOT NULL,
                canonical_user_id TEXT NOT NULL,
                global_name TEXT,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                global_name_updated_at REAL,
                PRIMARY KEY (identity_namespace, stable_user_id),
                UNIQUE (canonical_user_id)
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_scope_members (
                identity_namespace TEXT NOT NULL,
                stable_user_id TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                scope_name TEXT,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                scope_name_updated_at REAL,
                PRIMARY KEY (
                    identity_namespace, stable_user_id, scope_type, scope_id
                )
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_aliases (
                identity_namespace TEXT NOT NULL,
                stable_user_id TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (
                    identity_namespace, stable_user_id, scope_type, scope_id, alias
                )
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_identity_alias_lookup
            ON identity_aliases(identity_namespace, stable_user_id, scope_type, scope_id)
            """
        )
        await connection.commit()

    async def close(self) -> None:
        """等待当前写入完成并关闭持久数据库连接。"""

        async with self._write_lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def get_identity(
        self,
        identity_namespace: str,
        stable_user_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> StoredIdentity | None:
        """按稳定身份和可选作用域读取当前名称。"""

        connection = self._require_connection()
        async with self._write_lock:
            return await self._fetch_identity(
                connection,
                identity_namespace,
                stable_user_id,
                scope_type,
                scope_id,
            )

    async def find_aliases(
        self,
        identity_namespace: str,
        stable_user_id: str,
        scope_type: str,
        scope_id: str,
        limit: int = 128,
    ) -> list[str]:
        """精确读取指定身份作用域的历史别名。"""

        connection = self._require_connection()
        safe_limit = max(1, min(int(limit), 512))
        async with self._write_lock:
            cursor = await connection.execute(
                """
                SELECT alias FROM identity_aliases
                WHERE identity_namespace = ?
                  AND stable_user_id = ?
                  AND scope_type = ?
                  AND scope_id = ?
                ORDER BY created_at ASC, alias ASC
                LIMIT ?
                """,
                (
                    identity_namespace,
                    stable_user_id,
                    scope_type,
                    scope_id,
                    safe_limit,
                ),
            )
            rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]

    async def find_alias_owner_ids(
        self,
        identity_namespace: str,
        alias: str,
        scope_type: str,
        scope_id: str,
        *,
        member_scope_type: str | None = None,
        member_scope_id: str | None = None,
        limit: int = 2,
    ) -> list[str]:
        """按精确别名反查稳定身份，并可限制为指定作用域的现有成员。"""

        connection = self._require_connection()
        safe_limit = max(1, min(int(limit), 32))
        membership_enabled = (
            isinstance(member_scope_type, str)
            and bool(member_scope_type)
            and isinstance(member_scope_id, str)
            and bool(member_scope_id)
        )
        async with self._write_lock:
            if membership_enabled:
                cursor = await connection.execute(
                    """
                    SELECT DISTINCT a.stable_user_id
                    FROM identity_aliases a
                    WHERE a.identity_namespace = ?
                      AND a.alias = ?
                      AND a.scope_type = ?
                      AND a.scope_id = ?
                      AND EXISTS (
                          SELECT 1 FROM identity_scope_members s
                          WHERE s.identity_namespace = a.identity_namespace
                            AND s.stable_user_id = a.stable_user_id
                            AND s.scope_type = ?
                            AND s.scope_id = ?
                      )
                    ORDER BY a.stable_user_id ASC
                    LIMIT ?
                    """,
                    (
                        identity_namespace,
                        alias,
                        scope_type,
                        scope_id,
                        member_scope_type,
                        member_scope_id,
                        safe_limit,
                    ),
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT DISTINCT stable_user_id
                    FROM identity_aliases
                    WHERE identity_namespace = ?
                      AND alias = ?
                      AND scope_type = ?
                      AND scope_id = ?
                    ORDER BY stable_user_id ASC
                    LIMIT ?
                    """,
                    (
                        identity_namespace,
                        alias,
                        scope_type,
                        scope_id,
                        safe_limit,
                    ),
                )
            rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]

    async def record_aliases(
        self,
        identity_namespace: str,
        stable_user_id: str,
        aliases: Iterable[tuple[str, str, str]],
        created_at: float | None = None,
    ) -> int:
        """在一个事务中幂等写入已验证的作用域别名。"""

        connection = self._require_connection()
        alias_rows = [
            (identity_namespace, stable_user_id, scope_type, scope_id, alias)
            for scope_type, scope_id, alias in aliases
            if isinstance(scope_type, str)
            and isinstance(scope_id, str)
            and isinstance(alias, str)
            and alias
        ]
        if not alias_rows:
            return 0
        timestamp = float(created_at) if created_at is not None else 0.0
        async with self._write_lock:
            try:
                await connection.execute("BEGIN IMMEDIATE")
                cursor = await connection.executemany(
                    """
                    INSERT OR IGNORE INTO identity_aliases
                    (identity_namespace, stable_user_id, scope_type, scope_id, alias, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [row + (timestamp,) for row in alias_rows],
                )
                await connection.commit()
                return cursor.rowcount if cursor.rowcount >= 0 else 0
            except BaseException:
                with suppress(Exception):
                    await connection.rollback()
                raise

    async def merge_observation(
        self,
        identity: ResolvedIdentity,
        merger: IdentityMerger,
    ) -> StoredIdentity:
        """在单事务内读取当前值、合并 Service 计划并写入身份与别名。"""

        connection = self._require_connection()
        if (
            not identity.identity_namespace
            or not identity.stable_user_id
            or not identity.canonical_user_id
            or not identity.scope_type
            or identity.scope_id is None
        ):
            raise ValueError("身份快照缺少持久化所需字段")

        async with self._write_lock:
            try:
                await connection.execute("BEGIN IMMEDIATE")
                current = await self._fetch_identity(
                    connection,
                    identity.identity_namespace,
                    identity.stable_user_id,
                    identity.scope_type,
                    identity.scope_id,
                )
                mutation = merger(current)
                if current is None:
                    await connection.execute(
                        """
                        INSERT INTO identity_users
                        (identity_namespace, stable_user_id, canonical_user_id,
                         global_name, first_seen_at, last_seen_at, global_name_updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identity.identity_namespace,
                            identity.stable_user_id,
                            identity.canonical_user_id,
                            mutation.global_name
                            if mutation.global_name_changed
                            else None,
                            identity.observed_at,
                            identity.observed_at,
                            mutation.global_name_updated_at
                            if mutation.global_name_changed
                            else None,
                        ),
                    )
                else:
                    if current.canonical_user_id != identity.canonical_user_id:
                        raise ValueError("稳定身份对应的 canonical user ID 不一致")
                    await connection.execute(
                        """
                        UPDATE identity_users
                        SET canonical_user_id = ?,
                            global_name = CASE WHEN ? THEN ? ELSE global_name END,
                            global_name_updated_at = CASE
                                WHEN ? THEN ? ELSE global_name_updated_at END,
                            first_seen_at = ?,
                            last_seen_at = ?
                        WHERE identity_namespace = ? AND stable_user_id = ?
                        """,
                        (
                            identity.canonical_user_id,
                            int(mutation.global_name_changed),
                            mutation.global_name,
                            int(mutation.global_name_changed),
                            mutation.global_name_updated_at,
                            min(current.first_seen_at, identity.observed_at),
                            max(current.last_seen_at, identity.observed_at),
                            identity.identity_namespace,
                            identity.stable_user_id,
                        ),
                    )
                scope_current = current
                if (
                    scope_current is None
                    or scope_current.scope_type != identity.scope_type
                ):
                    scope_name = (
                        mutation.scope_name if mutation.scope_name_changed else None
                    )
                    scope_updated_at = (
                        mutation.scope_name_updated_at
                        if mutation.scope_name_changed
                        else None
                    )
                    await connection.execute(
                        """
                        INSERT INTO identity_scope_members
                        (identity_namespace, stable_user_id, scope_type, scope_id,
                         scope_name, first_seen_at, last_seen_at, scope_name_updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(identity_namespace, stable_user_id, scope_type, scope_id)
                        DO UPDATE SET
                            scope_name = excluded.scope_name,
                            first_seen_at = MIN(
                                identity_scope_members.first_seen_at,
                                excluded.first_seen_at
                            ),
                            last_seen_at = MAX(
                                identity_scope_members.last_seen_at,
                                excluded.last_seen_at
                            ),
                            scope_name_updated_at = excluded.scope_name_updated_at
                        """,
                        (
                            identity.identity_namespace,
                            identity.stable_user_id,
                            identity.scope_type,
                            identity.scope_id,
                            scope_name,
                            identity.observed_at,
                            identity.observed_at,
                            scope_updated_at,
                        ),
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE identity_scope_members
                        SET scope_name = CASE WHEN ? THEN ? ELSE scope_name END,
                            scope_name_updated_at = CASE
                                WHEN ? THEN ? ELSE scope_name_updated_at END,
                            first_seen_at = MIN(first_seen_at, ?),
                            last_seen_at = MAX(last_seen_at, ?)
                        WHERE identity_namespace = ? AND stable_user_id = ?
                          AND scope_type = ? AND scope_id = ?
                        """,
                        (
                            int(mutation.scope_name_changed),
                            mutation.scope_name,
                            int(mutation.scope_name_changed),
                            mutation.scope_name_updated_at,
                            identity.observed_at,
                            identity.observed_at,
                            identity.identity_namespace,
                            identity.stable_user_id,
                            identity.scope_type,
                            identity.scope_id,
                        ),
                    )

                if mutation.aliases:
                    await connection.executemany(
                        """
                        INSERT OR IGNORE INTO identity_aliases
                        (identity_namespace, stable_user_id, scope_type, scope_id, alias, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                identity.identity_namespace,
                                identity.stable_user_id,
                                scope_type,
                                scope_id,
                                alias,
                                identity.observed_at,
                            )
                            for scope_type, scope_id, alias in mutation.aliases
                        ],
                    )

                result = await self._fetch_identity(
                    connection,
                    identity.identity_namespace,
                    identity.stable_user_id,
                    identity.scope_type,
                    identity.scope_id,
                )
                await connection.commit()
                if result is None:
                    raise RuntimeError("身份观察提交后无法读取结果")
                return result
            except BaseException:
                with suppress(Exception):
                    await connection.rollback()
                raise

    async def _fetch_identity(
        self,
        connection: aiosqlite.Connection,
        identity_namespace: str,
        stable_user_id: str,
        scope_type: str | None,
        scope_id: str | None,
    ) -> StoredIdentity | None:
        """在给定连接快照中读取身份与作用域成员。"""

        cursor = await connection.execute(
            """
            SELECT u.identity_namespace, u.stable_user_id, u.canonical_user_id,
                   u.global_name, u.first_seen_at, u.last_seen_at,
                   u.global_name_updated_at,
                   s.scope_type, s.scope_id, s.scope_name,
                   s.scope_name_updated_at
            FROM identity_users u
            LEFT JOIN identity_scope_members s
              ON s.identity_namespace = u.identity_namespace
             AND s.stable_user_id = u.stable_user_id
             AND s.scope_type = ? AND s.scope_id = ?
            WHERE u.identity_namespace = ? AND u.stable_user_id = ?
            """,
            (
                scope_type or "",
                scope_id or "",
                identity_namespace,
                stable_user_id,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        admin_display_name = await self._admin_display_name(connection, row[2])
        scope_name = row[9] if len(row) > 9 else None
        display_name = admin_display_name or scope_name or row[3] or row[2]
        return StoredIdentity(
            identity_namespace=str(row[0]),
            stable_user_id=str(row[1]),
            canonical_user_id=str(row[2]),
            global_name=row[3],
            scope_type=row[7] if len(row) > 7 else None,
            scope_id=row[8] if len(row) > 8 else None,
            scope_name=scope_name,
            display_name=str(display_name),
            first_seen_at=float(row[4]),
            last_seen_at=float(row[5]),
            global_name_updated_at=(float(row[6]) if row[6] is not None else None),
            scope_name_updated_at=(
                float(row[10]) if len(row) > 10 and row[10] is not None else None
            ),
            admin_display_name=admin_display_name,
        )

    async def _admin_display_name(
        self,
        connection: aiosqlite.Connection,
        canonical_user_id: str,
    ) -> str | None:
        """读取已存在的管理员画像备注，不创建或修改画像表。"""

        cursor = await connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            ("user_profiles",),
        )
        if await cursor.fetchone() is None:
            return None
        cursor = await connection.execute(
            "SELECT display_name FROM user_profiles WHERE user_id = ?",
            (canonical_user_id,),
        )
        row = await cursor.fetchone()
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            return None
        return row[0].strip()

    def _require_connection(self) -> aiosqlite.Connection:
        """返回已初始化连接，否则抛出稳定运行时错误。"""

        if self._connection is None:
            raise RuntimeError("ProtocolIdentityStore 未初始化")
        return self._connection
