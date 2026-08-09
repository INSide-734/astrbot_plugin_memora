"""跨 feature 共享的无状态契约与基础类型。"""

from .constants import (
    FAKE_TOOL_CALL_ID_PREFIX,
    FAKE_TOOL_CALL_NAME,
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)
from .contracts import (
    IDENTITY_SCHEMA_VERSION,
    CanonicalMemoryCommitted,
    CanonicalMemoryPort,
    CanonicalSourceReaderPort,
    DerivedMetadataSourceRef,
    DerivedWorkPublisher,
    FinalVisibilityPort,
    IdentityConversationPort,
    MemorySourceRef,
    PromptProtectionPort,
    RealtimePublisher,
    SourceReadRequest,
    SourceReadResult,
)

__all__ = [
    "FAKE_TOOL_CALL_ID_PREFIX",
    "FAKE_TOOL_CALL_NAME",
    "MEMORY_INJECTION_FOOTER",
    "MEMORY_INJECTION_HEADER",
    "CanonicalMemoryCommitted",
    "CanonicalMemoryPort",
    "CanonicalSourceReaderPort",
    "DerivedMetadataSourceRef",
    "DerivedWorkPublisher",
    "FinalVisibilityPort",
    "IdentityConversationPort",
    "IDENTITY_SCHEMA_VERSION",
    "MemorySourceRef",
    "PromptProtectionPort",
    "RealtimePublisher",
    "SourceReadRequest",
    "SourceReadResult",
]
