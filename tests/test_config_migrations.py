"""旧配置到当前公开配置契约的迁移测试。"""

from __future__ import annotations

import pytest


def test_legacy_reranker_config_migrates_to_embedding_similarity() -> None:
    """旧策略和权重键应迁移为新名称，且不修改调用方源字典。"""

    from core.platform.config import migrate_legacy_config

    source = {
        "reranker": {
            "strategy": "cross_encoder",
            "cross_encoder_lambda": 0.42,
        }
    }

    migrated, applied = migrate_legacy_config(source)

    assert migrated["reranker"] == {
        "strategy": "embedding_similarity",
        "embedding_similarity_lambda": 0.42,
    }
    assert applied == ("reranker.cross_encoder_to_embedding_similarity",)
    assert source["reranker"]["strategy"] == "cross_encoder"
    assert source["reranker"]["cross_encoder_lambda"] == 0.42


def test_new_reranker_weight_wins_over_legacy_weight() -> None:
    """新旧权重同时存在时保留新键，并移除旧键避免双轨运行。"""

    from core.platform.config import migrate_legacy_config

    migrated, applied = migrate_legacy_config(
        {
            "reranker": {
                "strategy": "embedding_similarity",
                "cross_encoder_lambda": 0.2,
                "embedding_similarity_lambda": 0.8,
            }
        }
    )

    assert migrated["reranker"] == {
        "strategy": "embedding_similarity",
        "embedding_similarity_lambda": 0.8,
    }
    assert applied == ("reranker.cross_encoder_to_embedding_similarity",)


def test_config_manager_runtime_snapshot_contains_only_new_reranker_names() -> None:
    """生产配置快照应消费迁移结果，且不再暴露任何旧运行时键。"""

    from core.platform.config import ConfigManager

    manager = ConfigManager(
        {
            "reranker": {
                "strategy": "cross_encoder",
                "cross_encoder_lambda": 0.35,
            }
        }
    )

    reranker = manager.get_section("reranker")
    assert reranker["strategy"] == "embedding_similarity"
    assert reranker["embedding_similarity_lambda"] == 0.35
    assert "cross_encoder_lambda" not in reranker


