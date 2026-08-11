"""验证领域派生对象对 canonical source 的 revision 与作用域约束。"""

from __future__ import annotations

import json
import time

import aiosqlite
import pytest

from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.managers.atom_source_binding import bind_atoms_to_canonical_source
from core.retrieval.atom_retriever import AtomRetriever
from core.storage.atom_store import AtomStore


async def _create_document(
    db_path: str,
    *,
    memory_id: int = 17,
    revision: str = "rev-17",
    scope_key: str = "private:user-a",
    privacy_level: str = "confidential",
) -> None:
    """创建 Atom 完整性测试使用的匿名 canonical 文档。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT OR REPLACE INTO documents
               (id,text,metadata,created_at,updated_at) VALUES(?,?,?,?,?)""",
            (
                memory_id,
                "匿名 canonical 正文",
                json.dumps(
                    {
                        "scope_key": scope_key,
                        "privacy_level": privacy_level,
                    },
                    ensure_ascii=False,
                ),
                "2026-07-21T00:00:00+00:00",
                revision,
            ),
        )
        await db.commit()


def _source_bound_atom(**overrides) -> MemoryAtom:
    """构造绑定 canonical revision 的 MemoryAtom。"""

    defaults = {
        "parent_memory_id": 17,
        "parent_revision": "rev-17",
        "parent_scope_key": "private:user-a",
        "parent_privacy_level": "confidential",
        "atom_type": AtomType.FACTUAL,
        "content": "数据库使用 SQLite",
        "importance": 0.8,
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return MemoryAtom(**defaults)


@pytest.mark.asyncio
async def test_atom_parent_reference_round_trips(tmp_db_path: str) -> None:
    """新 Atom 必须完整保存创建时的父 revision、scope 与 privacy。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    atom_id = await store.insert(_source_bound_atom())

    stored = await store.get(atom_id)

    assert stored is not None
    assert stored.parent_revision == "rev-17"
    assert stored.parent_scope_key == "private:user-a"
    assert stored.parent_privacy_level == "confidential"


@pytest.mark.asyncio
async def test_atom_retriever_drops_stale_parent_revision(
    tmp_db_path: str,
) -> None:
    """父 revision 变化后只丢弃 Atom 信号，不创建其他召回身份。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await _create_document(tmp_db_path)
    await store.insert(_source_bound_atom())
    retriever = AtomRetriever(store)

    current = await retriever.search("数据库", k=5)
    assert len(current) == 1
    assert current[0].parent_memory_id == 17

    await _create_document(tmp_db_path, revision="rev-18")

    assert await retriever.search("数据库", k=5) == []


@pytest.mark.asyncio
async def test_atom_retriever_drops_deleted_parent(tmp_db_path: str) -> None:
    """父文档删除后 Atom 不可读，内部 atom_id 不得成为替代 doc_id。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await _create_document(tmp_db_path)
    atom_id = await store.insert(_source_bound_atom())
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute("DELETE FROM documents WHERE id = 17")
        await db.commit()

    results = await AtomRetriever(store).search("数据库", k=5)

    assert results == []
    assert atom_id > 0


@pytest.mark.asyncio
async def test_legacy_atom_without_parent_provenance_is_not_recalled(
    tmp_db_path: str,
) -> None:
    """旧行缺少父来源字段时保持可维护，但不得主动召回。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    legacy_id = await store.insert(
        MemoryAtom(
            parent_memory_id=17,
            atom_type=AtomType.FACTUAL,
            content="旧版原子内容",
        )
    )
    await _create_document(tmp_db_path)

    stored = await store.get_raw(legacy_id)
    public_value = await store.get(legacy_id)
    raw_children = await store.get_by_parent_raw(17)
    public_children = await store.get_by_parent(17)
    results = await AtomRetriever(store).search("旧版", k=5)

    assert stored is not None
    assert stored.parent_revision is None
    assert stored.parent_scope_key is None
    assert stored.parent_privacy_level is None
    assert public_value is None
    assert [atom.atom_id for atom in raw_children] == [legacy_id]
    assert public_children == []
    assert results == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "forged-session"),
        ("persona_id", "forged-persona"),
    ],
)
async def test_atom_insert_rejects_forged_session_or_persona(
    tmp_db_path: str,
    field: str,
    value: str,
) -> None:
    """Atom 的 session/persona 必须来自 canonical metadata。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT INTO documents
               (id,text,metadata,created_at,updated_at) VALUES(?,?,?,?,?)""",
            (
                17,
                "匿名 canonical 正文",
                json.dumps(
                    {
                        "scope_key": "private:user-a",
                        "privacy_level": "confidential",
                        "session_id": "session-a",
                        "persona_id": "persona-a",
                    },
                    ensure_ascii=False,
                ),
                "2026-07-21T00:00:00+00:00",
                "rev-17",
            ),
        )
        await db.commit()
    atom = _source_bound_atom(session_id="session-a", persona_id="persona-a")
    setattr(atom, field, value)

    with pytest.raises(ValueError, match="source_scope_mismatch"):
        await store.insert(atom)

    assert await store.count_atoms() == 0


