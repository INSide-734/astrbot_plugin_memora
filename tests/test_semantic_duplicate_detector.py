"""语义近重复窄端口的有界性、护栏与失败语义契约。"""

from __future__ import annotations

import asyncio
import math

import pytest

from core.features.quality.application.semantic_duplicate_detector import (
    MAX_TRACKED_WINDOWS,
    SEMANTIC_METRIC_MODES,
    SEMANTIC_OUTCOME_BUDGET_EXHAUSTED,
    SEMANTIC_OUTCOME_CHECKED,
    SEMANTIC_OUTCOME_HIT,
    SEMANTIC_OUTCOME_UNAVAILABLE,
    SEMANTIC_OUTCOMES,
    SemanticCandidate,
    SemanticQuery,
    SemanticRequestBudget,
    build_semantic_document_search,
    detect_semantic_duplicate,
)
from tests.fact_evidence_helpers import fact_evidence

_OWNER_ID = 100
_OWNER_CONTENT = (
    "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
)
_OWNER_FACT = "项目使用 SQLite 存储会话记录"
_QUERY = "会话数据放在 SQLite 里，固定周五发版，发布前要跑回归测试"


def _metadata(**overrides: object) -> dict[str, object]:
    """构造带完整 scope 与逐事实用户来源证据的候选 metadata。"""

    base: dict[str, object] = {
        "scope_key": "group:group-1:topic-a",
        "privacy_level": "public",
        "chat_type": "group",
        "participant_ids": ["user-1", "user-2"],
        "key_facts": [_OWNER_FACT],
    }
    base.update(overrides)
    facts = base["key_facts"]
    assert isinstance(facts, list)
    base.setdefault("fact_source_evidence", fact_evidence(facts))
    return base


def _owner(**overrides: object) -> dict[str, object]:
    """构造落库 owner 记录。"""

    fields: dict[str, object] = {
        "session_id": "session-1",
        "persona_id": None,
        "status": "active",
    }
    fields.update(overrides)
    metadata = _metadata(**fields)
    return {"id": _OWNER_ID, "text": _OWNER_CONTENT, "metadata": metadata}


class _Search:
    """可注入候选、异常与调用计数的语义检索替身。"""

    def __init__(
        self,
        candidates: list[SemanticCandidate] | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.candidates = list(candidates or [])
        self.failure = failure
        self.queries: list[SemanticQuery] = []

    async def __call__(self, query: SemanticQuery) -> list[SemanticCandidate]:
        await asyncio.sleep(0)
        self.queries.append(query)
        if self.failure is not None:
            raise self.failure
        return list(self.candidates)


class _Loader:
    """按整数 ID 返回 owner 记录的回读替身。"""

    def __init__(self, owners: dict[int, dict[str, object]]) -> None:
        self.owners = owners
        self.loads: list[int] = []

    async def __call__(self, memory_id: int) -> dict[str, object] | None:
        await asyncio.sleep(0)
        self.loads.append(memory_id)
        return self.owners.get(memory_id)


async def _detect(
    search: _Search | None,
    loader: _Loader,
    *,
    metadata: dict[str, object] | None = None,
    budget: SemanticRequestBudget | None = None,
    window_key: str = "window-1",
    threshold: float = 0.9,
    candidate_limit: int = 5,
    guard=None,
):
    """以默认护栏参数调用语义检测。"""

    return await detect_semantic_duplicate(
        content=_QUERY,
        metadata=metadata if metadata is not None else _metadata(),
        session_id="session-1",
        persona_id=None,
        search_semantic=search,
        load_memory=loader,
        budget=budget if budget is not None else SemanticRequestBudget(),
        window_key=window_key,
        threshold=threshold,
        candidate_limit=candidate_limit,
        min_tokens=12,
        fact_evidence_guard=guard,
    )


def test_budget_caps_requests_per_window_and_isolates_windows() -> None:
    """同一窗口最多 8 次查询，其他窗口不受影响；超限返回 False。"""

    budget = SemanticRequestBudget()
    assert [budget.consume("window-1") for _ in range(8)] == [True] * 8
    assert budget.consume("window-1") is False
    assert budget.consume("window-2") is True
    assert budget.tracked_windows == 2


def test_budget_evicts_oldest_window_beyond_capacity() -> None:
    """窗口数超过容量时淘汰最旧窗口，预算重新按上限初始化。"""

    budget = SemanticRequestBudget(limit=1, max_windows=2)
    assert budget.consume("a") is True
    assert budget.consume("b") is True
    assert budget.consume("c") is True
    assert budget.tracked_windows == 2
    # "a" 已被淘汰：重新出现时按新窗口给满预算，而不是记住耗尽状态。
    assert budget.consume("a") is True


def test_budget_rejects_invalid_limits() -> None:
    """上限与窗口容量必须是正整数。"""

    for invalid in (0, -1, True):
        with pytest.raises(ValueError):
            SemanticRequestBudget(invalid)
    with pytest.raises(ValueError):
        SemanticRequestBudget(1, 0)


@pytest.mark.asyncio
async def test_missing_port_is_unavailable_without_provider_work() -> None:
    """端口缺失返回 unavailable，且不消耗预算。"""

    budget = SemanticRequestBudget()
    decision = await _detect(None, _Loader({}), budget=budget)

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_UNAVAILABLE,)
    assert budget.tracked_windows == 0


