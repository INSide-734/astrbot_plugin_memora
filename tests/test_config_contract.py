"""配置契约测试：验证 candidate_reuse 配置同步层完整性。"""

import json
from pathlib import Path

import pytest

from core.features.memory.domain.memory_dedup_config import MemoryDedupConfig
from core.features.reflection.domain.config import CandidateReuseConfig
from core.platform.composition.engine_runtime_config import ENGINE_RUNTIME_FIELDS
from core.platform.config.config_validator import (
    MemoraConfig,
    get_default_config,
    validate_config,
)
from core.platform.config.ownership import resolve_config_ownership

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"


def _candidate_reuse_schema_leaves() -> dict:
    """读取公开 Schema 中 candidate_reuse 分支的叶子声明。"""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["topic_segmentation"]["items"]["candidate_reuse"]["items"]


class TestCandidateReuseConfigContract:
    """候选话题重用配置契约测试套件。"""

    def test_schema_defaults(self):
        """验证 Schema 默认值与 Pydantic 模型一致。"""
        config = MemoraConfig()
        cr = config.topic_segmentation.candidate_reuse

        assert cr.mode == "observe"
        assert cr.fixed_k == 8
        assert cr.activation_threshold == 32
        assert cr.max_full_topics == 32
        assert cr.max_full_prompt_tokens == 256

    def test_cross_validation_fixed_k(self):
        """验证 fixed_k <= max_full_topics 交叉校验。"""
        with pytest.raises(ValueError, match="fixed_k 不能大于 max_full_topics"):
            MemoraConfig(
                topic_segmentation={
                    "candidate_reuse": {"fixed_k": 15, "max_full_topics": 10}
                }
            )

    def test_cross_validation_activation_threshold(self):
        """验证 activation_threshold <= max_full_topics 交叉校验。"""
        with pytest.raises(
            ValueError, match="activation_threshold 不能大于 max_full_topics"
        ):
            MemoraConfig(
                topic_segmentation={
                    "candidate_reuse": {
                        "activation_threshold": 50,
                        "max_full_topics": 30,
                    }
                }
            )

    def test_flat_keys_registered(self):
        """验证 5 个扁平 key 已注册到 ENGINE_RUNTIME_FIELDS。"""
        flat_keys = {f.source_path for f in ENGINE_RUNTIME_FIELDS}
        expected = {
            "topic_segmentation.candidate_reuse.mode",
            "topic_segmentation.candidate_reuse.fixed_k",
            "topic_segmentation.candidate_reuse.activation_threshold",
            "topic_segmentation.candidate_reuse.max_full_topics",
            "topic_segmentation.candidate_reuse.max_full_prompt_tokens",
        }
        assert expected.issubset(flat_keys), (
            f"Missing flat keys: {expected - flat_keys}"
        )

    def test_ownership_rules(self):
        """验证 5 个扁平 key 的所有权规则已注册。"""
        paths = [
            "topic_segmentation.candidate_reuse.mode",
            "topic_segmentation.candidate_reuse.fixed_k",
            "topic_segmentation.candidate_reuse.activation_threshold",
            "topic_segmentation.candidate_reuse.max_full_topics",
            "topic_segmentation.candidate_reuse.max_full_prompt_tokens",
        ]
        for path in paths:
            ownership = resolve_config_ownership(path)
            assert ownership is not None, f"Missing ownership for {path}"
            assert (
                ownership.owner
                == "core.features.reflection.application.topic_candidate_selector"
            )

    def test_mode_enum_valid(self):
        """验证 mode 枚举值有效。"""
        for mode in ["off", "observe", "full", "top_k"]:
            config = MemoraConfig(
                topic_segmentation={"candidate_reuse": {"mode": mode}}
            )
            assert config.topic_segmentation.candidate_reuse.mode == mode

    def test_mode_enum_rejects_legacy_vocab(self):
        """验证旧词表 disabled/adaptive 被新闭集拒绝。"""
        for legacy in ["disabled", "adaptive"]:
            with pytest.raises(ValueError):
                MemoraConfig(topic_segmentation={"candidate_reuse": {"mode": legacy}})

    def test_mode_enum_invalid(self):
        """验证 mode 枚举拒绝非法值。"""
        with pytest.raises(ValueError):
            MemoraConfig(topic_segmentation={"candidate_reuse": {"mode": "invalid"}})

    def test_range_constraints(self):
        """验证字段范围约束。"""
        # fixed_k: 1-24（与预注册 K 网格上界一致）
        with pytest.raises(ValueError):
            MemoraConfig(topic_segmentation={"candidate_reuse": {"fixed_k": 0}})
        with pytest.raises(ValueError):
            MemoraConfig(topic_segmentation={"candidate_reuse": {"fixed_k": 25}})

        # activation_threshold: 3-100
        with pytest.raises(ValueError):
            MemoraConfig(
                topic_segmentation={"candidate_reuse": {"activation_threshold": 2}}
            )
        with pytest.raises(ValueError):
            MemoraConfig(
                topic_segmentation={"candidate_reuse": {"activation_threshold": 101}}
            )

        # max_full_topics: 1-50
        with pytest.raises(ValueError):
            MemoraConfig(topic_segmentation={"candidate_reuse": {"max_full_topics": 0}})
        with pytest.raises(ValueError):
            MemoraConfig(
                topic_segmentation={"candidate_reuse": {"max_full_topics": 51}}
            )

        # max_full_prompt_tokens: 50-2000
        with pytest.raises(ValueError):
            MemoraConfig(
                topic_segmentation={"candidate_reuse": {"max_full_prompt_tokens": 49}}
            )
        with pytest.raises(ValueError):
            MemoraConfig(
                topic_segmentation={"candidate_reuse": {"max_full_prompt_tokens": 2001}}
            )


