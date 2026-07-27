"""core/base/ 测试 — 配置默认值、配置管理器、配置验证器、
constants, and exceptions.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# 2. core/base/exceptions.py
# ---------------------------------------------------------------------------


class TestMemoraException:
    """Test the base MemoraException and its hierarchy."""

    def test_base_exception_with_default_error_code(self) -> None:
        from core.base.exceptions import MemoraException

        exc = MemoraException("test message")
        assert exc.message == "test message"
        assert exc.error_code == "UNKNOWN_ERROR"
        assert str(exc) == "test message"

    def test_base_exception_with_custom_error_code(self) -> None:
        from core.base.exceptions import MemoraException

        exc = MemoraException("custom error", error_code="CUSTOM_ERR")
        assert exc.message == "custom error"
        assert exc.error_code == "CUSTOM_ERR"
        assert str(exc) == "custom error"

    def test_base_exception_is_exception_subclass(self) -> None:
        from core.base.exceptions import MemoraException

        assert issubclass(MemoraException, Exception)


class TestExceptionSubclasses:
    """Verify each subclass carries the correct error_code."""

    @pytest.mark.parametrize(
        "exc_class, expected_code",
        [
            ("InitializationError", "INIT_ERROR"),
            ("ProviderNotReadyError", "PROVIDER_NOT_READY"),
            ("DatabaseError", "DATABASE_ERROR"),
            ("RetrievalError", "RETRIEVAL_ERROR"),
            ("MemoryProcessingError", "MEMORY_PROCESSING_ERROR"),
            ("ConfigurationError", "CONFIG_ERROR"),
            ("ValidationError", "VALIDATION_ERROR"),
        ],
    )
    def test_subclass_error_code(self, exc_class: str, expected_code: str) -> None:
        from core.base import exceptions

        cls = getattr(exceptions, exc_class)
        exc = cls("test")
        assert exc.error_code == expected_code

    @pytest.mark.parametrize(
        "exc_class",
        [
            "InitializationError",
            "ProviderNotReadyError",
            "DatabaseError",
            "RetrievalError",
            "MemoryProcessingError",
            "ConfigurationError",
            "ValidationError",
        ],
    )
    def test_subclass_is_memora_exception(self, exc_class: str) -> None:
        from core.base import exceptions
        from core.base.exceptions import MemoraException

        cls = getattr(exceptions, exc_class)
        assert issubclass(cls, MemoraException)

    def test_provider_not_ready_has_default_message(self) -> None:
        from core.base.exceptions import ProviderNotReadyError

        exc = ProviderNotReadyError()
        assert exc.message == "Provider未就绪"

    def test_provider_not_ready_accepts_custom_message(self) -> None:
        from core.base.exceptions import ProviderNotReadyError

        exc = ProviderNotReadyError("Custom message")
        assert exc.message == "Custom message"


# ---------------------------------------------------------------------------
# 3. core/base/config_validator.py
# ---------------------------------------------------------------------------


class TestGetDefaultConfig:
    """Tests for get_default_config()."""

    def test_returns_dict(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        assert isinstance(config, dict)

    def test_contains_expected_top_level_keys(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        expected_keys = {
            "session_manager",
            "recall_engine",
            "reflection_engine",
            "agent_tools",
            "dashboard",
            "forgetting_agent",
            "filtering_settings",
            "provider_settings",
            "migration_settings",
            "index_rebuild_settings",
            "graph_memory",
            "fusion_strategy",
            "importance_decay",
            "topic_segmentation",
        }
        missing = expected_keys - set(config.keys())
        assert not missing, f"Missing top-level keys: {missing}"

    def test_dashboard_defaults(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        dashboard = config["dashboard"]
        assert dashboard["allow_runtime_build"] is False
        assert dashboard["build_timeout_seconds"] == 120
        assert dashboard["max_output_chars"] == 20000

    def test_session_manager_defaults(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        sm = config["session_manager"]
        assert sm["max_sessions"] == 100
        assert sm["session_ttl"] == 3600
        assert sm["context_window_size"] == 50
        assert sm["enable_full_group_capture"] is True
        assert sm["max_messages_per_session"] == 1000
        assert sm["cleanup_batch_size"] == 50

    def test_recall_engine_defaults(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        re_cfg = config["recall_engine"]
        assert re_cfg["top_k"] == 5
        assert re_cfg["max_k"] == 10
        assert re_cfg["importance_weight"] == 1.0
        assert "injection_method" not in re_cfg
        assert re_cfg["injection_routing_mode"] == "manual"
        assert re_cfg["injection_manual_preset"] == "balanced"
        assert re_cfg["injection_auto_fallback_preset"] == "balanced"
        assert re_cfg["injection_hybrid_base_preset"] == "balanced"
        assert re_cfg["injection_hybrid_min_preset"] == "low_cost"
        assert re_cfg["injection_hybrid_max_preset"] == "quality"
        assert re_cfg["injection_delivery_override"] == "auto"
        assert re_cfg["injection_preset_overrides_enabled"] is False
        assert re_cfg["injection_budget_chars"] == 0
        assert re_cfg["injection_memory_max_chars"] == 0
        assert re_cfg["injection_metadata_max_chars"] == 0
        assert re_cfg["injection_decision_retention_days"] == 30
        assert re_cfg["injection_decision_max_rows"] == 100_000

    def test_graph_memory_defaults(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        gm = config["graph_memory"]
        assert gm["enabled"] is True
        assert gm["document_route_weight"] == pytest.approx(0.65)
        assert gm["graph_route_weight"] == pytest.approx(0.35)
        assert gm["expansion_hops"] == 1
        assert gm["max_topics_per_memory"] == 6

    def test_topic_segmentation_defaults(self) -> None:
        from core.base.config_validator import get_default_config

        config = get_default_config()
        ts = config["topic_segmentation"]
        assert ts["enabled"] is True
        assert ts["strategy"] == "a_b_hybrid"
        assert "strategy_b" in ts
        assert "strategy_c" in ts
        assert "strategy_d" in ts
        assert "legacy_backfill" in ts


class TestValidateConfig:
    """Tests for validate_config()."""

    def test_valid_empty_config_returns_memora_config(self) -> None:
        from core.base.config_validator import MemoraConfig, validate_config

        result = validate_config({})
        assert isinstance(result, MemoraConfig)

    def test_partial_override_preserves_defaults(self) -> None:
        from core.base.config_validator import validate_config

        result = validate_config({"recall_engine": {"top_k": 10}})
        assert result.recall_engine.top_k == 10
        # Other defaults preserved
        assert result.session_manager.max_sessions == 100

    def test_invalid_config_raises_value_error(self) -> None:
        from core.base.config_validator import validate_config

        with pytest.raises(ValueError, match="插件配置无效"):
            validate_config({"session_manager": {"max_sessions": -1}})

    def test_extra_fields_allowed_by_default(self) -> None:
        from core.base.config_validator import validate_config

        # Extra fields should not cause validation failure (model_config extra=allow)
        result = validate_config({"unknown_section": {"key": "value"}})
        assert result is not None

    def test_topic_segmentation_strategy_valid_values(self) -> None:
        from core.base.config_validator import validate_config

        for strat in [
            "a_b_hybrid",
            "strategy_a",
            "strategy_b",
            "strategy_c",
            "strategy_d",
        ]:
            result = validate_config({"topic_segmentation": {"strategy": strat}})
            assert result.topic_segmentation.strategy == strat


class TestMergeConfigWithDefaults:
    """Tests for merge_config_with_defaults()."""

    def test_empty_user_config_returns_full_defaults(self) -> None:
        from core.base.config_validator import merge_config_with_defaults

        merged = merge_config_with_defaults({})
        assert "session_manager" in merged
        assert "recall_engine" in merged
        assert "graph_memory" in merged

    def test_user_value_overrides_default(self) -> None:
        from core.base.config_validator import merge_config_with_defaults

        merged = merge_config_with_defaults({"recall_engine": {"top_k": 20}})
        assert merged["recall_engine"]["top_k"] == 20

    def test_deep_merge_preserves_nested_defaults(self) -> None:
        from core.base.config_validator import merge_config_with_defaults

        merged = merge_config_with_defaults({"recall_engine": {"top_k": 20}})
        # top_k overridden but importance_weight preserved from defaults
        assert merged["recall_engine"]["importance_weight"] == 1.0

    def test_non_dict_user_value_replaces_default(self) -> None:
        from core.base.config_validator import merge_config_with_defaults

        merged = merge_config_with_defaults({"recall_engine": "not_a_dict"})
        assert merged["recall_engine"] == "not_a_dict"

    def test_deep_nested_merge(self) -> None:
        from core.base.config_validator import merge_config_with_defaults

        merged = merge_config_with_defaults(
            {"topic_segmentation": {"strategy_b": {"similarity_threshold": 0.8}}}
        )
        assert merged["topic_segmentation"]["strategy_b"]["similarity_threshold"] == 0.8
        # Other fields in strategy_b preserved
        assert merged["topic_segmentation"]["strategy_b"]["min_cluster_size"] == 1


class TestValidateRuntimeConfigChanges:
    """Tests for validate_runtime_config_changes()."""

    def test_valid_simple_change(self) -> None:
        from core.base.config_validator import (
            MemoraConfig,
            validate_runtime_config_changes,
        )

        current = MemoraConfig()
        assert (
            validate_runtime_config_changes(current, {"recall_engine.top_k": 10})
            is True
        )

    def test_valid_nested_change(self) -> None:
        from core.base.config_validator import (
            MemoraConfig,
            validate_runtime_config_changes,
        )

        current = MemoraConfig()
        assert (
            validate_runtime_config_changes(
                current, {"topic_segmentation.legacy_backfill.batch_size": 100}
            )
            is True
        )

    def test_invalid_change_returns_false(self) -> None:
        from core.base.config_validator import (
            MemoraConfig,
            validate_runtime_config_changes,
        )

        current = MemoraConfig()
        assert (
            validate_runtime_config_changes(
                current, {"session_manager.max_sessions": -5}
            )
            is False
        )

    def test_multiple_changes_valid(self) -> None:
        from core.base.config_validator import (
            MemoraConfig,
            validate_runtime_config_changes,
        )

        current = MemoraConfig()
        assert (
            validate_runtime_config_changes(
                current,
                {
                    "recall_engine.top_k": 15,
                    "graph_memory.enabled": False,
                },
            )
            is True
        )

    def test_empty_changes_valid(self) -> None:
        from core.base.config_validator import (
            MemoraConfig,
            validate_runtime_config_changes,
        )

        current = MemoraConfig()
        assert validate_runtime_config_changes(current, {}) is True


class TestMemoraConfigBoundaries:
    """Test boundary values for Pydantic field constraints."""

    def test_session_manager_max_sessions_boundary_min(self) -> None:
        from core.base.config_validator import MemoraConfig

        cfg = MemoraConfig(session_manager={"max_sessions": 1})
        assert cfg.session_manager.max_sessions == 1

    def test_session_manager_max_sessions_boundary_max(self) -> None:
        from core.base.config_validator import MemoraConfig

        cfg = MemoraConfig(session_manager={"max_sessions": 10000})
        assert cfg.session_manager.max_sessions == 10000

    def test_recall_engine_top_k_boundary_zero(self) -> None:
        from core.base.config_validator import MemoraConfig

        cfg = MemoraConfig(recall_engine={"top_k": 0})
        assert cfg.recall_engine.top_k == 0

    def test_recall_engine_can_disable_auto_recall(self) -> None:
        from core.base.config_validator import MemoraConfig

        # Setting top_k=0 disables automatic recall per the description
        cfg = MemoraConfig(recall_engine={"top_k": 0})
        assert cfg.recall_engine.top_k == 0

    def test_graph_memory_route_weights_normalized(self) -> None:
        from core.base.config_validator import MemoraConfig

        # Values within field bounds (le=1.0) but sum != 1.0 should be normalized
        cfg = MemoraConfig(
            graph_memory={
                "document_route_weight": 0.4,
                "graph_route_weight": 0.2,
            }
        )
        total = 0.4 + 0.2
        assert cfg.graph_memory.document_route_weight == pytest.approx(0.4 / total)
        assert cfg.graph_memory.graph_route_weight == pytest.approx(0.2 / total)
        assert (
            cfg.graph_memory.document_route_weight + cfg.graph_memory.graph_route_weight
            == pytest.approx(1.0)
        )


# ---------------------------------------------------------------------------
# 4. core/base/config_manager.py
# ---------------------------------------------------------------------------


class TestConfigManager:
    """Tests for ConfigManager — get, get_section, get_all, and properties."""

    def test_get_top_level_key(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"test_key": "test_value"})
        assert mgr.get("test_key") == "test_value"

    def test_get_nested_key_with_dot_notation(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager(
            {
                "recall_engine": {
                    "top_k": 15,
                    "max_k": 25,
                }
            }
        )
        assert mgr.get("recall_engine.top_k") == 15
        assert mgr.get("recall_engine.max_k") == 25

    def test_get_deeply_nested_key(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager(
            {
                "topic_segmentation": {
                    "strategy_b": {
                        "similarity_threshold": 0.75,
                    }
                }
            }
        )
        assert mgr.get("topic_segmentation.strategy_b.similarity_threshold") == 0.75

    def test_get_missing_key_returns_default(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({})
        assert mgr.get("nonexistent.key", default=42) == 42

    def test_get_missing_key_no_default_returns_none(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({})
        assert mgr.get("nonexistent.key") is None

    def test_get_non_dict_intermediate_returns_default(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"flat_key": 123})
        assert mgr.get("flat_key.nested", default="fallback") == "fallback"

    def test_get_section_returns_dict(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"recall_engine": {"top_k": 5, "max_k": 10}})
        section = mgr.get_section("recall_engine")
        assert isinstance(section, dict)
        assert section["top_k"] == 5

    def test_get_section_missing_returns_empty_dict(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({})
        assert mgr.get_section("nonexistent") == {}

    def test_get_all_returns_copy(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"test_key": "test_value"})
        all_config = mgr.get_all()
        assert isinstance(all_config, dict)
        assert "test_key" in all_config
        # Verify it's a copy, not the original
        all_config["new_key"] = "new_value"
        assert mgr.get("new_key") is None

    def test_provider_settings_property(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"provider_settings": {"llm_provider_id": "test-provider"}})
        ps = mgr.provider_settings
        assert isinstance(ps, dict)
        assert ps["llm_provider_id"] == "test-provider"

    def test_session_manager_property(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"session_manager": {"max_sessions": 200}})
        sm = mgr.session_manager
        assert sm["max_sessions"] == 200

    def test_graph_memory_property(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"graph_memory": {"enabled": False}})
        gm = mgr.graph_memory
        assert gm["enabled"] is False

    def test_empty_user_config_loads_defaults(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager()
        assert mgr.get("session_manager.max_sessions") == 100
        assert mgr.get("recall_engine.top_k") == 5

    def test_merges_user_override_with_defaults(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager(
            {
                "recall_engine": {"top_k": 7},
                "graph_memory": {"enabled": False},
            }
        )
        assert mgr.get("recall_engine.top_k") == 7
        assert mgr.get("graph_memory.enabled") is False

    def test_invalid_section_falls_back_without_losing_other_sections(self) -> None:
        from core.base.config_manager import ConfigManager
        from core.base.config_validator import get_default_config

        defaults = get_default_config()
        mgr = ConfigManager(
            {
                "session_manager": {"max_sessions": -1},
                "recall_engine": {"top_k": 9},
                "graph_memory": {"enabled": False},
            }
        )

        assert (
            mgr.get("session_manager.max_sessions")
            == defaults["session_manager"]["max_sessions"]
        )
        assert mgr.get("recall_engine.top_k") == 9
        assert mgr.get("graph_memory.enabled") is False
        assert any(
            err["section"] == "session_manager" and err["action"] == "defaulted"
            for err in mgr.validation_errors
        )
        # Other defaults still present
        assert mgr.get("session_manager.max_sessions") == 100


class TestConfigManagerEdgeCases:
    """Edge case tests for ConfigManager."""

    def test_zero_value_is_not_treated_as_missing(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"recall_engine": {"top_k": 0}})
        assert mgr.get("recall_engine.top_k") == 0

    def test_false_value_is_not_treated_as_missing(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"graph_memory": {"enabled": False}})
        assert mgr.get("graph_memory.enabled") is False

    def test_empty_string_value_is_returned(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"provider_settings": {"llm_provider_id": ""}})
        assert mgr.get("provider_settings.llm_provider_id") == ""

    def test_empty_dict_value_is_returned(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"filtering_settings": {}})
        # Empty user dict gets deep-merged with defaults, so filtering_settings
        # still contains default sub-keys. The empty override doesn't wipe them.
        result = mgr.get("filtering_settings")
        assert isinstance(result, dict)

    def test_none_value_is_returned_not_default(self) -> None:
        from core.base.config_manager import ConfigManager

        mgr = ConfigManager({"provider_settings": {"embedding_provider_id": None}})
        # None is an explicit value, should be returned not replaced with default
        assert mgr.get("provider_settings.embedding_provider_id") is None
