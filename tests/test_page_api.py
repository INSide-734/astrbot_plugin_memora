"""测试 PluginPageApi — shared helpers, _ensure_plugin_ready, _normalize_metadata, etc."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.response_utils import error_response, ok_response
from core.jargon.jargon_store import JargonStore
from core.jargon.models import JargonMeaning
from core.page_api import (
    PAGE_API_ALIAS_PREFIXES,
    PAGE_API_PREFIX,
    PLUGIN_NAME,
    PluginPageApi,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestPageApiConstants:
    """测试模块级常量。"""

    def test_plugin_name(self) -> None:
        """PLUGIN_NAME is the expected string."""
        assert PLUGIN_NAME == "astrbot_plugin_memora"

    def test_page_api_prefix(self) -> None:
        """PAGE_API_PREFIX includes the plugin name."""
        assert PLUGIN_NAME in PAGE_API_PREFIX
        assert PAGE_API_PREFIX.endswith("/page")


# ---------------------------------------------------------------------------
# Static helper methods (no plugin needed)
# ---------------------------------------------------------------------------


class TestNormalizeMetadata:
    """测试 PluginPageApi._normalize_metadata 静态方法。"""

    def test_returns_dict_as_is(self) -> None:
        """A dict is returned unchanged."""
        md = {"key": "value"}
        assert PluginPageApi._normalize_metadata(md) is md

    def test_none_or_empty_returns_empty_dict(self) -> None:
        """None or falsy metadata returns empty dict."""
        assert PluginPageApi._normalize_metadata(None) == {}
        assert PluginPageApi._normalize_metadata("") == {}
        assert PluginPageApi._normalize_metadata(0) == {}

    def test_json_string_parsed(self) -> None:
        """A valid JSON string is parsed to a dict."""
        result = PluginPageApi._normalize_metadata('{"a": 1}')
        assert result == {"a": 1}

    def test_non_dict_json_returns_empty(self) -> None:
        """A JSON array or scalar string returns empty dict."""
        assert PluginPageApi._normalize_metadata("[1, 2, 3]") == {}
        assert PluginPageApi._normalize_metadata('"hello"') == {}
        assert PluginPageApi._normalize_metadata("42") == {}

    def test_invalid_json_returns_empty(self) -> None:
        """无效 JSON returns empty dict."""
        assert PluginPageApi._normalize_metadata("{broken") == {}

    def test_non_string_non_dict_returns_empty(self) -> None:
        """A non-string, non-dict value returns empty dict."""
        assert PluginPageApi._normalize_metadata(12345) == {}


class TestImportanceToDisplay:
    """测试 PluginPageApi._importance_to_display 静态方法。"""

    def test_small_value_scaled(self) -> None:
        """Values <= 1.0 are scaled by 10."""
        assert PluginPageApi._importance_to_display(0.5) == 5.0
        assert PluginPageApi._importance_to_display(0.75) == 7.5
        assert PluginPageApi._importance_to_display(1.0) == 10.0

    def test_large_value_not_scaled(self) -> None:
        """Values > 1.0 are left as-is (clamped to 10)."""
        assert PluginPageApi._importance_to_display(5.0) == 5.0
        assert PluginPageApi._importance_to_display(8.5) == 8.5

    def test_invalid_value_defaults(self) -> None:
        """Non-numeric values default to 0.5 → scaled to 5.0."""
        assert PluginPageApi._importance_to_display("bad") == 5.0
        assert PluginPageApi._importance_to_display(None) == 5.0

    def test_clamped_to_range(self) -> None:
        """Result is clamped to [0.0, 10.0]."""
        assert PluginPageApi._importance_to_display(-0.5) == 0.0
        assert PluginPageApi._importance_to_display(15.0) == 10.0
        assert PluginPageApi._importance_to_display(0.0) == 0.0


class TestOkError:
    """测试 _ok and _error static helpers."""

    def test_ok_returns_dict(self) -> None:
        """_ok wraps in standard ok_response."""
        result = PluginPageApi._ok({"items": [1, 2]})
        assert result == ok_response({"items": [1, 2]})

    def test_ok_none_data(self) -> None:
        """_ok with no args returns ok with None data."""
        result = PluginPageApi._ok()
        assert result == ok_response(None)

    def test_error_returns_dict(self) -> None:
        """_error wraps in standard error_response."""
        result = PluginPageApi._error("Something went wrong")
        assert result == error_response("Something went wrong")


class TestTokenizeGraphQuery:
    """测试 _tokenize_graph_query static method."""

    def test_none_returns_empty(self) -> None:
        """None query returns empty list."""
        assert PluginPageApi._tokenize_graph_query(None) == []

    def test_empty_string(self) -> None:
        """空 query returns empty list."""
        assert PluginPageApi._tokenize_graph_query("") == []

    def test_whitespace_only(self) -> None:
        """Whitespace-only query returns empty list."""
        assert PluginPageApi._tokenize_graph_query("   ") == []

    def test_single_word(self) -> None:
        """A single short word is too short (<2 chars) and filtered."""
        tokens = PluginPageApi._tokenize_graph_query("a")
        assert tokens == []

    def test_english_tokens(self) -> None:
        """English tokens are split and normalized."""
        tokens = PluginPageApi._tokenize_graph_query("hello world test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens
        # No duplicates
        assert len(tokens) == len(set(tokens))

    def test_chinese_tokens(self) -> None:
        """Chinese characters produce compact token and n-grams."""
        tokens = PluginPageApi._tokenize_graph_query("你好世界")
        # Should include compact form and n-grams
        assert "你好世界" in tokens

    def test_duplicate_removal(self) -> None:
        """Duplicate tokens are removed."""
        tokens = PluginPageApi._tokenize_graph_query("hello hello hello")
        assert len(tokens) == 1
        assert "hello" in tokens

    def test_punctuation_handled(self) -> None:
        """Punctuation is replaced with spaces."""
        tokens = PluginPageApi._tokenize_graph_query("hello, world!")
        assert "hello" in tokens
        assert "world" in tokens

    def test_max_12_tokens(self) -> None:
        """令牌 list is capped at 12."""
        long_query = " ".join(f"word{i}" for i in range(50))
        tokens = PluginPageApi._tokenize_graph_query(long_query)
        assert len(tokens) <= 12


class TestBuildGraphViewPayload:
    """测试 _build_graph_view_payload static method."""

    def test_basic_payload_structure(self) -> None:
        """基本 snapshot produces correctly-shaped payload."""
        snapshot = {
            "nodes": [],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        assert payload["nodes"] == []
        assert payload["edges"] == []
        assert payload["enabled"] is True
        assert payload["mode"] == "local"
        assert "summary" in payload

    def test_isolated_edges_filtered(self) -> None:
        """Edges referencing non-existent nodes are filtered out."""
        snapshot = {
            "nodes": [{"id": 1}, {"id": 2}],
            "edges": [
                {"source": 1, "target": 2},
                {"source": 3, "target": 4},  # isolated
                {"source": 1, "target": 5},  # half isolated
            ],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        assert len(payload["edges"]) == 1
        assert payload["edges"][0]["source"] == 1

    def test_top_nodes_sorted(self) -> None:
        """Top nodes are sorted by weight/degree and limited to 8."""
        snapshot = {
            "nodes": [
                {"id": i, "weight": float(10 - i), "degree": i, "label": f"node{i}"}
                for i in range(15)
            ],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        assert len(payload["top_nodes"]) == 8
        # First node should be highest weight (id=0, weight=10)
        assert payload["top_nodes"][0]["weight"] == 10.0

    def test_matched_node_highlighting(self) -> None:
        """Nodes in matched_node_ids are flagged as highlighted."""
        snapshot = {
            "nodes": [{"id": 1}, {"id": 2}, {"id": 3}],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="local",
            matched_node_ids=[1, 3],
        )
        nodes = payload["nodes"]
        assert nodes[0]["highlighted"] is True   # id=1
        assert nodes[1]["highlighted"] is False  # id=2
        assert nodes[2]["highlighted"] is True   # id=3

    def test_node_type_breakdown(self) -> None:
        """节点 type breakdown is computed in summary."""
        snapshot = {
            "nodes": [
                {"id": 1, "type": "person"},
                {"id": 2, "type": "person"},
                {"id": 3, "type": "place"},
            ],
            "edges": [
                {"source": 1, "target": 2, "relation_type": "friend"},
                {"source": 1, "target": 3, "relation_type": "visited"},
            ],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        breakdown = payload["summary"]["node_type_breakdown"]
        assert breakdown["person"] == 2
        assert breakdown["place"] == 1

    def test_relation_breakdown(self) -> None:
        """Relation type breakdown is computed in summary."""
        snapshot = {
            "nodes": [{"id": 1}, {"id": 2}],
            "edges": [
                {"source": 1, "target": 2, "relation_type": "friend"},
                {"source": 1, "target": 2, "relation_type": "friend"},
            ],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        breakdown = payload["summary"]["relation_breakdown"]
        assert breakdown["friend"] == 2

    def test_default_type_and_relation(self) -> None:
        """缺失 type/relation_type defaults to 'unknown'/'related'."""
        snapshot = {
            "nodes": [{"id": 1}],  # no type
            "edges": [{"source": 1, "target": 1}],  # no relation_type
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        assert payload["summary"]["node_type_breakdown"]["unknown"] == 1
        assert payload["summary"]["relation_breakdown"]["related"] == 1

    def test_none_type_and_relation_fallback(self) -> None:
        """None type and None relation_type also get fallback values."""
        snapshot = {
            "nodes": [{"id": 1, "type": None}],
            "edges": [{"source": 1, "target": 1, "relation_type": None}],
            "entries": [],
            "memories": [],
        }
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="local"
        )
        assert payload["summary"]["node_type_breakdown"]["unknown"] == 1
        assert payload["summary"]["relation_breakdown"]["related"] == 1

    def test_retrieval_lookup_on_memories(self) -> None:
        """Retrieval items are merged into memory entries by memory_id."""
        snapshot = {
            "nodes": [],
            "edges": [],
            "entries": [],
            "memories": [
                {"memory_id": 42, "text": "test"},
                {"memory_id": 99, "text": "other"},
            ],
        }
        retrieval_items = [
            {"memory_id": 42, "final_score": 0.95},
        ]
        stats = {}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="local",
            retrieval_items=retrieval_items,
        )
        memories = payload["snapshot"]["memories"]
        assert "retrieval" in memories[0]
        assert memories[0]["retrieval"]["final_score"] == 0.95
        assert "retrieval" not in memories[1]

    def test_summary_counts(self) -> None:
        """Summary section reports item counts and stats."""
        snapshot = {
            "nodes": [{"id": 1}, {"id": 2}, {"id": 3}],
            "edges": [{"source": 1, "target": 2}],
            "entries": [{"id": 1}],
            "memories": [{"memory_id": 1}],
        }
        stats = {"graph_nodes": 100, "graph_edges": 50, "graph_entries": 30}
        payload = PluginPageApi._build_graph_view_payload(
            snapshot, stats, enabled=True, mode="remote"
        )
        s = payload["summary"]
        assert s["visible_node_count"] == 3
        assert s["visible_edge_count"] == 1
        assert s["visible_entry_count"] == 1
        assert s["visible_memory_count"] == 1
        assert s["graph_node_count"] == 100
        assert s["graph_edge_count"] == 50
        assert s["graph_entry_count"] == 30
        assert s["graph_memory_enabled"] is True

    def test_tolerates_malformed_snapshot_retrieval_and_stats(self) -> None:
        """Malformed graph payload inputs should be skipped or normalized, not crash the builder."""
        snapshot = {
            "nodes": [
                {"id": "1", "weight": "2.5", "degree": "bad", "label": "alpha"},
                {"id": 2, "weight": None, "degree": 3, "type": None},
                "bad-node",
            ],
            "edges": [
                {"source": "1", "target": 2, "relation_type": None},
                {"source": 9, "target": 10, "relation_type": "isolated"},
                "bad-edge",
            ],
            "entries": [{"id": 1}, "bad-entry"],
            "memories": [
                {
                    "memory_id": "42",
                    "entry_count": "bad",
                    "node_count": 2,
                    "edge_count": "3",
                    "importance": "0.8",
                },
                {"memory_id": "bad-memory-id"},
                "bad-memory",
            ],
        }
        retrieval_items = [
            {"memory_id": "42", "final_score": 0.91},
            {"memory_id": "oops", "final_score": 0.5},
            "bad-retrieval",
        ]
        payload = PluginPageApi._build_graph_view_payload(
            snapshot,
            {"graph_nodes": "100", "graph_edges": None, "graph_entries": "oops"},
            enabled=True,
            mode="query",
            retrieval_items=retrieval_items,
            matched_node_ids=["1", "bad", 2],
        )

        assert [node["id"] for node in payload["nodes"]] == ["1", 2]
        assert payload["nodes"][0]["highlighted"] is True
        assert payload["nodes"][1]["highlighted"] is True
        assert payload["edges"] == [{"source": "1", "target": 2, "relation_type": None}]
        assert payload["summary"]["relation_breakdown"] == {"related": 1}
        assert payload["summary"]["node_type_breakdown"]["unknown"] == 2
        assert payload["summary"]["graph_node_count"] == 100
        assert payload["summary"]["graph_edge_count"] == 0
        assert payload["summary"]["graph_entry_count"] == 0
        assert payload["matched_node_ids"] == [1, 2]
        assert payload["matched_memory_ids"] == ["42"]
        assert payload["retrieval"]["total"] == 1
        assert len(payload["snapshot"]["entries"]) == 1
        assert len(payload["snapshot"]["memories"]) == 1
        assert payload["snapshot"]["memories"][0]["retrieval"]["final_score"] == 0.91
        assert payload["top_memories"][0]["memory_id"] == "42"


class TestGetGraphStore:
    """测试 _get_graph_store static method."""

    def test_retrieves_graph_store_attr(self) -> None:
        """Gets graph_store from memory_engine if present."""
        engine = MagicMock()
        engine.graph_store = "fake_store"
        assert PluginPageApi._get_graph_store(engine) == "fake_store"

    def test_returns_none_when_missing(self) -> None:
        """Returns None when engine has no graph_store attr."""
        engine = MagicMock(spec=[])  # no graph_store
        assert PluginPageApi._get_graph_store(engine) is None


# ---------------------------------------------------------------------------
# PluginPageApi instance + _ensure_plugin_ready
# ---------------------------------------------------------------------------


class TestEnsurePluginReady:
    """测试 _ensure_plugin_ready 异步方法。"""

    @pytest.fixture
    def api(self) -> PluginPageApi:
        """创建 a PluginPageApi instance with a mocked plugin."""
        plugin = MagicMock()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.index_validator = MagicMock()
        return PluginPageApi(plugin)

    @pytest.mark.asyncio
    async def test_ready_returns_components(self, api: PluginPageApi) -> None:
        """当 plugin is ready, returns engine components dict."""
        ready_dict, error_dict = await api._ensure_plugin_ready()
        assert ready_dict is not None
        assert error_dict is None
        assert "memory_engine" in ready_dict
        assert "conversation_manager" in ready_dict
        assert "index_validator" in ready_dict

    @pytest.mark.asyncio
    async def test_not_ready_returns_error(self) -> None:
        """当 plugin._ensure_plugin_ready returns False, returns error."""
        plugin = MagicMock()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "Not ready yet"))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        ready_dict, error_dict = await api._ensure_plugin_ready()
        assert ready_dict is None
        assert error_dict is not None
        assert error_dict["status"] == "error"
        assert "Not ready yet" in error_dict["message"]

    @pytest.mark.asyncio
    async def test_not_ready_no_message_uses_default(self) -> None:
        """当 plugin returns False with no message, uses default message."""
        plugin = MagicMock()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(False, None))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        ready_dict, error_dict = await api._ensure_plugin_ready()
        assert ready_dict is None
        assert error_dict is not None
        assert "插件尚未就绪" in error_dict["message"]

    @pytest.mark.asyncio
    async def test_ensure_plugin_ready_exception(self) -> None:
        """当 _ensure_plugin_ready raises, returns error dict."""
        plugin = MagicMock()
        plugin._ensure_plugin_ready = AsyncMock(side_effect=RuntimeError("Boom"))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        ready_dict, error_dict = await api._ensure_plugin_ready()
        assert ready_dict is None
        assert error_dict is not None
        assert error_dict["status"] == "error"
        assert "Boom" in error_dict["message"]

    @pytest.mark.asyncio
    async def test_memory_engine_none_returns_error(self) -> None:
        """当 memory_engine is None, returns error."""
        plugin = MagicMock()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = None  # engine not ready
        api = PluginPageApi(plugin)
        ready_dict, error_dict = await api._ensure_plugin_ready()
        assert ready_dict is None
        assert error_dict is not None
        assert "记忆引擎" in error_dict["message"]

    @pytest.mark.asyncio
    async def test_initializer_exception(self) -> None:
        """当 accessing initializer raises, returns error."""
        plugin = MagicMock()
        plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))

        class _BadInit:
            @property
            def memory_engine(self) -> None:
                msg = "Init gone"
                raise RuntimeError(msg)

        plugin.initializer = _BadInit()
        api = PluginPageApi(plugin)
        ready_dict, error_dict = await api._ensure_plugin_ready()
        assert ready_dict is None
        assert error_dict is not None
        assert error_dict["status"] == "error"


class TestMaintenanceWriteGuard:
    """测试 Page API 维护写入守卫辅助函数。"""

    def test_returns_none_when_backup_manager_missing(self) -> None:
        plugin = SimpleNamespace()
        api = PluginPageApi(plugin)

        assert api._maintenance_write_guard() is None

    def test_returns_none_when_no_pending_restores(self) -> None:
        plugin = MagicMock()
        plugin._backup_manager = MagicMock()
        plugin._backup_manager.has_pending_restores.return_value = False
        api = PluginPageApi(plugin)

        assert api._maintenance_write_guard() is None

    def test_pending_restore_response_redacts_file_paths(self) -> None:
        secret = "C:/secret/pending_restore.db"
        plugin = MagicMock()
        plugin._backup_manager = MagicMock()
        plugin._backup_manager.has_pending_restores.return_value = True
        plugin._backup_manager.list_pending_restores.return_value = [secret]
        api = PluginPageApi(plugin)

        result = api._maintenance_write_guard()

        assert result == {
            "status": "error",
            "message": "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。",
            "code": "maintenance_blocked",
        }
        assert secret not in str(result)

    def test_returns_error_when_restore_status_check_raises(self) -> None:
        plugin = MagicMock()
        plugin._backup_manager = MagicMock()
        plugin._backup_manager.has_pending_restores.side_effect = RuntimeError("status broken")
        api = PluginPageApi(plugin)

        result = api._maintenance_write_guard()

        assert result == {
            "status": "error",
            "message": "维护状态检查失败，请稍后重试。",
            "code": "maintenance_guard_failed",
        }
        assert "status broken" not in str(result)

    def test_pending_restore_without_listing_support_still_returns_guard(self) -> None:
        class BackupManagerStub:
            def has_pending_restores(self):
                return True

        plugin = MagicMock()
        plugin._backup_manager = BackupManagerStub()
        api = PluginPageApi(plugin)

        result = api._maintenance_write_guard()

        assert result == {
            "status": "error",
            "message": "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。",
            "code": "maintenance_blocked",
        }

    def test_pending_restore_listing_failure_is_redacted_and_still_blocks(
        self,
    ) -> None:
        secret = r"pending-list-secret C:\private\restore.queue"
        backup_manager = MagicMock()
        backup_manager.has_pending_restores.return_value = True
        backup_manager.list_pending_restores.side_effect = RuntimeError(secret)
        api = PluginPageApi(SimpleNamespace(_backup_manager=backup_manager))

        with patch("core.page_api.logger.debug") as log_debug:
            result = api._maintenance_write_guard()

        assert result == {
            "status": "error",
            "message": "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。",
            "code": "maintenance_blocked",
        }
        assert secret not in str(result)
        assert all(secret not in str(arg) for arg in log_debug.call_args.args)
        assert log_debug.call_args.kwargs == {}
        log_debug.assert_called_once_with(
            "[页面接口] operation=%s error_class=%s",
            "maintenance_write_guard_list_pending",
            "RuntimeError",
        )


# ---------------------------------------------------------------------------
# _get_memory_record tests
# ---------------------------------------------------------------------------


class TestGetMemoryRecord:
    """测试 _get_memory_record async method."""

    @pytest.fixture
    def api_with_engine(self) -> PluginPageApi:
        """PluginPageApi with a memory_engine that has get_memory."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.get_memory = AsyncMock(return_value={
            "id": 1, "doc_id": "doc-1", "text": "hello",
            "metadata": {"key": "val"},
            "created_at": "2024-01-01", "updated_at": "2024-01-02",
        })
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        return PluginPageApi(plugin)

    @pytest.mark.asyncio
    async def test_returns_memory_from_engine(self, api_with_engine: PluginPageApi) -> None:
        """当 engine.get_memory returns a record, it is returned directly."""
        result = await api_with_engine._get_memory_record(1)
        assert result is not None
        assert result["id"] == 1
        assert result["text"] == "hello"

    @pytest.mark.asyncio
    async def test_returns_none_when_engine_is_none(self) -> None:
        """当 memory_engine is None, returns None."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = None
        api = PluginPageApi(plugin)
        result = await api._get_memory_record(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_db_query(self) -> None:
        """当 engine.get_memory returns None, falls back to raw DB query."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.get_memory = AsyncMock(return_value=None)  # engine miss
        engine.db_connection = AsyncMock()
        engine.db_connection.execute = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(
            1, "doc-1", "hello", '{"a":1}', "2024-01-01", "2024-01-02"
        ))
        engine.db_connection.execute.return_value = cursor
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)
        result = await api._get_memory_record(1)
        assert result is not None
        assert result["id"] == 1
        assert result["text"] == "hello"
        assert result["metadata"] == {"a": 1}

    @pytest.mark.asyncio
    async def test_db_fallback_returns_none_when_no_row(self) -> None:
        """当 DB query returns no row, returns None."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.get_memory = AsyncMock(return_value=None)
        engine.db_connection = AsyncMock()
        engine.db_connection.execute = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        engine.db_connection.execute.return_value = cursor
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)
        result = await api._get_memory_record(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_db_fallback_handles_exception(self) -> None:
        """当 DB query fails, returns None (with warning)."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.get_memory = AsyncMock(return_value=None)
        engine.db_connection = AsyncMock()
        engine.db_connection.execute = AsyncMock(side_effect=Exception("DB error"))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)
        result = await api._get_memory_record(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_db_fallback_when_no_connection(self) -> None:
        """当 db_connection is None, returns None."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.get_memory = AsyncMock(return_value=None)
        engine.db_connection = None
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)
        result = await api._get_memory_record(1)
        assert result is None


# ---------------------------------------------------------------------------
# get_groups tests
# ---------------------------------------------------------------------------


class TestGetGroups:
    """测试跨数据源的群组聚合。"""

    @pytest.mark.asyncio
    async def test_includes_groups_from_jargon_store(self, tmp_db_path: str) -> None:
        """Jargon groups are read from the real jargon_terms table."""
        store = JargonStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert(
                JargonMeaning(
                    term="梗",
                    group_id="group-jargon",
                    meaning="meaning",
                    confidence=0.9,
                    is_jargon=True,
                    is_confirmed=True,
                )
            )

            plugin = SimpleNamespace(
                initializer=SimpleNamespace(
                    jargon_store=store,
                    memory_engine=None,
                    conversation_manager=None,
                )
            )
            api = PluginPageApi(plugin)

            result = await api.get_groups()

            assert result["status"] == "ok"
            assert {
                "group_id": "group-jargon",
                "source": "jargon",
                "message_count": 0,
            } in result["data"]["groups"]
            assert result["data"]["sources"]["jargon"] == {"ok": True, "count": 1}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_awaits_async_relation_manager_list_all(self) -> None:
        """Social groups are included when RelationManager.list_all is async."""

        class RelationManagerStub:
            async def list_all(self):
                return [SimpleNamespace(group_id="group-social")]

            async def get_relations_by_group(self, group_id):
                return []

        plugin = SimpleNamespace(
            initializer=SimpleNamespace(
                relation_manager=RelationManagerStub(),
                memory_engine=None,
                conversation_manager=None,
            )
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        assert {
            "group_id": "group-social",
            "source": "social",
            "message_count": 0,
        } in result["data"]["groups"]
        assert result["data"]["sources"]["social"] == {"ok": True, "count": 1}

    @pytest.mark.asyncio
    async def test_includes_groups_from_affection_store_plugin_fallback(self) -> None:
        """Affection groups are discovered from plugin-level store fallback."""

        class AffectionStoreStub:
            async def list_group_ids(self):
                return ["group-affection"]

        plugin = SimpleNamespace(
            _affection_store=AffectionStoreStub(),
            initializer=SimpleNamespace(
                memory_engine=None,
                conversation_manager=None,
            ),
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        assert {
            "group_id": "group-affection",
            "source": "affection",
            "message_count": 0,
        } in result["data"]["groups"]
        assert result["data"]["sources"]["affection"] == {"ok": True, "count": 1}

    @pytest.mark.asyncio
    async def test_merges_duplicate_groups_and_keeps_highest_message_count(self) -> None:
        """Duplicate groups across sources collapse to one entry with max count."""

        class JargonStoreStub:
            async def list_group_ids(self):
                return ["shared-group"]

        class ConversationStoreStub:
            async def list_session_origins(self):
                return [
                    {"session_id": "shared-group", "message_count": 5},
                    {"session_id": "conv-only", "message_count": 3},
                ]

        engine = SimpleNamespace(
            stats=AsyncMock(
                return_value={
                    "sessions": {
                        "shared-group": {"message_count": 7},
                        "session-only": {"message_count": 2},
                    }
                }
            )
        )
        plugin = SimpleNamespace(
            initializer=SimpleNamespace(
                jargon_store=JargonStoreStub(),
                memory_engine=engine,
                conversation_manager=SimpleNamespace(store=ConversationStoreStub()),
            )
        )
        api = PluginPageApi(plugin)
        api._get_jargon_store = AsyncMock(return_value=JargonStoreStub())  # type: ignore[attr-defined]

        result = await api.get_groups()

        assert result["status"] == "ok"
        groups = {item["group_id"]: item for item in result["data"]["groups"]}
        assert groups["shared-group"]["message_count"] == 7
        assert groups["shared-group"]["source"] == "jargon"
        assert groups["conv-only"]["message_count"] == 3
        assert groups["session-only"]["message_count"] == 2

    @pytest.mark.asyncio
    async def test_social_source_count_uses_distinct_groups_in_list_all_fallback(self) -> None:
        """Social fallback count should report distinct groups, not relation rows."""

        class RelationManagerStub:
            async def list_all(self):
                return [
                    SimpleNamespace(group_id="group-social"),
                    SimpleNamespace(group_id="group-social"),
                    SimpleNamespace(group_id="group-other"),
                ]

        plugin = SimpleNamespace(
            initializer=SimpleNamespace(
                relation_manager=RelationManagerStub(),
                memory_engine=None,
                conversation_manager=None,
            )
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        assert result["data"]["sources"]["social"] == {"ok": True, "count": 2}

    @pytest.mark.asyncio
    async def test_social_source_tolerates_non_iterable_list_all_result(self) -> None:
        """A malformed social list_all result should degrade to an empty social source."""

        class RelationManagerStub:
            async def list_all(self):
                return 123

        plugin = SimpleNamespace(
            initializer=SimpleNamespace(
                relation_manager=RelationManagerStub(),
                memory_engine=None,
                conversation_manager=None,
            )
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        assert result["data"]["sources"]["social"] == {"ok": True, "count": 0}

    @pytest.mark.asyncio
    async def test_reports_partial_source_failures(self) -> None:
        """A failing source should be visible in the response envelope."""

        class BrokenJargonStore:
            async def list_group_ids(self):
                raise RuntimeError("jargon db unavailable")

        plugin = SimpleNamespace(
            config_manager=SimpleNamespace(get=lambda key, default=None: False),
            initializer=SimpleNamespace(
                jargon_store=BrokenJargonStore(),
                memory_engine=None,
                conversation_manager=None,
            ),
        )
        api = PluginPageApi(plugin)
        api._get_jargon_store = AsyncMock(return_value=BrokenJargonStore())  # type: ignore[attr-defined]

        result = await api.get_groups()

        assert result["status"] == "ok"
        assert result["data"]["groups"] == []
        assert result["data"]["sources"]["jargon"]["ok"] is False
        assert result["data"]["sources"]["jargon"]["error"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_debug_mode_exposes_detailed_source_errors(self) -> None:
        """Debug mode should return the concrete error string for failing sources."""

        class BrokenConversationStore:
            async def list_session_origins(self):
                raise ValueError("session schema drift")

        plugin = SimpleNamespace(
            config_manager=SimpleNamespace(
                get=lambda key, default=None: True if key == "debug" else default
            ),
            initializer=SimpleNamespace(
                jargon_store=None,
                memory_engine=None,
                conversation_manager=SimpleNamespace(store=BrokenConversationStore()),
            ),
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        assert result["data"]["sources"]["conversation"]["ok"] is False
        assert result["data"]["sources"]["conversation"]["error"] == "session schema drift"

    @pytest.mark.asyncio
    async def test_conversation_source_skips_malformed_origin_items(self) -> None:
        """Malformed conversation origin rows should be skipped without failing the source."""

        class ConversationStoreStub:
            async def list_session_origins(self):
                return [
                    {"session_id": "group-good", "message_count": "3"},
                    "bad-origin-row",
                    {"session_id": "group-other", "message_count": "oops"},
                    {"session_id": "", "message_count": 9},
                ]

        plugin = SimpleNamespace(
            initializer=SimpleNamespace(
                jargon_store=None,
                memory_engine=None,
                conversation_manager=SimpleNamespace(store=ConversationStoreStub()),
            )
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        groups = {item["group_id"]: item for item in result["data"]["groups"]}
        assert groups["group-good"]["message_count"] == 3
        assert groups["group-good"]["source"] == "conversation"
        assert groups["group-other"]["message_count"] == 0
        assert result["data"]["sources"]["conversation"] == {"ok": True, "count": 2}

    @pytest.mark.asyncio
    async def test_session_source_tolerates_malformed_stats_payload(self) -> None:
        """Malformed session stats payload should degrade to an empty session source."""

        class ConversationStoreStub:
            async def list_session_origins(self):
                return [{"session_id": "group-conv", "message_count": 4}]

        engine = SimpleNamespace(stats=AsyncMock(return_value="bad-stats"))
        plugin = SimpleNamespace(
            initializer=SimpleNamespace(
                jargon_store=None,
                memory_engine=engine,
                conversation_manager=SimpleNamespace(store=ConversationStoreStub()),
            )
        )
        api = PluginPageApi(plugin)

        result = await api.get_groups()

        assert result["status"] == "ok"
        groups = {item["group_id"]: item for item in result["data"]["groups"]}
        assert groups["group-conv"]["message_count"] == 4
        assert result["data"]["sources"]["session"] == {"ok": True, "count": 0}
        assert result["data"]["sources"]["conversation"] == {"ok": True, "count": 1}


# ---------------------------------------------------------------------------
# register_routes tests
# ---------------------------------------------------------------------------


class TestRegisterRoutes:
    """测试 register_routes 方法。"""

    def test_register_routes_calls_register_web_api(self) -> None:
        """register_routes calls context.register_web_api multiple times."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        api.register_routes()
        register = plugin.context.register_web_api
        # At minimum several routes should be registered
        assert register.call_count >= 10

    def test_register_routes_adds_page_name_aliases(self) -> None:
        """Page API works from metadata id and known page-name aliases."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        paths = [c[0][0] for c in plugin.context.register_web_api.call_args_list]
        assert f"{PAGE_API_PREFIX}/stats" in paths
        assert "/Memora/page/stats" in paths
        assert "/Memora/page/stats" in paths

    def test_registered_aliases_match_astrbot_dashboard_bridge_paths(self) -> None:
        """AstrBot dashboard forwards `{pluginName}/page/...` into registered APIs."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        registered_paths = [
            call.args[0] for call in plugin.context.register_web_api.call_args_list
        ]
        for forwarded_path in (
            "/Memora/page/stats",
            f"{PAGE_API_PREFIX}/stats",
        ):
            assert any(
                re.fullmatch(re.escape(route), forwarded_path)
                for route in registered_paths
            ), forwarded_path

    def test_routes_start_with_prefix(self) -> None:
        """所有 route paths start with PAGE_API_PREFIX."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        api.register_routes()
        register = plugin.context.register_web_api
        allowed_prefixes = (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES)
        for call_args in register.call_args_list:
            route_path = call_args[0][0]  # first positional arg
            assert route_path.startswith(allowed_prefixes), f"Bad route: {route_path}"

    def test_stats_route_registered(self) -> None:
        """Stats endpoint is registered."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        api.register_routes()
        register = plugin.context.register_web_api
        paths = [c[0][0] for c in register.call_args_list]
        assert f"{PAGE_API_PREFIX}/stats" in paths

    def test_metrics_summary_route_registered(self) -> None:
        """Metrics summary endpoint is registered for dashboard observability."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)
        api.register_routes()
        register = plugin.context.register_web_api
        paths = [c[0][0] for c in register.call_args_list]
        assert f"{PAGE_API_PREFIX}/metrics/summary" in paths

    def test_config_routes_register_primary_aliases_and_metadata(self) -> None:
        plugin = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        calls = plugin.context.register_web_api.call_args_list
        registered = {
            (call.args[0], tuple(call.args[2])): call.args[1]
            for call in calls
        }
        for suffix, methods in (
            ("/config/schema", ("GET",)),
            ("/config/state", ("GET",)),
            ("/config/apply", ("POST",)),
        ):
            assert (f"{PAGE_API_PREFIX}{suffix}", methods) in registered
            assert (f"/Memora/page{suffix}", methods) in registered

        metadata = {item["path"]: item for item in api.get_route_metadata()}
        schema_metadata = metadata[f"{PAGE_API_PREFIX}/config/schema"]
        state_metadata = metadata[f"{PAGE_API_PREFIX}/config/state"]
        apply_metadata = metadata[f"{PAGE_API_PREFIX}/config/apply"]

        assert schema_metadata["requires_ready"] is False
        assert state_metadata["requires_ready"] is False
        assert apply_metadata["requires_ready"] is False
        assert apply_metadata["risk"] == "maintenance"
        assert apply_metadata["auth"] == "admin"
        assert apply_metadata["write_guard"] is True
        assert all(not path.startswith("/Memora/page") for path in metadata)

    def test_social_write_routes_register_all_prefixes_as_post_only(self) -> None:
        plugin = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        registered = {
            (call.args[0], tuple(call.args[2])): call.args[1]
            for call in plugin.context.register_web_api.call_args_list
        }
        prefixes = (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES)
        handlers = {
            "/social/create": api.create_social_relation,
            "/social/update": api.update_social_relation,
            "/social/delete": api.delete_social_relation,
            "/social/batch": api.batch_social_relations,
        }
        for prefix in prefixes:
            for suffix, handler in handlers.items():
                assert registered[(f"{prefix}{suffix}", ("POST",))] == handler
                assert (f"{prefix}{suffix}", ("GET",)) not in registered

    def test_profile_create_registers_all_prefixes_as_post_only(self) -> None:
        plugin = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        registered = {
            (call.args[0], tuple(call.args[2])): call.args[1]
            for call in plugin.context.register_web_api.call_args_list
        }
        for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
            path = f"{prefix}/profiles/create"
            assert registered[(path, ("POST",))] == api.create_profile
            assert (path, ("GET",)) not in registered

        metadata = {item["path"]: item for item in api.get_route_metadata()}
        create = metadata[f"{PAGE_API_PREFIX}/profiles/create"]
        assert create["methods"] == ["POST"]
        assert create["auth"] == "admin"
        assert create["risk"] == "write"
        assert create["write_guard"] is True

    def test_jargon_write_routes_register_all_prefixes_as_post_only(self) -> None:
        plugin = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        registered = {
            (call.args[0], tuple(call.args[2])): call.args[1]
            for call in plugin.context.register_web_api.call_args_list
        }
        handlers = {
            "/jargon/create": api.create_jargon,
            "/jargon/update": api.update_jargon,
            "/jargon/delete": api.delete_jargon,
            "/jargon/batch": api.batch_jargon,
        }
        for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
            for suffix, handler in handlers.items():
                assert registered[(f"{prefix}{suffix}", ("POST",))] == handler
                assert (f"{prefix}{suffix}", ("GET",)) not in registered

        metadata = {item["path"]: item for item in api.get_route_metadata()}
        for suffix in handlers:
            route = metadata[f"{PAGE_API_PREFIX}{suffix}"]
            assert route["methods"] == ["POST"]
            assert route["auth"] == "admin"
            assert route["write_guard"] is True
        assert metadata[f"{PAGE_API_PREFIX}/jargon/create"]["risk"] == "write"
        assert metadata[f"{PAGE_API_PREFIX}/jargon/update"]["risk"] == "write"
        assert metadata[f"{PAGE_API_PREFIX}/jargon/delete"]["risk"] == "destructive"
        assert metadata[f"{PAGE_API_PREFIX}/jargon/batch"]["risk"] == "write"

    def test_affection_editing_routes_register_all_prefixes_with_expected_methods(self) -> None:
        plugin = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        registered = {
            (call.args[0], tuple(call.args[2])): call.args[1]
            for call in plugin.context.register_web_api.call_args_list
        }
        handlers = {
            "/affection/users": ("GET", api.list_affection_users),
            "/affection/users/create": ("POST", api.create_affection_user),
            "/affection/users/update": ("POST", api.update_affection_user),
            "/affection/users/delete": ("POST", api.delete_affection_user),
            "/affection/users/batch": ("POST", api.batch_affection_users),
            "/affection/mood/set": ("POST", api.set_affection_mood),
            "/affection/mood/reset": ("POST", api.reset_affection_mood),
            "/affection/moods/history": ("GET", api.get_affection_mood_history),
        }
        for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
            for suffix, (method, handler) in handlers.items():
                assert registered[(f"{prefix}{suffix}", (method,))] == handler
                other_method = "POST" if method == "GET" else "GET"
                assert (f"{prefix}{suffix}", (other_method,)) not in registered

        metadata = {item["path"]: item for item in api.get_route_metadata()}
        assert metadata[f"{PAGE_API_PREFIX}/affection/users/delete"]["risk"] == "destructive"
        assert metadata[f"{PAGE_API_PREFIX}/affection/mood/reset"]["write_guard"] is True

    def test_route_metadata_declares_risk_auth_and_guards_for_post_routes(self) -> None:
        """Plugin-side route metadata is available for audit and frontend contracts."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        api = PluginPageApi(plugin)

        api.register_routes()

        metadata = api.get_route_metadata()
        registered_paths = [c[0][0] for c in plugin.context.register_web_api.call_args_list]
        primary_registered_paths = [
            path for path in registered_paths if path.startswith(PAGE_API_PREFIX)
        ]
        assert [item["path"] for item in metadata] == primary_registered_paths

        for route in metadata:
            assert route["auth"] in {"admin", "host"}
            assert route["risk"] in {
                "read",
                "write",
                "maintenance",
                "destructive",
                "runtime_exec",
            }
            if "POST" in route["methods"]:
                assert route["auth"] == "admin"
                assert route["risk"] != "read"
            if route["risk"] in {"write", "destructive", "runtime_exec", "maintenance"}:
                assert route["write_guard"] is True

        by_path = {item["path"]: item for item in metadata}
        assert by_path[f"{PAGE_API_PREFIX}/dashboard/build"]["risk"] == "runtime_exec"
        assert by_path[f"{PAGE_API_PREFIX}/backup/delete"]["risk"] == "destructive"


