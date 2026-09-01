"""总结候选跨数据库副作用使用的不可变来源 fence。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SummarySourceFence:
    """描述一个候选写入允许使用的 session epoch 与 claim generation。"""

    job_id: str
    session_id: str
    session_epoch: int
    start_seq: int
    end_seq: int
    expected_count: int
    source_digest: str
    worker_generation: int
    claim_token: str

    def __post_init__(self) -> None:
        """验证范围和 fence 字段，禁止把无效来源送入外部 Store。"""

        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("summary_source_fence_invalid")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("summary_source_fence_invalid")
        if (
            isinstance(self.session_epoch, bool)
            or not isinstance(self.session_epoch, int)
            or self.session_epoch <= 0
            or isinstance(self.start_seq, bool)
            or not isinstance(self.start_seq, int)
            or self.start_seq < 0
            or isinstance(self.end_seq, bool)
            or not isinstance(self.end_seq, int)
            or self.end_seq <= self.start_seq
            or isinstance(self.expected_count, bool)
            or not isinstance(self.expected_count, int)
            or self.expected_count != self.end_seq - self.start_seq
            or isinstance(self.worker_generation, bool)
            or not isinstance(self.worker_generation, int)
            or self.worker_generation <= 0
        ):
            raise ValueError("summary_source_fence_invalid")
        if (
            not isinstance(self.source_digest, str)
            or not self.source_digest.strip()
            or len(self.source_digest.strip()) > 128
            or not isinstance(self.claim_token, str)
            or not self.claim_token.strip()
        ):
            raise ValueError("summary_source_fence_invalid")

    @property
    def opaque_token(self) -> str:
        """返回不含原始 claim token 的稳定 fence 摘要。"""

        return hashlib.sha256(
            f"{self.session_epoch}:{self.worker_generation}:{self.claim_token}".encode(
                "utf-8"
            )
        ).hexdigest()


__all__ = ["SummarySourceFence"]
