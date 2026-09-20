"""core/api/maintenance_api.py 测试 — MaintenanceApiMixin。

Covers rebuild, purge, compact, backup CRUD, restore, and export endpoints.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.platform.transport.page_api.response_utils import error_response

# ── helpers ───────────────────────────────────────────────────────────


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(
    *,
    plugin_ready: bool = True,
    has_backup: bool = True,
    has_maint: bool = False,
    has_exporter: bool = False,
    backup_path: str = "/tmp/backup.zip",
):
    from core.platform.transport.page_api.maintenance_api import MaintenanceApiMixin

    class Stub:
        rebuild_index = MaintenanceApiMixin.rebuild_index
        _record_index_rebuild_observability = (
            MaintenanceApiMixin._record_index_rebuild_observability
        )
        _coerce_result_int = MaintenanceApiMixin._coerce_result_int
        rebuild_graph_index = MaintenanceApiMixin.rebuild_graph_index
        _resolve_rebuild_coordinator = staticmethod(
            MaintenanceApiMixin._resolve_rebuild_coordinator
        )
        get_persistence_health = MaintenanceApiMixin.get_persistence_health
        repair_persistence_health = MaintenanceApiMixin.repair_persistence_health
        _persistence_health_validator = staticmethod(
            MaintenanceApiMixin._persistence_health_validator
        )
        _coerce_orphan_ids = staticmethod(MaintenanceApiMixin._coerce_orphan_ids)
        _purge_orphan_index_rows = MaintenanceApiMixin._purge_orphan_index_rows
        _delete_orphan_bm25_rows = staticmethod(
            MaintenanceApiMixin._delete_orphan_bm25_rows
        )
        _delete_orphan_main_vector_ids = staticmethod(
            MaintenanceApiMixin._delete_orphan_main_vector_ids
        )
        _delete_orphan_graph_vector_ids = staticmethod(
            MaintenanceApiMixin._delete_orphan_graph_vector_ids
        )
        purge_deleted_memories = MaintenanceApiMixin.purge_deleted_memories
        compact_database = MaintenanceApiMixin.compact_database
        create_backup = MaintenanceApiMixin.create_backup
        list_backups = MaintenanceApiMixin.list_backups
        delete_backup = MaintenanceApiMixin.delete_backup
        batch_delete_backups = MaintenanceApiMixin.batch_delete_backups
        restore_backup = MaintenanceApiMixin.restore_backup
        export_memories = MaintenanceApiMixin.export_memories
        install_dashboard_deps = MaintenanceApiMixin.install_dashboard_deps
        build_dashboard = MaintenanceApiMixin.build_dashboard
        _run_npm_command = MaintenanceApiMixin._run_npm_command
        _dashboard_runtime_config = MaintenanceApiMixin._dashboard_runtime_config
        _truncate_command_output = MaintenanceApiMixin._truncate_command_output
        _dashboard_runtime_build_disabled_response = (
            MaintenanceApiMixin._dashboard_runtime_build_disabled_response
        )
        _get_dashboard_runtime_lock = MaintenanceApiMixin._get_dashboard_runtime_lock
        _resolve_command_executable = MaintenanceApiMixin._resolve_command_executable

        def __init__(self):
            self.plugin = MagicMock()
            if has_backup:
                self.plugin._backup_manager = MagicMock()
                self.plugin._backup_manager.create_backup = AsyncMock(
                    return_value=backup_path
                )
                self.plugin._backup_manager.delete_backup = MagicMock(return_value=True)
                self.plugin._backup_manager.stage_restore = MagicMock(
                    return_value={
                        "staged": 1,
                        "skipped": 0,
                        "pending": True,
                        "staged_files": ["memora.db.restore"],
                        "skipped_files": [],
                    }
                )
                self.plugin._backup_manager.data_dir = "/fake/data"
            else:
                self.plugin._backup_manager = None
            self.plugin.config_manager = MagicMock()
            self.plugin.config_manager.get.side_effect = lambda key, default=None: {
                "dashboard.allow_runtime_build": False,
                "dashboard.build_timeout_seconds": 120,
                "dashboard.max_output_chars": 20000,
            }.get(key, default)
            self.plugin.initializer = MagicMock()
            self.plugin.initializer.data_dir = "/fake/data"
            self.plugin.initializer.index_validator = MagicMock()
            self.plugin.initializer.index_validator.rebuild_indexes = AsyncMock(
                return_value={"success": True, "processed": 3, "errors": 0, "total": 3}
            )

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, error_response("not ready")
            engine = MagicMock(spec=["rebuild_graph_index"])
            engine.rebuild_graph_index = AsyncMock()
            if has_maint:
                engine.maintenance = MagicMock()
                engine.maintenance.purge_deleted = AsyncMock(return_value=5)
            if has_exporter:
                engine.memory_exporter = MagicMock()
                engine.memory_exporter.export_jsonl = AsyncMock(return_value=10)
                engine.memory_exporter.export_markdown = AsyncMock(return_value=10)
            return {"memory_engine": engine}, None

    return Stub()


# ── tests ─────────────────────────────────────────────────────────────


class TestMaintenanceValidation:
    """Plugin-not-ready and error path tests."""

    @pytest.mark.asyncio
    async def test_rebuild_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.rebuild_index()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_purge_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.purge_deleted_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_compact_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.compact_database()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_backup_plugin_not_ready(self) -> None:
        mixin = _make_mixin(plugin_ready=False)
        result = await mixin.create_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_export_plugin_not_ready(self) -> None:
        req = _mock_request()
        with patch("quart.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.export_memories()
        assert result["status"] == "error"


class TestMaintenanceHappyPath:
    """Happy path tests with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_rebuild_index_ok(self) -> None:
        mixin = _make_mixin()
        result = await mixin.rebuild_index()
        assert result["status"] == "ok"
        mixin.plugin.initializer.index_validator.rebuild_indexes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rebuild_index_records_observability_snapshot_on_success(
        self,
    ) -> None:
        mixin = _make_mixin()
        mixin.plugin.initializer.index_validator.rebuild_indexes.return_value = {
            "success": True,
            "processed": 8,
            "errors": 1,
            "total": 9,
            "message": "索引已按失败率阈值完成可接受切换",
        }

        result = await mixin.rebuild_index()

        assert result["status"] == "ok"
        observability = mixin.plugin._index_observability
        assert observability["last_rebuild_success"] is True
        assert observability["last_rebuild_errors"] == 1
        assert observability["last_rebuild_total"] == 9
        assert (
            observability["last_rebuild_message"] == "索引已按失败率阈值完成可接受切换"
        )
        assert observability["last_rebuild_duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_rebuild_index_records_observability_snapshot_on_exception(
        self,
    ) -> None:
        mixin = _make_mixin()
        mixin.plugin.initializer.index_validator.rebuild_indexes.side_effect = (
            RuntimeError("boom")
        )

        result = await mixin.rebuild_index()

        assert result["status"] == "error"
        observability = mixin.plugin._index_observability
        assert observability["last_rebuild_success"] is False
        assert observability["last_rebuild_errors"] == 1
        assert observability["last_rebuild_total"] == 0
        assert observability["last_rebuild_message"] == "boom"
        assert observability["last_rebuild_duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_rebuild_index_does_not_call_graph_rebuild(self) -> None:
        mixin = _make_mixin()
        engines, _ = await mixin._ensure_plugin_ready()
        engine = engines["memory_engine"]
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": engine}, None)
        )

        result = await mixin.rebuild_index()

        assert result["status"] == "ok"
        engine.rebuild_graph_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rebuild_graph_index_ok(self) -> None:
        mixin = _make_mixin()
        engines, _ = await mixin._ensure_plugin_ready()
        engine = engines["memory_engine"]
        engine.rebuild_graph_index.return_value = {"rebuilt": 2, "skipped": 1}
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": engine}, None)
        )

        result = await mixin.rebuild_graph_index()

        assert result["status"] == "ok"
        assert result["data"]["result"] == {"rebuilt": 2, "skipped": 1}
        engine.rebuild_graph_index.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_persistence_health_returns_validator_report(self) -> None:
        mixin = _make_mixin()
        validator = MagicMock()
        validator.check = AsyncMock(return_value={"ok": True, "issues": {}})
        with patch(
            "core.platform.transport.page_api.maintenance_api.PersistenceHealthValidator",
            return_value=validator,
        ):
            result = await mixin.get_persistence_health()

        assert result["status"] == "ok"
        assert result["data"]["ok"] is True
        validator.check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_repair_persistence_health_requires_explicit_targets(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={})
        with patch("quart.request", req):
            mixin = _make_mixin()
            result = await mixin.repair_persistence_health()

        assert result["status"] == "error"
        assert "targets" in result["message"]


