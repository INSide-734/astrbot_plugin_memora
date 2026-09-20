"""再巩固 canonical revision、metadata 与恢复边界回归测试。"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.memory_engine import MemoryEngine
from core.features.reconsolidation.application.reconsolidation import (
    ReconsolidationManager,
)
from core.features.reconsolidation.infrastructure.reconsolidation_store import (
    ReconsolidationStore,
)
from tests.fact_evidence_helpers import fact_evidence


def _memory(
    content: str,
    *,
    revision: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造带稳定 revision 与独立 metadata 副本的 canonical 读取结果。"""

    return {
        "id": 7,
        "text": content,
        "updated_at": revision,
        "metadata": deepcopy(metadata or {}),
    }


class _CanonicalHarness:
    """模拟会推进 revision 的最小 canonical CAS 存储。"""

    def __init__(self) -> None:
        """初始化原始记忆及写入观测状态。"""

        self.memory = _memory(
            "原始记忆正文",
            revision="r-7",
            metadata={"access_count": 8, "scope_key": "private:user-a"},
        )
        self.write_count = 0
        self._last_write_reason_code: str | None = None

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """返回 canonical 当前快照；未知 ID 返回 None。"""

        if memory_id != 7:
            return None
        return deepcopy(self.memory)

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
        *,
        expected_revision: str | None = None,
    ) -> bool:
        """按 revision CAS 原子合并正文与 metadata，并推进测试 revision。"""

        self._last_write_reason_code = None
        if memory_id != 7:
            self._last_write_reason_code = "source_not_found"
            return False
        if expected_revision != self.memory["updated_at"]:
            self._last_write_reason_code = "source_revision_mismatch"
            return False
        metadata = deepcopy(self.memory["metadata"])
        metadata.update(deepcopy(updates.get("metadata") or {}))
        self.write_count += 1
        next_revision = f"r-{7 + self.write_count}"
        metadata["updated_at"] = next_revision
        self.memory = _memory(
            str(updates.get("content", self.memory["text"])),
            revision=next_revision,
            metadata=metadata,
        )
        return True

    def get_last_write_reason_code(self) -> str | None:
        """返回最近一次 canonical 写入的稳定原因码。"""

        return self._last_write_reason_code


async def _applied_candidate(
    store: ReconsolidationStore,
    canonical: _CanonicalHarness,
) -> tuple[ReconsolidationManager, str]:
    """通过公开 Manager 路径生成并批准一条候选。"""

    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=canonical.get_memory,
        update_memory_cb=canonical.update_memory,
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="近期上下文")
    assert proposed is not None
    applied = await manager.apply_candidate(
        proposed["candidate_id"],
        canonical.update_memory,
    )
    assert applied["applied"] is True
    return manager, str(proposed["candidate_id"])


