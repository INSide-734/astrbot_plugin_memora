"""协议身份应用服务的兼容导出。

唯一实现位于 ``core.features.identity.application``；本模块保留旧导入路径，
避免迁移期间出现两套名称合并规则。
"""

from ..features.identity.application.service import ProtocolIdentityService

__all__ = ["ProtocolIdentityService"]
