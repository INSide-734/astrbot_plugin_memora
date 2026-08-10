"""向后兼容导出 reflection feature 的候选写入服务。"""

from ..features.reflection.application.candidate_writer import (
    build_reflection_idempotency_key,
    store_reflection_candidates,
)

__all__ = ["build_reflection_idempotency_key", "store_reflection_candidates"]
