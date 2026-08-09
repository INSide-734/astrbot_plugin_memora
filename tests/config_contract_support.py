"""配置持久化测试使用的确定性同步边界辅助对象。"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

from core.platform.resources import PluginResourceLocator


class SavingConfig(dict[str, Any]):
    """模拟 AstrBot 同步保存边界的可变配置对象。"""

    def __init__(
        self,
        *args: Any,
        fail_save: bool = False,
        **kwargs: Any,
    ) -> None:
        """初始化可控保存结果与保存快照。"""

        super().__init__(*args, **kwargs)
        self.resource_locator = PluginResourceLocator(
            Path(__file__).resolve().parents[1]
        )
        self.fail_save = fail_save
        self.saved_snapshots: list[dict[str, Any]] = []
        self.save_thread_id: int | None = None

    def save_config(self) -> None:
        """记录当前配置快照，并按设置模拟保存失败。"""

        self.save_thread_id = threading.get_ident()
        self.saved_snapshots.append(copy.deepcopy(dict(self)))
        if self.fail_save:
            raise OSError("simulated atomic save failure")


class BlockingSavingConfig(SavingConfig):
    """暴露确定性线程边界的持久化模拟对象。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化保存进入、释放与完成三个同步事件。"""

        super().__init__(*args, **kwargs)
        self.save_entered = threading.Event()
        self.release_save = threading.Event()
        self.save_finished = threading.Event()
        self.clear_calls = 0

    def clear(self) -> None:
        """记录配置替换次数后清空映射。"""

        self.clear_calls += 1
        super().clear()

    def save_config(self) -> None:
        """阻塞到测试释放事件，再记录成功快照或抛出失败。"""

        self.save_thread_id = threading.get_ident()
        self.save_entered.set()
        try:
            if not self.release_save.wait(timeout=5):
                raise TimeoutError("test did not release save_config")
            if self.fail_save:
                raise OSError("simulated atomic save failure")
            self.saved_snapshots.append(copy.deepcopy(dict(self)))
        finally:
            self.save_finished.set()