@pytest.mark.asyncio
async def test_incomplete_scope_skips_provider_without_outcomes() -> None:
    """作用域不完整时不查询 provider，也不伪造「已检测」计数。"""

    search = _Search([SemanticCandidate(_OWNER_ID, 0.99)])
    decision = await _detect(
        search,
        _Loader({_OWNER_ID: _owner()}),
        metadata=_metadata(scope_key=None),
    )

    assert decision.document is None
    assert decision.outcomes == ()
    assert search.queries == []


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_provider_calls() -> None:
    """预算耗尽后返回稳定 outcome，且不再查询 provider。"""

    search = _Search([SemanticCandidate(_OWNER_ID, 0.99)])
    budget = SemanticRequestBudget(limit=1)
    loader = _Loader({_OWNER_ID: _owner()})

    first = await _detect(search, loader, budget=budget)
    second = await _detect(search, loader, budget=budget)

    assert first.outcomes == (SEMANTIC_OUTCOME_CHECKED, SEMANTIC_OUTCOME_HIT)
    assert second.outcomes == (SEMANTIC_OUTCOME_BUDGET_EXHAUSTED,)
    assert len(search.queries) == 1


@pytest.mark.asyncio
async def test_qualified_candidate_returns_canonical_snapshot() -> None:
    """阈值内且通过全部护栏的候选返回 canonical 快照与命中 outcome。"""

    search = _Search([SemanticCandidate(_OWNER_ID, 0.95)])
    loader = _Loader({_OWNER_ID: _owner()})

    decision = await _detect(search, loader)

    assert decision.document is not None
    assert decision.document.memory_id == _OWNER_ID
    assert decision.document.content == _OWNER_CONTENT
    assert decision.score == pytest.approx(0.95)
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED, SEMANTIC_OUTCOME_HIT)
    assert search.queries[0].content == _QUERY
    assert search.queries[0].limit == 5


@pytest.mark.asyncio
async def test_low_score_candidate_is_never_loaded() -> None:
    """阈值以下的候选在回读 canonical 之前就被丢弃。"""

    search = _Search([SemanticCandidate(_OWNER_ID, 0.5)])
    loader = _Loader({_OWNER_ID: _owner()})

    decision = await _detect(search, loader)

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)
    assert loader.loads == []