@pytest.mark.asyncio
async def test_real_engine_apply_atomically_writes_payload_metadata_and_revision(
    tmp_path: Path,
) -> None:
    """真实引擎 CAS 必须同时落正文、再巩固 metadata 与 apply 后 revision。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    old_metadata = {"access_count": 8, "scope_key": "private:user-a"}
    captured_metadata: dict[str, Any] = {}
    read_count = 0

    async def read_memory(memory_id: int) -> dict[str, Any] | None:
        """依次返回提案、CAS 前和 CAS 后的 canonical 快照。"""

        nonlocal read_count
        assert memory_id == 7
        read_count += 1
        if read_count <= 2:
            return _memory(
                "原始记忆正文",
                revision="r-7",
                metadata=old_metadata,
            )
        return _memory(
            "修正后的记忆正文内容",
            revision="r-8",
            metadata=captured_metadata,
        )

    async def update_content(
        memory_id: int,
        content: str,
        metadata: dict[str, Any],
        expected_revision: str,
    ) -> bool:
        """捕获真实引擎交给 canonical 层的原子正文与 metadata payload。"""

        assert (memory_id, content, expected_revision) == (
            7,
            "修正后的记忆正文内容",
            "r-7",
        )
        captured_metadata.update(deepcopy(metadata))
        return True

    engine.get_memory = read_memory  # type: ignore[method-assign]
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.update_content_if_revision = update_content
    engine.graph_memory_manager = None
    engine._invalidate_evolution_after_revision = AsyncMock()
    engine._schedule_evolution_after_write = AsyncMock()
    engine._retrieval.invalidate_cache = MagicMock()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=engine.get_memory,
        update_memory_cb=engine.update_memory,
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="近期上下文")
    assert proposed is not None

    result = await manager.apply_candidate(
        proposed["candidate_id"],
        engine.update_memory,
    )

    assert result["applied"] is True
    assert captured_metadata["scope_key"] == "private:user-a"
    assert captured_metadata["reconsolidation_count"] == 1
    assert isinstance(captured_metadata["last_reconsolidated_at"], float)
    persisted = await store.get_candidate(proposed["candidate_id"])
    assert persisted is not None
    assert persisted["applied_revision"] == "r-8"


_OLD_CONTENT = "原始记忆正文"
_OLD_FACT = "用户偏好手冲咖啡"


class _EngineHarness:
    """维护 canonical 快照的真实引擎夹具，捕获引擎交给 canonical 层的写入。"""

    def __init__(
        self,
        *,
        content: str,
        revision: str,
        metadata: dict[str, Any],
    ) -> None:
        """按给定时点装配真实引擎，其余写入副作用以最小替身隔离。"""

        self.engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        self.memory = _memory(content, revision=revision, metadata=metadata)
        self.stored_metadata: dict[str, Any] = {}
        self.cleared_keys: tuple[str, ...] = ()
        self.write_count = 0
        self.engine.get_memory = self.get_memory  # type: ignore[method-assign]
        self.engine.hybrid_retriever = MagicMock()
        self.engine.hybrid_retriever.update_content_if_revision = self.update_content
        self.engine.graph_memory_manager = None
        self.engine._invalidate_evolution_after_revision = AsyncMock()
        self.engine._schedule_evolution_after_write = AsyncMock()
        self.engine._retrieval.invalidate_cache = MagicMock()

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """返回当前 canonical 快照；未知 ID 返回 None。"""

        return deepcopy(self.memory) if memory_id == 7 else None

    async def update_content(
        self,
        memory_id: int,
        content: str,
        metadata: dict[str, Any],
        expected_revision: str,
        *,
        drop_metadata_keys: Sequence[str] = (),
    ) -> bool:
        """按 canonical 键级合并与 drop 契约落库，并捕获同事务清理键。"""

        assert (memory_id, expected_revision) == (7, self.memory["updated_at"])
        self.cleared_keys = tuple(drop_metadata_keys)
        merged = deepcopy(self.memory["metadata"])
        merged.update(deepcopy(metadata))
        for key in self.cleared_keys:
            merged.pop(key, None)
        self.write_count += 1
        self.stored_metadata = merged
        self.memory = _memory(
            content,
            revision=f"r-{7 + self.write_count}",
            metadata=merged,
        )
        return True

    def manager(
        self, store: ReconsolidationStore, llm_output: str
    ) -> ReconsolidationManager:
        """按注入回调装配 Manager，复用同一份 canonical 快照。"""

        return ReconsolidationManager(
            store=store,
            get_memory_cb=self.engine.get_memory,
            update_memory_cb=self.engine.update_memory,
            llm_caller=AsyncMock(return_value=llm_output),
            enabled=True,
        )


async def _apply_with_real_engine(
    store: ReconsolidationStore,
    *,
    old_metadata: dict[str, Any],
    proposed_content: str,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """用真实引擎执行一次 apply，返回结果、落库 metadata 与同事务清理键。"""

    harness = _EngineHarness(
        content=_OLD_CONTENT,
        revision="r-7",
        metadata=old_metadata,
    )
    manager = harness.manager(store, proposed_content)
    proposed = await manager.maybe_propose(7, context="近期上下文")
    assert proposed is not None

    result = await manager.apply_candidate(
        proposed["candidate_id"],
        harness.engine.update_memory,
    )
    return result, harness.stored_metadata, harness.cleared_keys


@pytest.mark.asyncio
async def test_apply_clears_old_facts_missing_from_rewritten_content(
    tmp_path: Path,
) -> None:
    """改写后正文不再包含旧事实时，apply 必须落地并在同一事务清掉旧事实表示。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    old_metadata = {
        "access_count": 8,
        "scope_key": "private:user-a",
        "key_facts": [_OLD_FACT],
        "fact_source_evidence": fact_evidence([_OLD_FACT]),
    }

    result, stored_metadata, cleared_keys = await _apply_with_real_engine(
        store,
        old_metadata=old_metadata,
        proposed_content="修正后的记忆正文内容",
    )

    assert result["applied"] is True
    assert "key_facts" not in stored_metadata
    assert "fact_source_evidence" not in stored_metadata
    assert set(cleared_keys) == {"key_facts", "fact_source_evidence"}
    assert stored_metadata["scope_key"] == "private:user-a"
    assert stored_metadata["reconsolidation_count"] == 1
    approved = await store.list_candidates(status="approved")
    assert [item["applied_revision"] for item in approved] == ["r-8"]


