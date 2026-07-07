"""
自定义异常定义
"""


class MemoraException(Exception):  # noqa: N818
    """Memora 插件基础异常"""

    def __init__(self, message: str, error_code: str | None = None):
        self.message = message
        self.error_code = error_code or "UNKNOWN_ERROR"
        super().__init__(self.message)


class InitializationError(MemoraException):
    """初始化错误"""

    def __init__(self, message: str):
        super().__init__(message, "INIT_ERROR")


class ProviderNotReadyError(MemoraException):
    """Provider未就绪错误"""

    def __init__(self, message: str = "Provider未就绪"):
        super().__init__(message, "PROVIDER_NOT_READY")


class DatabaseError(MemoraException):
    """数据库错误"""

    def __init__(self, message: str):
        super().__init__(message, "DATABASE_ERROR")


class RetrievalError(MemoraException):
    """检索错误"""

    def __init__(self, message: str):
        super().__init__(message, "RETRIEVAL_ERROR")


class MemoryProcessingError(MemoraException):
    """记忆处理错误"""

    def __init__(self, message: str):
        super().__init__(message, "MEMORY_PROCESSING_ERROR")


class ConfigurationError(MemoraException):
    """配置错误"""

    def __init__(self, message: str):
        super().__init__(message, "CONFIG_ERROR")


class ValidationError(MemoraException):
    """验证错误"""

    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")


class StorageError(MemoraException):
    """存储层错误（向量库/SQLite读写失败）"""

    def __init__(self, message: str):
        super().__init__(message, "STORAGE_ERROR")


class EmbeddingError(MemoraException):
    """嵌入生成失败"""

    def __init__(self, message: str):
        super().__init__(message, "EMBEDDING_ERROR")


class DecayError(MemoraException):
    """记忆衰减/调度失败"""

    def __init__(self, message: str):
        super().__init__(message, "DECAY_ERROR")


class BackupError(MemoraException):
    """备份/恢复操作失败"""

    def __init__(self, message: str):
        super().__init__(message, "BACKUP_ERROR")


class GraphError(MemoraException):
    """图数据库操作失败"""

    def __init__(self, message: str):
        super().__init__(message, "GRAPH_ERROR")


class TopicSplitError(MemoraException):
    """话题分割失败"""

    def __init__(self, message: str):
        super().__init__(message, "TOPIC_SPLIT_ERROR")


class IndexCorruptionError(MemoraException):
    """FAISS索引损坏或重建失败"""

    def __init__(self, message: str):
        super().__init__(message, "INDEX_ERROR")


class RecallInjectionError(MemoraException):
    """记忆注入LLM上下文失败"""

    def __init__(self, message: str):
        super().__init__(message, "RECALL_INJECTION_ERROR")


class FeatureDelegationError(MemoraException):
    """跨插件功能委托失败"""

    def __init__(self, message: str):
        super().__init__(message, "FEATURE_DELEGATION_ERROR")
