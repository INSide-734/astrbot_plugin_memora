"""请求局部检索计时收集器。"""

from __future__ import annotations

from collections.abc import Mapping

from ..monitoring.recall_timing import sanitize_recall_sample


class RetrievalTimingSink:
    """收集单次检索的隐私安全标量，避免依赖共享的最近一次状态。"""

    def __init__(self) -> None:
        """创建空的请求局部计时容器。"""

        self._values: dict[str, float | int | bool | str] = {}

    def update(self, values: Mapping[str, object]) -> None:
        """合并 allowlist 内的有限标量，丢弃正文、查询和复杂对象。"""

        self._values.update(sanitize_recall_sample(values))

    def record(self, key: str, value: object) -> None:
        """记录单个安全字段；未知字段不会进入容器。"""

        self.update({key: value})

    def snapshot(self) -> dict[str, float | int | bool | str]:
        """返回当前请求计时的独立副本。"""

        return dict(self._values)


__all__ = ["RetrievalTimingSink"]
