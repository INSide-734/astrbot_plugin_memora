"""共享时间原语导出契约。"""

from __future__ import annotations

import core.shared.temporal as shared_temporal


def test_shared_temporal_exports_public_contract() -> None:
    """shared 模块必须稳定导出全部时间原语。"""

    assert shared_temporal.__all__ == [
        "TIME_PRECISIONS",
        "TIME_SOURCES",
        "canonical_visible_at",
        "infer_time_precision",
        "normalize_datetime",
        "normalize_reference_time",
        "parse_datetime",
        "reference_time_key",
        "serialize_datetime",
        "validate_time_labels",
        "visible_at",
    ]
