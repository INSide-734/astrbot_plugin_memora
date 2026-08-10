"""Adapter 能力契约的旧路径兼容导出。"""

from .shared.adapter_capabilities import (
    ASTRBOT_FAISS_CAPABILITIES,
    UNKNOWN_ADAPTER_CAPABILITIES,
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
    SupportLevel,
    UnsupportedAdapterCapability,
    adapter_contract,
    bind_default_adapter_contract,
    declared_adapter_contract,
    require_capability,
)

__all__ = [
    "AdapterCapability",
    "AdapterCapabilityContract",
    "AdapterKind",
    "ASTRBOT_FAISS_CAPABILITIES",
    "NormalizationScope",
    "ScoreDirection",
    "ScoreSemantics",
    "SupportLevel",
    "UNKNOWN_ADAPTER_CAPABILITIES",
    "UnsupportedAdapterCapability",
    "adapter_contract",
    "bind_default_adapter_contract",
    "declared_adapter_contract",
    "require_capability",
]
