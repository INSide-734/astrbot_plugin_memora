"""reflection feature 的包边界契约。"""

import subprocess
import sys

import pytest


def test_reflection_package_defers_feature_layer_imports() -> None:
    """导入 reflection 包边界时不得提前加载 feature 分层实现。"""

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
