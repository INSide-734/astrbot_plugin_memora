"""会话优先召回离线实验契约。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from core.evaluation.retrieval_quality import EvaluationCase, load_fixture_dir
from core.evaluation.session_first_ablation import (
    SESSION_REASON_CODES,
    SessionFirstPreset,
    load_session_first_cases,
    make_session_first_retrievers,
    run_session_first,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
FIXTURE_PATH = FIXTURE_ROOT / "session_first.jsonl"


def _candidate(
    case: EvaluationCase,
    doc_id: str,
    score: float,
    **overrides: Any,
) -> dict[str, Any]:
    """构造通过可信上下文校验的匿名候选。"""

    metadata = {
        key: case.metadata[key]
        for key in (
            "session_id",
            "scope",
            "privacy_level",
            "role",
            "source_revision",
            "reference_time",
        )
        if key in case.metadata
    }
    metadata.update({"canonical": True, "valid": True})
    metadata.update(overrides)
    return {"doc_id": doc_id, "score": score, "metadata": metadata}


def _single_case(**metadata_overrides: Any) -> EvaluationCase:
    """构造适合门控负测的单条匿名用例。"""

    metadata = {
        "dataset": "session_first",
        "scenario": "session_hit",
        "trusted_session": True,
        "session_id": "session-synthetic",
        "intent": "fact",
        "scope": "private:synthetic",
        "privacy_level": "shared",
        "role": "user",
        "source_revision": "rev-current",
        "source_revision_required": True,
        "critical_long_term_doc_ids": ["mem-relevant"],
        "latency_ms": 1.0,
    }
    metadata.update(metadata_overrides)
    return EvaluationCase(
        case_id="session-case-synthetic",
        query="匿名合成查询",
        relevant_doc_ids={"mem-relevant"},
        metadata=metadata,
    )


def test_load_session_first_cases_validates_specialized_fixture(tmp_path: Path) -> None:
    """专用 loader 应读取三类场景并拒绝未知场景。"""

    cases = load_session_first_cases(FIXTURE_PATH)

    assert len(cases) == 3
    assert {case.metadata["scenario"] for case in cases} == {
        "session_hit",
        "session_no_hit",
        "mixed",
    }
    assert "session_first" not in load_fixture_dir(FIXTURE_ROOT)
    assert "session_first" in load_fixture_dir(
        FIXTURE_ROOT,
        include_experimental=True,
    )

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(
        json.dumps(
            {
                "case_id": "invalid",
                "query": "匿名查询",
                "relevant_doc_ids": ["mem-a"],
                "metadata": {"scenario": "unknown"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="session_fixture_scenario_invalid"):
        load_session_first_cases(invalid)


@pytest.mark.asyncio
async def test_double_run_keeps_baseline_and_blocks_mixed_false_shortcut() -> None:
    """每个用例必须双跑，混合场景不能遮蔽关键长期事实。"""

    cases = load_session_first_cases(FIXTURE_PATH)
    baseline_calls: list[str] = []
    session_calls: list[str] = []

    async def baseline(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回完整召回中的相关长期事实和背景候选。"""

        baseline_calls.append(case.case_id)
        relevant = next(iter(case.relevant_doc_ids))
        return [
            {"doc_id": relevant, "score": 0.98},
            {"doc_id": f"background-{case.case_id}", "score": 0.2},
        ]

    async def session(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """按场景返回会话命中、空命中或表面命中。"""

        session_calls.append(case.case_id)
        scenario = case.metadata["scenario"]
        if scenario == "session_hit":
            relevant = next(iter(case.relevant_doc_ids))
            return [_candidate(case, relevant, 0.95)]
        if scenario == "mixed":
            return [_candidate(case, "mem-surface", 0.96)]
        return []

    report = await run_session_first(cases, baseline, session, k=2)

    assert baseline_calls == [case.case_id for case in cases]
    assert session_calls == [case.case_id for case in cases]
    assert report.status == "completed"
    assert report.reason_code_aggregates == {
        "session_evidence_insufficient": 1,
        "session_evidence_sufficient": 1,
        "session_no_hit": 1,
    }
    assert report.would_short_circuit == 1
    assert report.wrong_short_circuit == 0
    assert report.estimated_full_recall_savings == pytest.approx(1 / 3)
    assert report.baseline is not None and report.baseline.recall_at_k == 1.0
    assert report.effective is not None and report.effective.recall_at_k == 1.0
    assert report.session is not None and report.session.recall_at_k < 1.0
    assert report.effective.p50_latency_ms == 9.0
    assert report.provider_calls == 2.0
    assert report.token_cost == 30.0


@pytest.mark.asyncio
async def test_engine_adapter_keeps_baseline_and_session_filter_explicit() -> None:
    """engine 适配器应分别传递完整基线和精确 Session。"""

    calls: list[dict[str, Any]] = []

    class Engine:
        async def search_memories(self, **kwargs: Any) -> list[Any]:
            """记录检索上下文而不访问真实存储。"""

            calls.append(kwargs)
            return []

    case = _single_case()
    baseline, session = make_session_first_retrievers(
        Engine(),
        baseline_uses_session_filter=False,
    )

    await baseline(case, 3)
    await session(case, 3)

    assert calls[0]["session_id"] is None
    assert calls[1]["session_id"] == "session-synthetic"
    assert calls[0]["query_intent"] == "fact"
    assert calls[1]["query_intent"] == "fact"
    assert case.metadata["session_id"] == "session-synthetic"


@pytest.mark.asyncio
async def test_intent_score_margin_and_visibility_fail_closed() -> None:
    """复杂意图、低 margin 和越权候选都必须回退完整召回。"""

    relation_case = _single_case(intent="relation")
    margin_case = _single_case(case_variant="margin")
    stale_case = _single_case(case_variant="stale")
    cases = [relation_case, margin_case, stale_case]

    async def baseline(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """为每条用例返回完整召回的相关事实。"""

        return [{"doc_id": "mem-relevant", "score": 0.99}]

    async def session(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """分别注入复杂意图、低 margin 和 stale revision。"""

        if case.metadata.get("case_variant") == "margin":
            return [
                _candidate(case, "mem-relevant", 0.82),
                _candidate(case, "mem-near", 0.80),
            ]
        if case.metadata.get("case_variant") == "stale":
            return [_candidate(case, "mem-relevant", 0.95, source_revision="rev-old")]
        return [_candidate(case, "mem-relevant", 0.95)]

    report = await run_session_first(cases, baseline, session, k=2)

    assert report.would_short_circuit == 0
    assert report.reason_code_aggregates == {
        "intent_requires_full_recall": 1,
        "session_evidence_insufficient": 2,
    }
    assert set(report.reason_code_aggregates) <= SESSION_REASON_CODES


@pytest.mark.asyncio
async def test_missing_session_and_session_failure_still_run_baseline() -> None:
    """缺失可信 Session 或会话分支失败时仍必须运行完整基线。"""

    missing = _single_case(trusted_session=False, session_id=None)
    failed = _single_case(case_variant="failed")
    baseline_calls: list[str] = []

    async def baseline(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """记录完整基线是否实际执行。"""

        baseline_calls.append(case.case_id)
        return [{"doc_id": "mem-relevant", "score": 0.99}]

    async def session(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """为第二条用例模拟普通可恢复失败。"""

        if case.metadata.get("case_variant") == "failed":
            raise RuntimeError("PROVIDER-SECRET-CANARY")
        return []

    report = await run_session_first([missing, failed], baseline, session, k=1)

    assert len(baseline_calls) == 2
    assert report.reason_code_aggregates == {
        "missing_trusted_session": 1,
        "session_stage_failed": 1,
    }
    assert "PROVIDER-SECRET-CANARY" not in json.dumps(asdict(report))


@pytest.mark.asyncio
async def test_cancellation_and_baseline_failure_propagate() -> None:
    """取消与基线失败不能被降级为成功报告。"""

    case = _single_case()

    async def cancelled(_case: EvaluationCase, _k: int) -> list[Any]:
        """模拟会话分支取消。"""

        raise asyncio.CancelledError()

    async def baseline(_case: EvaluationCase, _k: int) -> list[Any]:
        """返回空基线。"""

        return []

    with pytest.raises(asyncio.CancelledError):
        await run_session_first([case], baseline, cancelled, k=1)

    async def failed_baseline(_case: EvaluationCase, _k: int) -> list[Any]:
        """模拟完整基线失败。"""

        raise RuntimeError("baseline-failed")

    with pytest.raises(RuntimeError, match="baseline-failed"):
        await run_session_first([case], failed_baseline, baseline, k=1)


@pytest.mark.asyncio
async def test_equivalent_branch_and_missing_snapshot_are_skipped() -> None:
    """等价读取和缺失只读快照都不能标记为完成。"""

    case = _single_case(critical_long_term_doc_ids=[])
    calls = 0

    async def same(case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回两个分支完全相同的候选集合。"""

        nonlocal calls
        calls += 1
        return [_candidate(case, "mem-relevant", 0.95)]

    equivalent = await run_session_first([case], same, same, k=1)
    unavailable = await run_session_first(
        [case],
        same,
        same,
        k=1,
        snapshot_available=False,
    )

    assert equivalent.status == "skipped"
    assert equivalent.reason_code == "equivalent_to_baseline"
    assert unavailable.status == "skipped"
    assert unavailable.reason_code == "readonly_snapshot_unavailable"
    assert unavailable.baseline is None
    assert calls == 2


@pytest.mark.asyncio
async def test_safe_report_omits_query_identity_ids_and_exception_text() -> None:
    """报告序列化不得包含查询、身份、文档 ID 或异常原文。"""

    case = _single_case(
        session_id="SESSION-SECRET-CANARY",
        user_id="USER-SECRET-CANARY",
        persona_id="PERSONA-SECRET-CANARY",
    )
    case.query = "QUERY-SECRET-CANARY"
    case.relevant_doc_ids = {"MEMORY-ID-CANARY"}
    case.metadata["critical_long_term_doc_ids"] = ["MEMORY-ID-CANARY"]

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回仅用于内存评分的 canary 文档。"""

        return [{"doc_id": "MEMORY-ID-CANARY", "score": 0.99}]

    async def session(_case: EvaluationCase, _k: int) -> list[Any]:
        """模拟包含敏感原文的会话分支错误。"""

        raise RuntimeError("PROVIDER-SECRET-CANARY")

    report = await run_session_first([case], baseline, session, k=1)
    serialized = json.dumps(asdict(report), ensure_ascii=False)

    for canary in (
        "QUERY-SECRET-CANARY",
        "SESSION-SECRET-CANARY",
        "USER-SECRET-CANARY",
        "PERSONA-SECRET-CANARY",
        "MEMORY-ID-CANARY",
        "PROVIDER-SECRET-CANARY",
    ):
        assert canary not in serialized


def test_preset_rejects_invalid_thresholds() -> None:
    """固定 preset 必须拒绝非有限或越界阈值。"""

    with pytest.raises(ValueError, match="session_score_threshold_invalid"):
        SessionFirstPreset(minimum_score=float("nan"))
    with pytest.raises(ValueError, match="session_margin_threshold_invalid"):
        SessionFirstPreset(minimum_margin=1.1)
