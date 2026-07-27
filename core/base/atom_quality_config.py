"""记忆原子质量、观察期、去重与冷存储配置模型。"""

from pydantic import BaseModel, Field


class AtomQualityFilterConfig(BaseModel):
    """验证 `_conf_schema.json` 中原子质量过滤的全部运行时字段。"""

    atom_quality_filter_enabled: bool = True
    atom_min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    atom_min_importance: float = Field(default=0.3, ge=0.0, le=1.0)
    atom_min_content_length: int = Field(default=5, ge=1, le=10_000)
    atom_info_check_enabled: bool = True
    atom_probationary_enabled: bool = True
    atom_probationary_ttl_days: float = Field(default=3.0, ge=1.0, le=365.0)
    atom_dedup_enabled: bool = True
    atom_dedup_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    atom_cold_storage_enabled: bool = True
    atom_cold_days_threshold: float = Field(default=14.0, ge=1.0, le=3650.0)
    atom_cold_max_importance: float = Field(default=0.4, ge=0.0, le=1.0)


__all__ = ["AtomQualityFilterConfig"]
