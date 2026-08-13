"""MemoryExporter 和 MemoryImporter 测试 — JSONL / Markdown 导出和导入。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.memory_exporter import (
    MemoryExporter,
    MemoryImporter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_memories() -> list[dict]:
    return [
        {
            "id": "mem-1",
            "text": "这是一条测试记忆",
            "content": "这是一条测试记忆",
            "metadata": {
                "topics": ["测试", "记忆"],
                "emotion_tags": ["neutral"],
                "importance": 0.75,
                "create_time": 1700000000.0,
            },
        },
        {
            "id": "mem-2",
            "text": "第二段记忆内容",
            "content": "第二段记忆内容",
            "metadata": {
                "topics": ["示例"],
                "emotion_tags": ["happy"],
                "importance": 0.60,
                "create_time": 1700001000.0,
            },
        },
    ]


# ---------------------------------------------------------------------------
# MemoryExporter - JSONL
# ---------------------------------------------------------------------------


class TestExporterJsonl:
    """JSONL export format and output validation."""

    @pytest.mark.asyncio
    async def test_export_empty(self, tmp_path: Path) -> None:
        cb = AsyncMock(return_value=[])
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "empty.jsonl")
        count = await exporter.export_jsonl(output)
        assert count == 0
        assert os.path.exists(output)

    @pytest.mark.asyncio
    async def test_export_jsonl_with_memories(self, tmp_path: Path) -> None:
        memories = _sample_memories()
        cb = AsyncMock(return_value=memories)
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "export.jsonl")
        count = await exporter.export_jsonl(output)
        assert count == 2
        assert os.path.exists(output)
        # Read back and verify each line is valid JSON
        lines = Path(output).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            record = json.loads(line)
            assert "id" in record
            assert "content" in record
            assert "metadata" in record
            assert "exported_at" in record

    @pytest.mark.asyncio
    async def test_export_jsonl_filters_by_session(self, tmp_path: Path) -> None:
        cb = AsyncMock(return_value=_sample_memories())
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "session.jsonl")
        await exporter.export_jsonl(output, session_id="sess-1")
        # The callback should be called with the session_id
        cb.assert_called_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_export_jsonl_no_callback(self, tmp_path: Path) -> None:
        exporter = MemoryExporter()  # no callback
        output = str(tmp_path / "no_cb.jsonl")
        count = await exporter.export_jsonl(output)
        assert count == 0

    @pytest.mark.asyncio
    async def test_export_jsonl_nested_dirs(self, tmp_path: Path) -> None:
        cb = AsyncMock(return_value=[])
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "nested" / "dirs" / "export.jsonl")
        count = await exporter.export_jsonl(output)
        assert count == 0
        assert os.path.exists(output)


# ---------------------------------------------------------------------------
# MemoryExporter - Markdown
# ---------------------------------------------------------------------------


class TestExporterMarkdown:
    """Markdown export format and output validation."""

    @pytest.mark.asyncio
    async def test_export_markdown_with_memories(self, tmp_path: Path) -> None:
        memories = _sample_memories()
        cb = AsyncMock(return_value=memories)
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "export.md")
        count = await exporter.export_markdown(output)
        assert count == 2
        content = Path(output).read_text(encoding="utf-8")
        assert "# Memora Export" in content
        assert "Count: 2" in content
        assert "## Memory #1" in content
        assert "## Memory #2" in content
        assert "这是一条测试记忆" in content
        assert "**Importance**: 0.75" in content
        assert "**Topics**: 测试, 记忆" in content
        assert "**Emotions**: neutral" in content
        assert "第二段记忆内容" in content

    @pytest.mark.asyncio
    async def test_export_markdown_empty(self, tmp_path: Path) -> None:
        cb = AsyncMock(return_value=[])
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "empty.md")
        count = await exporter.export_markdown(output)
        assert count == 0

    @pytest.mark.asyncio
    async def test_export_markdown_filters_by_session(self, tmp_path: Path) -> None:
        cb = AsyncMock(return_value=_sample_memories())
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "sess.md")
        await exporter.export_markdown(output, session_id="sess-2")
        cb.assert_called_once_with("sess-2")


# ---------------------------------------------------------------------------
# MemoryExporter - edge cases
# ---------------------------------------------------------------------------


class TestExporterEdgeCases:
    """Edge cases for exporter."""

    @pytest.mark.asyncio
    async def test_memory_without_text_field(self, tmp_path: Path) -> None:
        mem = [{"id": "x", "content": "only content", "metadata": {}}]
        cb = AsyncMock(return_value=mem)
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "edge.jsonl")
        await exporter.export_jsonl(output)
        lines = Path(output).read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["content"] == "only content"

    @pytest.mark.asyncio
    async def test_memory_without_content_nor_text(self, tmp_path: Path) -> None:
        mem = [{"id": "x", "metadata": {}}]
        cb = AsyncMock(return_value=mem)
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "no_content.jsonl")
        await exporter.export_jsonl(output)
        lines = Path(output).read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["content"] == ""

    @pytest.mark.asyncio
    async def test_markdown_without_metadata_keys(self, tmp_path: Path) -> None:
        mem = [{"id": "x", "text": "bare content"}]
        cb = AsyncMock(return_value=mem)
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "bare.md")
        count = await exporter.export_markdown(output)
        assert count == 1
        content = Path(output).read_text(encoding="utf-8")
        assert "bare content" in content

    @pytest.mark.asyncio
    async def test_markdown_handles_none_metadata(self, tmp_path: Path) -> None:
        mem = [{"id": "x", "text": "bare", "metadata": None}]
        cb = AsyncMock(return_value=mem)
        exporter = MemoryExporter(get_all_memories_cb=cb)
        output = str(tmp_path / "none_meta.md")
        count = await exporter.export_markdown(output)
        assert count == 1


# ---------------------------------------------------------------------------
# MemoryImporter - content hash
# ---------------------------------------------------------------------------


class TestImporterContentHash:
    """Content hashing for dedup."""

    def test_hash_deterministic(self) -> None:
        h1 = MemoryImporter._content_hash("hello world")
        h2 = MemoryImporter._content_hash("hello world")
        assert h1 == h2

    def test_hash_strips_whitespace(self) -> None:
        h1 = MemoryImporter._content_hash("  hello  ")
        h2 = MemoryImporter._content_hash("hello")
        assert h1 == h2

    def test_hash_different_content(self) -> None:
        h1 = MemoryImporter._content_hash("content a")
        h2 = MemoryImporter._content_hash("content b")
        assert h1 != h2

    def test_hash_length(self) -> None:
        h = MemoryImporter._content_hash("something")
        assert len(h) == 16  # first 16 chars of sha256 hex


# ---------------------------------------------------------------------------
# MemoryImporter - import_jsonl
# ---------------------------------------------------------------------------


class TestImporterImportJsonl:
    """JSONL import with dry run, dedup, and error handling."""

    @pytest.mark.asyncio
    async def test_import_file_not_found(self) -> None:
        imp = MemoryImporter()
        with pytest.raises(FileNotFoundError):
            await imp.import_jsonl("/nonexistent/path.jsonl")

    @pytest.mark.asyncio
    async def test_import_empty_file(self, tmp_path: Path) -> None:
        input_path = tmp_path / "empty.jsonl"
        input_path.write_text("", encoding="utf-8")
        imp = MemoryImporter()
        result = await imp.import_jsonl(str(input_path))
        assert result["total"] == 0
        assert result["imported"] == 0
        assert result["skipped_duplicate"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_import_dry_run(self, tmp_path: Path) -> None:
        records = [
            {"id": "r1", "content": "记忆一"},
            {"id": "r2", "content": "记忆二"},
        ]
        input_path = tmp_path / "test.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        imp = MemoryImporter()
        result = await imp.import_jsonl(str(input_path), dry_run=True)
        assert result["total"] == 2
        assert result["imported"] == 2
        assert result["skipped_duplicate"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_import_dry_run_dedup(self, tmp_path: Path) -> None:
        records = [
            {"id": "r1", "content": "same content"},
            {"id": "r2", "content": "same content"},  # duplicate
        ]
        input_path = tmp_path / "dedup.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        imp = MemoryImporter()
        result = await imp.import_jsonl(str(input_path), dry_run=True)
        # First record imported, second is duplicate
        assert result["total"] == 2
        assert result["imported"] == 1
        assert result["skipped_duplicate"] == 1

    @pytest.mark.asyncio
    async def test_import_skips_empty_content(self, tmp_path: Path) -> None:
        records = [
            {"id": "r1", "content": ""},
            {"id": "r2", "content": "  "},
            {"id": "r3", "content": "valid content"},
        ]
        input_path = tmp_path / "empty_content.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        imp = MemoryImporter()
        result = await imp.import_jsonl(str(input_path), dry_run=True)
        assert result["total"] == 3
        assert result["imported"] == 1  # only the valid one
        assert result["errors"] == 2

    @pytest.mark.asyncio
    async def test_import_skip_malformed_lines(self, tmp_path: Path) -> None:
        input_path = tmp_path / "malformed.jsonl"
        input_path.write_text(
            '{"content": "good"}\nnot valid json\n{"content": "also good"}\n',
            encoding="utf-8",
        )
        imp = MemoryImporter()
        result = await imp.import_jsonl(str(input_path), dry_run=True)
        assert result["total"] == 2  # only the valid JSON lines
        assert result["imported"] == 2

    @pytest.mark.asyncio
    async def test_import_with_callbacks(self, tmp_path: Path) -> None:
        add_mock = AsyncMock()
        search_mock = AsyncMock(return_value=[])  # no existing memories
        imp = MemoryImporter(
            add_memory_cb=add_mock,
            search_memories_cb=search_mock,
        )
        records = [
            {"id": "r1", "content": "test content", "metadata": {"importance": 0.8}},
        ]
        input_path = tmp_path / "with_cb.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        result = await imp.import_jsonl(
            str(input_path), session_id="sess", persona_id="pers"
        )
        assert result["imported"] == 1
        add_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_dedup_against_existing(self, tmp_path: Path) -> None:
        # Create a mock search that returns an existing memory matching the import
        existing = MagicMock()
        existing.content = "already present"

        search_mock = AsyncMock(return_value=[existing])
        add_mock = AsyncMock()
        imp = MemoryImporter(
            add_memory_cb=add_mock,
            search_memories_cb=search_mock,
        )
        records = [
            {"id": "r1", "content": "already present"},
            {"id": "r2", "content": "new content"},
        ]
        input_path = tmp_path / "existing.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        result = await imp.import_jsonl(str(input_path))
        assert result["imported"] == 1  # only the new one
        assert result["skipped_duplicate"] == 1

    @pytest.mark.asyncio
    async def test_import_applies_metadata_baggage(self, tmp_path: Path) -> None:
        add_mock = AsyncMock()
        imp = MemoryImporter(add_memory_cb=add_mock)
        records = [{"id": "src-1", "content": "test", "metadata": {"importance": 0.9}}]
        input_path = tmp_path / "meta.jsonl"
        input_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        await imp.import_jsonl(str(input_path), session_id="sid", persona_id="pid")
        # Check call args passed to add_memory_cb
        call_kwargs = add_mock.call_args.kwargs
        assert call_kwargs["session_id"] == "sid"
        assert call_kwargs["persona_id"] == "pid"
        assert call_kwargs["importance"] == 0.9
        assert "imported_at" in call_kwargs["metadata"]
        assert call_kwargs["metadata"]["import_source_id"] == "src-1"