def _make_repair_mixin(
    *,
    db_path: str,
    issues: dict,
    faiss_db=None,
    graph_faiss_db=None,
    graph_store=None,
):
    """构造使用真实 canonical 库路径与固定孤儿报告的维护 API 替身。"""

    mixin = _make_mixin()
    engine = MagicMock()
    engine.db_path = db_path
    engine.faiss_db = faiss_db
    engine.graph_vector_db = graph_faiss_db
    engine.graph_store = graph_store
    engine.graph_memory_manager = MagicMock()
    engine.graph_memory_manager.faiss_db = graph_faiss_db
    engine.graph_memory_manager.graph_store = graph_store
    mixin._ensure_plugin_ready = AsyncMock(
        return_value=({"memory_engine": engine}, None)
    )
    mixin.plugin.initializer.index_validator.db_path = db_path
    mixin.plugin.initializer.index_validator.faiss_db = faiss_db
    validator = MagicMock()
    validator.check = AsyncMock(
        return_value={
            "ok": False,
            "needs_repair": True,
            "counts": {"bm25": 0, "main_vectors": 0, "graph_vectors": 0},
            "issues": issues,
        }
    )
    return mixin, validator


async def _bm25_fixture(tmp_db_path: str) -> None:
    """写入 canonical 行与 FTS 行（含孤儿）用于孤儿清理验证。"""

    db = await aiosqlite.connect(tmp_db_path)
    try:
        await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT)")
        await db.execute("INSERT INTO documents (id, text) VALUES (1, 'kept')")
        await db.execute(
            "CREATE VIRTUAL TABLE memora_memories_fts USING fts5("
            "content, doc_id UNINDEXED, tokenize='unicode61')"
        )
        for doc_id in (1, 2, 3):
            await db.execute(
                "INSERT INTO memora_memories_fts (content, doc_id) VALUES (?, ?)",
                (f"memory-{doc_id}", doc_id),
            )
        await db.commit()
    finally:
        await db.close()