@pytest.mark.asyncio
async def test_foreign_scope_or_inactive_owner_is_skipped() -> None:
    """不同 scope 或不可召回的 owner 不构成语义命中。"""

    for overrides in (
        {"scope_key": "group:group-2:topic-b"},
        {"status": "orphan"},
        {"privacy_level": "confidential"},
    ):
        search = _Search([SemanticCandidate(_OWNER_ID, 0.99)])
        loader = _Loader({_OWNER_ID: _owner(**overrides)})

        decision = await _detect(search, loader)

        assert decision.document is None
        assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)


@pytest.mark.asyncio
async def test_confidential_owner_requires_participant_overlap() -> None:
    """机密/私聊边界要求主体交集；无交集时跳过。"""

    metadata = _metadata(
        privacy_level="confidential",
        chat_type="private",
        participant_ids=["user-9"],
    )
    owner = _owner(
        privacy_level="confidential",
        chat_type="private",
        participant_ids=["user-1"],
    )
    decision = await _detect(
        _Search([SemanticCandidate(_OWNER_ID, 0.99)]),
        _Loader({_OWNER_ID: owner}),
        metadata=metadata,
    )

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)


@pytest.mark.asyncio
async def test_fact_guard_rejects_owner_without_matching_facts() -> None:
    """事实护栏不通过时不构成命中，但仍保留已检测计数。"""

    owner = _owner(
        key_facts=["团队改用 PostgreSQL 保存日志快照"],
        fact_source_evidence=fact_evidence(["团队改用 PostgreSQL 保存日志快照"]),
    )
    decision = await _detect(
        _Search([SemanticCandidate(_OWNER_ID, 0.99)]),
        _Loader({_OWNER_ID: owner}),
    )

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)


@pytest.mark.asyncio
async def test_non_finite_score_is_not_a_candidate() -> None:
    """非有限分数的候选不进入回读与命中判定。"""

    decision = await _detect(
        _Search([SemanticCandidate(_OWNER_ID, float("nan"))]),
        _Loader({_OWNER_ID: _owner()}),
    )

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)


@pytest.mark.asyncio
async def test_evidence_guard_rejection_keeps_checked_without_hit() -> None:
    """注入的用户来源证据护栏不通过时不产生 semantic_hit。"""

    decision = await _detect(
        _Search([SemanticCandidate(_OWNER_ID, 0.99)]),
        _Loader({_OWNER_ID: _owner()}),
        guard=lambda _metadata: False,
    )

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)


@pytest.mark.asyncio
async def test_bad_candidate_rows_are_skipped() -> None:
    """坏 ID/坏分数/不可回读的候选只跳过，不影响后续候选。"""

    search = _Search(
        [
            SemanticCandidate(0, 0.99),
            SemanticCandidate(_OWNER_ID + 1, 0.99),
            SemanticCandidate(_OWNER_ID, 0.91),
        ]
    )
    loader = _Loader({_OWNER_ID: _owner()})

    decision = await _detect(search, loader)

    assert decision.document is not None
    assert loader.loads == [_OWNER_ID + 1, _OWNER_ID]


@pytest.mark.asyncio
async def test_candidate_limit_bounds_loads() -> None:
    """返回上限同时约束回读次数。"""

    search = _Search(
        [SemanticCandidate(_OWNER_ID + offset, 0.99) for offset in range(5)]
    )
    loader = _Loader({})

    decision = await _detect(search, loader, candidate_limit=2)

    assert decision.document is None
    assert loader.loads == [_OWNER_ID, _OWNER_ID + 1]


@pytest.mark.asyncio
async def test_provider_failure_propagates_for_coordinator_fail_open() -> None:
    """provider 异常与取消都不在本模块吞掉，由协调器统一 fail-open。"""

    with pytest.raises(RuntimeError):
        await _detect(
            _Search(failure=RuntimeError("provider down")),
            _Loader({}),
        )
    with pytest.raises(asyncio.CancelledError):
        await _detect(
            _Search(failure=asyncio.CancelledError()),
            _Loader({}),
        )


