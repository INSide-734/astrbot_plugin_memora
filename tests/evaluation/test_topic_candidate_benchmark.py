"""Topic 候选回放：真实 canonical oracle、K24 和失败状态。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace

import pytest
import pytest_asyncio

from core.features.evaluation.application.topic_candidate_evidence import TOP_K_VALUES
from core.features.evaluation.application.topic_candidate_replay import (
    ReplayConfig,
    batch_replay,
    create_synthetic_windows,
    create_temp_catalog,
    replay_window,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.recall.processors.conversation_formatter import ConversationFormatter
from core.features.recall.processors.text_processor import TextProcessor
from core.features.reflection.application.topic_candidate_selector import (
    TopicCandidateSelector,
)
from core.features.reflection.domain.topic_label_renderer import render_topic_labels


def _document(identifier, labels, **metadata):
    """构造可被生产 canonical 来源校验读取的文档。"""
    return {
        "id": identifier,
        "text": "合成历史，不来自生产",
        "metadata": {
            "topics": labels,
            "scope_key": "scope-a",
            "chat_type": "private",
            "privacy_level": "shared",
            "resolver_revision": "resolver-1",
            "source_provenance_complete": True,
            **metadata,
        },
    }


@pytest_asyncio.fixture
async def catalog():
    """80 个真实安全标签及确实存在但不可复用的负向来源。"""
    documents = [_document(index + 1, [f"话题 {index:03}"]) for index in range(80)]
    documents.extend(
        [
            _document(81, ["禁止来源"], source_provenance_complete=False),
            _document(82, ["其他作用域"], scope_key="scope-other"),
            _document(83, ["已归档来源"], status="archived"),
        ]
    )
    path, db = await create_temp_catalog(documents)
    store = TopicCatalogStore(db)
    selector = TopicCandidateSelector(store, TextProcessor(), ConversationFormatter())
    try:
        yield db, store, selector
    finally:
        await db.close()
        path.unlink(missing_ok=True)
        path.parent.rmdir()


def _windows(**scenario):
    """固定 scope 的独立合成来源窗口。"""
    return create_synthetic_windows(
        [
            {
                "bucket": "medium",
                "chat_type": "private",
                "scope_key": "scope-a",
                "privacy_level": "shared",
                "resolver_revision": "resolver-1",
                "semantic_clusters": ["话题 000"],
                "fragmentation_count": 0,
                "negative_topics": ["禁止来源", "已归档来源", "其他作用域"],
                "messages": [{"role": "user", "content": "讨论 话题 000 禁止来源"}],
                **scenario,
            }
        ]
    )


def _configs():
    """非 oracle 的每个 K 均在真实生产约束内构造。"""
    common = ReplayConfig(
        mode="full",
        activation_threshold=3,
        fixed_k=4,
        max_full_topics=24,
        max_full_prompt_tokens=2000,
        max_query_chars=2000,
        overfetch_factor=3,
        metrics_retention_days=30,
    )
    return {
        "off": replace(common, mode="off"),
        "strict_full": common,
        **{
            f"top_k_{k}": replace(common, mode="top_k", fixed_k=k) for k in TOP_K_VALUES
        },
    }


@pytest.mark.asyncio
async def test_real_replay_pairs_all_k_and_runs_k24(catalog):
    """每个 K 真正完成选择，不以存在 ReplayCase 或吞异常作为成功。"""
    _, store, selector = catalog
    cases = await batch_replay(_windows(), selector, _configs(), catalog_store=store)
    by_variant = {case.mode: case for case in cases}
    assert set(by_variant) == {
        "off",
        "strict_full",
        *(f"top_k_{k}" for k in TOP_K_VALUES),
    }
    assert all(case.execution_status == "success" for case in cases)
    assert by_variant["top_k_24"].record.candidate_count == 24
    assert by_variant["top_k_24"].reason_code == "top_k_success"
    assert by_variant["strict_full"].record.candidate_count == 80
    assert all(case.record.catalog_topic_count == 80 for case in cases)
    assert len({case.record.source_window_digest for case in cases}) == 1
    assert by_variant["off"].record.exact_topic_count is None
    assert by_variant["off"].rendered_prompt == ""
    assert all(case.record.safety_violation_count == 0 for case in cases)


@pytest.mark.asyncio
async def test_strict_full_reads_canonical_when_derived_index_is_missing(catalog):
    """oracle 独立于派生目录；相同目录缺陷不能让 full 与 K 一起假正确。"""
    db, store, selector = catalog
    await db.execute("DELETE FROM memory_topic_sources")
    await db.commit()
    configs = _configs()
    cases = await replay_window(
        _windows()[0],
        selector,
        {
            "strict_full": configs["strict_full"],
            "top_k_4": configs["top_k_4"],
        },
        catalog_store=store,
    )
    full, candidate = cases
    expected = tuple(f"话题 {index:03}" for index in range(80))
    rendered = render_topic_labels(
        expected, max_total_tokens=0, max_labels=80, max_chars=10000
    )
    assert full.execution_status == "success"
    assert full.rendered_prompt == rendered.rendered_block
    assert full.record.candidate_count == 80
    assert candidate.execution_status == "degraded"
    assert candidate.record.exact_topic_count is None
    assert candidate.record.candidate_count == 0


@pytest.mark.asyncio
async def test_strict_full_is_unbounded_but_observe_remains_bounded(catalog):
    """oracle 渲染完整 80 个标签；线上 full/observe 预算仍真实拒绝越界。"""
    _, store, selector = catalog
    configs = _configs()
    cases = await replay_window(
        _windows()[0],
        selector,
        {
            "strict_full": replace(
                configs["strict_full"], max_full_topics=4, max_full_prompt_tokens=50
            ),
            "observe": replace(configs["strict_full"], mode="observe"),
        },
        catalog_store=store,
    )
    full, observe = cases
    assert full.record.candidate_count == 80
    assert '"话题 079"' in full.rendered_prompt
    assert observe.execution_status == "degraded"
    assert observe.rendered_prompt == ""
    assert observe.record.candidate_count == 0
    assert "strict_full_success" == full.reason_code


@pytest.mark.asyncio
async def test_raw_annotations_cannot_become_provider_measurements(catalog):
    """场景的 Provider 数字仅是原始注解；没有实际调用时 usage/时延均为空。"""
    _, store, selector = catalog
    windows = _windows(provider_durations_ms={"strict_full": 40, "top_k_24": 20})
    cases = await batch_replay(windows, selector, _configs(), catalog_store=store)
    for case in cases:
        assert case.record.provider_duration_ms is None
        assert case.record.estimated_tokens is None
        assert case.record.token_availability == "unavailable"
        payload = json.dumps(asdict(case.record), ensure_ascii=False)
        assert "scope-a" not in payload
        assert "禁止来源" not in payload
        assert "话题 000" not in payload


@pytest.mark.asyncio
async def test_invalid_k_is_error_not_privacy_failure_or_success(catalog):
    """真实配置拒绝网格以外 K，不再 model_construct 绕过验证。"""
    _, store, selector = catalog
    config = replace(_configs()["top_k_24"], fixed_k=25, max_full_topics=25)
    cases = await replay_window(
        _windows()[0], selector, {"top_k_24": config}, catalog_store=store
    )
    case = cases[0]
    assert case.execution_status == case.record.execution_status == "error"
    assert not case.privacy_reject
    assert case.record.exact_topic_count is None
    assert case.record.candidate_count == 0
    assert case.rendered_prompt == ""


@pytest.mark.asyncio
async def test_catalog_generation_is_measured_not_hardcoded(catalog):
    """独立 oracle 记录当前 canonical generation，不伪造固定 generation=1。"""
    db, store, selector = catalog
    await db.execute("UPDATE topic_catalog_state SET active_generation=7 WHERE id=1")
    await db.commit()
    cases = await replay_window(
        _windows()[0],
        selector,
        {"strict_full": _configs()["strict_full"]},
        catalog_store=store,
    )
    assert cases[0].execution_status == "success"
    assert cases[0].record.catalog_generation == 7
    assert cases[0].record.catalog_topic_count == 80


@pytest.mark.asyncio
async def test_cancellation_is_not_an_error_record(catalog, monkeypatch):
    """取消必须向上传播，不能被回放降级捕获并继续其它变体。"""
    _, store, selector = catalog

    async def cancel(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(selector, "select_candidates", cancel)
    with pytest.raises(asyncio.CancelledError):
        await replay_window(
            _windows()[0],
            selector,
            {"top_k_4": _configs()["top_k_4"]},
            catalog_store=store,
        )