async def _bm25_doc_ids(tmp_db_path: str) -> list[int]:
    """读取 FTS 中剩余的 doc_id，用于验证删除范围。"""

    db = await aiosqlite.connect(tmp_db_path)
    try:
        cursor = await db.execute(
            "SELECT doc_id FROM memora_memories_fts ORDER BY doc_id"
        )
        return [int(row[0]) for row in await cursor.fetchall()]
    finally:
        await db.close()


class TestPersistenceHealthRepair:
    """health repair 的孤儿清理入口：成功、重新核对与失败路径。"""

    @pytest.mark.asyncio
    async def test_repair_removes_bm25_orphans_and_keeps_live_rows(
        self, tmp_db_path: str
    ) -> None:
        """BM25 孤儿按报告清理，仍存在于 canonical 的候选不得删除。"""

        await _bm25_fixture(tmp_db_path)
        mixin, validator = _make_repair_mixin(
            db_path=tmp_db_path,
            issues={"orphan_bm25_doc_ids": [1, 2, 3]},
        )
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["orphan_bm25_doc_ids"]})

        with (
            patch("quart.request", req),
            patch(
                "core.platform.transport.page_api.maintenance_api."
                "PersistenceHealthValidator",
                return_value=validator,
            ),
        ):
            result = await mixin.repair_persistence_health()

        assert result["status"] == "ok"
        assert result["data"]["repaired"] == {"orphan_bm25_doc_ids": 2}
        assert await _bm25_doc_ids(tmp_db_path) == [1]
        validator.check.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_repair_reports_zero_without_orphans(self, tmp_db_path: str) -> None:
        """没有孤儿时清理计数为零且不触碰索引。"""

        await _bm25_fixture(tmp_db_path)
        mixin, _validator = _make_repair_mixin(db_path=tmp_db_path, issues={})
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["orphan_bm25_doc_ids"]})

        with (
            patch("quart.request", req),
            patch(
                "core.platform.transport.page_api.maintenance_api."
                "PersistenceHealthValidator",
                return_value=_validator,
            ),
        ):
            result = await mixin.repair_persistence_health()

        assert result["status"] == "ok"
        assert result["data"]["repaired"] == {"orphan_bm25_doc_ids": 0}
        assert await _bm25_doc_ids(tmp_db_path) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_repair_rejects_unsupported_target(self) -> None:
        """不在报告闭集内的目标必须显式拒绝，不能静默忽略。"""

        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["atom_orphan_parent_ids"]})
        with patch("quart.request", req):
            mixin = _make_mixin()
            result = await mixin.repair_persistence_health()

        assert result["status"] == "error"
        assert "孤儿索引清理" in result["message"]

    @pytest.mark.asyncio
    async def test_repair_returns_explicit_error_when_vector_store_missing(
        self, tmp_db_path: str
    ) -> None:
        """向量库不可用时返回显式错误，而不是未实现占位。"""

        mixin, validator = _make_repair_mixin(
            db_path=tmp_db_path,
            issues={"orphan_main_vector_ids": [7]},
            faiss_db=None,
        )
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["orphan_main_vector_ids"]})

        with (
            patch("quart.request", req),
            patch(
                "core.platform.transport.page_api.maintenance_api."
                "PersistenceHealthValidator",
                return_value=validator,
            ),
        ):
            result = await mixin.repair_persistence_health()

        assert result["status"] == "error"
        assert "未实现" not in result["message"]

    @pytest.mark.asyncio
    async def test_repair_rejects_missing_vector_store_without_reported_orphans(
        self, tmp_db_path: str
    ) -> None:
        """目标后端缺失时不能把无法检查误报成零修复。"""

        mixin, validator = _make_repair_mixin(
            db_path=tmp_db_path,
            issues={},
            faiss_db=None,
        )
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["orphan_main_vector_ids"]})

        with (
            patch("quart.request", req),
            patch(
                "core.platform.transport.page_api.maintenance_api."
                "PersistenceHealthValidator",
                return_value=validator,
            ),
        ):
            result = await mixin.repair_persistence_health()

        assert result["status"] == "error"
        assert "未实现" not in result["message"]

    @pytest.mark.asyncio
    async def test_repair_rejects_missing_graph_store(self, tmp_db_path: str) -> None:
        """图孤儿删除没有图表复核端口时必须显式失败。"""

        graph_faiss_db = MagicMock()
        graph_faiss_db.delete = AsyncMock()
        mixin, validator = _make_repair_mixin(
            db_path=tmp_db_path,
            issues={"orphan_graph_vector_ids": [42]},
            graph_faiss_db=graph_faiss_db,
            graph_store=None,
        )
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["orphan_graph_vector_ids"]})

        with (
            patch("quart.request", req),
            patch(
                "core.platform.transport.page_api.maintenance_api."
                "PersistenceHealthValidator",
                return_value=validator,
            ),
        ):
            result = await mixin.repair_persistence_health()

        assert result["status"] == "error"
        graph_faiss_db.delete.assert_not_awaited()

    @pytest.mark.parametrize("live_id", [5, "5"])
    @pytest.mark.asyncio
    async def test_orphan_main_vector_delete_skips_reused_ids(self, live_id) -> None:
        """主向量孤儿清理跳过仍存在文档行的 ID，只删真正孤儿槽位。"""

        mixin = _make_mixin()
        faiss_db = MagicMock()
        faiss_db.document_storage.get_documents = AsyncMock(
            return_value=[{"id": live_id}]
        )
        faiss_db.embedding_storage.delete = AsyncMock()

        deleted = await mixin._delete_orphan_main_vector_ids(faiss_db, [5, 6])

        assert deleted == 1
        faiss_db.embedding_storage.delete.assert_awaited_once_with([6])

    @pytest.mark.asyncio
    async def test_orphan_main_vector_skips_unparseable_live_batch(self) -> None:
        """无法解析 live 行 ID 时整批跳过，避免误删任一候选。"""

        mixin = _make_mixin()
        faiss_db = MagicMock()
        faiss_db.document_storage.get_documents = AsyncMock(
            return_value=[{"id": "5"}, {"id": "not-an-id"}]
        )
        faiss_db.embedding_storage.delete = AsyncMock()

        deleted = await mixin._delete_orphan_main_vector_ids(faiss_db, [5, 6])

        assert deleted == 0
        faiss_db.embedding_storage.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repair_removes_unreferenced_graph_vectors(self) -> None:
        """图向量孤儿删除文档行与无文档槽位，并重新核对图表引用。"""

        mixin = _make_mixin()
        graph_faiss_db = MagicMock()
        graph_faiss_db.document_storage.get_documents = AsyncMock(
            return_value=[{"id": 42, "doc_id": "uuid-42"}]
        )
        graph_faiss_db.delete = AsyncMock()
        graph_faiss_db.embedding_storage.delete = AsyncMock()
        graph_store = MagicMock()
        graph_store.list_unreferenced_vector_doc_ids = AsyncMock(return_value=[42, 43])

        deleted = await mixin._delete_orphan_graph_vector_ids(
            graph_faiss_db, graph_store, [41, 42, 43]
        )

        assert deleted == 2
        graph_store.list_unreferenced_vector_doc_ids.assert_awaited_once_with(
            [41, 42, 43]
        )
        graph_faiss_db.delete.assert_awaited_once_with("uuid-42")
        graph_faiss_db.embedding_storage.delete.assert_awaited_once_with([43])

    @pytest.mark.asyncio
    async def test_repair_rejects_failed_health_scan(self, tmp_db_path: str) -> None:
        """扫描失败不是零孤儿，API 必须拒绝成功修复。"""
        mixin, validator = _make_repair_mixin(
            db_path=tmp_db_path, issues={"check_failed": "OperationalError"}
        )
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"targets": ["orphan_bm25_doc_ids"]})
        with (
            patch("quart.request", req),
            patch(
                "core.platform.transport.page_api.maintenance_api."
                "PersistenceHealthValidator",
                return_value=validator,
            ),
        ):
            result = await mixin.repair_persistence_health()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_graph_orphan_delete_rejects_backend_false(self) -> None:
        """图文档删除明确失败时，不得计为已清理。"""
        mixin = _make_mixin()
        graph_db = SimpleNamespace(
            document_storage=SimpleNamespace(
                get_documents=AsyncMock(return_value=[{"id": 42, "doc_id": "uuid"}])
            ),
            embedding_storage=SimpleNamespace(delete=AsyncMock()),
            delete=AsyncMock(return_value=False),
        )
        store = SimpleNamespace(
            list_unreferenced_vector_doc_ids=AsyncMock(return_value=[42])
        )
        with pytest.raises(RuntimeError, match="graph_vector_delete_failed"):
            await mixin._delete_orphan_graph_vector_ids(graph_db, store, [42])
        graph_db.embedding_storage.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purge_no_maint_returns_zero(self) -> None:
        mixin = _make_mixin()
        result = await mixin.purge_deleted_memories()
        assert result["status"] == "ok"
        assert result["data"]["purged"] == 0

    @pytest.mark.asyncio
    async def test_purge_with_maint(self) -> None:
        mixin = _make_mixin(has_maint=True)
        result = await mixin.purge_deleted_memories()
        assert result["status"] == "ok"
        assert result["data"]["purged"] == 5

    @pytest.mark.asyncio
    async def test_compact_ok(self) -> None:
        mixin = _make_mixin()
        result = await mixin.compact_database()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_create_backup_ok(self) -> None:
        mixin = _make_mixin(has_backup=True)
        result = await mixin.create_backup()
        assert result["status"] == "ok"
        assert "path" in result["data"]

    @pytest.mark.asyncio
    async def test_create_backup_no_manager(self) -> None:
        mixin = _make_mixin(has_backup=False)
        result = await mixin.create_backup()
        assert result["status"] == "error"


