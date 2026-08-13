"""成本控制旧路径兼容导出。

真实实现已迁至 ``core.platform.config.cost_control``；本模块只保留单实现
re-export，供尚未切换到 platform 路径的历史调用方与契约测试使用。
"""

from ..platform.config.cost_control import CostControl, build_cost_control_from_config

__all__ = ["CostControl", "build_cost_control_from_config"]
