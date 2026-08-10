"""reconsolidation feature 的领域、应用与基础设施所有权兼容契约。"""

from core.features.reconsolidation.domain.errors import (
    ReconsolidationCandidateConflictError,
    ReconsolidationCandidateNotFoundError,
)
from core.features.reconsolidation.infrastructure import (
    reconsolidation_schema as feature_schema,
)
from core.managers import reconsolidation as legacy_manager
from core.storage import reconsolidation_schema as legacy_schema
from core.storage import reconsolidation_store as legacy_store


def test_legacy_reconsolidation_errors_reuse_feature_types() -> None:
    """旧路径只能导出 reconsolidation domain 的唯一异常类型。"""

    assert (
        legacy_store.ReconsolidationCandidateConflictError
        is ReconsolidationCandidateConflictError
    )
    assert (
        legacy_store.ReconsolidationCandidateNotFoundError
        is ReconsolidationCandidateNotFoundError
    )
    assert (
        legacy_manager.ReconsolidationCandidateConflictError
        is ReconsolidationCandidateConflictError
    )
    assert (
        legacy_manager.ReconsolidationCandidateNotFoundError
        is ReconsolidationCandidateNotFoundError
    )


def test_legacy_reconsolidation_schema_reuses_feature_implementation() -> None:
    """旧 schema 路径只能导出 reconsolidation infrastructure 的唯一实现。"""

    assert legacy_schema.__all__ == feature_schema.__all__
    for name in feature_schema.__all__:
        assert getattr(legacy_schema, name) is getattr(feature_schema, name)