@pytest.mark.asyncio
async def test_failed_provider_still_consumes_window_budget() -> None:
    """失败的查询同样计入预算，避免错误重试放大 provider 成本。"""

    budget = SemanticRequestBudget(limit=1)
    with pytest.raises(RuntimeError):
        await _detect(
            _Search(failure=RuntimeError("provider down")),
            _Loader({}),
            budget=budget,
        )

    decision = await _detect(
        _Search([SemanticCandidate(_OWNER_ID, 0.99)]),
        _Loader({_OWNER_ID: _owner()}),
        budget=budget,
    )
    assert decision.outcomes == (SEMANTIC_OUTCOME_BUDGET_EXHAUSTED,)


@pytest.mark.asyncio
async def test_non_sequence_port_result_is_ignored() -> None:
    """端口返回非序列时按无候选处理，不抛异常。"""

    async def broken(_query: SemanticQuery) -> object:
        return object()

    decision = await detect_semantic_duplicate(
        content=_QUERY,
        metadata=_metadata(),
        session_id="session-1",
        persona_id=None,
        search_semantic=broken,  # type: ignore[arg-type]
        load_memory=_Loader({}),
        budget=SemanticRequestBudget(),
        window_key="window-1",
        threshold=0.9,
        candidate_limit=5,
        min_tokens=12,
    )

    assert decision.document is None
    assert decision.outcomes == (SEMANTIC_OUTCOME_CHECKED,)


def test_semantic_outcomes_and_modes_form_closed_sets() -> None:
    """指标闭集保持稳定取值与顺序无关的集合语义。"""

    assert set(SEMANTIC_OUTCOMES) == {
        "semantic_checked",
        "semantic_hit",
        "semantic_failed",
        "semantic_unavailable",
        "semantic_budget_exhausted",
    }
    assert SEMANTIC_METRIC_MODES == ("semantic_observe", "semantic_enforce")
    assert len(set(SEMANTIC_OUTCOMES)) == len(SEMANTIC_OUTCOMES)
    assert MAX_TRACKED_WINDOWS >= 1


@pytest.mark.asyncio
async def test_adapter_returns_none_without_vector_retriever() -> None:
    """引擎缺少向量检索能力时端口为 None，等价于 unavailable。"""

    assert build_semantic_document_search(object()) is None
    assert build_semantic_document_search(_EngineWithoutSearch()) is None


class _EngineWithoutSearch:
    """只有空 vector_retriever 的引擎替身。"""

    vector_retriever = None


class _Row:
    """最小向量检索结果替身。"""

    def __init__(self, doc_id: object, score: object) -> None:
        self.doc_id = doc_id
        self.score = score


class _FakeRetriever:
    """按调用返回固定行的向量检索替身。"""

    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows
        self.calls: list[tuple[object, ...]] = []

    async def search(self, *args: object) -> list[_Row]:
        self.calls.append(args)
        return self.rows


@pytest.mark.asyncio
async def test_adapter_maps_rows_and_skips_invalid_ones() -> None:
    """adapter 只映射合法 (int ID, 数值分数) 行，并传递有界查询参数。"""

    retriever = _FakeRetriever(
        [
            _Row(_OWNER_ID, 0.93),
            _Row(0, 0.99),
            _Row(True, 0.99),
            _Row(_OWNER_ID + 1, "high"),
            _Row(_OWNER_ID + 2, float("nan")),
        ]
    )
    engine = type("_Engine", (), {"vector_retriever": retriever})()
    port = build_semantic_document_search(engine)
    assert port is not None

    candidates = await port(
        SemanticQuery(
            content=_QUERY,
            session_id="session-1",
            persona_id=None,
            limit=3,
        )
    )

    # 非有限分数在检测层丢弃，因此 adapter 只保证数值投影。
    assert [item.memory_id for item in candidates] == [_OWNER_ID, _OWNER_ID + 2]
    assert candidates[0].score == pytest.approx(0.93)
    assert math.isnan(candidates[1].score)
    assert retriever.calls == [(_QUERY, 3, "session-1", None)]
