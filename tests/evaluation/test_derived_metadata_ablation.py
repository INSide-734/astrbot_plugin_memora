"""有限派生元数据 process-local 索引与离线消融。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from core.evaluation.derived_metadata_ablation import (
    RunLocalDerivedMetadataIndex,
    run_derived_metadata_ablation,
)
from core.evaluation.retrieval_quality import EvaluationCase
from core.models.derived_metadata import (
    DerivedMetadataProposal,
    DerivedMetadataSourceRef,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "retrieval"
    / "derived_metadata.jsonl"
)


def _source_ref(memory_id: int, revision: str = "rev-1") -> DerivedMetadataSourceRef:
    """构造匿名 source reference。"""

    return DerivedMetadataSourceRef(
        memory_id=memory_id,
        revision_token=revision,
        trusted_scope="private:synthetic",
        privacy_level="shared",
        source_role="user",
        extractor_version="fixture-v1",
    )


def _source(
    memory_id: int,
    *,
    revision: str = "rev-1",
    doc_id: str | None = None,
    score: float = 0.2,
    deleted: bool = False,
) -> dict[str, Any]:
    """构造只读 canonical source 视图。"""

    return {
        "doc_id": doc_id or f"mem-{memory_id}",
        "score": score,
        "revision_token": revision,
        "trusted_scope": "private:synthetic",
        "privacy_level": "shared",
        "source_role": "user",
        "valid": True,
        "deleted": deleted,
    }


def _case(case_id: str, query: str, relevant: str) -> EvaluationCase:
    """构造带可信可见性上下文的匿名用例。"""

    return EvaluationCase(
        case_id=case_id,
        query=query,
        relevant_doc_ids={relevant},
        metadata={
            "dataset": "derived_metadata",
            "scope": "private:synthetic",
            "privacy_level": "shared",
            "role": "user",
            "metadata_dependent": True,
            "annotated_baseline_latency_ms": 5.0,
            "annotated_variant_latency_ms": 5.5,
        },
    )


def test_index_accepts_only_valid_annotations_and_rebuilds_idempotently() -> None:
    """索引只接收 validator 结果，重复 source/revision 不增加可见条目。"""

    sources = {7: _source(7, doc_id="mem-coffee")}
    index = RunLocalDerivedMetadataIndex(lambda memory_id: sources.get(memory_id))
    valid = DerivedMetadataProposal(source=_source_ref(7), keywords=("咖啡",))
    unsafe = DerivedMetadataProposal(
        source=_source_ref(7), keywords=("https://bad.invalid",)
    )

    first = index.add_proposal(valid)
    replay = index.add_proposal(valid)
    rejected = index.add_proposal(unsafe)
    summary = index.summary()

    assert first.accepted is True
    assert replay.accepted is True
    assert rejected.reason_code == "annotation_prompt_like_rejected"
    assert summary.accepted_count == 1
    assert summary.rejected_count == 1
    assert summary.successful_match_count == 0

    index.rebuild([valid])
    rebuilt = index.summary()
    assert rebuilt.accepted_count == 1
    assert rebuilt.rejected_count == 0


def test_match_revalidates_revision_visibility_and_validity() -> None:
    """stale、删除、跨作用域和失效 source 只能丢弃 annotation。"""

    sources: dict[int, dict[str, Any] | None] = {
        7: _source(7, doc_id="mem-valid"),
        8: _source(8, revision="rev-new", doc_id="mem-stale"),
        9: _source(9, doc_id="mem-deleted", deleted=True),
        10: {**_source(10, doc_id="mem-private"), "privacy_level": "confidential"},
        11: {**_source(11, doc_id="mem-expired"), "valid": False},
    }
    index = RunLocalDerivedMetadataIndex(lambda memory_id: sources.get(memory_id))
    proposals = [
        DerivedMetadataProposal(source=_source_ref(7), keywords=("有效",)),
        DerivedMetadataProposal(source=_source_ref(8, "rev-old"), keywords=("陈旧",)),
        DerivedMetadataProposal(source=_source_ref(9), keywords=("删除",)),
        DerivedMetadataProposal(source=_source_ref(10), keywords=("私密",)),
        DerivedMetadataProposal(source=_source_ref(11), keywords=("过期",)),
    ]
    index.rebuild(proposals)

    matches = index.match(
        "有效 陈旧 删除 私密 过期",
        {"scope": "private:synthetic", "privacy_level": "shared", "role": "user"},
    )
    summary = index.summary()

    assert [item.memory_id for item in matches] == [7]
    assert summary.stale_count == 4
    assert summary.reason_code in {
        "source_revision_mismatch",
        "source_visibility_mismatch",
    }


def test_index_caps_source_signal_and_does_not_echo_query_or_source() -> None:
    """一个 source 多字段命中需封顶，安全摘要不能包含敏感值。"""

    source = _source(7, doc_id="MEMORY-ID-CANARY", score=0.9)
    index = RunLocalDerivedMetadataIndex(lambda _memory_id: source)
    index.add_proposal(
        DerivedMetadataProposal(
            source=_source_ref(7),
            keywords=("咖啡", "饮品"),
            topic_tags=("咖啡",),
            context_labels=("preference",),
        )
    )
    matches = index.match(
        "QUERY-SECRET-CANARY 咖啡 饮品 preference",
        {"scope": "private:synthetic", "privacy_level": "shared", "role": "user"},
    )
    serialized = json.dumps(asdict(index.summary()), ensure_ascii=False)

    assert matches[0].signal <= 0.2
    assert "QUERY-SECRET-CANARY" not in serialized
    assert "MEMORY-ID-CANARY" not in serialized


@pytest.mark.asyncio
async def test_ablation_improves_metadata_dependent_slice_without_online_cost() -> None:
    """有限信号可补充 metadata-dependent canonical 候选，且不增加 Provider 成本。"""

    case = _case("derived-case", "用户喜欢咖啡", "mem-derived-coffee")
    sources = {
        7: _source(7, doc_id="mem-derived-coffee", score=0.1),
    }
    proposal = DerivedMetadataProposal(source=_source_ref(7), keywords=("咖啡",))

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回不含目标 canonical 候选的 baseline。"""

        return [{"doc_id": "mem-noise", "score": 0.1}]

    report = await run_derived_metadata_ablation(
        [case],
        baseline,
        [proposal],
        lambda memory_id: sources.get(memory_id),
        k=1,
        expected_annotation_keys={(7, "rev-1")},
    )

    assert report.status == "completed"
    assert report.baseline.recall_at_k == 0.0
    assert report.bounded_variant.recall_at_k == 1.0
    assert report.metadata_dependent_recall_delta == 1.0
    assert report.macro_precision == 1.0
    assert report.bounded_variant.observed_provider_calls is None
    assert report.bounded_variant.observed_token_cost is None
    assert report.bounded_variant.annotated_p50_latency_ms == 5.5


