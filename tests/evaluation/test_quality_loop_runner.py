"""质量闭环 runner：配对构建、盲测顺序与阶段证据的回归测试。"""

from __future__ import annotations

import pytest

import core.features.evaluation.application.quality_loop as quality_loop_module
from core.features.evaluation.application.quality_loop import (
    QualityLoopPairError,
    blind_slot_order,
    build_quality_loop_pairs,
    run_quality_loop,
)
from core.features.evaluation.application.retrieval_quality import (
    EvaluationCase,
    RetrievedDocument,
)

_HASH = "f" * 64
_SECRET = b"quality-loop-test-secret-32-bytes!"


def _case(case_id: str, context_key: str, expectation: str, relevant=("mem-1",)):
    return EvaluationCase(
        case_id=case_id,
        query=f"query-{case_id}",
        relevant_doc_ids=set(relevant),
        metadata={"context_key": context_key, "expectation": expectation},
    )


def _retriever_factory(ranked: dict[str, list[str]]):
    """按 case_id 返回固定 ranked doc ids 的同步 retriever。"""

    def retriever(case, k):
        return [
            RetrievedDocument(doc_id=doc_id)
            for doc_id in ranked.get(case.case_id, [])[:k]
        ]

    return retriever


def test_build_pairs_matches_use_and_silence_by_context_key():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
        _case("u2", "ctx-b", "should_use"),
        _case("s2", "ctx-b", "should_silence", relevant={"__no_relevant__"}),
    ]
    pairs, rejects = build_quality_loop_pairs(cases)
    assert len(pairs) == 2
    assert rejects == []


def test_build_pairs_rejects_incomplete_duplicate_and_partial_key():
    incomplete = [
        _case("u1", "ctx-a", "should_use"),
    ]
    _, rejects = build_quality_loop_pairs(incomplete)
    assert "pair_incomplete" in rejects

    duplicate = [
        _case("u1", "ctx-a", "should_use"),
        _case("u1b", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]
    _, rejects = build_quality_loop_pairs(duplicate)
    assert "pair_duplicate" in rejects

    partial = [EvaluationCase("m1", "q", {"mem-1"}, {"context_key": "ctx-missing"})]
    _, rejects = build_quality_loop_pairs(partial)
    assert "context_key_missing" in rejects

    ordinary = [EvaluationCase("ordinary", "q", {"mem-1"}, {})]
    assert build_quality_loop_pairs(ordinary) == ([], [])


def test_blind_slot_order_deterministic_and_seed_sensitive():
    first = blind_slot_order(seed=1, context_key="ctx-a")
    repeat = blind_slot_order(seed=1, context_key="ctx-a")
    orders = {blind_slot_order(seed=seed, context_key="ctx-a") for seed in range(16)}
    assert first == repeat
    assert orders == {
        ("should_use", "should_silence"),
        ("should_silence", "should_use"),
    }


@pytest.mark.asyncio
async def test_final_payload_canary_ignores_short_numeric_document_ids():
    cases = [
        _case("use-1111", "ctx-numeric", "should_use", relevant={"1111"}),
        _case(
            "silence-2222",
            "ctx-numeric",
            "should_silence",
            relevant={"__no_relevant__"},
        ),
    ]
    payload = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"use-1111": ["1111"], "silence-2222": []}),
        k=1,
        seed=1,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    assert payload["pairs"]["should_use_hit_rate"] == 1.0


