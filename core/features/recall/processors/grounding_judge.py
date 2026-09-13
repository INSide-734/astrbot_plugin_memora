"""来源忠实性 Judge 的解析、预算与安全归因。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ....platform.security.guardrails import validate_and_clean_json
from ....shared.extra_llm_budget import (
    budgeted_extra_llm_call,
    current_extra_llm_budget,
)
from ...reflection.domain.summary_models import normalize_exception_type
from .memory_grounding import GroundingResult

if TYPE_CHECKING:
    from ....shared.cost_control import CostControl
    from ...quality.domain.gate_config import GateProfile
    from .llm_client import LLMClient


GroundingJudgeCallable = Callable[
    [dict[str, Any], str], Awaitable[bool | Mapping[str, Any]]
]

_JUDGE_FEATURE = "memory_grounding_judge"
_INVALID_RESPONSE_MARKER = "grounding_judge_invalid_response"
_UNAVAILABLE_CAUSES = frozenset(
    {
        "judge_disabled",
        "budget_denied",
        "budget_scope_missing",
        "budget_exhausted",
        "response_invalid",
        "call_failed",
        "unknown",
    }
)


def _normalized_exception_type(exception: BaseException | None) -> str:
    """返回不含异常正文的有限异常类别。"""
    if exception is None:
        return "unknown"
    return normalize_exception_type(type(exception).__name__) or "unknown"


def _report_unavailable(
    cause: str,
    exception: BaseException | None = None,
) -> None:
    """输出一次常开、低基数且不含载荷的 Judge 失败日志。"""
    safe_cause = (
        cause if isinstance(cause, str) and cause in _UNAVAILABLE_CAUSES else "unknown"
    )
    try:
        logger.warning(
            "[GroundingJudge] unavailable component=grounding_judge stage=resolve "
            "cause=%s exception_type=%s count=%d",
            safe_cause,
            _normalized_exception_type(exception),
            1,
        )
    except Exception:
        # 日志设施故障不能改变 Judge 的保守隔离语义。
        return


class GroundingJudgeMixin:
    """提供来源 Judge 的实际解析、调用和请求级预算行为。"""

    if TYPE_CHECKING:
        cost_control: CostControl
        llm_client: LLMClient
        _judge_prompt_default: str

    _grounding_judge: GroundingJudgeCallable

    def _judge_unavailable(
        self,
        grounding: GroundingResult,
        cause: str,
        exception: BaseException | None = None,
    ) -> GroundingResult:
        """记录安全归因并返回既有保守隔离结果。"""
        _report_unavailable(cause, exception)
        return grounding.with_unavailable_judge()

    @staticmethod
    def _budget_unavailable_cause() -> str:
        """区分预算上下文缺失、额度耗尽和其他预算拒绝。"""
        budget = current_extra_llm_budget()
        if budget is None:
            return "budget_scope_missing"
        try:
            remaining = budget.snapshot().remaining
        except asyncio.CancelledError:
            raise
        except Exception:
            return "unknown"
        if isinstance(remaining, bool) or not isinstance(remaining, int):
            return "unknown"
        return "budget_exhausted" if remaining <= 0 else "budget_denied"

    @staticmethod
    def _is_invalid_response(exception: BaseException) -> bool:
        """仅识别本地固定非法响应标记，不猜测外部异常语义。"""
        return isinstance(exception, ValueError) and exception.args == (
            _INVALID_RESPONSE_MARKER,
        )

    async def resolve_grounding_judge(
        self,
        grounding: GroundingResult,
        *,
        is_group_chat: bool,
        profile: GateProfile,
        topics: tuple[str, ...] = (),
        importance: float = 0.5,
    ) -> GroundingResult:
        """按 profile 开关解析 Judge，并保持原有预算与取消语义。"""
        try:
            judge_enabled = profile.judge.enabled
        except asyncio.CancelledError:
            raise
        except Exception as exception:
            return self._judge_unavailable(grounding, "unknown", exception)
        if not judge_enabled:
            return self._judge_unavailable(grounding, "judge_disabled")
        try:
            feature_allowed = self.cost_control.allow(_JUDGE_FEATURE)
        except asyncio.CancelledError:
            raise
        except Exception as exception:
            return self._judge_unavailable(grounding, "unknown", exception)
        payload = {
            "claim_text": grounding.claim_text,
            "source_text": grounding.source_text,
            "is_group_chat": bool(is_group_chat),
            "chat_type": "群聊" if is_group_chat else "私聊",
            "topics": "、".join(topics) or "无",
            "importance": str(importance),
        }
        try:
            if feature_allowed:
                async with budgeted_extra_llm_call(
                    self.cost_control,
                    _JUDGE_FEATURE,
                ) as allowed:
                    if not allowed:
                        return self._judge_unavailable(
                            grounding,
                            self._budget_unavailable_cause(),
                        )
                    judged = await self._grounding_judge(
                        payload, profile.judge.prompt_template
                    )
            else:
                # Judge 开关显式开启时，绕过功能许可但仍受请求级预算约束。
                budget = current_extra_llm_budget()
                if budget is None:
                    return self._judge_unavailable(grounding, "budget_scope_missing")
                reservation = await budget.reserve(_JUDGE_FEATURE)
                if reservation is None:
                    return self._judge_unavailable(
                        grounding,
                        self._budget_unavailable_cause(),
                    )
                try:
                    judged = await self._grounding_judge(
                        payload, profile.judge.prompt_template
                    )
                except BaseException:
                    # 失败或取消都必须释放预留；取消按控制流向上传播。
                    await budget.release(reservation)
                    raise
                else:
                    await budget.commit(reservation)
            if isinstance(judged, Mapping):
                supported = judged.get("supported") is True
            else:
                supported = judged is True
            return grounding.with_judge_result(supported)
        except asyncio.CancelledError:
            raise
        except Exception as exception:
            cause = (
                "response_invalid"
                if self._is_invalid_response(exception)
                else "call_failed"
            )
            return self._judge_unavailable(grounding, cause, exception)

    async def _call_grounding_judge(
        self,
        payload: dict[str, Any],
        template: str = "",
    ) -> Mapping[str, Any]:
        """只向 Provider 发送当前候选声明和已引用片段。"""
        # 空配置使用文件默认模板；占位符合法性由配置校验保证。
        template_text = template or self._judge_prompt_default
        prompt = template_text.format(
            claim_text=str(payload.get("claim_text") or "")[:1200],
            source_text=str(payload.get("source_text") or "")[:2400],
            chat_type=payload.get("chat_type", "私聊"),
            topics=payload.get("topics", "无"),
            importance=payload.get("importance", "0.5"),
        )
        response_text = await self.llm_client.call_llm_with_retry(
            prompt=prompt,
            system_prompt="只做来源忠实性判断，不补充来源之外的事实。",
            max_retries=1,
        )
        parsed = validate_and_clean_json(
            response_text,
            fallback_return_none=True,
        )
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("supported"), bool
        ):
            raise ValueError(_INVALID_RESPONSE_MARKER)
        return json.loads(json.dumps(parsed))


__all__ = ["GroundingJudgeCallable", "GroundingJudgeMixin"]