@pytest.mark.asyncio
async def test_apply_keeps_facts_still_present_in_rewritten_content(
    tmp_path: Path,
) -> None:
    """改写后正文仍逐字包含旧事实时，apply 成功后不得清掉仍对齐的事实表示。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    old_metadata = {
        "access_count": 8,
        "key_facts": [_OLD_FACT],
        "fact_source_evidence": fact_evidence([_OLD_FACT]),
    }

    result, stored_metadata, cleared_keys = await _apply_with_real_engine(
        store,
        old_metadata=old_metadata,
        proposed_content=f"{_OLD_FACT}，每天两杯",
    )

    assert result["applied"] is True
    assert cleared_keys == ()
    assert stored_metadata["key_facts"] == [_OLD_FACT]
    assert stored_metadata["fact_source_evidence"] == fact_evidence([_OLD_FACT])


@pytest.mark.asyncio
async def test_recovery_replay_strips_facts_recorded_in_legacy_intent(
    tmp_path: Path,
) -> None:
    """重启重放 apply intent 时同样不得携带其记录的旧事实，否则重试仍会被拒绝。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    old_metadata = {
        "access_count": 8,
        "key_facts": [_OLD_FACT],
        "fact_source_evidence": fact_evidence([_OLD_FACT]),
    }
    candidate = await store.stage_candidate(
        memory_id=7,
        source_revision="r-7",
        old_content=_OLD_CONTENT,
        old_metadata=old_metadata,
        proposed_content="修正后的记忆正文内容",
        change_summary="LLM 修订候选",
        evidence_type="llm_revision",
    )
    await store.begin_apply(
        candidate["candidate_id"],
        expected_revision="r-7",
        target_metadata={
            "access_count": 8,
            "key_facts": [_OLD_FACT],
            "fact_source_evidence": fact_evidence([_OLD_FACT]),
            "reconsolidation_count": 1,
            "last_reconsolidated_at": 1.0,
        },
    )
    harness = _EngineHarness(
        content=_OLD_CONTENT,
        revision="r-7",
        metadata=old_metadata,
    )
    manager = harness.manager(store, "修正后的记忆正文内容")

    result = await manager.recover_incomplete_applies()

    assert result == {"recovered": 1, "blocked": 0}
    assert harness.write_count == 1
    assert "key_facts" not in harness.stored_metadata
    assert "fact_source_evidence" not in harness.stored_metadata
    assert harness.stored_metadata["reconsolidation_count"] == 1
    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted is not None
    assert (persisted["status"], persisted["reason_code"]) == (
        "approved",
        "recovered_applied",
    )
    assert await store.list_incomplete_applies() == []


