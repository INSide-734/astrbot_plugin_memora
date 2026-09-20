"""add 修复在来源 revision 推进后的收敛与拒绝契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.infrastructure.schema_manager import SchemaManager
from core.features.memory.infrastructure.write_op_journal import WriteOpJournal
from core.shared.contracts.events import CanonicalMemoryCommitted
from tests.fact_evidence_helpers import source_evidence


async def _create_test_schema(
    db: aiosqlite.Connection,
    journal: WriteOpJournal,
) -> None:
    """使用生产 Schema 能力创建 canonical 与写操作日志表。"""

    await SchemaManager(db).create_tables(journal.create_table)


def _stale_atom_payload(atom_content: str) -> dict:
    """构造绑定旧 revision 的暂存 Atom 修复载荷。"""

    from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
    from core.features.memory.infrastructure.write_op_serialization import (
        serialize_atom_for_repair,
    )

    stale_atom = MemoryAtom(
        parent_memory_id=42,
        parent_revision="rev-old",
        parent_scope_key="scope-a",
        parent_privacy_level="shared",
        content=atom_content,
        atom_type=AtomType.FACTUAL,
        session_id="s1",
        persona_id="p1",
        source_evidence=source_evidence(atom_content),
    )
    return serialize_atom_for_repair(stale_atom)


async def _run_add_repair(
    db: aiosqlite.Connection,
    *,
    canonical_text: str,
    payload: dict,
    atom_content: str,
) -> tuple[int, MagicMock, MagicMock, aiosqlite.Row | None]:
    """在给定 canonical 正文与暂存载荷下执行一次 add 修复。"""

    existing_doc = {
        "id": 42,
        "text": canonical_text,
        "created_at": "rev-new",
        "updated_at": "rev-new",
        "metadata": {
            "session_id": "s1",
            "persona_id": "p1",
            "scope_key": "scope-a",
            "privacy_level": "shared",
        },
    }
    mock_get = AsyncMock(return_value=existing_doc)
    mock_graph = MagicMock()
    mock_graph.index_memory = AsyncMock()
    mock_atom = MagicMock()
    mock_atom.get_by_parent = AsyncMock(return_value=[])
    mock_atom.insert_many = AsyncMock()

    journal = WriteOpJournal(
        db_connection=db,
        graph_memory_manager=mock_graph,
        atom_store=mock_atom,
        atom_enabled=True,
        get_memory_cb=mock_get,
    )
    await _create_test_schema(db, journal)

    op_id = await journal.start_op(
        "add",
        {**payload, "atoms": [_stale_atom_payload(atom_content)]},
        memory_id=42,
    )
    await db.execute(
        "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
        (op_id,),
    )
    await db.commit()

    repaired = await journal.repair_incomplete()
    cursor = await db.execute(
        "SELECT status, step FROM memory_write_ops WHERE id = ?", (op_id,)
    )
    return repaired, mock_graph, mock_atom, await cursor.fetchone()


@pytest.mark.asyncio
class TestWriteOpRepairSourceRebind:
    """覆盖 _repair_add 的过期绑定收敛分支。"""

    async def test_repair_add_rebinds_when_only_revision_advanced(
        self, tmp_db_path: str
    ) -> None:
        """正文与 scope 未变时，revision 推进只按当前 canonical 重新绑定并完成。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            existing_doc = {
                "id": 42,
                "text": "候选正文内容",
                "created_at": "rev-new",
                "updated_at": "rev-new",
                "metadata": {
                    "session_id": "s1",
                    "persona_id": "p1",
                    "scope_key": "scope-a",
                    "privacy_level": "shared",
                },
            }
            mock_get = AsyncMock(return_value=existing_doc)
            mock_graph = MagicMock()
            mock_graph.index_memory = AsyncMock()

            from core.features.memory.domain.memory_atom import AtomType, MemoryAtom

            stale_atom = MemoryAtom(
                parent_memory_id=42,
                parent_revision="rev-old",
                parent_scope_key="scope-a",
                parent_privacy_level="shared",
                content="候选正文内容",
                atom_type=AtomType.FACTUAL,
                session_id="s1",
                persona_id="p1",
                source_evidence=source_evidence("候选正文内容"),
            )
            mock_atom = MagicMock()
            mock_atom.get_by_parent = AsyncMock(return_value=[])
            mock_atom.insert_many = AsyncMock()

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=mock_graph,
                atom_store=mock_atom,
                atom_enabled=True,
                get_memory_cb=mock_get,
            )
            await _create_test_schema(db, journal)

            from core.features.memory.infrastructure.write_op_serialization import (
                serialize_atom_for_repair,
            )

            op_id = await journal.start_op(
                "add",
                {
                    "content_preview": "候选正文内容",
                    "metadata": {
                        "scope_key": "scope-a",
                        "privacy_level": "shared",
                    },
                    "atoms": [serialize_atom_for_repair(stale_atom)],
                },
                memory_id=42,
            )
            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            assert await journal.repair_incomplete() == 1

            inserted = mock_atom.insert_many.await_args.args[0]
            assert inserted[0].parent_revision == "rev-new"
            mock_graph.index_memory.assert_called_once()
            cursor = await db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert (row["status"], row["step"]) == ("completed", "completed")

    async def test_repair_add_keeps_needs_repair_when_content_changed(
        self, tmp_db_path: str
    ) -> None:
        """正文已变化时不得以旧边界收口：保持 needs_repair 且不派生图。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row

            existing_doc = {
                "id": 42,
                "text": "被并发改写后的正文",
                "created_at": "rev-new",
                "updated_at": "rev-new",
                "metadata": {
                    "session_id": "s1",
                    "persona_id": "p1",
                    "scope_key": "scope-a",
                    "privacy_level": "shared",
                },
            }
            mock_get = AsyncMock(return_value=existing_doc)
            mock_graph = MagicMock()
            mock_graph.index_memory = AsyncMock()

            from core.features.memory.domain.memory_atom import AtomType, MemoryAtom

            stale_atom = MemoryAtom(
                parent_memory_id=42,
                parent_revision="rev-old",
                parent_scope_key="scope-a",
                parent_privacy_level="shared",
                content="候选正文内容",
                atom_type=AtomType.FACTUAL,
                session_id="s1",
                persona_id="p1",
                source_evidence=source_evidence("候选正文内容"),
            )

            journal = WriteOpJournal(
                db_connection=db,
                graph_memory_manager=mock_graph,
                atom_store=None,
                get_memory_cb=mock_get,
            )
            await _create_test_schema(db, journal)

            from core.features.memory.infrastructure.write_op_serialization import (
                serialize_atom_for_repair,
            )

            op_id = await journal.start_op(
                "add",
                {
                    "content_preview": "候选正文内容",
                    "metadata": {
                        "scope_key": "scope-a",
                        "privacy_level": "shared",
                    },
                    "atoms": [serialize_atom_for_repair(stale_atom)],
                },
                memory_id=42,
            )
            await db.execute(
                "UPDATE memory_write_ops SET status='needs_repair' WHERE id = ?",
                (op_id,),
            )
            await db.commit()

            assert await journal.repair_incomplete() == 0

            mock_graph.index_memory.assert_not_called()
            cursor = await db.execute(
                "SELECT status, step FROM memory_write_ops WHERE id = ?", (op_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert (row["status"], row["step"]) == ("needs_repair", "source_stale")

    async def test_repair_add_refuses_convergence_for_truncated_preview(
        self, tmp_db_path: str
    ) -> None:
        """暂存前缀被截断且无摘要时不得收敛：前缀相等不能证明正文未变。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            body = "甲" * 600

            repaired, mock_graph, mock_atom, row = await _run_add_repair(
                db,
                atom_content=body,
                canonical_text=body + "并发追加的正文",
                payload={
                    "content_preview": body[:500],
                    "metadata": {"scope_key": "scope-a", "privacy_level": "shared"},
                },
            )

        assert repaired == 0
        mock_graph.index_memory.assert_not_called()
        mock_atom.insert_many.assert_not_called()
        assert row is not None
        assert (row["status"], row["step"]) == ("needs_repair", "source_stale")

    async def test_repair_add_keeps_needs_repair_when_content_digest_mismatches(
        self, tmp_db_path: str
    ) -> None:
        """摘要不匹配说明正文已被改写，即使前缀一致也不得收敛。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            body = "乙" * 600

            repaired, mock_graph, mock_atom, row = await _run_add_repair(
                db,
                atom_content=body,
                canonical_text=body + "被并发改写后的正文",
                payload={
                    "content_preview": body[:500],
                    "content_digest": CanonicalMemoryCommitted.digest_content(body)[
                        :32
                    ],
                    "metadata": {"scope_key": "scope-a", "privacy_level": "shared"},
                },
            )

        assert repaired == 0
        mock_graph.index_memory.assert_not_called()
        mock_atom.insert_many.assert_not_called()
        assert row is not None
        assert (row["status"], row["step"]) == ("needs_repair", "source_stale")

    async def test_repair_add_refuses_legacy_full_length_preview(
        self, tmp_db_path: str
    ) -> None:
        """历史无摘要载荷的预览恰好 500 字符时不能证明全文未变，不得收敛。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            body = "丁" * 500

            repaired, mock_graph, mock_atom, row = await _run_add_repair(
                db,
                atom_content=body,
                # 旧正文被截成原本的前 500 字符：预览与当前全文恰好相等。
                canonical_text=body,
                payload={
                    "content_preview": body,
                    "metadata": {"scope_key": "scope-a", "privacy_level": "shared"},
                },
            )

        assert repaired == 0
        mock_graph.index_memory.assert_not_called()
        mock_atom.insert_many.assert_not_called()
        assert row is not None
        assert (row["status"], row["step"]) == ("needs_repair", "source_stale")

    async def test_repair_add_converges_when_content_digest_matches(
        self, tmp_db_path: str
    ) -> None:
        """摘要一致时正文可证明未变，revision 推进仍按当前 canonical 收敛。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            body = "丙" * 600

            repaired, mock_graph, mock_atom, row = await _run_add_repair(
                db,
                atom_content=body,
                canonical_text=body,
                payload={
                    "content_preview": body[:500],
                    "content_digest": CanonicalMemoryCommitted.digest_content(body)[
                        :32
                    ],
                    "metadata": {"scope_key": "scope-a", "privacy_level": "shared"},
                },
            )

        assert repaired == 1
        mock_graph.index_memory.assert_called_once()
        inserted = mock_atom.insert_many.await_args.args[0]
        assert inserted[0].parent_revision == "rev-new"
        assert row is not None
        assert (row["status"], row["step"]) == ("completed", "completed")
