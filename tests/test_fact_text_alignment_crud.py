"""CAS 正文更新的「事实文本单 owner」行为（C5/R4.2）。

覆盖 09-20-fact-text-ownership 的写入收敛：CAS 正文更新后 canonical 行不得留下
与正文矛盾的事实表示；提供对齐事实时正常写入，提供不一致事实时以稳定原因码
``fact_evidence_mismatch`` 拒绝且不改 canonical。图抽取与注入都直接消费
``key_facts``/``fact_source_evidence``，因此这些字段是否残留就是两条读取路径
的可观察前提。
"""

from __future__ import annotations

import json
import logging

import pytest

from tests.fact_evidence_helpers import source_evidence
from tests.representation_migration_support import (
    _create_schema,
    _DocumentStorage,
    _engine_with_real_cas,
    _read_row,
    _seed,
)


def _fact_row(
    memory_id: int,
    content: str,
    facts: list[str],
    *,
    summary: str | None = None,
) -> tuple[object, ...]:
    """历史/当前表示的单行 canonical 事实行。"""

    return (
        memory_id,
        content,
        {
            "importance": 0.5,
            "session_id": "s1",
            "privacy_level": "confidential",
            "key_facts": list(facts),
            "fact_source_evidence": [
                source_evidence(fact, message_id=index + 1, message_seq=index + 1)
                for index, fact in enumerate(facts)
            ],
            "canonical_summary": summary if summary is not None else content,
            "persona_summary": content,
            "summary_schema_version": "v2",
        },
        "rev-1-created",
        "rev-1",
    )


