"""reflection feature 的包边界契约。"""

import subprocess
import sys
from pathlib import Path
import pytest

from core.features.reflection.domain import (
    CandidateDisposition,
    SummaryFailure,
    SummaryJobStatus,
    SummaryReasonCode,
)


def test_reflection_package_defers_feature_layer_imports() -> None:
    """导入 reflection 包边界时不得提前加载 feature 分层实现。"""

    import os

    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import core.features.reflection as reflection; "
            "assert 'core.features.reflection.application' not in sys.modules; "
            "assert 'core.features.reflection.domain' not in sys.modules; "
            "print(','.join(reflection.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
    )

    assert result.returncode == 0, result.stderr
    assert "TopicBatchPreparer" in result.stdout.strip().split(",")


def test_reflection_package_lazily_exports_feature_layers() -> None:
    """包级公开名称应惰性解析到真实分层对象并拒绝未知属性。"""

    import core.features.reflection as reflection_feature
    from core.features.reflection.application import candidate_writer
    from core.features.reflection.domain import storage_outcomes

    assert (
        reflection_feature.__getattr__("build_reflection_idempotency_key")
        is candidate_writer.build_reflection_idempotency_key
    )
    assert (
        reflection_feature.__getattr__("ReflectionStoreOutcome")
        is storage_outcomes.ReflectionStoreOutcome
    )
    with pytest.raises(AttributeError, match="missing_reflection_contract"):
        reflection_feature.__getattr__("missing_reflection_contract")


def test_reflection_configs_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 reflection feature 的配置模型。"""

    from core.features.reflection.domain import (
        LegacyBackfillConfig,
        ReflectionEngineConfig,
        StrategyBConfig,
        StrategyCConfig,
        StrategyDConfig,
        TopicSegmentationConfig,
    )
    from core.platform.config.config_validator import (
        LegacyBackfillConfig as LegacyLegacyBackfillConfig,
    )
    from core.platform.config.config_validator import (
        ReflectionEngineConfig as LegacyReflectionEngineConfig,
    )
    from core.platform.config.config_validator import (
        StrategyBConfig as LegacyStrategyBConfig,
    )
    from core.platform.config.config_validator import (
        StrategyCConfig as LegacyStrategyCConfig,
    )
    from core.platform.config.config_validator import (
        StrategyDConfig as LegacyStrategyDConfig,
    )
    from core.platform.config.config_validator import (
        TopicSegmentationConfig as LegacyTopicSegmentationConfig,
    )

    assert LegacyReflectionEngineConfig is ReflectionEngineConfig
    assert LegacyStrategyBConfig is StrategyBConfig
    assert LegacyStrategyCConfig is StrategyCConfig
    assert LegacyStrategyDConfig is StrategyDConfig
    assert LegacyLegacyBackfillConfig is LegacyBackfillConfig
    assert LegacyTopicSegmentationConfig is TopicSegmentationConfig


def test_summary_reason_codes_extend_outcomes_without_new_statuses() -> None:
    """skip/invalid 只扩展 reason code，不扩展任务或候选状态。"""

    assert SummaryReasonCode.NO_FACTS.value == "no_facts"
    assert SummaryReasonCode.SUMMARY_INVALID.value == "summary_invalid"
    assert {item.value for item in SummaryJobStatus} == {
        "queued",
        "running",
        "failed",
        "completed",
        "blocked",
        "unknown",
        "cancelled",
        "abandoned",
    }
    assert {item.value for item in CandidateDisposition} == {
        "quarantined",
        "discard",
        "mark_write",
        "canonical",
        "skipped_idempotent",
        "failed",
    }


def test_summary_reason_code_remains_publicly_exported() -> None:
    """固定 reason code 必须继续通过 reflection domain 公开。"""

    import core.features.reflection.domain as reflection_domain

    assert "SummaryReasonCode" in reflection_domain.__all__
    assert reflection_domain.SummaryReasonCode is SummaryReasonCode


def test_failure_dto_does_not_accept_success_reason_as_failure() -> None:
    """失败 DTO 不得把 no-facts 或 completed 伪装成失败原因。"""

    failure = SummaryFailure(
        failed_stage="memory_extract",
        reason_code=SummaryReasonCode.NO_FACTS,
    )

    assert failure.reason_code is SummaryReasonCode.UNKNOWN
