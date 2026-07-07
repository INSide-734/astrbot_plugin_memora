"""控制台 API：伴侣插件检测的功能委托状态。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .response_utils import error_response, ok_response


class DelegationApiMixin:
    """为控制台提供功能委托状态端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_feature_delegation(self) -> Any | None:
        """从插件属性中解析 ``FeatureDelegation`` 实例。

        ``FeatureDelegation`` 在 ``main.py`` 插件初始化期间创建，
        并存储在插件实例的 ``self.feature_delegation`` 属性上。
        """
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        fd = getattr(plugin, "feature_delegation", None)
        if fd is not None:
            return fd
        # Try initializer
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            fd = getattr(initializer, "feature_delegation", None)
            if fd is not None:
                return fd
        return None

    # ------------------------------------------------------------------
    # GET /status
    # ------------------------------------------------------------------

    async def get_delegation_status(self):
        """返回当前的功能委托状态。

        返回一张快照，标明哪些伴侣插件处于活跃状态，
        以及哪些本地功能已委托给它们。
        """
        fd = self._get_feature_delegation()
        if fd is None:
            return error_response("功能委托不可用")

        try:
            status = fd.get_delegation_status()
            return ok_response(status)
        except Exception as e:
            logger.error(
                f"[DelegationApi] get_delegation_status failed: {e}", exc_info=True
            )
            return error_response(f"获取委托状态失败: {e}")


__all__ = ["DelegationApiMixin"]