@pytest.mark.asyncio
async def test_atom_store_migrates_parent_provenance_columns(
    tmp_db_path: str,
) -> None:
    """旧 memory_atoms 表可重复补齐父来源列，且不伪造字段值。"""

    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            """CREATE TABLE memory_atoms (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   parent_memory_id INTEGER NOT NULL,
                   atom_type TEXT NOT NULL DEFAULT 'unknown',
                   content TEXT NOT NULL,
                   entities TEXT DEFAULT '[]',
                   importance REAL NOT NULL DEFAULT 0.5,
                   confidence REAL NOT NULL DEFAULT 0.7,
                   created_at REAL NOT NULL,
                   last_accessed_at REAL NOT NULL,
                   last_reinforced_at REAL,
                   event_time REAL,
                   ttl_days REAL NOT NULL DEFAULT 30.0,
                   expires_at REAL NOT NULL,
                   status TEXT NOT NULL DEFAULT 'active',
                   reinforcement_count INTEGER NOT NULL DEFAULT 0,
                   decay_type TEXT NOT NULL DEFAULT 'exponential',
                   session_id TEXT,
                   persona_id TEXT,
                   metadata TEXT DEFAULT '{}'
               )"""
        )
        await db.commit()

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await store.initialize()

    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("PRAGMA table_info(memory_atoms)")
        columns = {str(row[1]) for row in await cursor.fetchall()}

    assert {
        "parent_revision",
        "parent_scope_key",
        "parent_privacy_level",
    }.issubset(columns)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("parent_revision", "stale", "source_revision_mismatch"),
        ("parent_scope_key", "group:other", "source_scope_mismatch"),
        ("parent_privacy_level", "shared", "source_privacy_mismatch"),
    ],
)
async def test_atom_insert_rejects_mismatched_parent_source(
    tmp_db_path: str,
    field: str,
    value: str,
    reason: str,
) -> None:
    """canonical 表存在时，Atom 写入必须拒绝伪造的父快照。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await _create_document(tmp_db_path)
    atom = _source_bound_atom(**{field: value})

    with pytest.raises(ValueError, match=reason):
        await store.insert(atom)
    assert await store.count_atoms() == 0


@pytest.mark.asyncio
async def test_atom_insert_many_is_atomic_on_parent_source_failure(
    tmp_db_path: str,
) -> None:
    """批量 Atom 任一父来源不匹配时，整批不得部分提交。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await _create_document(tmp_db_path)

    with pytest.raises(ValueError, match="source_revision_mismatch"):
        await store.insert_many(
            [
                _source_bound_atom(content="有效"),
                _source_bound_atom(content="陈旧", parent_revision="stale"),
            ]
        )
    assert await store.count_atoms() == 0


def test_atom_binding_requires_current_canonical_revision() -> None:
    """绑定辅助函数只接受带稳定 ID 与 revision 的 canonical 快照。"""

    atom = MemoryAtom(parent_memory_id=0, content="事实")
    bound = bind_atoms_to_canonical_source(
        [atom],
        {
            "id": 17,
            "updated_at": "rev-17",
            "metadata": {
                "scope_key": "private:user-a",
                "privacy_level": "confidential",
            },
        },
    )

    assert bound[0].parent_memory_id == 17
    assert bound[0].parent_revision == "rev-17"
    assert bound[0].parent_scope_key == "private:user-a"
    assert bound[0].parent_privacy_level == "confidential"


@pytest.mark.asyncio
async def test_planned_query_drops_stale_and_confidential_group_atoms(
    tmp_db_path: str,
) -> None:
    """前瞻查询复用父来源校验，并遵守群聊隐私边界。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await _create_document(tmp_db_path)
    atom = _source_bound_atom(
        atom_type=AtomType.PLANNED,
        content="明天开会",
        event_time=time.time() + 3600,
    )
    await store.insert(atom)

    assert await store.query_upcoming_planned(chat_type="group") == []
    private_results = await store.query_upcoming_planned(chat_type="private")
    assert len(private_results) == 1

    await _create_document(tmp_db_path, revision="rev-18")
    assert await store.query_upcoming_planned(chat_type="private") == []
