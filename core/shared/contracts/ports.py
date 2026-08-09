"""跨 feature 的窄异步端口。

端口只描述组合根需要连接的能力，不承载具体存储、Provider 或宿主对象。实现
可以继续位于旧目录，迁移阶段通过 adapter 保持唯一实现。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from .events import CanonicalMemoryCommitted


@runtime_checkable
class RealtimePublisher(Protocol):
    """实时事件发布端口；feature 不应依赖具体 SSE 类。"""

    async def publish(self, event_type: str, data: Mapping[str, Any]) -> bool:
        """发布允许观测的标量事件，返回是否至少投递给一个订阅者。"""


@runtime_checkable
class CostControlPort(Protocol):
    """额外 LLM 成本许可门的最小同步端口。"""

    def allow(self, feature: str) -> bool:
        """判断指定额外 LLM 能力是否允许执行。"""

    def deny_reason(self, feature: str) -> str:
        """返回指定能力被拒绝时的稳定原因文本。"""


@runtime_checkable
class CanonicalMemoryPort(Protocol):
    """canonical memory CRUD 的最小应用端口。"""

    async def get_memory(
        self, memory_id: int, **kwargs: Any
    ) -> Mapping[str, Any] | None:
        """按 canonical 整数 ID 读取记忆。"""

    async def add_memory(self, content: str, **kwargs: Any) -> Mapping[str, Any]:
        """提交一条 canonical 记忆并返回权威记录。"""

    async def update_memory(self, memory_id: int, **kwargs: Any) -> Mapping[str, Any]:
        """按 revision/CAS 语义更新 canonical 记忆。"""

    async def delete_memory(self, memory_id: int, **kwargs: Any) -> bool:
        """删除或标记 canonical 记忆。"""


@runtime_checkable
class DerivedWorkPublisher(Protocol):
    """canonical 提交后的派生工作发布端口。"""

    async def publish_committed(
        self,
        event: CanonicalMemoryCommitted,
        *,
        consumer: str,
    ) -> bool:
        """按 event/consumer/revision 幂等发布派生工作。"""


@runtime_checkable
class RecallPort(Protocol):
    """召回热路径的授权查询端口。"""

    async def recall(
        self,
        query: str,
        *,
        scope_key: str,
        stable_user_id: str,
        user_role: str,
        **kwargs: Any,
    ) -> Sequence[Mapping[str, Any]]:
        """返回已完成隐私和身份过滤的候选。"""


@runtime_checkable
class ReflectionWritePort(Protocol):
    """反思候选写入端口；质量门失败不得推进 canonical 窗口。"""

    async def write_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        scope_key: str,
        stable_user_id: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        """校验并写入候选，或返回 quarantine 状态。"""


@runtime_checkable
class EmbeddingPort(Protocol):
    """可选 embedding 能力探针和调用端口。"""

    @property
    def available(self) -> bool:
        """返回当前能力是否可安全使用。"""

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """为已授权本地文本生成向量。"""


@runtime_checkable
class ContinuityPort(Protocol):
    """会话连续性读写的显式边界。"""

    async def get_context(
        self, *, scope_key: str, stable_user_id: str
    ) -> Mapping[str, Any] | None:
        """读取授权连续性上下文。"""


@runtime_checkable
class IdentityConversationPort(Protocol):
    """协议身份与会话协作的最小应用端口。"""

    @property
    def enricher(self) -> Any | None:
        """返回可选的历史别名只读增强器。"""

    def resolve(self, event: Any) -> Any:
        """同步解析平台事件中的稳定身份。"""

    async def prepare(
        self,
        event: Any,
        *,
        writes_blocked: bool = False,
    ) -> Any:
        """解析身份，并在允许写入时尽力同步名称目录。"""

    async def synchronize(
        self,
        event: Any,
        identity: Any,
        *,
        writes_blocked: bool = False,
    ) -> None:
        """在可信且允许写入时同步身份与会话名称。"""

    async def get_identity(
        self,
        identity_namespace: str,
        stable_user_id: str,
    ) -> Any | None:
        """读取稳定身份的当前目录记录。"""


@runtime_checkable
class FinalVisibilityPort(Protocol):
    """注入前唯一最终可见性过滤端口。"""

    def filter_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        scope_key: str,
        stable_user_id: str,
        user_role: str,
        privacy_clearance: str,
    ) -> Sequence[Mapping[str, Any]]:
        """丢弃跨 scope/身份、隐私或 revision 不满足的候选。"""


@runtime_checkable
class PromptProtectionPort(Protocol):
    """提示词保护服务的注入端口。"""

    def wrap_prompt(
        self,
        content: str,
        label: str = "memory_context",
        *,
        register_for_filter: bool = True,
        scope_id: str | None = None,
    ) -> str:
        """包装并可选登记请求作用域内容。"""

    def sanitize_response(
        self,
        response: str,
        *,
        enable_validation: bool | None = None,
        scope_id: str | None = None,
        consume_scope: bool = False,
    ) -> tuple[str, Mapping[str, Any]]:
        """清理模型回复并返回安全报告。"""

    def has_scope(self, scope_id: str | None) -> bool:
        """返回请求保护作用域是否仍然有效。"""

    def discard_scope(self, scope_id: str | None) -> None:
        """释放请求作用域中的保护状态。"""


__all__ = [
    "CanonicalMemoryPort",
    "CostControlPort",
    "ContinuityPort",
    "DerivedWorkPublisher",
    "EmbeddingPort",
    "FinalVisibilityPort",
    "IdentityConversationPort",
    "PromptProtectionPort",
    "RecallPort",
    "RealtimePublisher",
    "ReflectionWritePort",
]
