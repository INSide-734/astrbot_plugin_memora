"""跨 feature 共享的记忆注入边界与伪工具调用常量。"""

# 记忆注入到模型上下文时使用的稳定边界标记。
MEMORY_INJECTION_HEADER = "<RAG-Faiss-Memory>"
MEMORY_INJECTION_FOOTER = "</RAG-Faiss-Memory>"

# 清理伪造工具调用时使用的稳定名称和 ID 前缀。
FAKE_TOOL_CALL_NAME = "recall_long_term_memory"
FAKE_TOOL_CALL_ID_PREFIX = "fake_recall_"

__all__ = [
    "FAKE_TOOL_CALL_ID_PREFIX",
    "FAKE_TOOL_CALL_NAME",
    "MEMORY_INJECTION_FOOTER",
    "MEMORY_INJECTION_HEADER",
]
