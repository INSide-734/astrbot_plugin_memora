"""请求级额外 LLM 调用预算。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

from astrbot.api import logger

if TYPE_CHECKING:
    from .cost_control import CostControl


@dataclass(frozen=True, slots=True)
class ExtraLlmReservation:
    """标识某一轮预算中的单个待提交槽。"""

    budget_id: str
    reservation_id: str
    feature: str


@dataclass(frozen=True, slots=True)
class ExtraLlmBudgetSnapshot:
    """提供不含请求内容的预算计数快照。"""

    max_calls: int
    used: int
    reserved: int
    remaining: int


@dataclass(frozen=True, slots=True)
class ExtraLlmBudgetObservation:
    """限制预算观测只能包含固定标量。"""

    feature: str
    allowed: bool
    used: int
    remaining: int
    reason_code: str


BudgetObserver = Callable[[ExtraLlmBudgetObservation], None]

_ALLOWED_FEATURES = frozenset(
    {
        "llm_query_rewrite",
        "llm_reranker",
        "memory_grounding_judge",
        "persona_interpretation",
        "reflection_extra_batch",
        "topic_strategy_d",
    }
)
_ALLOWED_REASON_CODES = frozenset(
    {
        "extra_llm_budget_exhausted",
        "extra_llm_budget_missing",
        "extra_llm_committed",
        "extra_llm_feature_disabled",
        "extra_llm_released",
        "extra_llm_reserved",
        "extra_llm_stale_token",
    }
)


class ExtraLlmBudget:
    """通过 reservation 防止同一请求中的额外 LLM 调用并发超卖。"""

    def __init__(
        self,
        max_calls: int,
        *,
        observer: BudgetObserver | None = None,
    ) -> None:
        """初始化总额度、异步锁和可选的安全观测回调。"""

        self._budget_id = uuid.uuid4().hex
        self._max_calls = max(0, int(max_calls))
        self._observer = observer
        self._used = 0
        self._reservations: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def snapshot(self) -> ExtraLlmBudgetSnapshot:
        """返回已提交、预留和剩余额度的瞬时快照。"""

        reserved = len(self._reservations)
        remaining = max(0, self._max_calls - self._used - reserved)
        return ExtraLlmBudgetSnapshot(
            max_calls=self._max_calls,
            used=self._used,
            reserved=reserved,
            remaining=remaining,
        )

    async def reserve(self, feature: str) -> ExtraLlmReservation | None:
        """原子预留一个调用槽；额度不足时返回 ``None``。"""

        normalized_feature = _normalize_feature(feature)
        async with self._lock:
            if self._used + len(self._reservations) >= self._max_calls:
                observation = self._observation(
                    normalized_feature,
                    allowed=False,
                    reason_code="extra_llm_budget_exhausted",
                )
                token = None
            else:
                reservation_id = uuid.uuid4().hex
                self._reservations[reservation_id] = normalized_feature
                token = ExtraLlmReservation(
                    budget_id=self._budget_id,
                    reservation_id=reservation_id,
                    feature=normalized_feature,
                )
                observation = self._observation(
                    normalized_feature,
                    allowed=True,
                    reason_code="extra_llm_reserved",
                )
        self._emit(observation)
        return token

    async def commit(self, token: ExtraLlmReservation) -> bool:
        """提交成功 Provider 调用；旧轮次或重复 token 返回 ``False``。"""

        async with self._lock:
            feature = self._take_token(token)
            if feature is None:
                observation = self._observation(
                    _token_feature(token),
                    allowed=False,
                    reason_code="extra_llm_stale_token",
                )
                committed = False
            else:
                self._used += 1
                observation = self._observation(
                    feature,
                    allowed=True,
                    reason_code="extra_llm_committed",
                )
                committed = True
        self._emit(observation)
        return committed

    async def release(self, token: ExtraLlmReservation) -> bool:
        """释放失败或取消的 Provider 调用 reservation。"""

        async with self._lock:
            feature = self._take_token(token)
            if feature is None:
                observation = self._observation(
                    _token_feature(token),
                    allowed=False,
                    reason_code="extra_llm_stale_token",
                )
                released = False
            else:
                observation = self._observation(
                    feature,
                    allowed=False,
                    reason_code="extra_llm_released",
                )
                released = True
        self._emit(observation)
        return released

    def record_denial(self, feature: str, reason_code: str) -> None:
        """记录功能门或上下文门拒绝，不改变预算计数。"""

        self._emit(
            self._observation(
                _normalize_feature(feature),
                allowed=False,
                reason_code=reason_code,
            )
        )

    def _take_token(self, token: ExtraLlmReservation) -> str | None:
        """验证 token 所属轮次并移除有效 reservation。"""

        if not isinstance(token, ExtraLlmReservation):
            return None
        if token.budget_id != self._budget_id:
            return None
        feature = self._reservations.get(token.reservation_id)
        if feature is None or feature != token.feature:
            return None
        del self._reservations[token.reservation_id]
        return feature

    def _observation(
        self,
        feature: str,
        *,
        allowed: bool,
        reason_code: str,
    ) -> ExtraLlmBudgetObservation:
        """从当前计数生成 allowlist 观测对象。"""

        snapshot = self.snapshot()
        return ExtraLlmBudgetObservation(
            feature=feature,
            allowed=allowed,
            used=snapshot.used,
            remaining=snapshot.remaining,
            reason_code=_normalize_reason_code(reason_code),
        )

    def _emit(self, observation: ExtraLlmBudgetObservation) -> None:
        """输出安全标量，并隔离可选观测回调自身的失败。"""

        logger.debug(
            "[额外 LLM 预算] feature=%s allowed=%s used=%d remaining=%d reason_code=%s",
            observation.feature,
            observation.allowed,
            observation.used,
            observation.remaining,
            observation.reason_code,
        )
        if self._observer is None:
            return
        try:
            self._observer(observation)
        except Exception:
            logger.debug("额外 LLM 预算观测回调失败", exc_info=True)


_CURRENT_EXTRA_LLM_BUDGET: ContextVar[ExtraLlmBudget | None] = ContextVar(
    "memora_extra_llm_budget",
    default=None,
)


def current_extra_llm_budget() -> ExtraLlmBudget | None:
    """返回当前异步请求上下文中的额外 LLM 预算。"""

    return _CURRENT_EXTRA_LLM_BUDGET.get()


@contextmanager
def extra_llm_budget_scope(budget: ExtraLlmBudget) -> Iterator[ExtraLlmBudget]:
    """在当前任务及其派生任务中绑定请求级预算。"""

    context_token = _CURRENT_EXTRA_LLM_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_EXTRA_LLM_BUDGET.reset(context_token)


@asynccontextmanager
async def budgeted_extra_llm_call(
    cost_control: CostControl,
    feature: str,
    *,
    budget: ExtraLlmBudget | None = None,
) -> AsyncIterator[bool]:
    """同时执行功能许可和额度门，并按调用结果提交或释放 reservation。"""

    normalized_feature = _normalize_feature(feature)
    active_budget = budget or current_extra_llm_budget()
    if not cost_control.allow(normalized_feature):
        if active_budget is not None:
            active_budget.record_denial(
                normalized_feature,
                "extra_llm_feature_disabled",
            )
        else:
            _log_context_denial(normalized_feature, "extra_llm_feature_disabled")
        yield False
        return
    if active_budget is None:
        _log_context_denial(normalized_feature, "extra_llm_budget_missing")
        yield False
        return

    reservation = await active_budget.reserve(normalized_feature)
    if reservation is None:
        yield False
        return
    try:
        yield True
    except BaseException:
        await active_budget.release(reservation)
        raise
    else:
        await active_budget.commit(reservation)


def _normalize_feature(feature: str) -> str:
    """把 feature 收敛到固定能力枚举，未知输入不得承载正文。"""

    normalized = str(feature or "unknown").strip().casefold()
    return normalized if normalized in _ALLOWED_FEATURES else "unknown"


def _normalize_reason_code(reason_code: str) -> str:
    """把原因收敛到稳定枚举，未知输入不得进入观测。"""

    normalized = str(reason_code or "").strip().casefold()
    return normalized if normalized in _ALLOWED_REASON_CODES else "extra_llm_unknown"


def _token_feature(token: object) -> str:
    """安全提取 token 的 feature，非法对象回退为 unknown。"""

    return _normalize_feature(getattr(token, "feature", "unknown"))


def _log_context_denial(feature: str, reason_code: str) -> None:
    """在没有预算对象时仅记录固定标量拒绝原因。"""

    logger.debug(
        "[额外 LLM 预算] feature=%s allowed=false used=0 remaining=0 reason_code=%s",
        feature,
        reason_code,
    )


__all__ = [
    "ExtraLlmBudget",
    "ExtraLlmBudgetObservation",
    "ExtraLlmBudgetSnapshot",
    "ExtraLlmReservation",
    "budgeted_extra_llm_call",
    "current_extra_llm_budget",
    "extra_llm_budget_scope",
]
