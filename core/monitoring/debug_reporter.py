"""向后兼容导出可观测性 feature 的隐私安全调试记录器。"""

from ..features.observability.infrastructure.debug_reporter import (
    ALLOWED_FIELDS,
    BACKUP_COUNT,
    EVENTS,
    FILE_NAME,
    MAX_BYTES,
    close_debug_reporting,
    configure_debug_reporting,
    debug_operation,
    is_debug_reporting_enabled,
    report_debug_event,
    report_debug_exception,
)

__all__ = [
    "ALLOWED_FIELDS",
    "BACKUP_COUNT",
    "EVENTS",
    "FILE_NAME",
    "MAX_BYTES",
    "close_debug_reporting",
    "configure_debug_reporting",
    "debug_operation",
    "is_debug_reporting_enabled",
    "report_debug_event",
    "report_debug_exception",
]