@pytest.mark.asyncio
async def test_ablation_stale_annotation_preserves_baseline_and_failure_is_stable() -> (
    None
):
    """stale 或普通 index 异常不能删除 baseline canonical 命中。"""

    case = _case("stale-case", "陈旧内容", "mem-canonical")
    stale = DerivedMetadataProposal(
        source=_source_ref(7, "rev-old"), keywords=("陈旧",)
    )

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回 canonical baseline 命中。"""

        return [{"doc_id": "mem-canonical", "score": 0.8}]

    report = await run_derived_metadata_ablation(
        [case],
        baseline,
        [stale],
        lambda _memory_id: _source(7, revision="rev-new", doc_id="mem-canonical"),
        k=1,
    )

    assert report.bounded_variant.recall_at_k == 1.0
    assert report.stale_count == 1
    assert report.reason_code == "source_revision_mismatch"

    def broken_loader(_memory_id: int) -> Mapping[str, Any] | None:
        """模拟不回显原文的 source loader 异常。"""

        raise RuntimeError("PROVIDER-SECRET-CANARY")

    broken = await run_derived_metadata_ablation(
        [case],
        baseline,
        [DerivedMetadataProposal(source=_source_ref(7), keywords=("陈旧",))],
        broken_loader,
        k=1,
    )
    serialized = json.dumps(asdict(broken), ensure_ascii=False)
    assert broken.bounded_variant.recall_at_k == broken.baseline.recall_at_k
    assert broken.reason_code == "variant_execution_failed"
    assert "PROVIDER-SECRET-CANARY" not in serialized


@pytest.mark.asyncio
async def test_index_cancellation_propagates() -> None:
    """source loader 的取消必须传播，不能被当成普通降级。"""

    index = RunLocalDerivedMetadataIndex(
        lambda _memory_id: (_ for _ in ()).throw(asyncio.CancelledError())
    )
    index.add_proposal(
        DerivedMetadataProposal(source=_source_ref(7), keywords=("取消",))
    )

    with pytest.raises(asyncio.CancelledError):
        index.match(
            "取消",
            {"scope": "private:synthetic", "privacy_level": "shared", "role": "user"},
        )