@pytest.mark.asyncio
async def test_real_bound_update_method_exposes_revision_conflict_reason(
    tmp_path: Path,
) -> None:
    """真实 bound method 失败时 Manager 必须读取引擎稳定原因码。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.get_memory = AsyncMock(  # type: ignore[method-assign]
        return_value=_memory(
            "原始记忆正文",
            revision="r-7",
            metadata={"access_count": 8},
        )
    )
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.update_content_if_revision = AsyncMock(return_value=False)
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=engine.get_memory,
        update_memory_cb=engine.update_memory,
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="近期上下文")
    assert proposed is not None

    result = await manager.apply_candidate(
        proposed["candidate_id"],
        engine.update_memory,
    )

    assert result == {
        "applied": False,
        "reason_code": "source_revision_mismatch",
    }


@pytest.mark.asyncio
async def test_apply_verification_failure_keeps_recoverable_intent(
    tmp_path: Path,
) -> None:
    """写入返回成功但即时读取失败时必须保留 pending intent 供重启对账。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    get_memory = AsyncMock(
        side_effect=[
            _memory(
                "原始记忆正文",
                revision="r-7",
                metadata={"access_count": 8},
            ),
            None,
        ]
    )
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=get_memory,
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="近期上下文")
    assert proposed is not None

    result = await manager.apply_candidate(
        proposed["candidate_id"],
        AsyncMock(return_value=True),
    )

    assert result == {
        "applied": False,
        "reason_code": "apply_result_unverified",
    }
    persisted = await store.get_candidate(proposed["candidate_id"])
    assert persisted is not None
    assert persisted["status"] == "pending"
    assert persisted["reason_code"] == "apply_result_unverified"
    operations = await store.list_incomplete_applies()
    assert [item["candidate_id"] for item in operations] == [proposed["candidate_id"]]
    assert operations[0]["reason_code"] == "apply_result_unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["content", "metadata"])
async def test_rollback_rejects_any_edit_after_candidate_apply(
    tmp_path: Path,
    changed_field: str,
) -> None:
    """批准后正文或 metadata 任一变化都必须阻断旧候选回滚。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    canonical = _CanonicalHarness()
    manager, candidate_id = await _applied_candidate(store, canonical)
    if changed_field == "content":
        canonical.memory["text"] = "管理员随后提交的新正文"
    else:
        canonical.memory["metadata"]["importance"] = 0.95
    canonical.memory["updated_at"] = "r-9"
    snapshot = deepcopy(canonical.memory)

    result = await manager.rollback_candidate(
        candidate_id,
        get_memory_cb=canonical.get_memory,
        update_memory_cb=canonical.update_memory,
    )

    assert result == {
        "restored": False,
        "reason_code": "source_revision_mismatch",
    }
    assert canonical.memory == snapshot
    assert canonical.write_count == 1
    persisted = await store.get_candidate(candidate_id)
    assert persisted is not None
    assert persisted["status"] == "approved"
    assert await store.list_incomplete_rollbacks() == []


@pytest.mark.asyncio
async def test_rollback_recovery_blocks_changed_metadata_even_when_content_is_old(
    tmp_path: Path,
) -> None:
    """恢复时即使正文像已回滚，只要 metadata 被后续编辑也不得再覆盖。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    canonical = _CanonicalHarness()
    _, candidate_id = await _applied_candidate(store, canonical)
    await store.begin_rollback(candidate_id, expected_revision="r-8")
    canonical.memory = _memory(
        "原始记忆正文",
        revision="r-9",
        metadata={
            "access_count": 8,
            "scope_key": "private:user-a",
            "importance": 0.95,
        },
    )
    snapshot = deepcopy(canonical.memory)
    restarted = ReconsolidationManager(
        store=store,
        get_memory_cb=canonical.get_memory,
        update_memory_cb=canonical.update_memory,
        enabled=True,
    )

    result = await restarted.recover_incomplete_rollbacks()

    assert result == {"recovered": 0, "blocked": 1}
    assert canonical.memory == snapshot
    assert canonical.write_count == 1
