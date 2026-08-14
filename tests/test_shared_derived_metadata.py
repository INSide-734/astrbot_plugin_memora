"""派生 metadata source provenance 的 shared 迁移回归。"""

from __future__ import annotations


def test_derived_metadata_source_ref_reuses_shared_type() -> None:
    """派生 metadata 应复用 shared source ref 类型。"""

    from core.features.evaluation.domain.derived_metadata import (
        DerivedMetadataSourceRef as EvaluationSourceRef,
    )
    from core.shared.contracts import DerivedMetadataSourceRef

    assert EvaluationSourceRef is DerivedMetadataSourceRef
