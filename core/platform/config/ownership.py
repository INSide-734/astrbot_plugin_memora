"""平台公开配置叶的产品分类与运行时责任方注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConfigOwnershipKind(str, Enum):
    """配置叶在控制面中的稳定分类。"""

    RUNTIME = "runtime"
    DASHBOARD_ONLY = "dashboard_only"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, slots=True)
class ConfigSectionOwnership:
    """描述一个顶层配置分支的分类和唯一责任模块。"""

    section: str
    kind: ConfigOwnershipKind
    owner: str


def _ownership(
    section: str,
    kind: ConfigOwnershipKind,
    owner: str,
) -> ConfigSectionOwnership:
    """构造不可变的配置分支所有权声明。"""

    return ConfigSectionOwnership(section=section, kind=kind, owner=owner)


# 注册表按公开 Schema 的顶层分支划分。一个分支新增不同生命周期的叶子时，
# 应先拆成更明确的配置分支，避免在这里维护点路径特例。
CONFIG_SECTION_OWNERSHIP: dict[str, ConfigSectionOwnership] = {
    "bot_language": _ownership(
        "bot_language", ConfigOwnershipKind.RUNTIME, "main.MemoraPlugin"
    ),
    "debug": _ownership(
        "debug",
        ConfigOwnershipKind.RUNTIME,
        "core.features.diagnostics",
    ),
    "provider_settings": _ownership(
        "provider_settings",
        ConfigOwnershipKind.RUNTIME,
        "core.platform.composition.provider_loader",
    ),
    "session_manager": _ownership(
        "session_manager",
        ConfigOwnershipKind.RUNTIME,
        "core.features.conversation.application.conversation_manager",
    ),
    "recall_engine": _ownership(
        "recall_engine",
        ConfigOwnershipKind.RUNTIME,
        "core.features.recall.application.recall_handler",
    ),
    "importance_decay": _ownership(
        "importance_decay",
        ConfigOwnershipKind.RUNTIME,
        "core.features.decay.application.operations",
    ),
    "fusion_strategy": _ownership(
        "fusion_strategy",
        ConfigOwnershipKind.RUNTIME,
        "core.features.retrieval.rrf_fusion",
    ),
    "hybrid_scoring": _ownership(
        "hybrid_scoring",
        ConfigOwnershipKind.RUNTIME,
        "core.features.retrieval.hybrid_retriever",
    ),
    "filtering_settings": _ownership(
        "filtering_settings",
        ConfigOwnershipKind.RUNTIME,
        "core.platform.transport.tools.memory_search_tool",
    ),
    "reflection_engine": _ownership(
        "reflection_engine",
        ConfigOwnershipKind.RUNTIME,
        "core.features.reflection.application.reflection_handler",
    ),
    "graph_memory": _ownership(
        "graph_memory",
        ConfigOwnershipKind.RUNTIME,
        "core.features.memory.application.memory_engine",
    ),
    "human_like_memory": _ownership(
        "human_like_memory",
        ConfigOwnershipKind.RUNTIME,
        "core.features.memory.application.retrieval_optimizer",
    ),
    "migration_settings": _ownership(
        "migration_settings",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.memory.infrastructure.schema_manager",
    ),
    "index_rebuild_settings": _ownership(
        "index_rebuild_settings",
        ConfigOwnershipKind.RUNTIME,
        "core.platform.composition.derived_rebuild_coordinator",
    ),
    "backup_settings": _ownership(
        "backup_settings",
        ConfigOwnershipKind.RUNTIME,
        "core.features.decay.application.scheduler",
    ),
    "write_reliability": _ownership(
        "write_reliability",
        ConfigOwnershipKind.RUNTIME,
        "core.features.memory.infrastructure.write_op_journal",
    ),
    "prompt_templates": _ownership(
        "prompt_templates",
        ConfigOwnershipKind.RUNTIME,
        "core.features.recall.processors.prompt_builder",
    ),
    "user_profile": _ownership(
        "user_profile",
        ConfigOwnershipKind.RUNTIME,
        "core.features.profiles.application.profile_manager",
    ),
    "cost_control": _ownership(
        "cost_control", ConfigOwnershipKind.RUNTIME, "core.shared.cost_control"
    ),
    "memory_evolution": _ownership(
        "memory_evolution",
        ConfigOwnershipKind.RUNTIME,
        "core.features.evolution.application.memory_evolution_manager",
    ),
    "reranker": _ownership(
        "reranker",
        ConfigOwnershipKind.RUNTIME,
        "core.features.retrieval.reranker_factory",
    ),
    "auto_learning": _ownership(
        "auto_learning",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.learning.application.auto_learning",
    ),
    "knowledge_base": _ownership(
        "knowledge_base",
        ConfigOwnershipKind.RUNTIME,
        "core.features.knowledge.application.knowledge_manager",
    ),
    "notes": _ownership(
        "notes",
        ConfigOwnershipKind.RUNTIME,
        "core.features.notes.application.note_manager",
    ),
    "anomaly_detection": _ownership(
        "anomaly_detection",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.memory.application.anomaly_detector",
    ),
    "continuity_tracking": _ownership(
        "continuity_tracking",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.memory.application.continuity_tracker",
    ),
    "semantic_compression": _ownership(
        "semantic_compression",
        ConfigOwnershipKind.RUNTIME,
        "core.features.evolution.application.semantic_compressor",
    ),
    "episode_clustering": _ownership(
        "episode_clustering",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.evolution.application.episode_clusterer",
    ),
    "agent_tools": _ownership(
        "agent_tools", ConfigOwnershipKind.RUNTIME, "main.MemoraPlugin"
    ),
    "jargon": _ownership(
        "jargon", ConfigOwnershipKind.RUNTIME, "core.features.cognition.jargon"
    ),
    "dashboard": _ownership(
        "dashboard",
        ConfigOwnershipKind.DASHBOARD_ONLY,
        "core.platform.transport.page_api.maintenance_api",
    ),
    "update_settings": _ownership(
        "update_settings",
        ConfigOwnershipKind.RUNTIME,
        "core.features.updates.application.manager",
    ),
    "security": _ownership(
        "security", ConfigOwnershipKind.RUNTIME, "core.platform.security"
    ),
    "persona_decay": _ownership(
        "persona_decay",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.memory.domain.memory_atom",
    ),
    "reconsolidation": _ownership(
        "reconsolidation",
        ConfigOwnershipKind.EXPERIMENTAL,
        "core.features.reconsolidation.application.reconsolidation",
    ),
    "export": _ownership(
        "export",
        ConfigOwnershipKind.RUNTIME,
        "core.features.memory.application.memory_exporter",
    ),
    "topic_segmentation": _ownership(
        "topic_segmentation",
        ConfigOwnershipKind.RUNTIME,
        "core.features.reflection.application.topic_batch_preparer",
    ),
    "atom_classifier": _ownership(
        "atom_classifier",
        ConfigOwnershipKind.RUNTIME,
        "core.features.recall.processors.atom_classifier",
    ),
    "flashbulb": _ownership(
        "flashbulb",
        ConfigOwnershipKind.RUNTIME,
        "core.features.decay.application.operations",
    ),
    "forgetting_agent": _ownership(
        "forgetting_agent",
        ConfigOwnershipKind.RUNTIME,
        "core.features.decay.application.operations",
    ),
    "atom_quality_filter": _ownership(
        "atom_quality_filter",
        ConfigOwnershipKind.RUNTIME,
        "core.features.recall.processors.atom_classifier",
    ),
}


def resolve_config_ownership(path: str) -> ConfigSectionOwnership:
    """按点路径返回配置所有权；未登记分支直接失败。"""

    section = path.strip().split(".", 1)[0]
    if not section or section not in CONFIG_SECTION_OWNERSHIP:
        raise KeyError(f"未声明配置所有权: {path}")
    return CONFIG_SECTION_OWNERSHIP[section]


__all__ = [
    "CONFIG_SECTION_OWNERSHIP",
    "ConfigOwnershipKind",
    "ConfigSectionOwnership",
    "resolve_config_ownership",
]
