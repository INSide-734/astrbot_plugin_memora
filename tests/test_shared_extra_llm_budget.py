"""验证 shared 额外 LLM 预算契约。"""

import pytest

from core.shared.cost_control import CostControl
from core.shared import extra_llm_budget as shared
from core.shared.contracts import CostControlPort


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
