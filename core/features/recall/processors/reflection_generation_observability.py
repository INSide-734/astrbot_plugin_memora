"""提供反思生成阶段的隐私安全诊断事件。"""

from __future__ import annotations

import time

from ...observability.infrastructure.debug_reporter import report_debug_event


def report_generation_stage(
    stage: str,
    status: str,
    reason_code: str,
    started: float,
    **numeric_fields: int | float | None,
) -> None:
    """发射固定阶段和非空数值字段，不记录生成输入或输出正文。"""

    fields = {key: value for key, value in numeric_fields.items() if value is not None}
    report_debug_event(
        "storage_task",
        component="reflection",
        stage=stage,
        status=status,
        reason_code=reason_code,
        task_type="storage",
        duration_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
        **fields,
    )


__all__ = ["report_generation_stage"]