@pytest.mark.asyncio
async def test_run_quality_loop_reports_stages_and_null_discipline():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]
    retriever = _retriever_factory({"u1": ["mem-1"], "s1": []})
    payload = await run_quality_loop(
        cases,
        retriever=retriever,
        k=5,
        seed=7,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    stages = {stage["stage"]: stage for stage in payload["stages"]}
    assert stages["recall"]["metrics"]["candidate_hit_rate"] == 1.0
    # 无标注输入的阶段保持 degraded + None，不伪装成 0
    assert stages["write"]["state"] == "degraded"
    assert stages["write"]["metrics"]["write_fact_correctness"] is None
    # 未提供端口的阶段 unavailable，不是零值
    assert stages["injection"]["state"] == "unavailable"
    assert stages["lifecycle"]["state"] == "unavailable"
    assert payload["pairs"]["total_pairs"] == 1
    assert payload["pairs"]["should_use_hit_rate"] == 1.0
    assert payload["pairs"]["should_silence_correct_rate"] == 1.0
    manifest = payload["manifest"]
    assert manifest is not None
    assert manifest["seed"] == 7
    assert manifest["pair_count"] == 1


@pytest.mark.asyncio
async def test_run_quality_loop_records_miss_and_negative_hit():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]
    # should_use 未命中（返回不相关），should_silence 注入了噪音（命中）
    retriever = _retriever_factory({"u1": ["mem-x"], "s1": ["mem-noise"]})
    payload = await run_quality_loop(
        cases,
        retriever=retriever,
        k=5,
        seed=7,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    assert payload["pairs"]["should_use_hit_rate"] == 0.0
    assert payload["pairs"]["should_silence_correct_rate"] == 0.0


@pytest.mark.asyncio
async def test_run_quality_loop_without_pairs_degrades_recall():
    payload = await run_quality_loop(
        [],
        retriever=_retriever_factory({}),
        k=5,
        seed=1,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    stages = {stage["stage"]: stage for stage in payload["stages"]}
    assert stages["recall"]["state"] == "degraded"
    assert stages["recall"]["metrics"]["candidate_hit_rate"] is None
    assert payload["manifest"] is None


@pytest.mark.asyncio
async def test_run_quality_loop_retrieval_failure_degrades_not_fakes():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]

    def broken_retriever(case, k):
        raise RuntimeError("boom")

    payload = await run_quality_loop(
        cases,
        retriever=broken_retriever,
        k=5,
        seed=1,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    stages = {stage["stage"]: stage for stage in payload["stages"]}
    assert stages["recall"]["state"] == "degraded"
    assert stages["recall"]["reason"] == "retrieval_failed"
    assert payload["pairs"]["should_use_hit_rate"] is None


@pytest.mark.asyncio
async def test_run_quality_loop_annotated_stages_populate_metrics():
    use = _case("u1", "ctx-a", "should_use")
    silence = _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"})
    use.metadata["annotated_write_fact_correctness"] = 0.9
    silence.metadata["annotated_write_fact_correctness"] = 0.7
    use.metadata["annotated_source_faithfulness"] = 0.8
    use.metadata["annotated_answer_faithfulness"] = 0.6
    payload = await run_quality_loop(
        [use, silence],
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=3,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    stages = {stage["stage"]: stage for stage in payload["stages"]}
    assert stages["write"]["metrics"]["write_fact_correctness"] == 0.8
    assert stages["write"]["state"] == "available"
    assert stages["source"]["metrics"]["source_faithfulness"] == 0.8
    assert stages["expression"]["metrics"]["annotated_answer_faithfulness"] == 0.6
    # relevancy 无标注保持 None
    assert stages["expression"]["metrics"]["annotated_answer_relevancy"] is None


@pytest.mark.asyncio
async def test_run_quality_loop_manifest_binds_actual_pairs():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]
    first = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=5,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    second = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=5,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    assert first["manifest"]["manifest_hash"] == second["manifest"]["manifest_hash"]
    changed = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=6,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    assert changed["manifest"]["manifest_hash"] != first["manifest"]["manifest_hash"]


@pytest.mark.asyncio
async def test_run_quality_loop_payload_has_no_query_or_context_key():
    cases = [
        _case("u1", "secret-context", "should_use"),
        _case("s1", "secret-context", "should_silence", relevant={"__no_relevant__"}),
    ]
    payload = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=1,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        context_key_secret=_SECRET,
    )
    dumped = repr(payload)
    assert "query-u1" not in dumped
    assert "secret-context" not in dumped


@pytest.mark.asyncio
async def test_injection_and_lifecycle_ports_feed_stages():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]

    class _InjectionPort:
        def read_pair_rates(self):
            return {
                "final_injected_hit_rate": 0.75,
                "negative_injection_rate": 0.1,
                "configured_budget_chars": 2000,
                "effective_budget_chars": 1500,
                "rogue": 1,
            }

    class _LifecyclePort:
        def read_counts(self):
            return {
                "retrieved_count": 10,
                "injected_count": 8,
                "observed_p50_latency_ms": 12.5,
                "observed_p95_latency_ms": 40.0,
            }

    payload = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=1,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        injection_port=_InjectionPort(),
        context_key_secret=_SECRET,
        lifecycle_port=_LifecyclePort(),
    )
    stages = {stage["stage"]: stage for stage in payload["stages"]}
    assert stages["injection"]["metrics"]["final_injected_hit_rate"] == 0.75
    assert "rogue" not in stages["injection"]["metrics"]
    assert stages["lifecycle"]["metrics"]["retrieved_count"] == 10


