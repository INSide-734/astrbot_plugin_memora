"""共享时间原语与旧模型路径的兼容性回归。"""

from __future__ import annotations

import core.models.temporal as model_temporal
import core.shared.temporal as shared_temporal


def test_model_temporal_reexports_shared_temporal_primitives() -> None:
    """旧模型路径必须继续导出共享实现的全部稳定时间原语。"""

    assert model_temporal.__all__ == shared_temporal.__all__
    for name in shared_temporal.__all__:
        assert getattr(model_temporal, name) is getattr(shared_temporal, name)