@pytest.mark.asyncio
async def test_cas_content_update_without_facts_clears_contradicting_facts(
    tmp_path,
) -> None:
    """未提供事实且旧事实已不在新正文中时必须清除，正文与摘要保持一致。"""

    db_path = str(tmp_path / "cas-facts-cleared.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        assert (
            await engine.update_memory(
                1, {"content": "用户改喝拿铁"}, expected_revision="rev-1"
            )
            is True
        )

        row = await _read_row(db_path, 1)
        assert row is not None
        assert row["text"] == "用户改喝拿铁"
        metadata = json.loads(row["metadata"])
        assert "key_facts" not in metadata
        assert "fact_source_evidence" not in metadata
        assert metadata["canonical_summary"] == "用户改喝拿铁"
        assert metadata["persona_summary"] == "用户喜欢咖啡"
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_content_update_rejects_provided_misaligned_facts(tmp_path) -> None:
    """调用方提供的事实与正文不一致时拒绝写入，canonical 保持原样。"""

    db_path = str(tmp_path / "cas-facts-rejected.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        before = await _read_row(db_path, 1)
        assert (
            await engine.update_memory(
                1,
                {
                    "content": "用户改喝拿铁",
                    "metadata": {
                        "key_facts": ["用户喜欢咖啡"],
                        "fact_source_evidence": [source_evidence("用户喜欢咖啡")],
                    },
                },
                expected_revision="rev-1",
            )
            is False
        )

        assert engine.get_last_write_reason_code() == "fact_evidence_mismatch"
        assert await _read_row(db_path, 1) == before
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_content_update_rejects_incomplete_fact_pair(tmp_path) -> None:
    """只提供事实、没有逐事实证据时按「提供但不对齐」fail closed，不静默清空。"""

    db_path = str(tmp_path / "cas-facts-partial.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "用户喜欢咖啡",
                {"importance": 0.5, "memory_status": "active"},
                "rev-1-created",
                "rev-1",
            )
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        before = await _read_row(db_path, 1)
        assert (
            await engine.update_memory(
                1,
                {
                    "content": "用户喜欢咖啡",
                    "metadata": {"key_facts": ["用户喜欢咖啡"]},
                },
                expected_revision="rev-1",
            )
            is False
        )

        assert engine.get_last_write_reason_code() == "fact_evidence_mismatch"
        assert await _read_row(db_path, 1) == before
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_content_update_writes_provided_aligned_facts(tmp_path) -> None:
    """提供与正文对齐的事实与证据时按提供值写入。"""

    db_path = str(tmp_path / "cas-facts-aligned.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        facts = ["用户改喝拿铁", "用户不加糖"]
        assert (
            await engine.update_memory(
                1,
                {
                    "content": "用户改喝拿铁；用户不加糖",
                    "metadata": {
                        "key_facts": facts,
                        "fact_source_evidence": [
                            source_evidence(
                                fact, message_id=index + 1, message_seq=index + 1
                            )
                            for index, fact in enumerate(facts)
                        ],
                        "canonical_summary": "用户改喝拿铁；用户不加糖",
                    },
                },
                expected_revision="rev-1",
            )
            is True
        )

        row = await _read_row(db_path, 1)
        assert row is not None
        metadata = json.loads(row["metadata"])
        assert metadata["key_facts"] == facts
        assert len(metadata["fact_source_evidence"]) == len(facts)
        assert metadata["canonical_summary"] == "用户改喝拿铁；用户不加糖"
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_content_update_keeps_facts_still_present_in_new_body(
    tmp_path,
) -> None:
    """旧事实仍属于新正文时保留原表示（v2 表示与表示迁移都依赖这一口径）。"""

    db_path = str(tmp_path / "cas-facts-retained.db")
    await _create_schema(db_path)
    await _seed(
        db_path, [_fact_row(1, "用户喜欢咖啡；用户常去咖啡馆", ["用户喜欢咖啡"])]
    )
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        assert (
            await engine.update_memory(
                1,
                {"content": "用户喜欢咖啡；用户改去茶馆"},
                expected_revision="rev-1",
            )
            is True
        )

        row = await _read_row(db_path, 1)
        assert row is not None
        metadata = json.loads(row["metadata"])
        assert metadata["key_facts"] == ["用户喜欢咖啡"]
        assert len(metadata["fact_source_evidence"]) == 1
        # 正文已变化：摘要必须与正文同步，不得残留与新正文矛盾的旧摘要。
        assert metadata["canonical_summary"] == "用户喜欢咖啡；用户改去茶馆"
    finally:
        await storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provided",
    [
        {"key_facts": ["用户改喝拿铁"]},
        {"fact_source_evidence": [source_evidence("用户改喝拿铁")]},
    ],
    ids=["facts_only", "evidence_only"],
)
async def test_cas_content_update_rejects_fact_pair_borrowing_old_side(
    tmp_path, provided
) -> None:
    """只提供事实或只提供证据时不得借用旧表示的另一侧拼出「对齐」表示。"""

    db_path = str(tmp_path / "cas-facts-partial-row.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        before = await _read_row(db_path, 1)
        assert (
            await engine.update_memory(
                1,
                {"content": "用户改喝拿铁", "metadata": dict(provided)},
                expected_revision="rev-1",
            )
            is False
        )

        assert engine.get_last_write_reason_code() == "fact_evidence_mismatch"
        assert await _read_row(db_path, 1) == before
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_content_update_syncs_summary_with_provided_aligned_facts(
    tmp_path,
) -> None:
    """提供对齐事实但不提供摘要时，旧摘要必须与新正文同步而非残留。"""

    db_path = str(tmp_path / "cas-summary-synced.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        new_body = "用户改喝拿铁"
        assert (
            await engine.update_memory(
                1,
                {
                    "content": new_body,
                    "metadata": {
                        "key_facts": [new_body],
                        "fact_source_evidence": [source_evidence(new_body)],
                    },
                },
                expected_revision="rev-1",
            )
            is True
        )

        row = await _read_row(db_path, 1)
        assert row is not None
        assert row["text"] == new_body
        metadata = json.loads(row["metadata"])
        assert metadata["key_facts"] == [new_body]
        assert metadata["canonical_summary"] == new_body
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_content_update_overrides_explicit_stale_summary(
    tmp_path,
) -> None:
    """显式提供与新正文不一致的旧摘要时，摘要同步为新正文而非提交矛盾值。"""

    db_path = str(tmp_path / "cas-explicit-summary.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        new_body = "用户改喝拿铁"
        assert (
            await engine.update_memory(
                1,
                {
                    "content": new_body,
                    "metadata": {
                        "key_facts": [new_body],
                        "fact_source_evidence": [source_evidence(new_body)],
                        "canonical_summary": "用户喜欢咖啡",
                    },
                },
                expected_revision="rev-1",
            )
            is True
        )

        row = await _read_row(db_path, 1)
        assert row is not None
        assert row["text"] == new_body
        metadata = json.loads(row["metadata"])
        assert metadata["canonical_summary"] == new_body
        assert metadata["key_facts"] == [new_body]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_cas_fact_mismatch_log_has_no_ids_or_sensitive_values(
    tmp_path, caplog
) -> None:
    """拒绝写入的告警只留稳定原因码：不出现 memory_id、正文、事实或 revision。"""

    db_path = str(tmp_path / "cas-facts-log.db")
    await _create_schema(db_path)
    await _seed(db_path, [_fact_row(1, "用户喜欢咖啡", ["用户喜欢咖啡"])])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    try:
        caplog.set_level(logging.WARNING)
        assert (
            await engine.update_memory(
                1,
                {
                    "content": "用户改喝拿铁",
                    "metadata": {
                        "key_facts": ["用户喜欢咖啡"],
                        "fact_source_evidence": [source_evidence("用户喜欢咖啡")],
                    },
                },
                expected_revision="rev-1",
            )
            is False
        )
    finally:
        await storage.close()

    assert "fact_evidence_mismatch" in caplog.text
    assert "memory_id" not in caplog.text
    assert "用户喜欢咖啡" not in caplog.text
    assert "用户改喝拿铁" not in caplog.text
    assert "rev-1" not in caplog.text
