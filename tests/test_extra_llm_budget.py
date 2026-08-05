"""请求级额外 LLM 预算与成本模式闭环测试。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from core.base.config_validator import CostControlConfig
from core.base.cost_control import CostControl, build_cost_control_from_config
from core.base.extra_llm_budget import (
    ExtraLlmBudget,
    budgeted_extra_llm_call,
    current_extra_llm_budget,
    extra_llm_budget_scope,
)


class _ConfigStub:
    """为话题批次准备器提供最小点号配置读取接口。"""

    def __init__(self, values: dict[str, object]) -> None:
        """保存测试所需的配置叶。"""

        self._values = values

    def get(self, key: str, default: object = None) -> object:
        """按点号键返回配置值。"""

        return self._values.get(key, default)

    def get_section(self, section: str) -> dict[str, object]:
        """把点号配置投影为指定叶子分支。"""

        prefix = f"{section}."
        return {
            key.removeprefix(prefix): value
            for key, value in self._values.items()
            if key.startswith(prefix)
        }


class _FormatterStub:
    """提供 Strategy D 所需的对话格式化入口。"""

    @staticmethod
    def format_conversation(_messages: list[object]) -> str:
        """返回固定匿名对话文本。"""

        return "匿名对话"


def _quality_control(max_calls: int = 1) -> CostControl:
    """构造允许额外能力且额度明确的质量档成本门。"""

    return CostControl(mode="quality", max_extra_llm_calls_per_turn=max_calls)


def test_cost_control_requires_typed_section_and_preserves_explicit_allows() -> None:
    """成本门必须读取单一配置分支，并保留 balanced 显式许可语义。"""

    section = CostControlConfig(
        mode="balanced",
        max_extra_llm_calls_per_turn=1,
        allow_llm_reranker_in_passive_recall=True,
    )

    control = build_cost_control_from_config(section)

    assert control.mode == "balanced"
    assert control.max_extra_llm_calls_per_turn == 1
    assert control.allow("llm_reranker") is True
    assert control.allow("llm_query_rewrite") is False
    with pytest.raises((TypeError, ValidationError)):
        build_cost_control_from_config({"cost_control": section.model_dump()})


@pytest.mark.asyncio
async def test_concurrent_reservations_never_oversell_single_slot() -> None:
    """并发 reservation 必须至多有一个成功。"""

    budget = ExtraLlmBudget(max_calls=1)
    start = asyncio.Event()

    async def _reserve(feature: str):
        """在同一屏障后竞争一个预算槽。"""

        await start.wait()
        return await budget.reserve(feature)

    tasks = [
        asyncio.create_task(_reserve("llm_reranker")),
        asyncio.create_task(_reserve("topic_strategy_d")),
    ]
    start.set()
    tokens = await asyncio.gather(*tasks)
    granted = [token for token in tokens if token is not None]

    assert len(granted) == 1
    assert budget.snapshot().reserved == 1
    assert budget.snapshot().used == 0
    assert await budget.commit(granted[0]) is True
    assert budget.snapshot().used == 1
    assert budget.snapshot().used <= budget.snapshot().max_calls


@pytest.mark.asyncio
async def test_new_turn_rejects_token_from_previous_budget() -> None:
    """新轮次预算不得接受旧轮次 reservation token。"""

    previous = ExtraLlmBudget(max_calls=1)
    current = ExtraLlmBudget(max_calls=1)
    old_token = await previous.reserve("llm_reranker")

    assert old_token is not None
    assert await current.commit(old_token) is False
    assert await current.release(old_token) is False
    assert current.snapshot().used == 0
    assert current.snapshot().reserved == 0
    assert await previous.release(old_token) is True


@pytest.mark.asyncio
async def test_cancelled_call_propagates_and_releases_reservation() -> None:
    """取消必须穿透预算上下文，并释放尚未提交的槽。"""

    budget = ExtraLlmBudget(max_calls=1)

    with pytest.raises(asyncio.CancelledError):
        async with budgeted_extra_llm_call(
            _quality_control(),
            "llm_query_rewrite",
            budget=budget,
        ) as allowed:
            assert allowed is True
            raise asyncio.CancelledError

    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    assert await budget.reserve("llm_reranker") is not None


@pytest.mark.asyncio
async def test_provider_failure_releases_uncommitted_reservation() -> None:
    """普通 Provider 失败不得永久占用未提交额度。"""

    budget = ExtraLlmBudget(max_calls=1)

    with pytest.raises(RuntimeError, match="provider failed"):
        async with budgeted_extra_llm_call(
            _quality_control(),
            "llm_reranker",
            budget=budget,
        ) as allowed:
            assert allowed is True
            raise RuntimeError("provider failed")

    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    assert await budget.reserve("topic_strategy_d") is not None


@pytest.mark.asyncio
async def test_successful_call_commits_and_denies_second_call() -> None:
    """成功调用提交额度后，同轮第二次调用必须稳定拒绝。"""

    budget = ExtraLlmBudget(max_calls=1)
    control = _quality_control()

    async with budgeted_extra_llm_call(
        control,
        "llm_query_rewrite",
        budget=budget,
    ) as first_allowed:
        assert first_allowed is True

    async with budgeted_extra_llm_call(
        control,
        "llm_reranker",
        budget=budget,
    ) as second_allowed:
        assert second_allowed is False

    snapshot = budget.snapshot()
    assert snapshot.used == 1
    assert snapshot.reserved == 0
    assert snapshot.remaining == 0


@pytest.mark.asyncio
async def test_observation_payload_has_only_allowlisted_scalars() -> None:
    """预算观测不得携带 query、Prompt、正文或 Provider 信息。"""

    observations = []
    budget = ExtraLlmBudget(max_calls=1, observer=observations.append)

    async with budgeted_extra_llm_call(
        _quality_control(),
        "llm_reranker",
        budget=budget,
    ) as allowed:
        assert allowed is True
    budget.record_denial("PRIVATE_QUERY_CANARY", "SECRET_PROMPT_CANARY")

    assert observations
    for observation in observations:
        assert set(asdict(observation)) == {
            "feature",
            "allowed",
            "used",
            "remaining",
            "reason_code",
        }
        assert all(
            canary not in repr(observation)
            for canary in (
                "SECRET_PROMPT_CANARY",
                "PRIVATE_QUERY_CANARY",
                "PROVIDER_KEY_CANARY",
            )
        )
    assert observations[-1].feature == "unknown"
    assert observations[-1].reason_code == "extra_llm_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control", "feature", "expected"),
    [
        (
            CostControl(mode="low_cost", max_extra_llm_calls_per_turn=2),
            "llm_reranker",
            False,
        ),
        (
            CostControl(mode="balanced", max_extra_llm_calls_per_turn=0),
            "llm_reranker",
            False,
        ),
        (
            CostControl(
                mode="balanced",
                max_extra_llm_calls_per_turn=1,
                allow_llm_reranker_in_passive_recall=True,
            ),
            "llm_reranker",
            True,
        ),
        (
            CostControl(mode="quality", max_extra_llm_calls_per_turn=2),
            "llm_query_rewrite",
            True,
        ),
    ],
)
async def test_tuning_profiles_require_feature_and_budget_gates(
    control: CostControl,
    feature: str,
    expected: bool,
) -> None:
    """低成本、均衡和高质量档必须符合公开文档中的双门语义。"""

    budget = ExtraLlmBudget(control.max_extra_llm_calls_per_turn)

    async with budgeted_extra_llm_call(
        control,
        feature,
        budget=budget,
    ) as allowed:
        assert allowed is expected


@pytest.mark.asyncio
async def test_passive_recall_and_reflection_share_one_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """查询改写占用额度后，反思 Strategy D 不得再次调用 Provider。"""

    from core.handlers.topic_batch_preparer import TopicBatchPreparer
    from core.processors.topic_splitter import TwoStageLLMStrategy
    from core.retrieval.query_rewriter import QueryRewriter

    llm_rewrite = AsyncMock(
        return_value=(
            '{"intent":"temporal","rewritten_queries":["最近"],'
            '"memory_types":["EPISODIC"]}'
        )
    )
    identify_topics = AsyncMock(
        return_value=[
            {"line_range": [1, 2]},
            {"line_range": [3, 3]},
        ]
    )
    monkeypatch.setattr(TwoStageLLMStrategy, "identify_topics", identify_topics)
    control = _quality_control(max_calls=1)
    budget = ExtraLlmBudget(max_calls=1)
    rewriter = QueryRewriter(llm_caller=llm_rewrite, cost_control=control)
    processor = SimpleNamespace(
        conversation_formatter=_FormatterStub(),
        llm_client_instance=MagicMock(),
    )
    preparer = TopicBatchPreparer(
        config_manager=_ConfigStub(
            {
                "topic_segmentation.strategy": "d",
                "topic_segmentation.strategy_d.stage1_max_topics": 5,
                "topic_segmentation.strategy_d.enable_parallel_stage2": True,
            }
        ),
        memory_processor=processor,
        cost_control=control,
    )
    messages = [object(), object(), object()]

    with extra_llm_budget_scope(budget):
        rewritten = await rewriter.rewrite("上次那个事", "匿名上下文")
        batches = await preparer.prepare_batches(messages, is_group_chat=False)

    assert rewritten.intent == "temporal"
    assert batches == [messages]
    llm_rewrite.assert_awaited_once()
    identify_topics.assert_not_awaited()
    assert budget.snapshot().used == 1


@pytest.mark.asyncio
async def test_reflection_extra_batch_uses_single_reservation() -> None:
    """额外反思批次必须单次调用 Provider，并提交一个共享预算槽。"""

    from core.handlers.reflection_llm_budget import (
        fit_batches_to_extra_llm_budget,
        process_reflection_batches,
    )

    control = _quality_control(max_calls=1)
    budget = ExtraLlmBudget(max_calls=1)
    process_conversation = AsyncMock(side_effect=[[{"batch": 0}], [{"batch": 1}]])
    source_batches = [["a"], ["b"], ["c"]]

    with extra_llm_budget_scope(budget):
        fitted = fit_batches_to_extra_llm_budget(source_batches, control)
        results = await process_reflection_batches(
            fitted,
            process_conversation=process_conversation,
            cost_control=control,
            is_group_chat=False,
            persona_id=None,
        )

    assert fitted == [["a"], ["b", "c"]]
    assert results == [[{"batch": 0}], [{"batch": 1}]]
    assert process_conversation.await_count == 2
    assert "llm_max_retries" not in process_conversation.await_args_list[0].kwargs
    assert process_conversation.await_args_list[1].kwargs["llm_max_retries"] == 1
    assert budget.snapshot().used == 1
    assert budget.snapshot().reserved == 0


@pytest.mark.asyncio
async def test_event_handler_reuses_and_clears_turn_budget() -> None:
    """召回与响应必须共享新轮次预算，后台任务继承后事件引用应清理。"""

    from core.event_handler import EventHandler

    control = _quality_control(max_calls=1)
    processor = SimpleNamespace(
        llm_client=SimpleNamespace(call_llm_with_retry=AsyncMock()),
    )
    handler = EventHandler(
        context=MagicMock(),
        config_manager=_ConfigStub({}),
        memory_engine=SimpleNamespace(cost_control=control),
        memory_processor=processor,
        conversation_manager=SimpleNamespace(),
    )
    identity = MagicMock()
    handler._resolve_identity = MagicMock(return_value=identity)
    handler._writes_blocked = MagicMock(return_value=False)
    observed_contexts: list[ExtraLlmBudget | None] = []
    background_tasks: list[asyncio.Task[ExtraLlmBudget | None]] = []

    async def _capture_recall(*_args, **_kwargs) -> None:
        """记录召回子处理器看到的当前预算。"""

        observed_contexts.append(current_extra_llm_budget())

    async def _capture_background_context() -> ExtraLlmBudget | None:
        """让任务在响应作用域退出后读取创建时继承的预算。"""

        await asyncio.sleep(0)
        return current_extra_llm_budget()

    async def _capture_reflection(*_args, **_kwargs) -> None:
        """记录响应预算并创建继承当前上下文的后台任务。"""

        observed_contexts.append(current_extra_llm_budget())
        background_tasks.append(asyncio.create_task(_capture_background_context()))

    handler._recall_handler.handle_memory_recall = AsyncMock(
        side_effect=_capture_recall
    )
    handler._reflection_handler.handle_memory_reflection = AsyncMock(
        side_effect=_capture_reflection
    )
    event = SimpleNamespace()

    await handler.handle_memory_recall(event, MagicMock())
    first_budget = event._memora_extra_llm_budget
    await handler.handle_memory_recall(event, MagicMock())
    second_budget = event._memora_extra_llm_budget
    await handler.handle_memory_reflection(event, MagicMock())

    assert first_budget is not second_budget
    assert observed_contexts == [first_budget, second_budget, second_budget]
    assert not hasattr(event, "_memora_extra_llm_budget")
    assert await background_tasks[0] is second_budget


@pytest.mark.asyncio
async def test_quality_runtime_constructs_llm_reranker(tmp_db_path: str) -> None:
    """质量档与 LLM 策略必须在真实引擎初始化链中创建 LLM reranker。"""

    from core.managers.memory_engine import MemoryEngine

    control = _quality_control(max_calls=2)
    engine = MemoryEngine(
        db_path=tmp_db_path,
        faiss_db=MagicMock(),
        llm_provider=MagicMock(),
        config={
            "graph_memory_enabled": False,
            "recall_engine.stopwords_path": "",
            "rrf_k": 60,
            "write_reliability.repair_enabled": False,
            "user_profile.enabled": False,
            "auto_learning.enabled": False,
            "knowledge_base.enabled": False,
            "notes.enabled": False,
            "reranker.enabled": True,
            "reranker.strategy": "llm",
            "export.enabled": False,
            "cost_control_runtime": control,
        },
    )
    engine._schema.create_tables = AsyncMock()
    expected_reranker = MagicMock()

    try:
        with (
            patch("core.managers.memory_engine_lifecycle.BM25Retriever") as bm25_class,
            patch(
                "core.retrieval.reranker_factory.create_reranker",
                new=AsyncMock(return_value=expected_reranker),
            ) as create_reranker,
        ):
            bm25_class.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert engine.reranker is expected_reranker
        create_reranker.assert_awaited_once()
        assert create_reranker.await_args.args[0] == "llm"
        assert create_reranker.await_args.kwargs["cost_control"] is control
    finally:
        await engine.close()
