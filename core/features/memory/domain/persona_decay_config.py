"""canonical memory 人格差异化衰减的配置模型。"""

from pydantic import BaseModel


class PersonaDecayConfig(BaseModel):
    """人格差异化衰减配置。"""

    enabled: bool = True
    default_modifier: float = 1.0


__all__ = ["PersonaDecayConfig"]
