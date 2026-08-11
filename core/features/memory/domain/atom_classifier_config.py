"""记忆原子分类规则的配置模型。"""

from pydantic import BaseModel


class AtomClassifierConfig(BaseModel):
    """记忆原子分类规则配置。"""

    negation_detection_enabled: bool = True


__all__ = ["AtomClassifierConfig"]