class TestMaintenanceWriteGuardCoverage:
    """Pending restores should block state-changing maintenance endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name,blocked_attr",
        [
            ("start_backfill", "_backfill_scheduler"),
            ("reset_quality", "_quality_scorer"),
            ("install_dashboard_deps", None),
        ],
    )
    async def test_pending_restore_blocks_maintenance_mutators(
        self,
        method_name: str,
        blocked_attr: str | None,
    ) -> None:
        backup_manager = MagicMock()
        backup_manager.has_pending_restores.return_value = True
        backup_manager.list_pending_restores.return_value = ["memory.db.pending_restore"]
        plugin = MagicMock()
        plugin._backup_manager = backup_manager
        plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = MagicMock()
        plugin.config_manager.get.return_value = True

        if blocked_attr == "_backfill_scheduler":
            scheduler = MagicMock()
            scheduler.is_running = False
            scheduler.start = AsyncMock(return_value="job-1")
            plugin._backfill_scheduler = scheduler
        elif blocked_attr == "_quality_scorer":
            scorer = MagicMock()
            scorer._score_history = []
            scorer._alert_history = []
            plugin._quality_scorer = scorer

        api = PluginPageApi(plugin)
        result = await getattr(api, method_name)()

        assert result["status"] == "error"
        assert "备份恢复" in result["message"]
        if blocked_attr == "_backfill_scheduler":
            plugin._backfill_scheduler.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_guard_check_failure_is_redacted_before_profile_create(self) -> None:
        secret = r"guard-secret C:\private\pending_restore.db"
        backup_manager = MagicMock()
        backup_manager.has_pending_restores.side_effect = RuntimeError(secret)
        plugin = SimpleNamespace(_backup_manager=backup_manager)
        api = PluginPageApi(plugin)
        api._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("engine lookup must not run")
        )
        request_mock = MagicMock()
        request_mock.get_json = AsyncMock(
            side_effect=AssertionError("JSON parsing must not run")
        )

        with (
            patch("core.page_api.logger.error") as log_error,
            patch("core.api.profile_api.request", request_mock),
        ):
            result = await api.create_profile()

        assert result == {
            "status": "error",
            "message": "维护状态检查失败，请稍后重试。",
            "code": "maintenance_guard_failed",
        }
        log_error.assert_called_once_with(
            "[页面接口] operation=%s error_class=%s",
            "maintenance_write_guard",
            "RuntimeError",
        )
        assert secret not in str(result)
        assert secret not in str(log_error.call_args_list)
        request_mock.get_json.assert_not_awaited()
        api._ensure_plugin_ready.assert_not_awaited()


# ---------------------------------------------------------------------------
# SSE stream tests
# ---------------------------------------------------------------------------


class TestSseStream:
    """测试 sse_stream 方法。"""

    @pytest.mark.asyncio
    async def test_sse_stream_when_available(self) -> None:
        """当 engine.sse.stream is available, returns it."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.sse = MagicMock()
        engine.sse.stream = AsyncMock(return_value="stream_response")
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)
        result = await api.sse_stream()
        assert result == "stream_response"

    @pytest.mark.asyncio
    async def test_sse_stream_when_engine_none(self) -> None:
        """当 engine is None, returns error dict."""
        plugin = MagicMock()
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = None
        api = PluginPageApi(plugin)
        result = await api.sse_stream()
        assert result["status"] == "error"
        assert "SSE" in result["message"]

    @pytest.mark.asyncio
    async def test_sse_stream_when_no_sse_attr(self) -> None:
        """当 engine has no sse attribute, returns error dict."""
        plugin = MagicMock()
        engine = MagicMock(spec=[])  # no sse attr
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)
        result = await api.sse_stream()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_sse_stream_propagates_stream_exception(self) -> None:
        """当前 SSE behavior bubbles stream failures to the caller."""
        plugin = MagicMock()
        engine = MagicMock()
        engine.sse = MagicMock()
        engine.sse.stream = AsyncMock(side_effect=RuntimeError("stream exploded"))
        plugin.initializer = MagicMock()
        plugin.initializer.memory_engine = engine
        api = PluginPageApi(plugin)

        with pytest.raises(RuntimeError, match="stream exploded"):
            await api.sse_stream()
