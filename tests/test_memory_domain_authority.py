"""验证跨领域人工/派生来源契约。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from core.models.domain_provenance import (
    DomainObjectOrigin,
    DomainProvenance,
    merge_domain_provenance,
)
from core.models.memory_evolution import MemorySourceRef


REFERENCE_TIME = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


def _source(
    memory_id: int = 17,
    *,
    revision: str = "rev-17",
    scope: str = "private:user-a",
    privacy: str = "shared",
    role: str = "primary",
) -> MemorySourceRef:
    """构造不包含真实用户数据的 canonical 来源快照。"""

    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision,
        scope_key=scope,
        privacy_level=privacy,
        occurred_at=REFERENCE_TIME,
        content="仅用于测试且不应持久化的正文",
        source_role=role,
        valid_from=REFERENCE_TIME - timedelta(days=1),
        valid_to=REFERENCE_TIME + timedelta(days=1),
    )


def test_manual_origin_rejects_derived_sources() -> None:
    """人工对象使用自身 revision，不得伪装为 canonical 派生对象。"""

    assert DomainProvenance(DomainObjectOrigin.MANUAL).sources == ()
    with pytest.raises(ValueError, match="manual_origin_has_sources"):
        DomainProvenance(DomainObjectOrigin.MANUAL, (_source(),))


def test_derived_origin_requires_one_primary_source() -> None:
    """派生对象必须有且仅有一个 primary canonical 来源。"""

    with pytest.raises(ValueError, match="derived_sources_required"):
        DomainProvenance(DomainObjectOrigin.DERIVED)
    with pytest.raises(ValueError, match="primary_source_required"):
        DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (_source(role="supporting"),),
        )
    with pytest.raises(ValueError, match="primary_source_required"):
        DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (
                _source(17, role="primary"),
                _source(18, revision="rev-18", role="primary"),
            ),
        )


def test_derived_sources_reject_duplicates_and_cross_scope() -> None:
    """同一派生对象不能重复引用来源或跨可信作用域合并。"""

    with pytest.raises(ValueError, match="duplicate_source_memory_id"):
        DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (
                _source(17, role="primary"),
                _source(17, role="supporting"),
            ),
        )
    with pytest.raises(ValueError, match="source_scope_mismatch"):
        DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (
                _source(17, role="primary"),
                _source(
                    18,
                    revision="rev-18",
                    role="supporting",
                    scope="group:other",
                ),
            ),
        )


def test_source_ref_rejects_boolean_id_role_and_reversed_validity() -> None:
    """canonical 来源必须使用稳定整数身份、固定 role 和合法区间。"""

    with pytest.raises(ValueError, match="memory_id"):
        _source(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source_role"):
        _source(role="owner")
    with pytest.raises(ValueError, match="valid_to"):
        MemorySourceRef(
            memory_id=17,
            revision_token="rev-17",
            scope_key="private:user-a",
            privacy_level="shared",
            occurred_at=REFERENCE_TIME,
            valid_from=REFERENCE_TIME,
            valid_to=REFERENCE_TIME - timedelta(seconds=1),
        )


def test_provenance_round_trip_excludes_evidence_content() -> None:
    """持久化 provenance 只保存引用，不复制 canonical 正文。"""

    provenance = DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            _source(17, role="primary"),
            _source(
                18,
                revision="rev-18",
                role="supporting",
                privacy="confidential",
            ),
        ),
    )

    payload = provenance.to_dict()
    serialized = str(payload)
    assert "仅用于测试" not in serialized
    assert payload["origin"] == "derived"
    assert payload["privacy_level"] == "confidential"
    assert payload["sources"][0]["source_role"] == "primary"

    restored = DomainProvenance.from_dict(payload)
    assert restored == DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            replace(_source(17, role="primary"), content=None),
            replace(
                _source(
                    18,
                    revision="rev-18",
                    role="supporting",
                    privacy="confidential",
                ),
                content=None,
            ),
        ),
    )
    assert all(source.content is None for source in restored.sources)


def test_provenance_merge_preserves_manual_and_combines_sources() -> None:
    """人工权威优先；两个派生证据集合按 canonical ID 合并。"""

    manual = DomainProvenance(DomainObjectOrigin.MANUAL)
    first = DomainProvenance(DomainObjectOrigin.DERIVED, (_source(),))
    second = DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (_source(18, revision="rev-18"),),
    )

    assert merge_domain_provenance(manual, first) == manual
    merged = merge_domain_provenance(first, second)
    assert [source.memory_id for source in merged.sources] == [17, 18]
    assert [source.source_role for source in merged.sources] == [
        "primary",
        "supporting",
    ]
