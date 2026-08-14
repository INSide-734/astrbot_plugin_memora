"""React 兼容别名与配置/质量等 feature 路由注册 mixin。"""

from __future__ import annotations

from typing import Any

from .constants import PAGE_API_PREFIX


class FeatureRoutesApiMixin:
    """承载 register_routes 后半部分的路由注册。"""

    def _register_feature_routes(self: Any, register: Any) -> None:
        """注册 React 兼容别名与配置、质量、审查等 feature 路由。"""
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
            f"{PAGE_API_PREFIX}/backup/status",
            self.get_backup_status,
            ["GET"],
            "页面接口：备份恢复状态",
        )
        register(
            f"{PAGE_API_PREFIX}/backup/restore/cancel",
            self.cancel_restore,
            ["POST"],
            "页面接口：取消备份恢复",
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

        # ---- 门禁 dry-run ----
        register(
            f"{PAGE_API_PREFIX}/gate/dry-run",
            self.dry_run_gate,
            ["POST"],
            "页面接口：门禁规则 dry-run",
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
        register(
            f"{PAGE_API_PREFIX}/review/derived",
            self.list_memory_evolution_review_candidates,
            ["GET"],
            "页面接口：高影响派生候选复核队列",
        )
        register(
            f"{PAGE_API_PREFIX}/review/derived/detail",
            self.get_memory_evolution_review_candidate,
            ["GET"],
            "页面接口：高影响派生候选复核详情",
        )
        register(
            f"{PAGE_API_PREFIX}/review/derived/action",
            self.apply_memory_evolution_review_action,
            ["POST"],
            "页面接口：处置高影响派生候选",
        )
        register(
            f"{PAGE_API_PREFIX}/review/reconsolidation",
            self.list_reconsolidation_review_candidates,
            ["GET"],
            "页面接口：再巩固候选复核队列",
        )
        register(
            f"{PAGE_API_PREFIX}/review/reconsolidation/detail",
            self.get_reconsolidation_review_candidate,
            ["GET"],
            "页面接口：再巩固候选复核详情",
        )
        register(
            f"{PAGE_API_PREFIX}/review/reconsolidation/action",
            self.apply_reconsolidation_review_action,
            ["POST"],
            "页面接口：处置再巩固候选",
        )
        register(
            f"{PAGE_API_PREFIX}/review/quarantine",
            self.list_quarantine_candidates,
            ["GET"],
            "页面接口：pre-canonical 记忆隔离队列",
        )
        register(
            f"{PAGE_API_PREFIX}/review/quarantine/detail",
            self.get_quarantine_candidate_detail,
            ["GET"],
            "页面接口：pre-canonical 记忆隔离详情",
        )
        register(
            f"{PAGE_API_PREFIX}/review/quarantine/action",
            self.apply_quarantine_action,
            ["POST"],
            "页面接口：处置 pre-canonical 记忆隔离候选",
        )
        register(
            f"{PAGE_API_PREFIX}/review/quarantine/repair",
            self.repair_quarantine_approval,
            ["POST"],
            "页面接口：修复 pre-canonical 隔离批准收口",
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
            f"{PAGE_API_PREFIX}/jargon/create",
            self.create_jargon,
            ["POST"],
            "页面接口：创建黑话",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/update",
            self.update_jargon,
            ["POST"],
            "页面接口：更新黑话",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/delete",
            self.delete_jargon,
            ["POST"],
            "页面接口：删除黑话",
        )
        register(
            f"{PAGE_API_PREFIX}/jargon/batch",
            self.batch_jargon,
            ["POST"],
            "页面接口：批量处理黑话",
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
        register(
            f"{PAGE_API_PREFIX}/affection/users",
            self.list_affection_users,
            ["GET"],
            "页面接口：好感度用户列表",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/users/create",
            self.create_affection_user,
            ["POST"],
            "页面接口：创建好感度用户",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/users/update",
            self.update_affection_user,
            ["POST"],
            "页面接口：更新好感度用户",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/users/delete",
            self.delete_affection_user,
            ["POST"],
            "页面接口：删除好感度用户",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/users/batch",
            self.batch_affection_users,
            ["POST"],
            "页面接口：批量处理好感度用户",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/mood/set",
            self.set_affection_mood,
            ["POST"],
            "页面接口：设置好感度情绪",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/mood/reset",
            self.reset_affection_mood,
            ["POST"],
            "页面接口：重置好感度情绪",
        )
        register(
            f"{PAGE_API_PREFIX}/affection/moods/history",
            self.get_affection_mood_history,
            ["GET"],
            "页面接口：好感度情绪历史",
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
