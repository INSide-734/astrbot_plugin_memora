"""真实 AstrBot 黑盒测试运行器的公开接口。"""

from .client import DRIVER_PATH, LOGIN_PATH, MEMORA_READY_PATH, AstrBotClient
from .process import AstrBotProcess
from .scenario import AstrBotScenario
from .template import AstrBotTemplate

__all__ = [
    "DRIVER_PATH",
    "LOGIN_PATH",
    "MEMORA_READY_PATH",
    "AstrBotClient",
    "AstrBotProcess",
    "AstrBotScenario",
    "AstrBotTemplate",
]