class TestMaintenanceRebuildRouting:
    """维护 API 必须经统一阶段入口执行单阶段重建。"""

    @pytest.mark.asyncio
    async def test_rebuild_index_routes_through_stage_entry(self) -> None:
        """初始化器发布统一入口时，API 经 rebuild_stages 执行 indexes 阶段。"""

        from core.platform.composition import DerivedRebuildCoordinator

        mixin = _make_mixin()
        validator = mixin.plugin.initializer.index_validator
        validator._get_document_count = AsyncMock(return_value=3)
        mixin.plugin.initializer.derived_rebuild_coordinator = (
            DerivedRebuildCoordinator(validator, SimpleNamespace())
        )

        result = await mixin.rebuild_index()

        assert result["status"] == "ok"
        assert result["data"]["result"]["processed"] == 3
        # 阶段入口会补充 status 字段；直连路径不会，因此它证明走了统一入口。
        assert result["data"]["result"]["status"] == "completed"
        validator.rebuild_indexes.assert_awaited_once()
        observability = mixin.plugin._index_observability
        assert observability["last_rebuild_success"] is True
        assert observability["last_rebuild_total"] == 3

    @pytest.mark.asyncio
    async def test_rebuild_graph_routes_through_stage_entry(self) -> None:
        """图重建 API 经 rebuild_stages 单阶段执行，保持既有响应结构。"""

        from core.platform.composition import DerivedRebuildCoordinator

        mixin = _make_mixin()
        engines, _ = await mixin._ensure_plugin_ready()
        engine = engines["memory_engine"]
        mixin._ensure_plugin_ready = AsyncMock(
            return_value=({"memory_engine": engine}, None)
        )
        validator = mixin.plugin.initializer.index_validator
        validator._get_document_count = AsyncMock(return_value=1)
        mixin.plugin.initializer.derived_rebuild_coordinator = (
            DerivedRebuildCoordinator(validator, engine)
        )

        result = await mixin.rebuild_graph_index()

        assert result["status"] == "ok"
        # 阶段入口会补充 status 字段；直连路径返回的是引擎原始结果。
        assert result["data"]["result"]["status"] == "completed"
        engine.rebuild_graph_index.assert_awaited_once()


