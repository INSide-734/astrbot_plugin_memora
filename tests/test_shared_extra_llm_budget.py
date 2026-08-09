"""验证额外 LLM 预算迁移到 shared 后的兼容契约。"""

import pytest

from core.base import extra_llm_budget as legacy
from core.base.cost_control import CostControl
from core.shared import extra_llm_budget as shared
from core.shared.contracts import CostControlPort


def test_legacy_budget_exports_preserve_object_identity() -> None:
    """旧路径与 shared 路径必须指向同一组预算对象。"""

    assert legacy.ExtraLlmBudget is shared.ExtraLlmBudget
    assert legacy.ExtraLlmReservation is shared.ExtraLlmReservation
    assert legacy.ExtraLlmBudgetSnapshot is shared.ExtraLlmBudgetSnapshot
    assert legacy.ExtraLlmBudgetObservation is shared.ExtraLlmBudgetObservation
    assert legacy.budgeted_extra_llm_call is shared.budgeted_extra_llm_call
    assert legacy.current_extra_llm_budget is shared.current_extra_llm_budget
    assert legacy.extra_llm_budget_scope is shared.extra_llm_budget_scope


def test_cost_control_implements_shared_port() -> None:
    """现有成本门实现必须满足 shared 的窄协议。"""

    assert isinstance(CostControl(mode="quality"), CostControlPort)


@pytest.mark.asyncio
async def test_shared_budget_context_commits_successful_call() -> None:
    """shared 入口成功退出时必须提交 reservation。"""

    budget = shared.ExtraLlmBudget(max_calls=1)
    with shared.extra_llm_budget_scope(budget):
        async with shared.budgeted_extra_llm_call(
            CostControl(mode="quality"),
            "llm_reranker",
        ) as allowed:
            assert allowed is True

    assert budget.snapshot().used == 1
