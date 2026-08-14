"""派生元数据使用的最小 canonical source provenance 契约。"""

from __future__ import annotations

from dataclasses import dataclass

DERIVED_METADATA_SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class DerivedMetadataSourceRef:
    """描述派生注解对应的 canonical source 快照。"""

    memory_id: int
    revision_token: str
    trusted_scope: str
    privacy_level: str
    source_role: str
    valid_from: str | None = None
    valid_to: str | None = None
    schema_version: str = DERIVED_METADATA_SCHEMA_VERSION
    extractor_version: str = "extractor-v1"
    stale: bool = False

    def __post_init__(self) -> None:
        """校验 source identity、可见性边界和固定版本字段。"""

        if (
            isinstance(self.memory_id, bool)
            or not isinstance(self.memory_id, int)
            or self.memory_id <= 0
        ):
            raise ValueError("source_memory_id_invalid")
        for value, reason in (
            (self.revision_token, "source_revision_invalid"),
            (self.trusted_scope, "source_scope_invalid"),
            (self.privacy_level, "source_privacy_invalid"),
            (self.source_role, "source_role_invalid"),
            (self.schema_version, "source_schema_version_invalid"),
            (self.extractor_version, "source_extractor_version_invalid"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(reason)
        if not isinstance(self.stale, bool):
            raise ValueError("source_stale_invalid")


__all__ = ["DERIVED_METADATA_SCHEMA_VERSION", "DerivedMetadataSourceRef"]