class TestBackupCRUD:
    """Backup listing, deletion, and restore."""

    @pytest.mark.asyncio
    async def test_list_backups_ok(self) -> None:
        mixin = _make_mixin(has_backup=True)
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert "backups" in result["data"]

    @pytest.mark.asyncio
    async def test_list_backups_no_data_dir(self) -> None:
        mixin = _make_mixin(has_backup=False)
        mixin.plugin.initializer = None
        result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []

    @pytest.mark.asyncio
    async def test_list_backups_fallback_to_initializer(self) -> None:
        mixin = _make_mixin(has_backup=False)
        result = await mixin.list_backups()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_list_backups_tolerates_non_list_manager_payload(self) -> None:
        mixin = _make_mixin(has_backup=True)
        with patch(
            "core.features.backup.application.BackupManager.list_backups",
            return_value="bad-backups",
        ):
            result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_list_backups_accepts_iterable_manager_payload(self) -> None:
        mixin = _make_mixin(has_backup=True)
        backups = (
            {"name": "backup-a", "path": "a.zip"},
            {"name": "backup-b", "path": "b.zip"},
        )
        with patch(
            "core.features.backup.application.BackupManager.list_backups",
            return_value=backups,
        ):
            result = await mixin.list_backups()
        assert result["status"] == "ok"
        assert result["data"]["backups"] == list(backups)
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_delete_backup_missing_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": ""})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.delete_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_backup_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["backup1"])
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"
        assert "JSON" in result["message"]
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_backup_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "backup1"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=False)
            result = await mixin.delete_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_backup_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "nonexistent"})
        mixin = _make_mixin(has_backup=True)
        mixin.plugin._backup_manager.delete_backup.return_value = False
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_backup_ok(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "backup1"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "ok"
        mixin.plugin._backup_manager.delete_backup.assert_called_once_with("backup1")

    @pytest.mark.asyncio
    async def test_delete_backup_rejects_path_traversal_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "../outside"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_backup_rejects_invalid_characters(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "bad name"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.delete_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_missing_names(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": []})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_list_names_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": "backup1"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["b1", "b2"])
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"
        assert "JSON" in result["message"]
        mixin.plugin._backup_manager.delete_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": ["b1", "b2"]})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=False)
            result = await mixin.batch_delete_backups()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_ok(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": ["b1", "b2", "b3"]})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.batch_delete_backups()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] == 3
        mixin.plugin._backup_manager.delete_backup.assert_any_call("b1")
        mixin.plugin._backup_manager.delete_backup.assert_any_call("b2")
        mixin.plugin._backup_manager.delete_backup.assert_any_call("b3")

    @pytest.mark.asyncio
    async def test_batch_delete_counts_invalid_names_as_failed(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"names": ["good", "../bad", "bad name"]})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.batch_delete_backups()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] == 1
        assert result["data"]["failed"] == 2
        mixin.plugin._backup_manager.delete_backup.assert_called_once_with("good")

    @pytest.mark.asyncio
    async def test_restore_missing_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": ""})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=True)
            result = await mixin.restore_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restore_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["backup1"])
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"
        assert "JSON" in result["message"]
        mixin.plugin._backup_manager.stage_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "b1"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_backup=False)
            result = await mixin.restore_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restore_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "nonexistent"})
        mixin = _make_mixin(has_backup=True)
        mixin.plugin._backup_manager.stage_restore.side_effect = FileNotFoundError(
            "backup not found: nonexistent"
        )
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restore_rejects_path_traversal_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "../outside"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.stage_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_rejects_invalid_characters(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "bad name"})
        mixin = _make_mixin(has_backup=True)
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "error"
        mixin.plugin._backup_manager.stage_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_tolerates_malformed_stage_restore_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"name": "backup1"})
        mixin = _make_mixin(has_backup=True)
        mixin.plugin._backup_manager.stage_restore.return_value = {
            "staged": "1",
            "skipped": "bad-count",
            "staged_files": "bad-files",
            "skipped_files": {"bad": "files"},
        }
        with patch("quart.request", req):
            result = await mixin.restore_backup()
        assert result["status"] == "ok"
        assert result["data"]["staged"] == 1
        assert result["data"]["skipped"] == 0
        assert result["data"]["pending"] is True
        assert result["data"]["staged_files"] == []
        assert result["data"]["skipped_files"] == []


