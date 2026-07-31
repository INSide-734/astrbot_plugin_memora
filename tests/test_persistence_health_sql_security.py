"""持久化健康检查 SQL 标识符边界测试。"""

from __future__ import annotations

import aiosqlite
import pytest

from core.validators.persistence_health_validator import PersistenceHealthValidator


@pytest.mark.asyncio
async def test_ids_rejects_unapproved_sql_identifiers(tmp_db_path: str) -> None:
    """动态表名、列名或谓词不得进入健康检查 SQL。"""

    validator = PersistenceHealthValidator(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        with pytest.raises(ValueError, match="不支持的持久化健康检查 ID 查询"):
            await validator._ids(db, "documents; DROP TABLE documents", "id")
        with pytest.raises(ValueError, match="不支持的持久化健康检查 ID 查询"):
            await validator._ids(db, "documents", "id OR 1=1")
        with pytest.raises(ValueError, match="不支持的持久化健康检查 ID 查询"):
            await validator._ids(db, "documents", "id", where="1=1; SELECT 1")


@pytest.mark.asyncio
async def test_count_rows_rejects_unapproved_table(tmp_db_path: str) -> None:
    """计数助手只接受内部固定表。"""

    validator = PersistenceHealthValidator(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        with pytest.raises(ValueError, match="不支持的持久化健康检查计数查询"):
            await validator._count_rows(db, "memory_atoms; DROP TABLE memory_atoms")
