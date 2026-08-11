"""共享注入边界常量契约。"""

from core.shared.constants import (
    FAKE_TOOL_CALL_ID_PREFIX,
    FAKE_TOOL_CALL_NAME,
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)


def test_shared_constants_keep_stable_protocol_values() -> None:
    """共享常量应保持注入边界和伪工具调用的稳定协议值。"""

    assert MEMORY_INJECTION_HEADER == "<RAG-Faiss-Memory>"
    assert MEMORY_INJECTION_FOOTER == "</RAG-Faiss-Memory>"
    assert FAKE_TOOL_CALL_NAME == "recall_long_term_memory"
    assert FAKE_TOOL_CALL_ID_PREFIX == "fake_recall_"
