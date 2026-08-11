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

    from core.base.config_manager import ConfigManager

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

    from core.base.config_manager import ConfigManager

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

    from core.base.config_manager import ConfigManager

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
