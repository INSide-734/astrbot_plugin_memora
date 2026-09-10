"""隔离批准链稳定消息来源的测试替身。"""

from __future__ import annotations

import asyncio

from core.shared.contracts.conversation import Message
from core.shared.summary_source import source_window_digest


class StableApprovalConversation:
    """以固定 session epoch 和 message_seq 提供批准链所需最小能力。"""

    def __init__(self, message: Message) -> None:
        """绑定一个连续序号为 1 的来源消息。"""

        self._message = message
        self._lock = asyncio.Lock()

    def _summary_source_lock_for(self, session_id: str) -> asyncio.Lock:
        """返回测试会话的同一把来源锁。"""

        assert session_id == self._message.session_id
        return self._lock

    async def get_summary_epoch(self, session_id: str) -> tuple[int, int]:
        """返回与固定来源窗口匹配的 epoch。"""

        assert session_id == self._message.session_id
        return 1, 0

    async def get_messages_seq_range(
        self,
        session_id: str,
        start_seq: int,
        end_seq: int,
        *,
        expected_count: int,
    ) -> list[Message]:
        """只接受完整固定窗口，模拟稳定序号读取。"""

        assert session_id == self._message.session_id
        assert (start_seq, end_seq, expected_count) == (0, 1, 1)
        return [self._message]


def stable_source_window(message: Message) -> dict[str, object]:
    """构造与 ``StableApprovalConversation`` 对应的完整来源围栏。"""

    return {
        "session_id": message.session_id,
        "start_seq": 0,
        "end_seq": 1,
        "expected_count": 1,
        "message_count": 1,
        "session_epoch": 1,
        "source_digest": source_window_digest((message,), (1,)),
        "worker_generation": 1,
        "source_fence": "test-source-fence",
    }


def source_correlation(source_window: dict[str, object]) -> dict[str, object]:
    """投影批准 canonical 必须保留的来源相关性元数据。"""

    return {
        "source_epoch": source_window["session_epoch"],
        "source_digest": source_window["source_digest"],
        "source_fence_generation": source_window["worker_generation"],
        "source_fence": source_window["source_fence"],
    }
