"""记忆衰减调度器的旧路径兼容导出。"""

from ..features.decay.application import scheduler as _feature_scheduler

DecayScheduler = _feature_scheduler.DecayScheduler

__all__ = ["DecayScheduler"]
