"""
官方插件页面接口适配层，负责路由注册与共享辅助逻辑。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger as logger

from .affection_api import AffectionApiMixin
from .backup_api import BackupApiMixin
from .config_api import ConfigApiMixin
from .constants import (
    PAGE_API_ALIAS_PREFIXES,
    PAGE_API_ALIASES,
    PAGE_API_PREFIX,
    PLUGIN_NAME,
)
from .delegation_api import DelegationApiMixin
from .diagnostics_api import DiagnosticsApiMixin
from .evaluation_api import EvaluationApiMixin
from .expression_api import ExpressionApiMixin
from .feature_routes import FeatureRoutesApiMixin
from .gate_api import GateApiMixin
from .graph_api import GraphApiMixin
from .group_list import GroupListApiMixin
from .injection_strategy_api import (
    InjectionStrategyApiMixin,
)
from .jargon_api import JargonApiMixin
from .knowledge_api import KnowledgeApiMixin
from .learning_api import LearningApiMixin
from .maintenance_api import MaintenanceApiMixin
from .memory_batch_api import MemoryBatchApiMixin
from .memory_evolution_review_api import (
    MemoryEvolutionReviewApiMixin,
)
from .memory_read_api import MemoryReadApiMixin
from .memory_stats_recall_api import (
    MemoryStatsRecallApiMixin,
)
from .memory_write_api import MemoryWriteApiMixin
from .metrics_api import MetricsApiMixin
from .note_api import NoteApiMixin
from .profile_api import ProfileApiMixin
from .quality_api import QualityApiMixin
from .quarantine_api import QuarantineApiMixin
from .recall_trace_api import RecallTraceApiMixin
from .reconsolidation_review_api import (
    ReconsolidationReviewApiMixin,
)
from .review_api import ReviewApiMixin
from .route_registration import make_page_route_registrar
from .shared_helpers import SharedPageApiHelpersMixin
from .social_api import SocialApiMixin
from .topic_segmentation_api import (
    TopicSegmentationApiMixin,
)
from .update_api import UpdateApiMixin

__all__ = [
    "PAGE_API_ALIASES",
    "PAGE_API_ALIAS_PREFIXES",
    "PAGE_API_PREFIX",
    "PLUGIN_NAME",
    "PluginPageApi",
]


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
    QuarantineApiMixin,
    RecallTraceApiMixin,
    InjectionStrategyApiMixin,
    ReviewApiMixin,
    MemoryEvolutionReviewApiMixin,
    ReconsolidationReviewApiMixin,
    JargonApiMixin,
    DelegationApiMixin,
    EvaluationApiMixin,
    AffectionApiMixin,
    SocialApiMixin,
    ExpressionApiMixin,
    UpdateApiMixin,
    FeatureRoutesApiMixin,
    GateApiMixin,
    GroupListApiMixin,
    SharedPageApiHelpersMixin,
):
    """记忆插件的官方页面接口集合。"""

    def __init__(self, plugin) -> None:
        """绑定插件实例并初始化路由元数据与共享页面状态。"""

        self.plugin = plugin
        self._route_metadata: list[dict[str, Any]] = []

    async def sse_stream(self):
        """D4：基于 AstrBot 公共流式响应的 SSE 实时记忆流端点。"""
        initializer = getattr(self.plugin, "initializer", None)
        engine = getattr(initializer, "memory_engine", None)
        if engine is None or not hasattr(engine, "sse"):
            return {"status": "error", "message": "SSE 服务不可用"}
        return await engine.sse.stream()

    def register_routes(self) -> None:
        """注册主前缀及兼容前缀下的全部 Page API 路由。"""

        self._route_metadata = []
        register = make_page_route_registrar(
            raw_register=self.plugin.context.register_web_api,
            route_metadata=self._route_metadata,
            metadata_builder=self._build_route_metadata,
            primary_prefix=PAGE_API_PREFIX,
            alias_prefixes=PAGE_API_ALIAS_PREFIXES,
        )

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
            f"{PAGE_API_PREFIX}/metrics/recall-samples",
            self.get_recall_samples,
            ["GET"],
            "页面接口：召回性能样本（游标分页）",
        )
        register(
            f"{PAGE_API_PREFIX}/update/check",
            self.check_update,
            ["GET"],
            "页面接口：检查插件更新",
        )
        register(
            f"{PAGE_API_PREFIX}/update/ignore",
            self.ignore_update,
            ["POST"],
            "页面接口：忽略插件更新",
        )
        register(
            f"{PAGE_API_PREFIX}/update/download",
            self.download_update,
            ["POST"],
            "页面接口：下载插件更新",
        )
        register(
            f"{PAGE_API_PREFIX}/update/apply",
            self.apply_update,
            ["POST"],
            "页面接口：安装并重载插件更新",
        )
        register(
            f"{PAGE_API_PREFIX}/update/status",
            self.get_update_status,
            ["GET"],
            "页面接口：插件更新操作状态",
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
            f"{PAGE_API_PREFIX}/evaluation/datasets/import",
            self.import_evaluation_dataset,
            ["POST"],
            "页面接口：导入检索评测数据集",
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
        for suffix, handler, description in (
            (
                "/injection-strategy/catalog",
                self.get_injection_strategy_catalog,
                "页面接口：注入策略目录",
            ),
            (
                "/injection-strategy/summary",
                self.get_injection_strategy_summary,
                "页面接口：注入策略摘要",
            ),
            (
                "/injection-strategy/decisions",
                self.list_injection_decisions,
                "页面接口：注入决策列表",
            ),
            (
                "/injection-strategy/decisions/detail",
                self.get_injection_decision_detail,
                "页面接口：注入决策详情",
            ),
        ):
            register(
                f"{PAGE_API_PREFIX}{suffix}",
                handler,
                ["GET"],
                description,
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
            f"{PAGE_API_PREFIX}/profiles/create",
            self.create_profile,
            ["POST"],
            "页面接口：创建画像",
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
            f"{PAGE_API_PREFIX}/learning/action",
            self.learning_action,
            ["POST"],
            "页面接口：学习生产动作",
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
        self._register_feature_routes(register)
