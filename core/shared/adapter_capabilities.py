"""Provider、Store 与 Retriever 共享的能力契约。"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class AdapterKind(str, Enum):
    """Memora 当前需要描述的 adapter 类别。"""

    UNKNOWN = "unknown"
    LLM_PROVIDER = "llm_provider"
    LLM_CLIENT = "llm_client"
    EMBEDDING_PROVIDER = "embedding_provider"
    VECTOR_BACKEND = "vector_backend"
    VECTOR_RETRIEVER = "vector_retriever"
    LEXICAL_RETRIEVER = "lexical_retriever"
    HYBRID_RETRIEVER = "hybrid_retriever"
    GRAPH_RETRIEVER = "graph_retriever"
    DERIVED_READER = "derived_reader"
    PERSISTENT_STORE = "persistent_store"
    RERANKER = "reranker"
    HOST_RESPONSE_BOUNDARY = "host_response_boundary"


class AdapterCapability(str, Enum):
    """跨 adapter 使用的稳定能力名称。"""

    FILTERING = "filtering"
    BATCH_READ = "batch_read"
    BATCH_WRITE = "batch_write"
    SCORING = "scoring"
    UPDATE = "update"
    DELETE = "delete"
    CANCELLATION = "cancellation"
    RETRY = "retry"
    REFERENCE_TIME = "reference_time"
    TEXT_GENERATION = "text_generation"
    SYNC_TEXT_GENERATION = "sync_text_generation"
    EMBEDDING = "embedding"
    TOOL_DELIVERY = "tool_delivery"
    VECTOR_ACCESS = "vector_access"
    STREAMING_PRECHECK = "streaming_precheck"
    EXPLICIT_FAILURE_TERMINAL = "explicit_failure_terminal"
    DELIVERY_ACK = "delivery_ack"


class SupportLevel(str, Enum):
    """能力由谁保证。"""

    NATIVE = "native"
    CALLER_ENFORCED = "caller_enforced"
    UNSUPPORTED = "unsupported"


class ScoreDirection(str, Enum):
    """分数排序方向。"""

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    RANK_ONLY = "rank_only"
    UNKNOWN = "unknown"


class NormalizationScope(str, Enum):
    """分数归一化发生的位置。"""

    NONE = "none"
    BACKEND = "backend"
    PER_QUERY = "per_query"
    CALLER = "caller"
    UNKNOWN = "unknown"


class ResponseDelivery(str, Enum):
    """AstrBot 事件当前已发布的可见投递路径。"""

    DIRECT = "direct"
    """宿主尚未发布流式结果：结果链在发送前仍可被调用方替换。"""
    STREAMING = "streaming"
    """宿主已把本次回复标记为流式：可见分片先于响应后处理交付。"""
    UNKNOWN = "unknown"
    """事件没有暴露可验证的投递信号；不得据此宣称保护先于投递。"""


def _enum_value(enum_type, value, reason_code: str):
    """把字符串或枚举规范化为稳定枚举。"""

    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(reason_code) from exc


@dataclass(frozen=True, slots=True)
class ScoreSemantics:
    """描述 adapter 分数方向、范围和归一化位置。"""

    direction: ScoreDirection = ScoreDirection.UNKNOWN
    minimum: float | None = None
    maximum: float | None = None
    normalization: NormalizationScope = NormalizationScope.UNKNOWN

    def __post_init__(self) -> None:
        """规范化枚举和范围，并拒绝非有限或逆序边界。"""

        object.__setattr__(
            self,
            "direction",
            _enum_value(ScoreDirection, self.direction, "score_direction_invalid"),
        )
        object.__setattr__(
            self,
            "normalization",
            _enum_value(
                NormalizationScope,
                self.normalization,
                "score_normalization_invalid",
            ),
        )
        try:
            minimum = None if self.minimum is None else float(self.minimum)
            maximum = None if self.maximum is None else float(self.maximum)
        except (TypeError, ValueError) as exc:
            raise ValueError("score_range_invalid") from exc
        bounds = (minimum, maximum)
        if any(value is not None and not math.isfinite(value) for value in bounds):
            raise ValueError("score_range_invalid")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("score_range_invalid")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)


@dataclass(frozen=True, slots=True)
class AdapterCapabilityContract:
    """单个 adapter 的不可变能力快照。"""

    kind: AdapterKind = AdapterKind.UNKNOWN
    native: frozenset[AdapterCapability] = frozenset()
    caller_enforced: frozenset[AdapterCapability] = frozenset()
    score: ScoreSemantics | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        """冻结能力集合并验证能力等级与分数语义的一致性。"""

        kind = _enum_value(AdapterKind, self.kind, "adapter_kind_invalid")
        native = frozenset(
            _enum_value(AdapterCapability, item, "adapter_capability_invalid")
            for item in self.native
        )
        caller_enforced = frozenset(
            _enum_value(AdapterCapability, item, "adapter_capability_invalid")
            for item in self.caller_enforced
        )
        if native & caller_enforced:
            raise ValueError("capability_level_overlap")
        score = self.score
        if isinstance(score, Mapping):
            score = ScoreSemantics(**dict(score))
        if score is not None and not isinstance(score, ScoreSemantics):
            raise ValueError("score_semantics_invalid")
        if (
            score is not None
            and AdapterCapability.SCORING not in native | caller_enforced
        ):
            raise ValueError("score_capability_missing")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "native", native)
        object.__setattr__(self, "caller_enforced", caller_enforced)
        object.__setattr__(self, "score", score)

    def level(self, capability: AdapterCapability | str) -> SupportLevel:
        """返回能力等级；未声明能力一律视为 unsupported。"""

        normalized = _enum_value(
            AdapterCapability,
            capability,
            "adapter_capability_invalid",
        )
        if normalized in self.native:
            return SupportLevel.NATIVE
        if normalized in self.caller_enforced:
            return SupportLevel.CALLER_ENFORCED
        return SupportLevel.UNSUPPORTED

    def supports(self, capability: AdapterCapability | str) -> bool:
        """判断能力是否由 adapter 或调用方精确保证。"""

        return self.level(capability) is not SupportLevel.UNSUPPORTED

    def safe_summary(self) -> dict[str, Any]:
        """返回不含 adapter 身份或业务数据的安全摘要。"""

        levels = {
            capability.value: self.level(capability).value
            for capability in AdapterCapability
        }
        score_semantics = self.score
        if isinstance(score_semantics, Mapping):
            score_semantics = ScoreSemantics(**dict(score_semantics))
        score = None
        if isinstance(score_semantics, ScoreSemantics):
            score = {
                "direction": score_semantics.direction.value,
                "minimum": score_semantics.minimum,
                "maximum": score_semantics.maximum,
                "normalization": score_semantics.normalization.value,
            }
        return {"kind": self.kind.value, "capabilities": levels, "score": score}


class UnsupportedAdapterCapability(RuntimeError):
    """请求的 adapter 能力未被显式支持。"""

    reason_code = "adapter_capability_unsupported"

    def __init__(self, kind: AdapterKind, capability: AdapterCapability) -> None:
        """使用安全枚举构造不支持能力错误。"""

        self.safe_details = {
            "reason_code": self.reason_code,
            "adapter_kind": kind.value,
            "capability": capability.value,
        }
        super().__init__(self.reason_code)


UNKNOWN_ADAPTER_CAPABILITIES = AdapterCapabilityContract()

ASTRBOT_FAISS_CAPABILITIES = AdapterCapabilityContract(
    kind=AdapterKind.VECTOR_BACKEND,
    native=frozenset(
        {
            AdapterCapability.EMBEDDING,
            AdapterCapability.SCORING,
            AdapterCapability.VECTOR_ACCESS,
        }
    ),
    caller_enforced=frozenset(
        {
            AdapterCapability.FILTERING,
            AdapterCapability.UPDATE,
            AdapterCapability.DELETE,
            AdapterCapability.CANCELLATION,
        }
    ),
    score=ScoreSemantics(direction=ScoreDirection.HIGHER_IS_BETTER),
)

ASTRBOT_HOST_RESPONSE_CAPABILITIES = AdapterCapabilityContract(
    kind=AdapterKind.HOST_RESPONSE_BOUNDARY,
)
"""真实 AstrBot 安装包（4.27/4.28）只读复核结论：插件侧没有可用的显式失败终态、
请求级缓冲或发送回执。

