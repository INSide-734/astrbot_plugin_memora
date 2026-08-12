"""身份运行时协作层的旧路径兼容边界。

协议解析、目录存储与服务已迁至 ``core.features.identity``；运行时、会话同步
与召回增强也已迁至 ``core.features.identity.application``。本包只保留单实现
re-export，禁止在此复制实现。
"""
