"""lifecycle_operations 测试 — 阶段转换（最小化，主要是编排）。"""

from __future__ import annotations

from core.managers.lifecycle_operations import LifecycleOperationsMixin


class TestLifecycleStructure:
    """Smoke tests for LifecycleOperationsMixin structure."""

    def test_cleanup_old_memories_exists(self) -> None:
        """cleanup_old_memories method is defined."""
        assert hasattr(LifecycleOperationsMixin, "cleanup_old_memories")

    def test_batch_update_status_exists(self) -> None:
        """_batch_update_status method is defined."""
        assert hasattr(LifecycleOperationsMixin, "_batch_update_status")

    def test_migrate_session_if_needed_exists(self) -> None:
        """migrate_session_if_needed method is defined."""
        assert hasattr(LifecycleOperationsMixin, "migrate_session_if_needed")