class TestCandidateReuseSchemaSyncContract:
    """schema 叶集合与 Pydantic 字段一一对应的契约测试。"""

    def test_schema_leaf_set_matches_model_fields(self):
        """Schema candidate_reuse 叶集合必须与模型字段集合完全一致。"""
        schema_leaves = _candidate_reuse_schema_leaves()
        model_fields = set(CandidateReuseConfig.model_fields)
        assert set(schema_leaves) == model_fields

    def test_schema_leaf_defaults_match_model_defaults(self):
        """每叶 default 必须与模型字段默认值一致。"""
        defaults = MemoraConfig().topic_segmentation.candidate_reuse
        mismatches = {
            leaf: (node["default"], getattr(defaults, leaf))
            for leaf, node in _candidate_reuse_schema_leaves().items()
            if leaf != "bucket_overrides" and node["default"] != getattr(defaults, leaf)
        }
        assert mismatches == {}

    @pytest.mark.parametrize(
        "leaf",
        sorted(set(CandidateReuseConfig.model_fields) - {"mode", "bucket_overrides"}),
    )
    def test_schema_leaf_bounds_match_field_metadata(self, leaf):
        """每叶 min/max 必须与 Field 约束元数据一致（无约束叶除外）。"""
        node = _candidate_reuse_schema_leaves()[leaf]
        field_info = CandidateReuseConfig.model_fields[leaf]
        metadata = field_info.metadata
        ge = next(
            (m.ge for m in metadata if hasattr(m, "ge") and m.ge is not None), None
        )
        le = next(
            (m.le for m in metadata if hasattr(m, "le") and m.le is not None), None
        )
        # Literal[3]（overfetch_factor）无数值约束，schema 以 min=max=3 表达
        if leaf == "overfetch_factor":
            assert (node["min"], node["max"]) == (3, 3)
            return
        assert node["min"] == ge, f"{leaf} min 与 Field ge 不一致"
        assert node["max"] == le, f"{leaf} max 与 Field le 不一致"

    def test_catalog_reconcile_interval_leaf_registered(self):
        """目录周期收敛间隔叶必须全链登记（schema + 模型 + ownership）。"""
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        node = schema["topic_segmentation"]["items"][
            "catalog_reconcile_interval_seconds"
        ]
        assert node["default"] == 60
        assert (node["min"], node["max"]) == (0, 86400)

        config = MemoraConfig().topic_segmentation
        assert config.catalog_reconcile_interval_seconds == 60

        ownership = resolve_config_ownership(
            "topic_segmentation.catalog_reconcile_interval_seconds"
        )
        assert (
            ownership.owner
            == "core.features.memory.application.catalog_reconcile_scheduler"
        )

    def test_new_candidate_reuse_leaves_have_owner(self):
        """新增 6 叶必须登记所有权且责任方同 selector。"""
        for leaf in (
            "max_query_chars",
            "overfetch_factor",
            "metrics_retention_days",
            "observe_max_candidates",
            "observe_max_rows",
            "observe_max_duration_ms",
        ):
            ownership = resolve_config_ownership(
                f"topic_segmentation.candidate_reuse.{leaf}"
            )
            assert (
                ownership.owner
                == "core.features.reflection.application.topic_candidate_selector"
            )


