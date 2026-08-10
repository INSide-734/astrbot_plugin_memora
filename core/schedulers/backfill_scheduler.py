"""旧版记忆回填应用服务的旧路径兼容导出。"""

from ..features.backfill.application import scheduler as _feature_scheduler

BackfillScheduler = _feature_scheduler.BackfillScheduler

__all__ = ["BackfillScheduler"]