def test_config_manager_reports_each_legacy_migration_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一管理器重复协调旧源配置时不得重复输出迁移告警。"""

    from core.platform.config import ConfigManager

    warnings: list[str] = []
    monkeypatch.setattr("core.platform.config.manager.logger.warning", warnings.append)
    manager = ConfigManager({"reranker": {"strategy": "cross_encoder"}})

    manager._read_source_state()

    assert warnings == [
        "已迁移旧配置运行时快照: reranker.cross_encoder_to_embedding_similarity"
    ]


@pytest.mark.asyncio
async def test_next_config_save_persists_only_new_reranker_names() -> None:
    """旧配置载入后的下一次正常保存应把外部源收敛到新名称。"""

    from core.platform.config import ConfigManager

    source = {
        "reranker": {
            "strategy": "cross_encoder",
            "cross_encoder_lambda": 0.35,
        }
    }
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()

    await manager.apply_config_changes(
        {"reranker.mmr_lambda": 0.6},
        expected_revision=revision,
    )

    assert source["reranker"]["strategy"] == "embedding_similarity"
    assert source["reranker"]["embedding_similarity_lambda"] == 0.35
    assert "cross_encoder_lambda" not in source["reranker"]


def test_legacy_llm_formatter_mode_migrates_to_rule() -> None:
    """早期文档宣传过但从未实现的 llm 格式化模式应迁移为 rule，且不修改源字典。"""

    from core.platform.config import migrate_legacy_config

    source = {
        "human_like_memory": {
            "emotion_scoring_mode": "basic",
            "human_like_formatter_mode": "llm",
            "type_aware_decay_enabled": False,
        }
    }

    migrated, applied = migrate_legacy_config(source)

    assert migrated["human_like_memory"] == {
        "emotion_scoring_mode": "basic",
        "human_like_formatter_mode": "rule",
        "type_aware_decay_enabled": False,
    }
    assert applied == ("human_like_memory.formatter_llm_to_rule",)
    assert source["human_like_memory"]["human_like_formatter_mode"] == "llm"


def test_valid_formatter_modes_are_not_migrated() -> None:
    """rule 与 disabled 是当前合法模式，不得触发迁移标识。"""

    from core.platform.config import migrate_legacy_config

    for mode in ("rule", "disabled"):
        migrated, applied = migrate_legacy_config(
            {"human_like_memory": {"human_like_formatter_mode": mode}}
        )
        assert migrated["human_like_memory"]["human_like_formatter_mode"] == mode
        assert applied == ()


def test_config_manager_snapshot_normalizes_legacy_llm_formatter_mode() -> None:
    """生产快照应把旧 llm 值消费为 rule，并保留同节其它用户设置，不再整节降级。"""

    from core.platform.config import ConfigManager

    manager = ConfigManager(
        {
            "human_like_memory": {
                "emotion_scoring_mode": "basic",
                "human_like_formatter_mode": "llm",
                "type_aware_decay_enabled": False,
            }
        }
    )

    section = manager.get_section("human_like_memory")
    assert section["human_like_formatter_mode"] == "rule"
    assert section["emotion_scoring_mode"] == "basic"
    assert section["type_aware_decay_enabled"] is False


def test_legacy_candidate_reuse_disabled_migrates_to_off() -> None:
    """旧 disabled 模式应迁移为计划闭集的 off，且不修改源字典。"""

    from core.platform.config import migrate_legacy_config

    source = {
        "topic_segmentation": {"candidate_reuse": {"mode": "disabled", "fixed_k": 5}}
    }

    migrated, applied = migrate_legacy_config(source)

    assert migrated["topic_segmentation"]["candidate_reuse"]["mode"] == "off"
    assert applied == ("topic_segmentation.candidate_reuse.mode_vocabulary",)
    assert source["topic_segmentation"]["candidate_reuse"]["mode"] == "disabled"


def test_legacy_candidate_reuse_adaptive_migrates_to_top_k() -> None:
    """旧 adaptive 模式应迁移为计划闭集的 top_k，且不修改源字典。"""

    from core.platform.config import migrate_legacy_config

    source = {
        "topic_segmentation": {"candidate_reuse": {"mode": "adaptive", "fixed_k": 5}}
    }

    migrated, applied = migrate_legacy_config(source)

    assert migrated["topic_segmentation"]["candidate_reuse"]["mode"] == "top_k"
    assert applied == ("topic_segmentation.candidate_reuse.mode_vocabulary",)
    assert source["topic_segmentation"]["candidate_reuse"]["mode"] == "adaptive"


def test_valid_candidate_reuse_modes_are_not_migrated() -> None:
    """计划闭集内的模式值不得触发迁移标识。"""

    from core.platform.config import migrate_legacy_config

    for mode in ("off", "observe", "full", "top_k"):
        migrated, applied = migrate_legacy_config(
            {"topic_segmentation": {"candidate_reuse": {"mode": mode}}}
        )
        assert migrated["topic_segmentation"]["candidate_reuse"]["mode"] == mode
        assert applied == ()


def test_config_manager_snapshot_consumes_legacy_candidate_reuse_mode() -> None:
    """生产快照应把旧词表值消费为新闭集值，同节其它设置保留。"""

    from core.platform.config import ConfigManager

    manager = ConfigManager(
        {
            "topic_segmentation": {
                "candidate_reuse": {
                    "mode": "adaptive",
                    "fixed_k": 4,
                }
            }
        }
    )

    section = manager.get_section("topic_segmentation")["candidate_reuse"]
    assert section["mode"] == "top_k"
    assert section["fixed_k"] == 4


def test_missing_candidate_reuse_section_is_not_migrated() -> None:
    """无 candidate_reuse 节点时不产生迁移标识。"""

    from core.platform.config import migrate_legacy_config

    migrated, applied = migrate_legacy_config({"topic_segmentation": {}})

    assert migrated == {"topic_segmentation": {}}
    assert applied == ()