class TestRestoreBackup:
    """Restore backup with real temp directories."""

    @pytest.mark.asyncio
    async def test_restore_rejects_legacy_backup_without_canonical_database(
        self,
    ) -> None:
        import tempfile

        req = _mock_request()
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "data")
            backup_dir = os.path.join(data_dir, "backups", "test_backup")
            os.makedirs(backup_dir, exist_ok=True)
            # Create some files to restore
            test_file = os.path.join(backup_dir, "test.db")
            with open(test_file, "w") as f:
                f.write("test data")
            restore_file = os.path.join(backup_dir, "memora.index")
            with open(restore_file, "w") as f:
                f.write("index data")

            req.get_json = AsyncMock(return_value={"name": "test_backup"})
            mixin = _make_mixin(has_backup=True)
            from core.features.backup.application import BackupManager

            mixin.plugin._backup_manager = BackupManager(data_dir)
            with patch("quart.request", req):
                r = await mixin.restore_backup()
            assert r["status"] == "error"
            assert r["code"] == "backup_invalid"


class TestExportMemories:
    """Memory export endpoint tests."""

    @pytest.mark.asyncio
    async def test_export_no_exporter(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"format": "jsonl"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=False)
            result = await mixin.export_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_export_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["jsonl"])
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=True)
            result = await mixin.export_memories()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_export_tolerates_non_numeric_export_count(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"format": "jsonl"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=True)
            engines, _ = await mixin._ensure_plugin_ready()
            engine = engines["memory_engine"]
            engine.memory_exporter.export_jsonl = AsyncMock(return_value="bad-count")
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=({"memory_engine": engine}, None)
            )
            with (
                patch("builtins.open", create=True) as mock_open,
                patch("tempfile.NamedTemporaryFile") as mock_tmp,
                patch("os.unlink") as mock_unlink,
            ):
                tmp = MagicMock()
                tmp.__enter__.return_value.name = "/tmp/export.jsonl"
                tmp.__exit__.return_value = False
                mock_tmp.return_value = tmp
                mock_open.return_value.__enter__.return_value.read.return_value = (
                    "line-1\n"
                )
                result = await mixin.export_memories()
        assert result["status"] == "ok"
        assert result["data"]["content"] == "line-1\n"
        assert result["data"]["count"] == 0
        assert result["data"]["format"] == "jsonl"
        mock_unlink.assert_called_once_with("/tmp/export.jsonl")

    @pytest.mark.asyncio
    async def test_export_failure_still_removes_temp_file_and_hides_details(
        self,
    ) -> None:
        """导出失败必须清理临时文件，且不把异常原文回显给页面。"""
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"format": "jsonl"})
        with patch("quart.request", req):
            mixin = _make_mixin(has_exporter=True)
            engines, _ = await mixin._ensure_plugin_ready()
            engine = engines["memory_engine"]
            engine.memory_exporter.export_jsonl = AsyncMock(
                side_effect=RuntimeError("export failed at /tmp/export.jsonl")
            )
            mixin._ensure_plugin_ready = AsyncMock(
                return_value=({"memory_engine": engine}, None)
            )
            with (
                patch("tempfile.NamedTemporaryFile") as mock_tmp,
                patch("os.unlink") as mock_unlink,
            ):
                tmp = MagicMock()
                tmp.__enter__.return_value.name = "/tmp/export.jsonl"
                tmp.__exit__.return_value = False
                mock_tmp.return_value = tmp
                result = await mixin.export_memories()

        assert result["status"] == "error"
        assert result["message"] == "导出记忆失败"
        assert "export failed" not in repr(result)
        mock_unlink.assert_called_once_with("/tmp/export.jsonl")