@pytest.mark.asyncio
async def test_failing_ports_report_unavailable_not_zero():
    cases = [
        _case("u1", "ctx-a", "should_use"),
        _case("s1", "ctx-a", "should_silence", relevant={"__no_relevant__"}),
    ]

    class _BrokenPort:
        def read_pair_rates(self):
            raise RuntimeError("down")

        def read_counts(self):
            raise RuntimeError("down")

    payload = await run_quality_loop(
        cases,
        retriever=_retriever_factory({"u1": ["mem-1"], "s1": []}),
        k=5,
        seed=1,
        code_revision="8f0681e",
        config_hash=_HASH,
        schema_hash=_HASH,
        injection_port=_BrokenPort(),
        context_key_secret=_SECRET,
        lifecycle_port=_BrokenPort(),
    )
    stages = {stage["stage"]: stage for stage in payload["stages"]}
    assert stages["injection"]["state"] == "unavailable"
    assert stages["lifecycle"]["state"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "query",
        "context",
        "case_id",
        "relevant_id",
        "identity",
        "scope",
        "provider_secret",
    ],
)
async def test_final_payload_canary_rejects_fixture_value_leaks(monkeypatch, source):
    canary = f"FINAL-{source.upper()}-CANARY-91"
    use = _case("use-case", "ctx-final", "should_use")
    silence = _case(
        "silence-case", "ctx-final", "should_silence", relevant={"__no_relevant__"}
    )
    if source == "query":
        use.query = canary
    elif source == "context":
        use.metadata["context_key"] = canary
        silence.metadata["context_key"] = canary
    elif source == "case_id":
        use.case_id = canary
    elif source == "relevant_id":
        use.relevant_doc_ids = {canary}
    elif source == "identity":
        use.metadata["identity"] = canary
    elif source == "scope":
        use.metadata["scope_key"] = canary
    else:
        use.metadata["provider_secret"] = canary

    original = quality_loop_module.stage_read_to_payload

    def leaking_stage_payload(read):
        return {**original(read), "diagnostic": canary}

    monkeypatch.setattr(
        quality_loop_module, "stage_read_to_payload", leaking_stage_payload
    )
    with pytest.raises(QualityLoopPairError) as error:
        await run_quality_loop(
            [use, silence],
            retriever=_retriever_factory({"use-case": ["mem-1"], "silence-case": []}),
            k=5,
            seed=1,
            code_revision="8f0681e",
            config_hash=_HASH,
            schema_hash=_HASH,
            context_key_secret=_SECRET,
        )
    assert error.value.code == "privacy_canary_failed"
    assert canary not in str(error.value)


@pytest.mark.asyncio
async def test_quality_loop_rejects_missing_or_invalid_hmac_secret():
    cases = [
        _case("use-case", "ctx-secret", "should_use"),
        _case(
            "silence-case", "ctx-secret", "should_silence", relevant={"__no_relevant__"}
        ),
    ]
    for secret in (None, b"too-short"):
        with pytest.raises(QualityLoopPairError) as error:
            await run_quality_loop(
                cases,
                retriever=_retriever_factory({}),
                k=5,
                seed=1,
                code_revision="8f0681e",
                config_hash=_HASH,
                schema_hash=_HASH,
                context_key_secret=secret,  # type: ignore[arg-type]
            )
        assert error.value.code == "hmac_secret_unavailable"