- 宿主 `pipeline/respond/stage.py` 对非流式结果在 `OnLLMResponseEvent` 之后才建立
  结果链并发送，因此插件可以在发送前替换文本；但空链只会被静默跳过，不存在
  插件可设置的失败终态（`ResultContentType.AGENT_RUNNER_ERROR` 与 `role="err"`
  由宿主 runner 内部产生）。
- 流式结果 `STREAMING_RESULT` 直接交给 `event.send_streaming()`，分片在
  `OnLLMResponseEvent` 之前已交付平台适配器。
- `event.send()`/`send_streaming()` 不返回送达回执；`after_message_sent` 只表示
  已尝试发送。

三项能力均未声明，按 `unsupported` 处理，调用方不得把它们当作可用保证。
"""


@dataclass(frozen=True, slots=True)
class HostResponseBoundary:
    """AstrBot 事件暴露的响应对投递边界快照，不含请求内容或身份。"""

    delivery: ResponseDelivery = ResponseDelivery.UNKNOWN
    send_operation_started: bool | None = None
    contract: AdapterCapabilityContract = ASTRBOT_HOST_RESPONSE_CAPABILITIES

    def __post_init__(self) -> None:
        """规范化投递枚举；非布尔发送信号按未知处理。"""

        object.__setattr__(
            self,
            "delivery",
            _enum_value(ResponseDelivery, self.delivery, "response_delivery_invalid"),
        )
        if not isinstance(self.send_operation_started, bool):
            object.__setattr__(self, "send_operation_started", None)


def declared_adapter_contract(adapter: Any) -> AdapterCapabilityContract | None:
    """读取 adapter 显式声明的 contract；不根据方法名猜测。"""

    value = getattr(adapter, "adapter_capabilities", None)
    return value if isinstance(value, AdapterCapabilityContract) else None


def adapter_contract(adapter: Any) -> AdapterCapabilityContract:
    """读取显式 contract，未知 adapter 返回保守空契约。"""

    return declared_adapter_contract(adapter) or UNKNOWN_ADAPTER_CAPABILITIES


def bind_default_adapter_contract(
    adapter: Any,
    default: AdapterCapabilityContract,
) -> AdapterCapabilityContract:
    """返回显式 contract，或把已审计默认 contract 绑定到固定 adapter。

    参数:
        adapter: 当前固定后端实例。
        default: 仅适用于该固定装配点的已审计能力快照。

    返回:
        显式声明或调用方使用的默认能力快照。不可扩展对象不会被强行修改。
    """

    declared = declared_adapter_contract(adapter)
    if declared is not None:
        return declared
    try:
        adapter.adapter_capabilities = default
    except (AttributeError, TypeError):
        pass
    return default


def require_capability(
    adapter: Any,
    capability: AdapterCapability | str,
    *,
    allow_caller_enforced: bool = True,
) -> SupportLevel:
    """验证能力并返回等级，不支持时抛出稳定安全错误。"""

    normalized = _enum_value(
        AdapterCapability,
        capability,
        "adapter_capability_invalid",
    )
    contract = adapter_contract(adapter)
    level = contract.level(normalized)
    if level is SupportLevel.UNSUPPORTED or (
        level is SupportLevel.CALLER_ENFORCED and not allow_caller_enforced
    ):
        raise UnsupportedAdapterCapability(contract.kind, normalized)
    return level


_STREAMING_RESULT_NAMES = frozenset({"STREAMING_RESULT", "STREAMING_FINISH"})
_DIRECT_RESULT_NAMES = frozenset({"LLM_RESULT", "AGENT_RUNNER_ERROR", "GENERAL_RESULT"})


def _published_delivery(result: Any) -> ResponseDelivery:
    """按宿主已核对的 ``ResultContentType`` 成员名判断投递路径。"""

    try:
        content_type = getattr(result, "result_content_type", None)
        if not isinstance(content_type, Enum):
            return ResponseDelivery.UNKNOWN
        name = getattr(content_type, "name", "")
    except asyncio.CancelledError:
        raise
    except Exception:
        return ResponseDelivery.UNKNOWN
    if name in _STREAMING_RESULT_NAMES:
        return ResponseDelivery.STREAMING
    if name in _DIRECT_RESULT_NAMES:
        return ResponseDelivery.DIRECT
    return ResponseDelivery.UNKNOWN


def probe_host_response_boundary(event: Any) -> HostResponseBoundary:
    """只读探测 AstrBot 事件当前已发布的响应对投递边界。

    只接受宿主显式类型信号：`MessageEventResult.result_content_type` 与宿主文档化
    语义的 `_has_send_oper` 发送记录。事件缺失、形状不同或读取异常时返回保守
    `unknown`，不根据方法名猜测能力，也不修改事件、结果或宿主对象。

    参数:
        event: 当前 AstrBot 消息事件或等价替身；允许为 `None`。

    返回:
        不含请求内容、身份或正文字段的投递边界快照。
    """

    try:
        raw_send_operation = getattr(event, "_has_send_oper", None)
    except asyncio.CancelledError:
        raise
    except Exception:
        return HostResponseBoundary()
    send_operation_started: bool | None = (
        raw_send_operation if isinstance(raw_send_operation, bool) else None
    )

    try:
        getter = getattr(event, "get_result", None)
    except asyncio.CancelledError:
        raise
    except Exception:
        return HostResponseBoundary(send_operation_started=send_operation_started)
    if not callable(getter):
        return HostResponseBoundary(send_operation_started=send_operation_started)
    try:
        result = getter()
    except asyncio.CancelledError:
        raise
    except Exception:
        return HostResponseBoundary(send_operation_started=send_operation_started)
    if result is None:
        # clear_result() is used between tool steps and after streaming deltas;
        # absence of a result cannot prove direct, pre-send delivery.
        return HostResponseBoundary(
            delivery=ResponseDelivery.UNKNOWN,
            send_operation_started=send_operation_started,
        )
    return HostResponseBoundary(
        delivery=_published_delivery(result),
        send_operation_started=send_operation_started,
    )


__all__ = [
    "AdapterCapability",
    "AdapterCapabilityContract",
    "AdapterKind",
    "ASTRBOT_FAISS_CAPABILITIES",
    "ASTRBOT_HOST_RESPONSE_CAPABILITIES",
    "HostResponseBoundary",
    "NormalizationScope",
    "ResponseDelivery",
    "ScoreDirection",
    "ScoreSemantics",
    "SupportLevel",
    "UNKNOWN_ADAPTER_CAPABILITIES",
    "UnsupportedAdapterCapability",
    "adapter_contract",
    "bind_default_adapter_contract",
    "declared_adapter_contract",
    "probe_host_response_boundary",
    "require_capability",
]
