"""运行时配置投影及类人记忆开关的闭环测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from astrbot.api.platform import MessageType

from core.features.decay.application.operations import DecayOperationsMixin
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.recall.processors.atom_classifier import classify_atoms
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.features.retrieval.rrf_fusion import FusedResult, HybridResult
from core.features.retrieval.score_weighting import ScoreWeighting
from core.platform.composition.component_factory import ComponentFactory
from core.platform.config import (
    ConfigApplyResult,
    ConfigManager,
    ConfigOwnershipKind,
    resolve_config_ownership,
)
from core.platform.config.config_validator import get_default_config, validate_config
from core.platform.transport.tools.memory_search_tool import MemorySearchTool
from tests.tool_contract_support import call_text_handler


def _build_engine_config(values: dict[str, object]) -> dict[str, object]:
    """使用真实 ConfigManager 构造引擎运行时配置。"""

    manager = ConfigManager(user_config=values)
    factory = ComponentFactory(None, manager, "D:/memora-runtime-contract")
    return factory._build_engine_config(
        Path(factory.data_dir) / "stopwords",
        graph_memory_enabled=True,
    )


def _iter_schema_leaf_paths(
    schema: dict[str, object],
    prefix: str = "",
) -> list[str]:
    """递归收集 AstrBot 配置 Schema 中的全部叶子点路径。"""

    paths: list[str] = []
    for key, raw_node in schema.items():
        path = f"{prefix}.{key}" if prefix else key
        node = raw_node if isinstance(raw_node, dict) else {}
        items = node.get("items")
        if node.get("type") == "object" and isinstance(items, dict):
            paths.extend(_iter_schema_leaf_paths(items, path))
        else:
            paths.append(path)
    return paths


def _get_dotted_value(config: dict[str, object], path: str) -> object:
    """从嵌套配置字典读取点路径值。"""

    current: object = config
    for segment in path.split("."):
        assert isinstance(current, dict)
        current = current[segment]
    return current


def _hybrid_result(
    *,
    score: float = 1.0,
    emotion_tags: list[str] | None = None,
    emotional_intensity: float = 0.8,
    event_time: float | None = None,
) -> HybridResult:
    """构造只包含增强阶段所需字段的召回结果。"""

    metadata: dict[str, object] = {
        "emotion_tags": list(emotion_tags or []),
        "emotional_intensity": emotional_intensity,
    }
    if event_time is not None:
        metadata["event_time"] = event_time
    return HybridResult(
        doc_id=1,
        final_score=score,
        rrf_score=score,
        bm25_score=score,
        vector_score=score,
        content="测试记忆",
        metadata=metadata,
        score_breakdown={},
    )


def test_factory_projects_lifecycle_feature_sections() -> None:
    """工厂必须保留生命周期组件的显式开关和非默认参数。"""

    config = _build_engine_config(
        {
            "user_profile": {"enabled": False, "boost_strength": 0.31},
            "auto_learning": {"enabled": False},
            "knowledge_base": {"enabled": False, "dedup_threshold": 0.73},
            "notes": {"enabled": False, "max_tags": 4},
            "continuity_tracking": {"enabled": True, "topic_ttl_days": 11},
            "anomaly_detection": {"enabled": True, "sigma_threshold": 2.4},
            "reconsolidation": {"enabled": True, "min_recall_count": 8},
            "export": {"enabled": False},
        }
    )

    assert config["user_profile.enabled"] is False
    assert config["user_profile.boost_strength"] == pytest.approx(0.31)
    assert config["auto_learning.enabled"] is False
    assert config["knowledge_base.enabled"] is False
    assert config["knowledge_base.dedup_threshold"] == pytest.approx(0.73)
    assert config["notes.enabled"] is False
    assert config["notes.max_tags"] == 4
    assert config["continuity_tracking.enabled"] is True
    assert config["continuity_tracking.topic_ttl_days"] == 11
    assert config["anomaly_detection.enabled"] is True
    assert config["anomaly_detection.sigma_threshold"] == pytest.approx(2.4)
    assert config["reconsolidation.enabled"] is True
    assert config["reconsolidation.min_recall_count"] == 8
    assert config["export.enabled"] is False


def test_factory_projects_migration_settings() -> None:
    """迁移开关必须从公开配置到达 MemoryEngine 运行时快照。"""

    config = _build_engine_config(
        {
            "migration_settings": {
                "auto_migrate": False,
                "create_backup": False,
            }
        }
    )

    assert config["migration_settings.auto_migrate"] is False
    assert config["migration_settings.create_backup"] is False


def test_factory_projects_security_strict_mode_for_provider_prefilter() -> None:
    """安全严格模式必须进入引擎快照并控制 Provider 前预过滤降级。"""

    assert (
        _build_engine_config({"security": {"strict_mode": True}})[
            "security.strict_mode"
        ]
        is True
    )


@pytest.mark.asyncio
async def test_projected_lifecycle_flags_control_real_engine_components(
    tmp_db_path: str,
) -> None:
    """工厂投影的 false 和 true 必须控制真实引擎组件装配。"""

    config = _build_engine_config(
        {
            "user_profile": {"enabled": False},
            "auto_learning": {"enabled": False},
            "knowledge_base": {"enabled": False},
            "notes": {"enabled": False},
            "continuity_tracking": {"enabled": False},
            "reconsolidation": {"enabled": False},
            "anomaly_detection": {"enabled": True},
            "export": {"enabled": False},
            "reranker": {"enabled": False},
            "write_reliability": {"repair_enabled": False},
        }
    )
    config["graph_memory_enabled"] = False
    config["data_dir"] = str(Path(tmp_db_path).parent)
    engine = MemoryEngine(
        db_path=tmp_db_path,
        faiss_db=MagicMock(),
        config=config,
    )
    engine._schema.create_tables = AsyncMock()

    with (
        patch(
            "core.features.memory.application.memory_engine_lifecycle.BM25Retriever"
        ) as bm25_class,
        patch(
            "core.features.memory.application.anomaly_detector.AnomalyDetector"
        ) as detector_class,
    ):
        bm25_class.return_value.initialize = AsyncMock()
        await engine.initialize()

    assert engine.profile_manager is None
    assert engine.anomaly_detector is detector_class.return_value
    await engine.close()


def test_factory_projects_retrieval_graph_decay_and_atom_fields() -> None:
    """评分、图边、衰减和分类器字段必须使用公开配置值。"""

    config = _build_engine_config(
        {
            "hybrid_scoring": {
                "score_alpha": 0.62,
                "score_beta": 0.21,
                "score_gamma": 0.17,
                "mmr_lambda": 0.43,
            },
            "graph_memory": {
                "score_alpha": 0.41,
                "score_beta": 0.29,
                "score_gamma": 0.19,
                "score_delta": 0.11,
                "temporal_edges_enabled": False,
                "causal_edges_enabled": False,
            },
            "flashbulb": {"enabled": False, "intensity_threshold": 0.77},
            "human_like_memory": {
                "recency_bump_enabled": False,
                "seasonal_recall_enabled": False,
                "emotion_scoring_mode": "basic",
                "human_like_formatter_mode": "disabled",
                "type_aware_decay_enabled": False,
            },
            "atom_classifier": {"negation_detection_enabled": False},
        }
    )

    expected = {
        "hybrid_scoring.score_alpha": 0.62,
        "hybrid_scoring.score_beta": 0.21,
        "hybrid_scoring.score_gamma": 0.17,
        "hybrid_scoring.mmr_lambda": 0.43,
        "graph_memory.score_alpha": 0.41,
        "graph_memory.score_beta": 0.29,
        "graph_memory.score_gamma": 0.19,
        "graph_memory.score_delta": 0.11,
        "graph_memory.temporal_edges_enabled": False,
        "graph_memory.causal_edges_enabled": False,
        "flashbulb.enabled": False,
        "flashbulb.intensity_threshold": 0.77,
        "human_like_memory.recency_bump_enabled": False,
        "human_like_memory.seasonal_recall_enabled": False,
        "human_like_memory.emotion_scoring_mode": "basic",
        "human_like_memory.human_like_formatter_mode": "disabled",
        "human_like_memory.type_aware_decay_enabled": False,
        "atom_classifier.negation_detection_enabled": False,
    }
    for key, value in expected.items():
        assert config[key] == value


def test_runtime_mapping_is_explicit_unique_and_marks_graph_rebuild() -> None:
    """运行时映射不得重复来源或目标，图边开关必须标记重建。"""

    from core.platform.composition.engine_runtime_config import (
        ENGINE_RUNTIME_FIELDS,
        RuntimeConfigEffect,
    )
    from core.platform.config import REBUILD_REQUIRED_PATHS

    sources = [field.source_path for field in ENGINE_RUNTIME_FIELDS]
    targets = [field.target_key for field in ENGINE_RUNTIME_FIELDS]
    assert len(sources) == len(set(sources))
    assert len(targets) == len(set(targets))
    effects = {field.source_path: field.effect for field in ENGINE_RUNTIME_FIELDS}
    assert effects["graph_memory.temporal_edges_enabled"] is RuntimeConfigEffect.REBUILD
    assert effects["graph_memory.causal_edges_enabled"] is RuntimeConfigEffect.REBUILD
    assert {
        path
        for path, effect in effects.items()
        if effect is RuntimeConfigEffect.REBUILD
    } == REBUILD_REQUIRED_PATHS


def test_runtime_mapping_fallbacks_match_pydantic_defaults() -> None:
    """映射表后备值必须与唯一 Pydantic 默认配置完全一致。"""

    from core.platform.composition.engine_runtime_config import ENGINE_RUNTIME_FIELDS

    defaults = get_default_config()
    mismatches = {
        field.source_path: (
            field.default,
            _get_dotted_value(defaults, field.source_path),
        )
        for field in ENGINE_RUNTIME_FIELDS
        if field.default != _get_dotted_value(defaults, field.source_path)
    }
    assert mismatches == {}


def test_every_schema_leaf_has_an_explicit_owner_classification() -> None:
    """每个公开配置叶都必须声明分类和唯一运行时责任方。"""

    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    paths = _iter_schema_leaf_paths(schema)
    ownership = {path: resolve_config_ownership(path) for path in paths}

    assert len(paths) == len(set(paths))
    assert all(item.owner for item in ownership.values())
    assert (
        ownership["topic_segmentation.strategy"].owner
        == "core.features.reflection.application.topic_batch_preparer"
    )
    assert ownership["memory_evolution.enabled"].owner == (
        "core.features.evolution.application.memory_evolution_manager"
    )
    assert ownership["episode_clustering.enabled"].owner == (
        "core.features.evolution.application.episode_clusterer"
    )
    assert ownership["semantic_compression.enabled"].owner == (
        "core.features.evolution.application.semantic_compressor"
    )
    assert {
        ownership["recall_engine.top_k"].kind,
        ownership["dashboard.allow_runtime_build"].kind,
        ownership["episode_clustering.enabled"].kind,
    } == {
        ConfigOwnershipKind.RUNTIME,
        ConfigOwnershipKind.DASHBOARD_ONLY,
        ConfigOwnershipKind.EXPERIMENTAL,
    }


def test_removed_index_management_branch_is_not_published() -> None:
    """孤立索引控制面不得继续出现在默认配置、Schema 或所有权表。"""

    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))

    assert "index_management" not in schema
    assert "index_management" not in get_default_config()
    with pytest.raises(KeyError, match="index_management"):
        resolve_config_ownership("index_management.ivf_switch_threshold")


def test_pydantic_models_runtime_sections_and_rejects_fake_llm_mode() -> None:
    """正式运行时分支必须获得类型模型并拒绝未实现的 formatter 模式。"""

    config = validate_config(
        {
            "human_like_memory": {
                "recency_bump_enabled": False,
                "emotion_scoring_mode": "basic",
                "human_like_formatter_mode": "disabled",
            },
            "hybrid_scoring": {
                "score_alpha": 0.6,
                "score_beta": 0.2,
                "score_gamma": 0.2,
            },
            "atom_classifier": {"negation_detection_enabled": False},
        }
    )

    assert config.human_like_memory.recency_bump_enabled is False
    assert config.human_like_memory.emotion_scoring_mode == "basic"
    assert config.hybrid_scoring.score_alpha == pytest.approx(0.6)
    assert config.atom_classifier.negation_detection_enabled is False
    with pytest.raises(ValueError):
        validate_config({"human_like_memory": {"human_like_formatter_mode": "llm"}})
    with pytest.raises(ValueError):
        validate_config({"hybrid_scoring": {"score_alpha": 1.1}})


@pytest.mark.parametrize(
    "section, values",
    [
        (
            "hybrid_scoring",
            {
                "score_alpha": 0.4,
                "score_beta": 0.3,
                "score_gamma": 0.2,
            },
        ),
        (
            "graph_memory",
            {
                "score_alpha": 0.4,
                "score_beta": 0.2,
                "score_gamma": 0.2,
                "score_delta": 0.1,
            },
        ),
    ],
)
def test_score_weights_reject_legal_individual_values_with_invalid_sum(
    section: str,
    values: dict[str, float],
) -> None:
    """评分权重即使单值均合法，总和不为一也必须拒绝。"""

    with pytest.raises(ValueError):
        validate_config({section: values})


def test_recency_bump_can_be_disabled_without_disabling_decay() -> None:
    """关闭近因加成后近期记忆仍衰减，但不再获得额外倍率。"""

    now = time.time()
    fused = FusedResult(
        doc_id=1,
        rrf_score=1.0,
        bm25_score=1.0,
        vector_score=1.0,
        content="近期记忆",
        metadata={"importance": 0.5, "create_time": now},
    )
    enabled = ScoreWeighting(
        decay_rate=0.0,
        score_alpha=0.0,
        score_beta=0.0,
        score_gamma=1.0,
        recency_bump_enabled=True,
    ).apply_weighting([fused], now)[0]
    disabled = ScoreWeighting(
        decay_rate=0.0,
        score_alpha=0.0,
        score_beta=0.0,
        score_gamma=1.0,
        recency_bump_enabled=False,
    ).apply_weighting([fused], now)[0]

    assert enabled.score_breakdown is not None
    assert disabled.score_breakdown is not None
    assert enabled.score_breakdown["recency_weight"] == pytest.approx(1.5)
    assert disabled.score_breakdown["recency_weight"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_emotion_and_seasonal_boost_modes_have_neutral_paths() -> None:
    """情感 disabled 与季节关闭时必须保持分数中性。"""

    disabled = RetrievalOptimizer(
        config={
            "human_like_memory.emotion_scoring_mode": "disabled",
            "human_like_memory.seasonal_recall_enabled": False,
        }
    )
    disabled_result = _hybrid_result(
        emotion_tags=["joy"],
        event_time=time.time() - 365 * 86400,
    )
    output = await disabled.apply_boosts([disabled_result], ["joy"])
    assert output[0].final_score == pytest.approx(1.0)

    basic = RetrievalOptimizer(
        config={
            "human_like_memory.emotion_scoring_mode": "basic",
            "human_like_memory.seasonal_recall_enabled": False,
        }
    )
    enhanced = RetrievalOptimizer(
        config={
            "human_like_memory.emotion_scoring_mode": "enhanced",
            "human_like_memory.seasonal_recall_enabled": False,
        }
    )
    basic_score = (
        await basic.apply_boosts([_hybrid_result(emotion_tags=["joy"])], ["joy"])
    )[0].final_score
    enhanced_score = (
        await enhanced.apply_boosts(
            [_hybrid_result(emotion_tags=["joy"])],
            ["joy"],
        )
    )[0].final_score
    assert basic_score > 1.0
    assert enhanced_score > 1.0
    assert basic_score != pytest.approx(enhanced_score)


def test_atom_negation_detection_can_be_disabled_end_to_end() -> None:
    """关闭否定检测后分类器和 MemoryProcessor 都不得写入负极性。"""

    atoms = classify_atoms(
        ["明天不去跑步"],
        enable_quality_filter=False,
        enable_negation_detection=False,
    )
    assert atoms[0].atom_type.value == "planned"
    assert "polarity" not in atoms[0].metadata

    processor = MemoryProcessor(
        config={
            "atom_enabled": True,
            "atom_quality_filter_enabled": False,
            "atom_classifier.negation_detection_enabled": False,
        }
    )
    processed = processor.classify_atoms_from_metadata({"key_facts": ["明天不去跑步"]})
    assert processed[0].atom_type.value == "planned"
    assert "polarity" not in processed[0].metadata


class _DecayHost(DecayOperationsMixin):
    """提供真实 SQLite 衰减边界所需的最小宿主。"""

    def __init__(self, db: aiosqlite.Connection, config: dict[str, object]) -> None:
        """保存连接、运行时配置和缓存失效回调。"""

        self._db = db
        self._config = config
        self._invalidate_cache = MagicMock()


async def _decay_importance_by_type(type_aware: bool) -> dict[str, float]:
    """运行一次真实衰减并按记忆类型返回最终重要性。"""

    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)")
    for memory_type in ("EPISODIC", "FACTUAL"):
        await db.execute(
            "INSERT INTO documents(metadata) VALUES (?)",
            (
                json.dumps(
                    {
                        "importance": 0.8,
                        "memory_type": memory_type,
                        "emotional_intensity": 0.2,
                    }
                ),
            ),
        )
    await db.commit()
    host = _DecayHost(
        db,
        {
            "access_decay_window_days": 30.0,
            "access_decay_max_count": 10.0,
            "access_count_decay_multiplier": 0.5,
            "human_like_memory.type_aware_decay_enabled": type_aware,
            "flashbulb.enabled": False,
        },
    )
    await host.apply_daily_decay(0.1)
    cursor = await db.execute("SELECT metadata FROM documents ORDER BY id")
    rows = await cursor.fetchall()
    await db.close()
    values = [json.loads(row["metadata"])["importance"] for row in rows]
    return {"EPISODIC": values[0], "FACTUAL": values[1]}


@pytest.mark.asyncio
async def test_type_aware_decay_uses_only_the_public_dotted_key() -> None:
    """类型衰减关闭时不同类型使用相同倍率，开启时产生差异。"""

    disabled = await _decay_importance_by_type(False)
    enabled = await _decay_importance_by_type(True)

    assert disabled["EPISODIC"] == pytest.approx(disabled["FACTUAL"])
    assert enabled["EPISODIC"] < enabled["FACTUAL"]


def _tool_event() -> MagicMock:
    """构造 MemorySearchTool 所需的最小消息事件。"""

    event = MagicMock(unified_msg_origin="session-1")
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
    event.get_sender_id.return_value = "user-1"
    event.get_extra.return_value = SimpleNamespace(trust_status="unsupported")
    return event


@pytest.mark.asyncio
async def test_memory_search_formatter_mode_can_return_structured_only() -> None:
    """格式化模式为 disabled 时仅返回结构化结果，rule 时保留片段。"""

    memory = SimpleNamespace(
        doc_id=1,
        content="用户喜欢无糖拿铁",
        final_score=0.9,
        metadata={"importance": 0.8},
    )
    engine = SimpleNamespace(search_memories=AsyncMock(return_value=[memory]))
    plugin_context = MagicMock()

    disabled_tool = MemorySearchTool(
        context=plugin_context,
        config_manager=ConfigManager(
            user_config={"human_like_memory": {"human_like_formatter_mode": "disabled"}}
        ),
        memory_engine=engine,
    )
    disabled_result = await call_text_handler(
        disabled_tool,
        _tool_event(),
        query="拿铁",
    )
    assert isinstance(disabled_result, str)
    disabled_data = json.loads(disabled_result)
    assert disabled_data["results"]
    assert disabled_data["formatted_recall"] == []

    rule_tool = MemorySearchTool(
        context=plugin_context,
        config_manager=ConfigManager(
            user_config={"human_like_memory": {"human_like_formatter_mode": "rule"}}
        ),
        memory_engine=engine,
    )
    rule_result = await call_text_handler(
        rule_tool,
        _tool_event(),
        query="拿铁",
    )
    assert isinstance(rule_result, str)
    rule_data = json.loads(rule_result)
    assert rule_data["formatted_recall"]


@pytest.mark.asyncio
async def test_config_apply_reports_restart_and_graph_rebuild_effects() -> None:
    """配置 API 必须区分重启需求、自动重载与图重建需求。"""

    from core.platform.transport.page_api.config_api import ConfigApiMixin

    class _ConfigApi(ConfigApiMixin):
        """暴露配置 API mixin 的最小测试实现。"""

        plugin: Any

        def _maintenance_write_guard(self):
            """测试中不阻止配置写入。"""

            return None

    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "base_revision": "rev-old",
                "changes": {"graph_memory.temporal_edges_enabled": False},
            }
        )
    )
    manager = MagicMock()
    manager.apply_config_changes = AsyncMock(
        return_value=ConfigApplyResult(
            "rev-new",
            ("graph_memory.temporal_edges_enabled",),
        )
    )
    plugin = SimpleNamespace(
        context=SimpleNamespace(request=request),
        config_manager=manager,
        instance_id="instance-1",
        schedule_plugin_reload=MagicMock(return_value=False),
    )
    api = _ConfigApi()
    api.plugin = plugin

    result = await api.apply_config()

    assert result["status"] == "ok"
    assert result["data"]["restart_required"] is True
    assert result["data"]["rebuild_required"] is True
    assert result["data"]["reload_scheduled"] is False
