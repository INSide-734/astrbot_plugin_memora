"""派生 metadata source provenance 的 shared 迁移回归。"""

from __future__ import annotations


def test_legacy_derived_metadata_source_ref_reexports_shared_type() -> None:
    """旧模型路径必须与 shared contracts 保持同一个 source ref 类型。"""

    from core.models.derived_metadata import (
        DerivedMetadataSourceRef as LegacySourceRef,
    )
    from core.shared.contracts import DerivedMetadataSourceRef

    assert LegacySourceRef is DerivedMetadataSourceRef
