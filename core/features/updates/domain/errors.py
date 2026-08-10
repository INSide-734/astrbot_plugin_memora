"""插件更新 feature 的领域异常。"""


class UpdateError(RuntimeError):
    """更新元数据、下载或校验失败。"""


class RuntimeUpdateError(UpdateError):
    """runtime 安装、重载或回滚无法安全完成。"""


__all__ = ["RuntimeUpdateError", "UpdateError"]
