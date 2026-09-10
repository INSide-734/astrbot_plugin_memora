"""可选认知组件的初始化生命周期辅助。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from astrbot.api import logger

from ...features.observability.infrastructure.debug_reporter import (
    report_debug_event,
    report_debug_exception,
)
from ..config.feature_config import is_jargon_discovery_enabled


async def initialize_cognitive_components(initializer: Any) -> None:
    """创建共享的 v1.0+ 认知组件实例。"""
    db_path = str(Path(initializer.data_dir) / "memora.db")
    initialization_started = time.perf_counter()
    success_count = 0
    failed_count = 0

    component_started = time.perf_counter()
    try:
        from ...features.cognition.affection import AffectionManager, AffectionStore

        initializer.affection_store = AffectionStore(db_path)
        await initializer.affection_store.initialize()
        initializer.affection_manager = AffectionManager(
            initializer.affection_store,
            # 宿主 Provider 的运行时能力由既有认知组件边界验证。
            llm_adapter=cast(Any, initializer.llm_provider),
        )
        success_count += 1
        report_debug_event(
            "plugin_initialized",
            component="initializer",
            stage="cognitive_components",
            status="completed",
            reason_code="cognitive_component_ready",
            capability="affection",
            duration_ms=max(
                0.0, (time.perf_counter() - component_started) * 1000.0
            ),
        )
        logger.info("好感度管理器已初始化")
    except Exception as exc:
        failed_count += 1
        report_debug_exception(
            "plugin_initialized",
            exc,
            component="initializer",
            stage="cognitive_components",
            status="degraded",
            reason_code="cognitive_component_unavailable",
            capability="affection",
            duration_ms=max(
                0.0, (time.perf_counter() - component_started) * 1000.0
            ),
        )
        logger.warning("好感度管理器初始化失败，已跳过: %s", exc, exc_info=True)
        initializer.affection_store = None
        initializer.affection_manager = None

    component_started = time.perf_counter()
    try:
        from ...features.cognition.expression import (
            ExpressionPatternLearner,
            ExpressionPatternStore,
        )

        initializer.expression_store = ExpressionPatternStore(db_path)
        await initializer.expression_store.initialize()
        initializer.expression_learner = ExpressionPatternLearner(
            initializer.expression_store)
        success_count += 1
        report_debug_event(
            "plugin_initialized",
            component="initializer",
            stage="cognitive_components",
            status="completed",
            reason_code="cognitive_component_ready",
            capability="expression",
            duration_ms=max(
                0.0, (time.perf_counter() - component_started) * 1000.0
            ),
        )
        logger.info("表达模式学习器已初始化")
    except Exception as exc:
        failed_count += 1
        report_debug_exception(
            "plugin_initialized",
            exc,
            component="initializer",
            stage="cognitive_components",
            status="degraded",
            reason_code="cognitive_component_unavailable",
            capability="expression",
            duration_ms=max(
                0.0, (time.perf_counter() - component_started) * 1000.0
            ),
        )
        logger.warning("表达模式学习器初始化失败，已跳过: %s", exc, exc_info=True)
        initializer.expression_store = None
        initializer.expression_learner = None

    if not is_jargon_discovery_enabled(initializer.config_manager):
        initializer.jargon_filter = None
        initializer.jargon_store = None
        initializer.jargon_query_service = None
        initializer.jargon_miner = None
        logger.info("黑话自动发现功能已禁用")
    else:
        component_started = time.perf_counter()
        try:
            from ...features.cognition.jargon import (
                JargonMiner,
                JargonQueryService,
                JargonStatisticalFilter,
                JargonStore,
            )

            initializer.jargon_filter = JargonStatisticalFilter()
            initializer.jargon_store = JargonStore(db_path)
            assert initializer.jargon_store is not None
            await initializer.jargon_store.initialize()
            initializer.jargon_query_service = JargonQueryService(
                initializer.jargon_store)
            initializer.jargon_miner = JargonMiner(
                initializer.llm_provider,
                initializer.jargon_filter,
                initializer.jargon_store,
            )
            success_count += 1
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="cognitive_components",
                status="completed",
                reason_code="cognitive_component_ready",
                capability="jargon",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.info("黑话组件已初始化")
        except Exception as exc:
            failed_count += 1
            report_debug_exception(
                "plugin_initialized",
                exc,
                component="initializer",
                stage="cognitive_components",
                status="degraded",
                reason_code="cognitive_component_unavailable",
                capability="jargon",
                duration_ms=max(
                    0.0, (time.perf_counter() - component_started) * 1000.0
                ),
            )
            logger.warning("黑话组件初始化失败，已跳过: %s", exc, exc_info=True)
            initializer.jargon_filter = None
            initializer.jargon_store = None
            initializer.jargon_query_service = None
            initializer.jargon_miner = None

    component_started = time.perf_counter()
    try:
        from ...features.cognition.social import RelationManager, RelationStore

        initializer.relation_store = RelationStore(db_path)
        await initializer.relation_store.initialize()
        initializer.relation_manager = RelationManager(
            initializer.relation_store)
        success_count += 1
        report_debug_event(
            "plugin_initialized",
            component="initializer",
            stage="cognitive_components",
            status="completed",
            reason_code="cognitive_component_ready",
            capability="social",
            duration_ms=max(
                0.0, (time.perf_counter() - component_started) * 1000.0
            ),
        )
        logger.info("关系管理器已初始化")
    except Exception as exc:
        failed_count += 1
        report_debug_exception(
            "plugin_initialized",
            exc,
            component="initializer",
            stage="cognitive_components",
            status="degraded",
            reason_code="cognitive_component_unavailable",
            capability="social",
            duration_ms=max(
                0.0, (time.perf_counter() - component_started) * 1000.0
            ),
        )
        logger.warning("关系管理器初始化失败，已跳过: %s", exc, exc_info=True)
        initializer.relation_store = None
        initializer.relation_manager = None

    report_debug_event(
        "plugin_initialized",
        component="initializer",
        stage="cognitive_components",
        status="completed" if failed_count == 0 else "degraded",
        reason_code=(
            "cognitive_components_ready"
            if failed_count == 0
            else "cognitive_components_partial"
        ),
        duration_ms=max(
            0.0, (time.perf_counter() - initialization_started) * 1000.0
        ),
        success_count=success_count,
        failed_count=failed_count,
    )


__all__ = ["initialize_cognitive_components"]
