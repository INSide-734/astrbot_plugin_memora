"""KnowledgeStore 测试 — 插入、获取、搜索、列出、更新、删除。"""

import pytest

from core.features.knowledge.domain import KnowledgeEntry, KnowledgeType
from core.features.knowledge.infrastructure import KnowledgeStore
from core.shared.list_sorting import SortQuery


def _make_entry(**overrides) -> KnowledgeEntry:
    defaults = dict(
        title="Test Knowledge",
        content="This is test knowledge content.",
        category=KnowledgeType.FACT,
        confidence=0.8,
        source_ids=[1, 2],
        tags=["test", "knowledge"],
    )
    defaults.update(overrides)
    return KnowledgeEntry(**defaults)  # type: ignore[arg-type]


class TestKnowledgeStoreCRUD:
    """验证基础增删改查操作。"""

    @pytest.mark.asyncio
    async def test_insert_and_get(self, tmp_db_path):
        """插入知识条目后应能按 ID 读取。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        entry = _make_entry(title="西湖知识", content="西湖位于杭州")
        entry_id = await store.insert(entry)
        assert entry_id > 0

        fetched = await store.get(entry_id)
        assert fetched is not None
        assert fetched.title == "西湖知识"
        assert fetched.content == "西湖位于杭州"
        assert fetched.category == KnowledgeType.FACT

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, tmp_db_path):
        """读取不存在的条目应返回 None。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()
        assert await store.get(99999) is None

    @pytest.mark.asyncio
    async def test_update(self, tmp_db_path):
        """更新应保存标题、正文、分类和访问次数。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        entry = _make_entry(title="Original")
        entry_id = await store.insert(entry)
        entry.entry_id = entry_id  # 回填 ID 以执行更新。

        entry.title = "Updated"
        entry.content = "New content"
        entry.category = KnowledgeType.CONCEPT
        entry.access_count = 5
        await store.update(entry)

        fetched = await store.get(entry_id)
        assert fetched is not None
        assert fetched.title == "Updated"
        assert fetched.content == "New content"
        assert fetched.category == KnowledgeType.CONCEPT
        assert fetched.access_count == 5

    @pytest.mark.asyncio
    async def test_delete(self, tmp_db_path):
        """删除条目后按 ID 读取应返回 None。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        entry_id = await store.insert(_make_entry())
        assert await store.delete(entry_id)
        assert await store.get(entry_id) is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, tmp_db_path):
        """删除不存在的条目应返回 False。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()
        assert not await store.delete(99999)

    @pytest.mark.asyncio
    async def test_count(self, tmp_db_path):
        """count 应返回知识条目总数。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()
        assert await store.count() == 0

        await store.insert(_make_entry())
        await store.insert(_make_entry())
        assert await store.count() == 2


class TestKnowledgeStoreSearch:
    """验证搜索、排序和分页操作。"""

    @pytest.mark.asyncio
    async def test_search_finds_by_keyword(self, tmp_db_path):
        """search 应返回标题或正文命中关键词的条目。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        await store.insert(
            _make_entry(title="Python编程", content="Python是流行的编程语言")
        )
        await store.insert(
            _make_entry(title="Java开发", content="Java是面向对象的语言")
        )

        results, total = await store.search("Python", limit=10)
        assert len(results) >= 1
        assert any("Python" in r.title for r in results)

    @pytest.mark.asyncio
    async def test_search_by_category(self, tmp_db_path):
        """传入分类时 search 应过滤其他分类。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        await store.insert(_make_entry(title="Fact1", category=KnowledgeType.FACT))
        await store.insert(_make_entry(title="Rule1", category=KnowledgeType.RULE))

        results, total = await store.search("", category="rule", limit=10)
        assert all(r.category == KnowledgeType.RULE for r in results)

    @pytest.mark.asyncio
    async def test_search_no_match(self, tmp_db_path):
        """没有命中时 search 应返回空结果。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        results, total = await store.search("不存在的关键词XYZ", limit=10)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_list_entries(self, tmp_db_path):
        """list_entries 应返回分页结果和总数。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        for i in range(5):
            await store.insert(_make_entry(title=f"Entry {i}"))

        results, total = await store.list_entries(limit=3, offset=0)
        assert len(results) == 3
        assert total == 5

    @pytest.mark.asyncio
    async def test_list_entries_by_category(self, tmp_db_path):
        """list_entries 应按分类过滤。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        await store.insert(_make_entry(title="F1", category=KnowledgeType.FACT))
        await store.insert(_make_entry(title="F2", category=KnowledgeType.FACT))
        await store.insert(_make_entry(title="R1", category=KnowledgeType.RULE))

        results, total = await store.list_entries(category="fact")
        assert total == 2
        assert all(r.category == KnowledgeType.FACT for r in results)

    @pytest.mark.asyncio
    async def test_list_entries_sorts_before_pagination(self, tmp_db_path):
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        for title in ("Gamma", "Alpha", "Beta"):
            await store.insert(_make_entry(title=title))
        async with store._connect() as db:
            await db.execute("UPDATE knowledge_entries SET updated_at = 100")
            await db.commit()

        results, total = await store.list_entries(
            limit=2,
            offset=0,
            sort=SortQuery("title", "asc"),
        )

        assert [entry.title for entry in results] == ["Alpha", "Beta"]
        assert total == 3

    @pytest.mark.asyncio
    async def test_list_entries_uses_id_as_the_stable_tie_breaker(self, tmp_db_path):
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        first_id = await store.insert(_make_entry(title="Same title"))
        second_id = await store.insert(_make_entry(title="Same title"))

        results, _ = await store.list_entries(
            sort=SortQuery("title", "asc"),
        )

        assert [entry.entry_id for entry in results] == [first_id, second_id]

    @pytest.mark.asyncio
    async def test_search_sorts_matches_before_limit(self, tmp_db_path):
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        for title, confidence in (
            ("Low event", 0.2),
            ("High event", 0.9),
            ("Mid event", 0.6),
        ):
            await store.insert(_make_entry(title=title, confidence=confidence))

        results, _ = await store.search(
            "event",
            limit=2,
            sort=SortQuery("confidence", "desc"),
        )

        assert [entry.confidence for entry in results] == [0.9, 0.6]


class TestKnowledgeStoreEdgeCases:
    """验证边界条件与数据完整性。"""

    @pytest.mark.asyncio
    async def test_insert_preserves_source_ids_and_tags(self, tmp_db_path):
        """插入应完整保留 source_ids 与 tags 数组。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        entry = _make_entry(source_ids=[10, 20, 30], tags=["tag-a", "tag-b"])
        entry_id = await store.insert(entry)

        fetched = await store.get(entry_id)
        assert fetched is not None
        assert fetched.source_ids == [10, 20, 30]
        assert fetched.tags == ["tag-a", "tag-b"]

    @pytest.mark.asyncio
    async def test_insert_with_different_categories(self, tmp_db_path):
        """所有知识分类都应能够插入和读取。"""
        store = KnowledgeStore(tmp_db_path)
        await store.init_table()

        for kt in KnowledgeType:
            entry = _make_entry(title=f"Type_{kt.value}", category=kt)
            eid = await store.insert(entry)
            fetched = await store.get(eid)
            assert fetched is not None
            assert fetched.category == kt
