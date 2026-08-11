"""recall feature 的包边界契约。"""

import subprocess
import sys

import pytest


def test_recall_package_defers_application_imports() -> None:
    """导入 recall 包边界时不得提前加载应用服务。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import core.features.recall as recall; "
            "assert 'core.features.recall.application' not in sys.modules; "
            "print(','.join(recall.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "build_continuity_context"


def test_recall_package_lazily_exports_application_contract() -> None:
    """recall 包应惰性解析真实应用对象并拒绝未知属性。"""

    import core.features.recall as recall_feature
    from core.features.recall.application import continuity

    assert (
        recall_feature.__getattr__("build_continuity_context")
        is continuity.build_continuity_context
    )
    with pytest.raises(AttributeError, match="missing_recall_contract"):
        recall_feature.__getattr__("missing_recall_contract")
