"""learning feature 的应用端口。

端口只描述反馈聚合与自主学习编排实际消费的能力，不绑定 SQLite、
ConfigManager、评测文件或旧 managers 路径。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .domain.models import (
    FeedbackAdapterKind,
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
    TrustedFeedbackEvent,
)


@runtime_checkable
class FeedbackSignalStorePort(Protocol):
    """反馈聚合服务所需的隔离持久化接口。"""

    def opaque_token(self, namespace: str, value: str) -> str:
        """为受控标识生成安装内稳定的不透明 token。"""

    def insert_events(
        self,
        events: Iterable[TrustedFeedbackEvent],
    ) -> dict[str, int]:
        """事务写入可信反馈事件并返回稳定计数。"""

    def list_events(
        self,
        *,
        scope_domain: str | None = None,
        persona_domain: str | None | object = ...,
    ) -> list[TrustedFeedbackEvent]:
        """按可选作用域读取聚合所需事件。"""

    def delete_events_before(self, cutoff: datetime) -> int:
        """删除保留期之前的事件并返回删除数量。"""

    def revoke_and_replace_aggregates(
        self,
        *,
        adapter_kind: FeedbackAdapterKind,
        decision_key: str,
        variant_key: str,
        scope_domain: str,
        persona_domain: str | None,
        retention_cutoff: datetime,
        aggregate_builder: Callable[
            [list[TrustedFeedbackEvent]], Iterable[FeedbackSignalAggregate]
        ],
    ) -> int:
        """在单个事务内撤销事件并替换聚合快照。"""

    def replace_aggregates(
        self,
        aggregates: Iterable[FeedbackSignalAggregate],
    ) -> None:
        """原子替换当前聚合快照。"""

    def list_aggregates(
        self,
        *,
        policy_version: int | None = None,
    ) -> list[Mapping[str, Any]]:
        """读取指定策略版本的低敏聚合行。"""

    def clear_aggregates(self) -> None:
        """删除派生聚合并保留可信事件。"""

    def close(self) -> None:
        """关闭隔离持久化资源。"""


@runtime_checkable
class FeedbackSignalServicePort(Protocol):
    """自主学习编排消费的反馈聚合服务接口。"""

    @property
    def policy(self) -> FeedbackSignalPolicy:
        """返回当前不可变反馈策略。"""

    def rebuild(
        self,
        *,
        reference_time: datetime,
    ) -> list[FeedbackSignalAggregate]:
        """按固定参考时间重建反馈聚合。"""

    def safe_summary(self) -> dict[str, int | float]:
        """返回不含反馈标识与作用域的摘要。"""


@runtime_checkable
class LearningConfigAdapterPort(Protocol):
    """自主学习发布与回滚所需的配置 revision-CAS 接口。"""

    async def get_weight_snapshot(self) -> Any:
        """读取权威 revision 和受限权重快照。"""

    async def apply_weights(
        self,
        target_weights: Mapping[str, object],
        *,
        expected_revision: str,
    ) -> Any:
        """按预期 revision 提交受限权重并返回类型化结果。"""


@runtime_checkable
class LearningEvidenceProviderPort(Protocol):
    """按当前聚合解析不可变离线证据的可调用接口。"""

    def __call__(
        self,
        aggregates: Sequence[FeedbackSignalAggregate],
    ) -> object | Awaitable[object | None] | None:
        """返回同步或异步证据结果；缺失时返回空值。"""


__all__ = [
    "FeedbackSignalServicePort",
    "FeedbackSignalStorePort",
    "LearningConfigAdapterPort",
    "LearningEvidenceProviderPort",
]
