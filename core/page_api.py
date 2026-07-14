"""
官方插件页面接口适配层，负责路由注册与共享辅助逻辑。
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from astrbot.api import logger

from .api.affection_api import AffectionApiMixin
from .api.backup_api import BackupApiMixin
from .api.config_api import ConfigApiMixin
from .api.delegation_api import DelegationApiMixin
from .api.diagnostics_api import DiagnosticsApiMixin
from .api.evaluation_api import EvaluationApiMixin
from .api.expression_api import ExpressionApiMixin
from .api.graph_api import GraphApiMixin
from .api.jargon_api import JargonApiMixin
from .api.knowledge_api import KnowledgeApiMixin
from .api.learning_api import LearningApiMixin
from .api.maintenance_api import MaintenanceApiMixin
from .api.memory_batch_api import MemoryBatchApiMixin
from .api.memory_read_api import MemoryReadApiMixin
from .api.memory_stats_recall_api import MemoryStatsRecallApiMixin
from .api.memory_write_api import MemoryWriteApiMixin
from .api.metrics_api import MetricsApiMixin
from .api.note_api import NoteApiMixin
from .api.profile_api import ProfileApiMixin
from .api.quality_api import QualityApiMixin
from .api.recall_trace_api import RecallTraceApiMixin
from .api.review_api import ReviewApiMixin
from .api.response_utils import error_response, ok_response
from .api.social_api import SocialApiMixin
from .api.topic_segmentation_api import TopicSegmentationApiMixin
from .storage.base import apply_perf_pragmas
from .utils.number_utils import safe_float

PLUGIN_NAME = "astrbot_plugin_memora"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
PAGE_API_ALIASES = ("Memora",)
PAGE_API_ALIAS_PREFIXES = tuple(f"/{name}/page" for name in PAGE_API_ALIASES)


class PluginPageApi(
    MemoryReadApiMixin,
    MemoryWriteApiMixin,
    MemoryBatchApiMixin,
    MemoryStatsRecallApiMixin,
    GraphApiMixin,
    MetricsApiMixin,
    DiagnosticsApiMixin,
    BackupApiMixin,
    ProfileApiMixin,
    KnowledgeApiMixin,
    NoteApiMixin,
    LearningApiMixin,
    MaintenanceApiMixin,
    ConfigApiMixin,
    TopicSegmentationApiMixin,
    QualityApiMixin,
    RecallTraceApiMixin,
    ReviewApiMixin,
    JargonApiMixin,
    DelegationApiMixin,
    EvaluationApiMixin,
    AffectionApiMixin,
    SocialApiMixin,
    ExpressionApiMixin,
):
    """记忆插件的官方页面接口集合。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._route_metadata: list[dict[str, Any]] = []

    async def sse_stream(self):
        """D4：基于 Quart 原生流式输出的 SSE 实时记忆流端点。"""
        initializer = getattr(self.plugin, "initializer", None)
        engine = getattr(initializer, "memory_engine", None)
        if engine is None or not hasattr(engine, "sse"):
            return {"status": "error", "message": "SSE 服务不可用"}
        return await engine.sse.stream()

    def register_routes(self) -> None:
        raw_register = self.plugin.context.register_web_api
        self._route_metadata = []

        def register(path, handler, methods, description):
            self._route_metadata.append(
                self._build_route_metadata(path, handler, methods, description)
            )
            result = raw_register(path, handler, methods, description)
            if path.startswith(PAGE_API_PREFIX):
                suffix = path[len(PAGE_API_PREFIX):]
                for alias_prefix in PAGE_API_ALIAS_PREFIXES:
                    raw_register(alias_prefix + suffix, handler, methods, f"{description}（别名）")
            return result

        register(
            f"{PAGE_API_PREFIX}/stats", self.get_stats, ["GET"], "页面接口：统计信息"
        )
        register(
            f"{PAGE_API_PREFIX}/metrics/summary",
            self.get_metrics_summary,
            ["GET"],
            "页面接口：运行观测摘要",
        )
        register(
            f"{PAGE_API_PREFIX}/diagnostics/health",
            self.get_diagnostics_health,
            ["GET"],
            "页面接口：诊断健康评分",
        )
        register(
            f"{PAGE_API_PREFIX}/diagnostics/events",
            self.get_diagnostics_events,
            ["GET"],
            "页面接口：诊断事件列表",
        )
        register(
            f"{PAGE_API_PREFIX}/diagnostics/events/detail",
            self.get_diagnostics_event_detail,
            ["GET"],
            "页面接口：诊断事件详情",
        )
        register(
            f"{PAGE_API_PREFIX}/diagnostics/actions/run",
            self.run_diagnostics_action,
            ["POST"],
            "页面接口：执行诊断恢复动作",
        )
        register(
            f"{PAGE_API_PREFIX}/evaluation/datasets",
            self.get_evaluation_datasets,
            ["GET"],
            "页面接口：检索评测数据集",
        )
        register(
            f"{PAGE_API_PREFIX}/evaluation/run",
            self.run_evaluation,
            ["POST"],
            "页面接口：运行检索评测",
        )
        register(
            f"{PAGE_API_PREFIX}/evaluation/reports",
            self.list_evaluation_reports,
            ["GET"],
            "页面接口：检索评测报告列表",
        )
        register(
            f"{PAGE_API_PREFIX}/evaluation/reports/detail",
            self.get_evaluation_report,
            ["GET"],
            "页面接口：检索评测报告详情",
        )
        register(
            f"{PAGE_API_PREFIX}/evaluation/reports/compare",
            self.compare_evaluation_reports,
            ["GET", "POST"],
            "页面接口：检索评测报告对比",
        )
        register(
            f"{PAGE_API_PREFIX}/memories",
            self.list_memories,
            ["GET"],
            "页面接口：记忆列表",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/detail",
            self.get_memory_detail,
            ["GET"],
            "页面接口：记忆详情",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/update",
            self.update_memory,
            ["POST"],
            "页面接口：更新记忆",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/batch-delete",
            self.batch_delete_memories,
            ["POST"],
            "页面接口：批量删除记忆",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/batch-update",
            self.batch_update_memories,
            ["POST"],
            "页面接口：批量更新记忆",
        )
        register(
            f"{PAGE_API_PREFIX}/recall/test",
            self.test_recall,
            ["POST"],
            "页面接口：召回测试",
        )
        register(
            f"{PAGE_API_PREFIX}/recall/trace",
            self.test_recall_with_trace,
            ["POST"],
            "页面接口：可解释召回跟踪",
        )
        register(
            f"{PAGE_API_PREFIX}/recall/trace/detail",
            self.get_recall_trace_detail,
            ["GET"],
            "页面接口：可解释召回跟踪详情",
        )
        register(
            f"{PAGE_API_PREFIX}/graph/overview",
            self.get_graph_overview,
            ["GET"],
            "页面接口：图谱概览",
        )
        register(
            f"{PAGE_API_PREFIX}/graph/query",
            self.query_graph,
            ["POST"],
            "页面接口：图谱查询",
        )
        register(
            f"{PAGE_API_PREFIX}/backups",
            self.list_backups,
            ["GET"],
            "页面接口：备份列表",
        )
        # v2.5：用户画像
        register(
            f"{PAGE_API_PREFIX}/profiles",
            self.list_profiles,
            ["GET"],
            "页面接口：画像列表",
        )
        register(
            f"{PAGE_API_PREFIX}/profiles/detail",
            self.get_profile_detail,
            ["GET"],
            "页面接口：画像详情",
        )
        register(
            f"{PAGE_API_PREFIX}/profiles/update",
            self.update_profile,
            ["POST"],
            "页面接口：更新画像",
        )
        register(
            f"{PAGE_API_PREFIX}/profiles/delete",
            self.delete_profile,
            ["POST"],
            "页面接口：删除画像",
        )
        register(
            f"{PAGE_API_PREFIX}/profiles/tags",
            self.manage_profile_tags,
            ["POST"],
            "页面接口：管理画像标签",
        )
        # v2.5：知识库
        register(
            f"{PAGE_API_PREFIX}/knowledge",
            self.list_knowledge,
            ["GET"],
            "页面接口：知识列表",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/search",
            self.search_knowledge,
            ["GET"],
            "页面接口：搜索知识",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/detail",
            self.get_knowledge_detail,
            ["GET"],
            "页面接口：知识详情",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/create",
            self.create_knowledge_entry,
            ["POST"],
            "页面接口：创建知识",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/update",
            self.update_knowledge_entry,
            ["POST"],
            "页面接口：更新知识",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/delete",
            self.delete_knowledge_entry,
            ["POST"],
            "页面接口：删除知识",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/batch-delete",
            self.batch_delete_knowledge,
            ["POST"],
            "页面接口：批量删除知识",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/batch-update",
            self.batch_update_knowledge,
            ["POST"],
            "页面接口：批量更新知识",
        )
        # v2.5：笔记
        register(
            f"{PAGE_API_PREFIX}/notes",
            self.list_notes,
            ["GET"],
            "页面接口：笔记列表",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/search",
            self.search_notes,
            ["GET"],
            "页面接口：搜索笔记",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/detail",
            self.get_note_detail,
            ["GET"],
            "页面接口：笔记详情",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/create",
            self.create_note,
            ["POST"],
            "页面接口：创建笔记",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/update",
            self.update_note,
            ["POST"],
            "页面接口：更新笔记",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/delete",
            self.delete_note,
            ["POST"],
            "页面接口：删除笔记",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/versions",
            self.get_note_versions,
            ["GET"],
            "页面接口：笔记版本",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/batch-delete",
            self.batch_delete_notes,
            ["POST"],
            "页面接口：批量删除笔记",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/batch-update",
            self.batch_update_notes,
            ["POST"],
            "页面接口：批量更新笔记",
        )
        # v2.5：自主学习
        register(
            f"{PAGE_API_PREFIX}/learning/status",
            self.get_learning_status,
            ["GET"],
            "页面接口：学习状态",
        )
        register(
            f"{PAGE_API_PREFIX}/learning/history",
            self.get_learning_history,
            ["GET"],
            "页面接口：学习历史",
        )
        register(
            f"{PAGE_API_PREFIX}/learning/reset",
            self.reset_learning,
            ["POST"],
            "页面接口：重置学习",
        )
        # D4：实时 SSE 流
        register(
            f"{PAGE_API_PREFIX}/realtime/stream",
            self.sse_stream,
            ["GET"],
            "页面接口：实时 SSE 流",
        )
        # 维护操作
        register(
            f"{PAGE_API_PREFIX}/maintenance/rebuild-index",
            self.rebuild_index,
            ["POST"],
            "页面接口：重建索引",
        )
        register(
            f"{PAGE_API_PREFIX}/maintenance/rebuild-graph",
            self.rebuild_graph_index,
            ["POST"],
            "页面接口：重建图索引",
        )
        register(
            f"{PAGE_API_PREFIX}/health/persistence",
            self.get_persistence_health,
            ["GET"],
            "页面接口：持久化健康检查",
        )
        register(
            f"{PAGE_API_PREFIX}/health/persistence/repair",
            self.repair_persistence_health,
            ["POST"],
            "页面接口：持久化健康修复",
        )
        register(
            f"{PAGE_API_PREFIX}/maintenance/purge-deleted",
            self.purge_deleted_memories,
            ["POST"],
            "页面接口：清理已删除记忆",
        )
        register(
            f"{PAGE_API_PREFIX}/maintenance/compact-db",
            self.compact_database,
            ["POST"],
            "页面接口：压缩数据库",
        )
        register(
            f"{PAGE_API_PREFIX}/maintenance/create-backup",
            self.create_backup,
            ["POST"],
            "页面接口：创建备份",
        )

        # === React 控制台兼容别名（v2.5.1） ===
        # memory 兼容别名：前端使用单数 memory，后端使用复数 memories
        register(
            f"{PAGE_API_PREFIX}/memory/detail",
            self.get_memory_detail,
            ["GET"],
            "页面接口：记忆详情（别名）",
        )
        register(
            f"{PAGE_API_PREFIX}/memory/update",
            self.update_memory,
            ["POST"],
            "页面接口：更新记忆（别名）",
        )
        # 统一批量端点
        register(
            f"{PAGE_API_PREFIX}/memories/batch",
            self.batch_memories,
            ["POST"],
            "页面接口：批量记忆操作（统一）",
        )
        register(
            f"{PAGE_API_PREFIX}/knowledge/batch",
            self.batch_knowledge,
            ["POST"],
            "页面接口：批量知识操作（统一）",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/batch",
            self.batch_notes,
            ["POST"],
            "页面接口：批量笔记操作（统一）",
        )
        register(
            f"{PAGE_API_PREFIX}/notes/archive",
            self.archive_note,
            ["POST"],
            "页面接口：归档笔记",
        )
        register(
            f"{PAGE_API_PREFIX}/profiles/batch",
            self.batch_delete_profiles,
            ["POST"],
            "页面接口：批量画像操作",
        )
        # 图谱 GET 搜索别名
        register(
            f"{PAGE_API_PREFIX}/graph/search",
            self.search_graph,
            ["GET"],
            "页面接口：图谱搜索（GET 别名）",
        )
        # system/backup 短路径兼容别名
        register(
            f"{PAGE_API_PREFIX}/system/rebuild",
            self.rebuild_index,
            ["POST"],
            "页面接口：重建索引（别名）",
        )
        register(
            f"{PAGE_API_PREFIX}/system/purge",
            self.purge_deleted_memories,
            ["POST"],
            "页面接口：清理已删除（别名）",
        )
        register(
            f"{PAGE_API_PREFIX}/system/compact",
            self.compact_database,
            ["POST"],
            "页面接口：压缩数据库（别名）",
        )
        register(
            f"{PAGE_API_PREFIX}/backup/create",
            self.create_backup,
            ["POST"],
            "页面接口：创建备份（别名）",
        )
        register(
            f"{PAGE_API_PREFIX}/backup/list",
            self.list_backups,
            ["GET"],
            "页面接口：列出备份",
        )
        register(
            f"{PAGE_API_PREFIX}/backup/restore",
            self.restore_backup,
            ["POST"],
            "页面接口：恢复备份",
        )
        register(
            f"{PAGE_API_PREFIX}/backup/delete",
            self.delete_backup,
            ["POST"],
            "页面接口：删除备份",
        )
        register(
            f"{PAGE_API_PREFIX}/backup/batch-delete",
            self.batch_delete_backups,
            ["POST"],
            "页面接口：批量删除备份",
        )

        # ---- 通用配置 ----
        register(
            f"{PAGE_API_PREFIX}/config/schema",
            self.get_config_schema,
            ["GET"],
            "页面接口：配置 Schema",
        )
        register(
            f"{PAGE_API_PREFIX}/config/state",
            self.get_config_state,
            ["GET"],
            "页面接口：配置状态",
        )
        register(
            f"{PAGE_API_PREFIX}/config/apply",
            self.apply_config,
            ["POST"],
            "页面接口：应用配置",
        )

        # ---- 话题分割配置 ----
        register(
            f"{PAGE_API_PREFIX}/config/topic-segmentation",
            self.get_topic_segmentation_config,
            ["GET"],
            "页面接口：话题分割配置",
        )
        register(
            f"{PAGE_API_PREFIX}/config/topic-segmentation",
            self.update_topic_segmentation_config,
            ["POST"],
            "页面接口：更新话题分割配置",
        )
        register(
            f"{PAGE_API_PREFIX}/backfill/start",
            self.start_backfill,
            ["POST"],
            "页面接口：启动存量回填",
        )
        register(
            f"{PAGE_API_PREFIX}/backfill/status",
            self.get_backfill_status,
            ["GET"],
            "页面接口：回填进度",
        )
        # 导出
        register(
            f"{PAGE_API_PREFIX}/export/memories",
            self.export_memories,
            ["POST"],
            "页面接口：导出记忆",
        )

        # ---- 质量评分 ----
        register(
            f"{PAGE_API_PREFIX}/quality/stats",
            self.get_quality_stats,
            ["GET"],
            "页面接口：质量统计",
        )
        register(
            f"{PAGE_API_PREFIX}/quality/recent",
            self.get_quality_recent,
            ["GET"],
            "页面接口：最近质量评分",
        )
        register(
            f"{PAGE_API_PREFIX}/quality/alerts",
            self.get_quality_alerts,
            ["GET"],
            "页面接口：质量告警",
        )
        register(
            f"{PAGE_API_PREFIX}/quality/reset",
            self.reset_quality,
            ["POST"],
            "页面接口：重置质量评分器",
        )

        # ---- 记忆审查 ----
        register(
            f"{PAGE_API_PREFIX}/review/items",
            self.list_review_items,
            ["GET"],
            "页面接口：记忆审查队列",
        )
        register(
            f"{PAGE_API_PREFIX}/review/items/detail",
            self.get_review_item_detail,
            ["GET"],
            "页面接口：记忆审查详情",
        )
        register(
            f"{PAGE_API_PREFIX}/review/refresh",
            self.refresh_review_items,
            ["POST"],
            "页面接口：刷新记忆审查队列",
        )
        register(
            f"{PAGE_API_PREFIX}/review/action",
            self.apply_review_action,
            ["POST"],
            "页面接口：执行记忆审查动作",
        )

        # ---- 黑话 ----
        register(
            f"{PAGE_API_PREFIX}/jargon/candidates",
            self.get_jargon_candidates,
            ["GET"],
            "页面接口：黑话候选词",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/meanings",
            self.get_jargon_meanings,
            ["GET"],
            "页面接口：黑话释义",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/stats",
            self.get_jargon_stats,
            ["GET"],
            "页面接口：黑话统计",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/confirm",
            self.confirm_jargon,
            ["POST"],
            "页面接口：确认黑话",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/mine",
            self.mine_jargon,
            ["POST"],
            "页面接口：挖掘黑话",
        )

        # ---- 功能委托 ----
        register(
            f"{PAGE_API_PREFIX}/delegation/status",
            self.get_delegation_status,
            ["GET"],
            "页面接口：功能委托状态",
        )
        register(
            f"{PAGE_API_PREFIX}/delegation/provided-services",
            self.get_provided_services,
            ["GET"],
            "页面接口：Memora 对外提供的服务状态",
        )

        # ---- 好感度 ----
        register(
            f"{PAGE_API_PREFIX}/affection/status",
            self.get_affection_status,
            ["GET"],
            "页面接口：好感度状态",
        )

        # ---- 社交关系 ----
        register(
            f"{PAGE_API_PREFIX}/social/relations",
            self.get_social_relations,
            ["GET"],
            "页面接口：社交关系列表",
        )
        register(
            f"{PAGE_API_PREFIX}/social/create",
            self.create_social_relation,
            ["POST"],
            "页面接口：创建社交关系",
        )
        register(
            f"{PAGE_API_PREFIX}/social/update",
            self.update_social_relation,
            ["POST"],
            "页面接口：更新社交关系",
        )
        register(
            f"{PAGE_API_PREFIX}/social/delete",
            self.delete_social_relation,
            ["POST"],
            "页面接口：删除社交关系",
        )
        register(
            f"{PAGE_API_PREFIX}/social/batch",
            self.batch_social_relations,
            ["POST"],
            "页面接口：批量编辑社交关系",
        )

        # ---- 表达模式 ----
        register(
            f"{PAGE_API_PREFIX}/expression/patterns",
            self.get_expression_patterns,
            ["GET"],
            "页面接口：表达模式列表",
        )

        # ---- 群组列表 ----
        register(
            f"{PAGE_API_PREFIX}/groups",
            self.get_groups,
            ["GET"],
            "页面接口：可用群组列表",
        )

        # ---- 控制台页面管理（安装依赖 / 构建） ----
        register(
            f"{PAGE_API_PREFIX}/dashboard/install",
            self.install_dashboard_deps,
            ["POST"],
            "页面接口：安装控制台页面依赖",
        )
        register(
            f"{PAGE_API_PREFIX}/dashboard/build",
            self.build_dashboard,
            ["POST"],
            "页面接口：构建控制台页面",
        )

    def get_route_metadata(self) -> list[dict[str, Any]]:
        """Return plugin-side audit metadata for registered Page API routes."""
        return [dict(item) for item in self._route_metadata]

    @staticmethod
    def _build_route_metadata(
        path: str,
        handler,
        methods: list[str],
        description: str,
    ) -> dict[str, Any]:
        normalized_methods = [str(method).upper() for method in methods]
        risk = PluginPageApi._infer_route_risk(path, normalized_methods)
        auth = "admin" if "POST" in normalized_methods else "host"
        return {
            "path": path,
            "handler_name": getattr(handler, "__name__", str(handler)),
            "methods": normalized_methods,
            "description": description,
            "risk": risk,
            "auth": auth,
            "aliases": "别名" in str(description),
            "requires_ready": not (
                path.endswith("/delegation/status")
                or path.endswith("/delegation/provided-services")
                or path.endswith("/config/schema")
                or path.endswith("/config/state")
                or path.endswith("/config/apply")
            ),
            "write_guard": risk in {
                "write",
                "maintenance",
                "destructive",
                "runtime_exec",
            },
        }

    @staticmethod
    def _infer_route_risk(path: str, methods: list[str]) -> str:
        if "POST" not in methods:
            return "read"
        lowered = path.lower()
        if "/dashboard/install" in lowered or "/dashboard/build" in lowered:
            return "runtime_exec"
        if any(token in lowered for token in ("/delete", "batch-delete", "/purge", "/restore", "/reset")):
            return "destructive"
        if any(
            token in lowered
            for token in (
                "/maintenance/",
                "/backup/",
                "/backfill/start",
                "/config/",
                "/quality/reset",
                "/system/",
            )
        ):
            return "maintenance"
        return "write"

    # ---- 群组列表 ----

    async def get_groups(self):
        """汇总所有数据来源中的可用群组列表。

        会聚合黑话存储、好感度存储、表达模式存储、社交关系存储
        以及会话数据中的不同群组标识，并在部分来源失败时返回
        按来源划分的可见性元数据。
        """
        groups: dict[str, dict[str, Any]] = {}
        sources: dict[str, dict[str, Any]] = {}

        def add_group(group_id: Any, source: str, message_count: int = 0) -> None:
            gid = str(group_id or "").strip()
            if not gid:
                return
            if gid not in groups:
                groups[gid] = {
                    "group_id": gid,
                    "source": source,
                    "message_count": int(message_count),
                }
                return
            groups[gid]["message_count"] = max(
                int(groups[gid].get("message_count", 0)),
                int(message_count),
            )

        def is_debug_enabled() -> bool:
            try:
                config_manager = getattr(self.plugin, "config_manager", None)
                if config_manager is None:
                    return False
                return bool(config_manager.get("debug", False))
            except Exception:
                return False

        debug_enabled = is_debug_enabled()

        def set_source_success(source: str, count: int) -> None:
            sources[source] = {"ok": True, "count": int(count)}

        def set_source_error(source: str, exc: Exception) -> None:
            logger.warning("[页面接口] 收集 %s 群组列表失败：%s", source, exc, exc_info=True)
            sources[source] = {
                "ok": False,
                "count": 0,
                "error": str(exc) if debug_enabled else exc.__class__.__name__,
            }

        # 1. 来自黑话存储
        try:
            jargon_store = await self._get_jargon_store() if hasattr(self, "_get_jargon_store") else None
            if jargon_store and hasattr(jargon_store, "list_group_ids"):
                group_ids = await jargon_store.list_group_ids()
                for gid in group_ids:
                    add_group(gid, "jargon", 0)
                set_source_success("jargon", len(group_ids))
            else:
                set_source_success("jargon", 0)
        except Exception as exc:
            set_source_error("jargon", exc)

        # 2. 来自好感度存储
        try:
            affection_store = getattr(self, "_get_affection_store", lambda: None)()
            if affection_store is None:
                plugin = getattr(self, "plugin", None)
                if plugin:
                    for attr in ("_affection_store", "affection_store"):
                        affection_store = getattr(plugin, attr, None)
                        if affection_store:
                            break
            if affection_store and hasattr(affection_store, "list_group_ids"):
                group_ids = await affection_store.list_group_ids()
                for gid in group_ids:
                    add_group(gid, "affection", 0)
                set_source_success("affection", len(group_ids))
            else:
                set_source_success("affection", 0)
        except Exception as exc:
            set_source_error("affection", exc)

        # 3. 来自社交关系存储
        try:
            rel_manager = getattr(self, "_get_relation_manager", lambda: None)()
            if rel_manager and hasattr(rel_manager, "list_group_ids"):
                group_ids = rel_manager.list_group_ids()
                if inspect.isawaitable(group_ids):
                    group_ids = await group_ids
                if not isinstance(group_ids, (list, tuple, set)):
                    group_ids = []
                for gid in group_ids:
                    add_group(gid, "social", 0)
                set_source_success("social", len(group_ids))
            elif rel_manager and hasattr(rel_manager, "list_all"):
                all_rels = rel_manager.list_all()
                if inspect.isawaitable(all_rels):
                    all_rels = await all_rels
                if not isinstance(all_rels, (list, tuple, set)):
                    all_rels = []
                distinct_group_ids: set[str] = set()
                for rel in all_rels or []:
                    gid = str(getattr(rel, "group_id", "") or "").strip()
                    if gid:
                        distinct_group_ids.add(gid)
                    add_group(gid, "social", 0)
                set_source_success("social", len(distinct_group_ids))
            else:
                set_source_success("social", 0)
        except Exception as exc:
            set_source_error("social", exc)

        # 4. 来自会话数据（记忆引擎）
        try:
            engine = self.plugin.initializer.memory_engine
            if engine and hasattr(engine, "stats"):
                stats_data = await engine.stats()
                if not isinstance(stats_data, dict):
                    stats_data = {}
                sessions = stats_data.get("sessions", {})
                if not isinstance(sessions, dict):
                    sessions = {}
                for sid, info in (sessions or {}).items():
                    cnt = info.get("message_count", 0) if isinstance(info, dict) else 0
                    add_group(sid, "session", cnt)
                set_source_success("session", len(sessions or {}))
            else:
                set_source_success("session", 0)
        except Exception as exc:
            set_source_error("session", exc)

        # 5. 兜底：从会话管理器提取
        try:
            conv_mgr = self.plugin.initializer.conversation_manager
            store = getattr(conv_mgr, "store", None) if conv_mgr else None
            if store and hasattr(store, "list_session_origins"):
                origins = await store.list_session_origins()
                valid_origin_count = 0
                for item in origins:
                    if not isinstance(item, dict):
                        continue
                    add_group(
                        item.get("session_id"),
                        "conversation",
                        safe_float(item.get("message_count"), 0.0),
                    )
                    if str(item.get("session_id", "") or "").strip():
                        valid_origin_count += 1
                set_source_success("conversation", valid_origin_count)
            elif conv_mgr and hasattr(conv_mgr, "cache"):
                cache = conv_mgr.cache
                if hasattr(cache, "keys"):
                    keys = list(cache.keys())
                    for key in keys:
                        add_group(key, "conversation", 0)
                    set_source_success("conversation", len(keys))
                else:
                    set_source_success("conversation", 0)
            else:
                set_source_success("conversation", 0)
        except Exception as exc:
            set_source_error("conversation", exc)

        result = list(groups.values())
        return self._ok({"groups": result, "total": len(result), "sources": sources})

    # ---- 共享辅助方法（供所有混入类复用） ----

    async def _ensure_plugin_ready(self) -> tuple[dict[str, Any] | None, dict | None]:
        """确保插件就绪，且永不抛出异常（所有异常都会转为错误响应）。

        返回值：``(ready_dict, error_dict)``，二者互斥，必有一个为 ``None``。
        """
        try:
            ready, message = await self.plugin._ensure_plugin_ready()
        except Exception as exc:
            logger.error(f"[页面接口] 插件就绪检查异常：{exc}", exc_info=True)
            return None, self._error(f"插件就绪检查失败: {exc}")
        if not ready:
            return None, self._error(message or "插件尚未就绪")
        try:
            memory_engine = self.plugin.initializer.memory_engine
            if memory_engine is None:
                return None, self._error("记忆引擎未初始化")
            return {
                "memory_engine": memory_engine,
                "conversation_manager": self.plugin.initializer.conversation_manager,
                "index_validator": self.plugin.initializer.index_validator,
            }, None
        except Exception as exc:
            logger.error(f"[页面接口] 获取引擎组件失败：{exc}", exc_info=True)
            return None, self._error(f"获取引擎组件失败：{exc}")

    async def _get_memory_record(self, memory_id: int) -> dict[str, Any] | None:
        memory_engine = self.plugin.initializer.memory_engine
        if memory_engine is None:
            return None
        memory = await memory_engine.get_memory(memory_id)
        if memory:
            return memory
        if memory_engine.db_connection is None:
            return None
        try:
            cursor = await memory_engine.db_connection.execute(
                "SELECT id, doc_id, text, metadata, created_at, updated_at "
                "FROM documents WHERE id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
        except Exception as e:
            logger.warning(f"获取记忆详情失败（id={memory_id}）：{e}")
            return None
        if not row:
            return None
        return {
            "id": row[0],
            "doc_id": row[1],
            "text": row[2],
            "metadata": self._normalize_metadata(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
        }

    @staticmethod
    def _normalize_metadata(metadata: Any) -> dict[str, Any]:
        if isinstance(metadata, dict):
            return metadata
        if not metadata:
            return {}
        try:
            parsed = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _importance_to_display(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = 0.5
        if parsed <= 1.0:
            parsed *= 10.0
        return round(max(0.0, min(10.0, parsed)), 2)

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        return ok_response(data)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return error_response(message)

    def _maintenance_write_guard(self) -> dict[str, Any] | None:
        backup_manager = getattr(self.plugin, "_backup_manager", None)
        if backup_manager is None:
            return None
        try:
            has_pending = bool(backup_manager.has_pending_restores())
        except AttributeError:
            has_pending = False
        except Exception as exc:
            logger.error(f"[页面接口] 检查恢复维护状态失败：{exc}", exc_info=True)
            return error_response(f"维护状态检查失败: {exc}")
        if not has_pending:
            return None
        pending_files = []
        try:
            pending_files = backup_manager.list_pending_restores()
        except Exception as exc:
            logger.debug("[页面接口] 获取待恢复文件列表失败: %s", exc, exc_info=True)
            pending_files = []
        return error_response(
            "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"
            f" 待恢复文件={pending_files}"
        )

    @staticmethod
    def _get_graph_store(memory_engine):
        return getattr(memory_engine, "graph_store", None)

    @staticmethod
    def _tokenize_graph_query(query: str) -> list[str]:
        query_text = str(query or "").strip().lower()
        if not query_text:
            return []
        normalized = "".join(c if c.isalnum() else " " for c in query_text)
        raw_tokens = [t for t in normalized.split() if t]
        tokens: list[str] = []
        seen: set[str] = set()

        def add_token(value: str):
            token = value.strip()
            if len(token) < 2 or token in seen:
                return
            seen.add(token)
            tokens.append(token)

        for token in raw_tokens:
            add_token(token)

        compact = "".join(c for c in query_text if c.isalnum())
        if compact and any(ord(c) > 127 for c in compact):
            add_token(compact)
            for size in (2, 3):
                if len(tokens) >= 12:
                    break
                max_index = max(0, len(compact) - size + 1)
                for idx in range(max_index):
                    add_token(compact[idx : idx + size])
                    if len(tokens) >= 12:
                        break
        return tokens[:12]

    @staticmethod
    def _build_graph_view_payload(
        snapshot: dict[str, Any],
        stats: dict[str, Any],
        *,
        enabled: bool,
        mode: str,
        query: str | None = None,
        memory_id: int | None = None,
        retrieval_items: list[dict[str, Any]] | None = None,
        matched_node_ids: list[int] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def _dict_items(values: Any) -> list[dict[str, Any]]:
            return [dict(item) for item in values if isinstance(item, dict)]

        def _coerce_int(value: Any, default: int = 0) -> int:
            if isinstance(value, bool):
                return default
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        nodes = _dict_items(snapshot.get("nodes", []))
        edges_raw = _dict_items(snapshot.get("edges", []))
        entries = _dict_items(snapshot.get("entries", []))
        memories = [
            item
            for item in _dict_items(snapshot.get("memories", []))
            if _coerce_int(item.get("memory_id"), None) is not None
        ]

        # 安全保护：过滤引用不存在节点的孤立边，防止前端 G6 抛出“节点不存在”错误
        node_ids = {item.get("id") for item in nodes}
        edges = [
            edge
            for edge in edges_raw
            if edge.get("source") in node_ids and edge.get("target") in node_ids
        ]
        if len(edges) < len(edges_raw):
            logger.warning(
                f"[页面接口] 已过滤 {len(edges_raw) - len(edges)} 条孤立边 "
                f"(节点数={len(nodes)}, 原始边数={len(edges_raw)})"
            )
        raw_retrieval_items = retrieval_items or []
        retrieval_items = []
        for item in raw_retrieval_items:
            if not isinstance(item, dict):
                continue
            memory_id = item.get("memory_id")
            if memory_id is None:
                continue
            if _coerce_int(memory_id, None) is None:
                continue
            retrieval_items.append(dict(item))

        raw_matched_node_ids = matched_node_ids or []
        matched_node_ids = []
        for item in raw_matched_node_ids:
            coerced = _coerce_int(item, None)
            if coerced is None:
                continue
            matched_node_ids.append(coerced)
        matched_node_id_set = set(matched_node_ids)
        retrieval_lookup = {
            _coerce_int(item["memory_id"]): item
            for item in retrieval_items
            if item.get("memory_id") is not None
        }

        node_type_breakdown: dict[str, int] = {}
        relation_breakdown: dict[str, int] = {}

        for node in nodes:
            node["highlighted"] = _coerce_int(node.get("id", 0), 0) in matched_node_id_set
            nt = str(node.get("type", "unknown") or "unknown")
            node_type_breakdown[nt] = node_type_breakdown.get(nt, 0) + 1

        for edge in edges:
            rt = str(edge.get("relation_type", "related") or "related")
            relation_breakdown[rt] = relation_breakdown.get(rt, 0) + 1

        for memory in memories:
            mk = memory.get("memory_id")
            if mk is None:
                continue
            retrieval = retrieval_lookup.get(_coerce_int(mk, -1))
            if retrieval is not None:
                memory["retrieval"] = retrieval

        top_nodes = sorted(
            nodes,
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -_coerce_int(item.get("degree", 0), 0),
                str(item.get("label", "")),
            ),
        )[:8]
        top_memories = sorted(
            memories,
            key=lambda item: (
                -safe_float((item.get("retrieval") or {}).get("final_score"), -1.0),
                -_coerce_int(item.get("entry_count", 0), 0),
                -_coerce_int(item.get("node_count", 0), 0),
                -_coerce_int(item.get("edge_count", 0), 0),
                -safe_float(item.get("importance"), 0.0),
            ),
        )[:8]

        return {
            # 前端兼容：GraphPage 会直接从数据根层读取 nodes/edges
            "nodes": nodes,
            "edges": edges,
            "enabled": enabled,
            "mode": mode,
            "query": query or None,
            "memory_id": memory_id,
            "filters": filters or {},
            "summary": {
                "visible_node_count": len(nodes),
                "visible_edge_count": len(edges),
                "visible_entry_count": len(entries),
                "visible_memory_count": len(memories),
                "graph_node_count": _coerce_int(stats.get("graph_nodes", 0), 0),
                "graph_edge_count": _coerce_int(stats.get("graph_edges", 0), 0),
                "graph_entry_count": _coerce_int(stats.get("graph_entries", 0), 0),
                "graph_memory_enabled": bool(enabled),
                "node_type_breakdown": node_type_breakdown,
                "relation_breakdown": relation_breakdown,
            },
            "matched_node_ids": matched_node_ids,
            "matched_memory_ids": [item["memory_id"] for item in retrieval_items],
            "top_nodes": top_nodes,
            "top_memories": top_memories,
            "retrieval": {"total": len(retrieval_items), "items": retrieval_items},
            "snapshot": {
                "nodes": nodes,
                "edges": edges,
                "entries": entries,
                "memories": memories,
            },
        }