class TestDashboardMaintenance:
    """Dashboard runtime install/build safety controls."""

    def test_dashboard_runtime_config_treats_false_string_as_disabled(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": "false",
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)

        allow_runtime_build, timeout_seconds, max_output_chars = (
            mixin._dashboard_runtime_config()
        )

        assert allow_runtime_build is False
        assert timeout_seconds == 120
        assert max_output_chars == 20000

    def test_dashboard_runtime_config_falls_back_and_clamps_numeric_values(
        self,
    ) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": "true",
            "dashboard.build_timeout_seconds": "bad",
            "dashboard.max_output_chars": 200,
        }.get(key, default)

        allow_runtime_build, timeout_seconds, max_output_chars = (
            mixin._dashboard_runtime_config()
        )

        assert allow_runtime_build is True
        assert timeout_seconds == 120
        assert max_output_chars == 1000

    def test_dashboard_runtime_config_treats_unknown_string_as_disabled(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": "definitely-not-a-bool",
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)

        allow_runtime_build, timeout_seconds, max_output_chars = (
            mixin._dashboard_runtime_config()
        )

        assert allow_runtime_build is False
        assert timeout_seconds == 120
        assert max_output_chars == 20000

    @pytest.mark.asyncio
    async def test_install_dashboard_disabled_by_default(self) -> None:
        mixin = _make_mixin()
        result = await mixin.install_dashboard_deps()
        assert result["status"] == "error"
        assert "已禁用" in result["message"]

    @pytest.mark.asyncio
    async def test_build_dashboard_disabled_by_default(self) -> None:
        mixin = _make_mixin()
        result = await mixin.build_dashboard()
        assert result["status"] == "error"
        assert "已禁用" in result["message"]

    @pytest.mark.asyncio
    async def test_install_dashboard_uses_npm_ci_when_enabled(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": True,
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)
        mixin._run_npm_command = AsyncMock(
            return_value={
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "timed_out": False,
            }
        )
        with patch("os.path.isfile", return_value=True):
            result = await mixin.install_dashboard_deps()
        assert result["status"] == "ok"
        assert result["data"]["command"] == "npm ci"
        mixin._run_npm_command.assert_awaited_once()
        assert mixin._run_npm_command.await_args.args[0] == ["npm", "ci"]

    @pytest.mark.asyncio
    async def test_build_dashboard_enabled_calls_build(self) -> None:
        mixin = _make_mixin()
        mixin.plugin.config_manager.get.side_effect = lambda key, default=None: {
            "dashboard.allow_runtime_build": True,
            "dashboard.build_timeout_seconds": 120,
            "dashboard.max_output_chars": 20000,
        }.get(key, default)
        mixin._run_npm_command = AsyncMock(
            return_value={
                "stdout": "built",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "timed_out": False,
            }
        )
        with patch("os.path.isfile", return_value=True):
            result = await mixin.build_dashboard()
        assert result["status"] == "ok"
        assert result["data"]["command"] == "npm run build"
        assert mixin._run_npm_command.await_args.args[0] == ["npm", "run", "build"]

    def test_truncate_command_output_short(self) -> None:
        mixin = _make_mixin()
        assert type(mixin)._truncate_command_output("abc", 10) == "abc"

    def test_truncate_command_output_long(self) -> None:
        mixin = _make_mixin()
        output = type(mixin)._truncate_command_output("a" * 50, 20)
        assert len(output) <= 20
        assert output != "a" * 50

    def test_resolve_command_executable_uses_direct_match(self) -> None:
        mixin = _make_mixin()
        with patch("shutil.which", side_effect=["C:/nodejs/npm.cmd"]):
            resolved = type(mixin)._resolve_command_executable("npm")
        assert resolved == "C:/nodejs/npm.cmd"

    def test_resolve_command_executable_falls_back_to_windows_suffixes(self) -> None:
        mixin = _make_mixin()
        with (
            patch("sys.platform", "win32"),
            patch(
                "shutil.which",
                side_effect=[None, "C:/nodejs/npm.cmd"],
            ),
        ):
            resolved = type(mixin)._resolve_command_executable("npm")
        assert resolved == "C:/nodejs/npm.cmd"
