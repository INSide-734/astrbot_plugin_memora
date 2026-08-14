"""测试 MemoryLifecycleManager — add/update/delete across storage layers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestMemoryLifecycleManager:
    @pytest.fixture
    def bm25(self) -> MagicMock:
        bm25 = MagicMock()
        bm25.add_document = AsyncMock()
        bm25.delete_document = AsyncMock(return_value=True)
        bm25.update_document = AsyncMock(return_value=True)
        bm25._connect = MagicMock()
        return bm25

    @pytest.fixture
    def vector(self) -> MagicMock:
        vec = MagicMock()
        vec.add_document = AsyncMock(return_value=42)
        vec.update_metadata = AsyncMock(return_value=True)
        vec.delete_document = AsyncMock(return_value=True)
        return vec

    @pytest.fixture
    def manager(self, bm25: MagicMock, vector: MagicMock) -> Any:
        from core.features.retrieval.memory_lifecycle import MemoryLifecycleManager

        return MemoryLifecycleManager(bm25_retriever=bm25, vector_retriever=vector)

    @pytest.mark.asyncio
    async def test_add_memory_returns_doc_id(
        self, manager: Any, bm25: MagicMock, vector: MagicMock
    ) -> None:
        """add_memory writes to vector first, then BM25, returns doc_id."""
        vector.add_document.return_value = 10
        doc_id = await manager.add_memory("test content", {"importance": 0.8})
        assert doc_id == 10
        vector.add_document.assert_called_once()
        # BM25 is called with injected defaults
        bm25.add_document.assert_called_once()
        call_kwargs = bm25.add_document.call_args
        assert call_kwargs.args[0] == 10  # doc_id
        assert call_kwargs.args[1] == "test content"  # content
        assert call_kwargs.args[2]["importance"] == 0.8  # provided metadata preserved

    @pytest.mark.asyncio
    async def test_add_memory_defaults_metadata(
        self, manager: Any, vector: MagicMock
    ) -> None:
        """缺失 metadata fields get default values."""
        doc_id = await manager.add_memory("test")
        assert isinstance(doc_id, int)
        # Check defaults were injected
        call_args = vector.add_document.call_args
        metadata = (
            call_args.kwargs["metadata"]
            if "metadata" in call_args.kwargs
            else call_args.args[1]
        )
        assert "importance" in metadata
        assert "create_time" in metadata
        assert "last_access_time" in metadata

    @pytest.mark.asyncio
    async def test_update_metadata_success(
        self, manager: Any, vector: MagicMock
    ) -> None:
        """update_metadata delegates to vector_retriever."""
        result = await manager.update_metadata(1, {"importance": 0.9})
        assert result is True
        vector.update_metadata.assert_called_once_with(1, {"importance": 0.9})

    @pytest.mark.asyncio
    async def test_update_metadata_failure(
        self, manager: Any, vector: MagicMock
    ) -> None:
        """当 vector update fails, returns False."""
        vector.update_metadata.return_value = False
        result = await manager.update_metadata(1, {"importance": 0.9})
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_memory_success(
        self, manager: Any, bm25: MagicMock, vector: MagicMock
    ) -> None:
        """delete_memory removes from BM25, then vector, then documents table."""
        # Mock the backup query to return a row
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value=("content", '{"k":"v"}'))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        bm25._connect.return_value = mock_conn

        result = await manager.delete_memory(42)
        assert result is True
        bm25.delete_document.assert_called_once_with(42)
        vector.delete_document.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_delete_memory_bm25_fails(
        self, manager: Any, bm25: MagicMock
    ) -> None:
        """当 BM25 deletion fails, return False early."""
        bm25.delete_document.return_value = False

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value=("content", "{}"))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        bm25._connect.return_value = mock_conn

        result = await manager.delete_memory(42)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_memory_vector_delete_fails_rollback(
        self, manager: Any, bm25: MagicMock, vector: MagicMock
    ) -> None:
        """当 vector deletion fails, BM25 is rolled back."""
        vector.delete_document.return_value = False

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value=("backup content", '{"k":"v"}'))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        bm25._connect.return_value = mock_conn

        result = await manager.delete_memory(42)
        assert result is False
        # Rollback should have been attempted
        bm25.update_document.assert_called()

    @pytest.mark.asyncio
    async def test_delete_memory_backup_fails(
        self, manager: Any, bm25: MagicMock, vector: MagicMock
    ) -> None:
        """当 backup query fails, deletion proceeds anyway (backup is best-effort)."""
        # Make ONLY the backup connection fail, not later ones
        call_count = [0]

        def _connect_sequence():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Backup failed")
            # Later calls: return a working mock for BM25 delete and docs delete
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone = AsyncMock(return_value=None)
            mock_conn.execute = AsyncMock(return_value=mock_cursor)
            mock_conn.commit = AsyncMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=None)
            return mock_conn

        bm25._connect = _connect_sequence
        # Vector delete succeeds
        vector.delete_document.return_value = True

        result = await manager.delete_memory(42)
        # Backup failure is logged but doesn't block deletion
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_memory_bm25_delete_raises(
        self, manager: Any, bm25: MagicMock
    ) -> None:
        """当 BM25 delete raises an exception, returns False."""
        bm25.delete_document.side_effect = Exception("BM25 delete error")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value=("content", "{}"))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        bm25._connect.return_value = mock_conn

        result = await manager.delete_memory(42)
        assert result is False

    @pytest.mark.asyncio
    async def test_update_metadata_vector_raises(
        self, manager: Any, vector: MagicMock
    ) -> None:
        """当 vector.update_metadata raises, returns False."""
        vector.update_metadata.side_effect = Exception("Update failed")
        result = await manager.update_metadata(1, {"importance": 0.9})
        assert result is False

    @pytest.mark.asyncio
    async def test_rollback_no_backup_content(
        self, manager: Any, bm25: MagicMock
    ) -> None:
        """_rollback_bm25_delete with no content is a no-op."""
        await manager._rollback_bm25_delete(1, None, {})
        bm25.update_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_success(self, manager: Any, bm25: MagicMock) -> None:
        """_rollback_bm25_delete successfully restores BM25 index."""
        await manager._rollback_bm25_delete(1, "restored content", {"importance": 0.5})
        bm25.update_document.assert_called_once_with(
            1, "restored content", {"importance": 0.5}
        )

    @pytest.mark.asyncio
    async def test_rollback_fails(self, manager: Any, bm25: MagicMock) -> None:
        """_rollback_bm25_delete handles update_document failure gracefully."""
        bm25.update_document.return_value = False
        await manager._rollback_bm25_delete(1, "content", {})
        bm25.update_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_raises(self, manager: Any, bm25: MagicMock) -> None:
        """_rollback_bm25_delete handles update_document exception gracefully."""
        bm25.update_document.side_effect = Exception("Rollback failed")
        await manager._rollback_bm25_delete(1, "content", {})
        bm25.update_document.assert_called_once()