def _memory_dedup_schema_leaves() -> dict:
    """读取公开 Schema 中 memory_dedup 分支的叶子声明。"""

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["memory_dedup"]["items"]


class TestMemoryDedupConfigContract:
    """跨窗口近重复合并配置的全链契约。"""

    def test_schema_leaf_set_matches_model_fields(self):
        """Schema memory_dedup 叶集合必须与模型字段集合完全一致。"""

        assert set(_memory_dedup_schema_leaves()) == set(MemoryDedupConfig.model_fields)

    def test_schema_leaf_defaults_match_model_defaults(self):
        """每叶 default 必须与模型字段默认值一致。"""

        defaults = MemoraConfig().memory_dedup
        mismatches = {
            leaf: (node["default"], getattr(defaults, leaf))
            for leaf, node in _memory_dedup_schema_leaves().items()
            if node["default"] != getattr(defaults, leaf)
        }
        assert mismatches == {}

    @pytest.mark.parametrize(
        "leaf", ["similarity_threshold", "candidate_limit", "min_tokens"]
    )
    def test_schema_leaf_bounds_match_field_metadata(self, leaf: str):
        """数值叶 min/max 必须与 Field 约束一致。"""

        node = _memory_dedup_schema_leaves()[leaf]
        metadata = MemoryDedupConfig.model_fields[leaf].metadata
        ge = next(
            (m.ge for m in metadata if hasattr(m, "ge") and m.ge is not None), None
        )
        le = next(
            (m.le for m in metadata if hasattr(m, "le") and m.le is not None), None
        )
        assert (node["min"], node["max"]) == (ge, le)

    def test_mode_enum_and_ranges(self):
        """mode 只接受闭集；数值叶拒绝越界值。"""

        for mode in ("off", "observe", "enforce"):
            config = validate_config({"memory_dedup": {"mode": mode}})
            assert config.memory_dedup.mode == mode
        for invalid in (
            {"mode": "enabled"},
            {"similarity_threshold": 1.01},
            {"similarity_threshold": -0.01},
            {"candidate_limit": 0},
            {"candidate_limit": 51},
            {"min_tokens": 0},
            {"min_tokens": 1001},
        ):
            with pytest.raises(ValueError):
                validate_config({"memory_dedup": invalid})

    def test_runtime_projection_and_ownership(self):
        """四叶必须投影到运行时映射并登记唯一责任方。"""

        projected = {
            field.source_path: field.default
            for field in ENGINE_RUNTIME_FIELDS
            if field.source_path.startswith("memory_dedup.")
        }
        defaults = get_default_config()["memory_dedup"]
        assert projected == {
            f"memory_dedup.{leaf}": value for leaf, value in defaults.items()
        }
        for leaf in defaults:
            ownership = resolve_config_ownership(f"memory_dedup.{leaf}")
            assert ownership.owner == "core.features.memory.application.canonical_merge"
