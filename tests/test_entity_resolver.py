"""entity_resolver.py 测试 — EntityResolver。"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.processors.entity_resolver import EntityResolver


class TestCanonicalize:
    def test_normalizes_ascii_to_lower(self) -> None:
        assert EntityResolver.canonicalize("Hello World") == "hello world"

    def test_chinese_preserves_case(self) -> None:
        # Chinese characters are not lowercased
        result = EntityResolver.canonicalize("旺财")
        assert result == "旺财"

    def test_strips_edge_punctuation(self) -> None:
        assert EntityResolver.canonicalize("  ,Hello!  ") == "hello"

    def test_strips_chinese_punctuation(self) -> None:
        result = EntityResolver.canonicalize("，你好！")
        assert result == "你好"

    def test_empty_string(self) -> None:
        assert EntityResolver.canonicalize("") == ""

    def test_collapses_whitespace(self) -> None:
        assert EntityResolver.canonicalize("hello   world") == "hello world"


class TestDedupePreserveOrder:
    def test_removes_duplicates(self) -> None:
        result = EntityResolver.dedupe_preserve_order(["Alice", "Bob", "alice"])
        assert result == ["Alice", "Bob"]

    def test_preserves_order(self) -> None:
        result = EntityResolver.dedupe_preserve_order(["Bob", "Alice", "Charlie"])
        assert result == ["Bob", "Alice", "Charlie"]

    def test_empty_list(self) -> None:
        assert EntityResolver.dedupe_preserve_order([]) == []

    def test_all_duplicates(self) -> None:
        assert EntityResolver.dedupe_preserve_order(["Alice", "Alice", "alice"]) == ["Alice"]


class TestISAHierarchy:
    """G3: IS-A entity hierarchy."""

    def setup_method(self) -> None:
        # Clear state between tests
        EntityResolver._isa_children.clear()
        EntityResolver._isa_parents.clear()

    def test_add_isa_basic(self) -> None:
        EntityResolver.add_isa("旺财", "宠物")
        assert "旺财" in EntityResolver._isa_parents

    def test_add_isa_self_reference_ignored(self) -> None:
        EntityResolver.add_isa("宠物", "宠物")
        assert "宠物" not in EntityResolver._isa_parents

    def test_add_isa_empty_values_ignored(self) -> None:
        EntityResolver.add_isa("", "parent")
        EntityResolver.add_isa("child", "")
        assert len(EntityResolver._isa_parents) == 0

    def test_expand_with_children(self) -> None:
        EntityResolver.add_isa("旺财", "宠物")
        EntityResolver.add_isa("加菲", "宠物")
        result = EntityResolver.expand_with_children("宠物")
        assert "宠物" in result
        assert "旺财" in result
        assert "加菲" in result
        assert len(result) == 3

    def test_expand_with_children_empty(self) -> None:
        result = EntityResolver.expand_with_children("")
        assert result == []

    def test_expand_with_parents(self) -> None:
        EntityResolver.add_isa("旺财", "宠物")
        EntityResolver.add_isa("宠物", "动物")
        result = EntityResolver.expand_with_parents("旺财")
        assert len(result) == 3
        assert "旺财" in result
        assert "宠物" in result
        assert "动物" in result

    def test_expand_with_parents_no_parent(self) -> None:
        result = EntityResolver.expand_with_parents("独立实体")
        assert len(result) == 1
        assert "独立实体" in result

    def test_expand_with_parents_empty(self) -> None:
        result = EntityResolver.expand_with_parents("")
        assert result == []

    def test_cycle_detection_in_parents(self) -> None:
        EntityResolver._isa_parents["a"] = "b"
        EntityResolver._isa_parents["b"] = "a"
        result = EntityResolver.expand_with_parents("a")
        assert len(result) >= 2  # Should detect cycle and not loop forever

    def test_hierarchy_stats(self) -> None:
        EntityResolver.add_isa("旺财", "宠物")
        EntityResolver.add_isa("加菲", "宠物")
        stats = EntityResolver.get_hierarchy_stats()
        assert stats["total_relations"] == 2
        assert stats["parent_count"] == 1

    @pytest.mark.asyncio
    async def test_save_and_load_hierarchy(self) -> None:
        EntityResolver.add_isa("旺财", "宠物")
        with tempfile.TemporaryDirectory() as tmpdir:
            await EntityResolver.save_hierarchy(tmpdir)
            fp = os.path.join(tmpdir, "entity_hierarchy.json")
            assert os.path.exists(fp)

            # Clear and reload
            EntityResolver._isa_children.clear()
            EntityResolver._isa_parents.clear()
            await EntityResolver.load_hierarchy(tmpdir)
            assert "旺财" in EntityResolver._isa_parents

    @pytest.mark.asyncio
    async def test_load_nonexistent_file_no_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            await EntityResolver.load_hierarchy(tmpdir)
            # Should not raise

    @pytest.mark.asyncio
    async def test_load_empty_dir_noop(self) -> None:
        await EntityResolver.load_hierarchy("")
        # Should not raise
