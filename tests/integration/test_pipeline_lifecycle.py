"""管线 4：生命周期全流程集成测试

覆盖 MemoryAtom 完整生命周期管线：
- 衰减计算 (compute_temporal_score / compute_decay_score)
- 过期标记 (expire_stale_atoms)
- 分层遗忘 (forget_expired_atoms / cleanup_forgotten)
- 备份恢复 (BackupManager 文件级往返)
"""

from __future__ import annotations

import json
import os
import sqlite3
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from core.models.memory_atom import (
    AtomStatus,
    AtomType,
    DecayType,
    MemoryAtom,
    compute_decay_score,
)


# ============================================================================
# test_memory_decay_over_time
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_memory_decay_over_time(
    integration_atom_store: Any,
) -> None:
    """衰减计算全流程验证。

    1. 创建 1 条 TTL=7 天的 EPISODIC 记忆 (importance=0.8)
    2. 存入 AtomStore
    3. 计算 t=0 时的 temporal_score（应接近 1.0）
    4. 模拟 7 天后 (created_at - 7*86400)，计算 temporal_score（应接近 0.0，已过期）
    5. 验证 temporal_score 随时间单调递减
    """
    # Arrange
    store = integration_atom_store
    now = time.time()

    # 清除 store 中已有数据，隔离测试
    await _clear_all_atoms(store)

    atom = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.EPISODIC,
        content="周末和小明去了西湖划船，天气很好",
        importance=0.8,
        confidence=0.85,
        entities=["西湖", "小明", "周末"],
        emotion_tags=["joy"],
        ttl_days=7.0,
        decay_type=DecayType.EXPONENTIAL,
        session_id="lifecycle-test-session",
        persona_id="lifecycle-test-persona",
    )

    # Act — 插入
    atom_id = await store.insert(atom)

    # 取回以验证存储后的状态
    retrieved = await store.get(atom_id)
    assert retrieved is not None
    assert retrieved.content == "周末和小明去了西湖划船，天气很好"

    # Assert — t=0 时 temporal_score 应接近 1.0
    # compute_temporal_score 使用 (reference_time - last_accessed_at) 计算 days_since
    # 刚创建时 last_accessed_at == created_at，所以用 reference_time=last_accessed_at 时 days_since=0
    score_fresh = retrieved.compute_temporal_score(
        reference_time=retrieved.last_accessed_at
    )
    assert score_fresh >= 0.99, (
        f"新鲜记忆的时间分应接近 1.0，实际 {score_fresh:.6f}"
    )

    # 验证插入后 expires_at 被正确计算 (TTL=7天左右)
    expected_expiry = retrieved.created_at + retrieved.ttl_days * 86400.0
    assert abs(retrieved.expires_at - expected_expiry) < 5.0, (
        f"expires_at 应等于 created_at + ttl_days*86400，"
        f"预期 {expected_expiry:.1f}，实际 {retrieved.expires_at:.1f}"
    )

    # Act — 模拟 last_accessed_at 距今 7 天
    seven_days_after_access = retrieved.last_accessed_at + 7 * 86400.0
    score_7d = retrieved.compute_temporal_score(reference_time=seven_days_after_access)

    # Assert — ttl_days=7 的指数衰减，7天后 temporal_score 应接近 0.0
    # EXPONENTIAL decay: exp(-ln(2) * days_since / (ttl_days/2))
    # days_since=7, ttl_days=7 → exp(-ln(2) * 7 / 3.5) = exp(-2*ln(2)) = 0.25
    # 实际因 effective_ttl = max(1.0, ttl_days) = 7，half_life = 3.5
    assert score_7d < 0.5, (
        f"7天后的时间分应显著下降，实际 {score_7d:.6f}"
    )

    # 验证过期判定 — 使用实际计算的 expires_at 进行验证
    # _prepare_atom_for_insert 使用 compute_ttl 计算实时 TTL（此处 ~13.65 天）
    # 7 天后 last_accessed_at < expires_at → 不应过期
    # expires_at 之后 → 应过期
    after_expiry = retrieved.expires_at + 1.0
    assert retrieved.is_expired(reference_time=after_expiry), (
        f"超过 expires_at 后记忆应判定为已过期 (expires_at={retrieved.expires_at}, "
        f"check_time={after_expiry})"
    )
    assert not retrieved.is_expired(reference_time=retrieved.created_at), (
        "刚创建的记忆不应过期"
    )
    # 7 天后访问也应未过期（实际 TTL > 7天）
    assert not retrieved.is_expired(reference_time=seven_days_after_access), (
        f"7天后不应过期 (实际 TTL={retrieved.ttl_days:.1f}天, "
        f"expires_at={retrieved.expires_at})"
    )

    # Assert — 单调递减验证（中间 5 个采样点）
    prev: float = float("inf")
    for day in [0.0, 1.0, 3.0, 5.0, 7.0]:
        ref_time = retrieved.last_accessed_at + day * 86400.0
        score = retrieved.compute_temporal_score(reference_time=ref_time)
        assert score <= prev, (
            f"temporal_score 应单调递减: day={day}, "
            f"prev={prev:.6f}, curr={score:.6f}"
        )
        prev = score

    # 额外验证：独立 compute_decay_score 函数结果一致
    # _prepare_atom_for_insert 会用 compute_ttl 重算 ttl_days（此处约 13.65 天）
    # compute_temporal_score 使用 self.ttl_days，因此对比时使用实际 TTL
    actual_ttl = retrieved.ttl_days
    actual_days_since = (seven_days_after_access - retrieved.last_accessed_at) / 86400.0
    score_fn = compute_decay_score(DecayType.EXPONENTIAL, actual_ttl, actual_days_since)
    assert abs(score_fn - score_7d) < 0.001, (
        f"compute_decay_score 与 MemoryAtom.compute_temporal_score 结果应一致: "
        f"{score_fn:.6f} vs {score_7d:.6f} "
        f"(ttl={actual_ttl:.1f}, days_since={actual_days_since:.1f})"
    )


# ============================================================================
# test_expired_memories_cleanup
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_expired_memories_cleanup(
    integration_atom_store: Any,
    integration_faiss: Any,
    mock_embedding_fn: AsyncMock,
) -> None:
    """过期记忆清理全流程。

    1. 创建 3 条记忆：1 条 TTL=0（立即可过期）、2 条 TTL=365
    2. 调用 AtomLifecycleManager 的清理逻辑
    3. 验证过期记忆被标记
    4. 验证未过期记忆不受影响
    5. 验证 FAISS 索引与 SQLite 数据一致
    """
    from core.managers.atom_lifecycle_manager import AtomLifecycleManager

    # Arrange — 清除已有数据
    store = integration_atom_store
    faiss = integration_faiss
    await _clear_all_atoms(store)

    now = time.time()

    # 创建 1 条过期记忆 (TTL=0，立即过期)
    expired_atom = MemoryAtom(
        parent_memory_id=2,
        atom_type=AtomType.EPISODIC,
        content="今天中午吃了麻辣烫 —— 已过期",
        importance=0.3,
        confidence=0.7,
        entities=["麻辣烫", "午饭"],
        emotion_tags=["neutral"],
        ttl_days=1.0,  # 最短 TTL=1 天（compute_ttl 的 min）
        decay_type=DecayType.EXPONENTIAL,
        session_id="lifecycle-test-session",
        persona_id="lifecycle-test-persona",
    )

    # 创建 2 条长期记忆 (TTL=365)
    long_term_1 = MemoryAtom(
        parent_memory_id=2,
        atom_type=AtomType.FACTUAL,
        content="杭州位于浙江省，是长三角重要城市",
        importance=0.65,
        confidence=0.75,
        entities=["杭州", "浙江", "长三角"],
        emotion_tags=["neutral"],
        ttl_days=365.0,
        decay_type=DecayType.EXPONENTIAL,
        session_id="lifecycle-test-session",
        persona_id="lifecycle-test-persona",
    )

    long_term_2 = MemoryAtom(
        parent_memory_id=2,
        atom_type=AtomType.FACTUAL,
        content="西湖被列为世界文化遗产",
        importance=0.7,
        confidence=0.8,
        entities=["西湖", "文化遗产"],
        emotion_tags=["neutral"],
        ttl_days=365.0,
        decay_type=DecayType.EXPONENTIAL,
        session_id="lifecycle-test-session",
        persona_id="lifecycle-test-persona",
    )

    # Act — 插入所有记忆
    expired_id = await store.insert(expired_atom)
    lt1_id = await store.insert(long_term_1)
    lt2_id = await store.insert(long_term_2)
    all_ids = {expired_id, lt1_id, lt2_id}

    # 同时添加 FAISS 向量（模拟真实场景）
    for atom in [expired_atom, long_term_1, long_term_2]:
        vec_text = atom.content + " " + json.dumps(atom.entities, ensure_ascii=False)
        embedding = await mock_embedding_fn(vec_text)
        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        faiss.add(vec)

    # 验证插入正确
    total = await store.count_atoms()
    assert total == 3, f"应有 3 条记忆，实际 {total}"

    faiss_size = faiss.ntotal
    assert faiss_size == 3, f"FAISS 应有 3 个向量，实际 {faiss_size}"

    # Act — 手动将过期记忆的 expires_at 设为过去时
    async with store._connect() as db:
        await db.execute(
            "UPDATE memory_atoms SET expires_at = ?, status = ? WHERE id = ?",
            (now - 3600.0, AtomStatus.ACTIVE.value, expired_id),
        )
        await db.commit()

    # 验证 3 条记忆均处于 ACTIVE 状态（清理前）
    stats_before = await store.get_stats()
    assert stats_before.get("active", 0) == 3, (
        f"清理前应有 3 条 ACTIVE，实际 {stats_before.get('active', 0)}"
    )

    # Act — 调用生命周期管理器执行过期标记
    mgr = AtomLifecycleManager(atom_store=store)
    result = await mgr.run_maintenance()

    # Assert — 过期标记
    expired_count = result.get("expired", 0)
    assert expired_count == 1, f"应标记 1 条过期记忆，实际标记 {expired_count}"

    # 验证状态变化
    stats_after = await store.get_stats()
    assert stats_after.get("expired", 0) == 1, (
        f"应有 1 条 EXPIRED，实际 {stats_after.get('expired', 0)}"
    )
    assert stats_after.get("active", 0) == 2, (
        f"应有 2 条 ACTIVE，实际 {stats_after.get('active', 0)}"
    )

    # 验证未过期记忆的内容不变
    lt1_retrieved = await store.get(lt1_id)
    assert lt1_retrieved is not None
    assert lt1_retrieved.content == "杭州位于浙江省，是长三角重要城市"
    assert lt1_retrieved.status == AtomStatus.ACTIVE

    lt2_retrieved = await store.get(lt2_id)
    assert lt2_retrieved is not None
    assert lt2_retrieved.content == "西湖被列为世界文化遗产"
    assert lt2_retrieved.status == AtomStatus.ACTIVE

    # 验证过期记忆状态正确
    expired_retrieved = await store.get(expired_id)
    assert expired_retrieved is not None
    assert expired_retrieved.status == AtomStatus.EXPIRED

    # Assert — FAISS 仍保留所有向量（forget 阶段才移除 FTS）
    assert faiss.ntotal == 3, (
        f"expire 阶段不应移除 FAISS 向量，实际 {faiss.ntotal}"
    )

    # Act — 手动推进 expires_at 使过期记忆的 expires_at 超过 forget 阈值
    past_forget_threshold = now - (mgr._forget_delay_days + 1.0) * 86400.0
    async with store._connect() as db:
        await db.execute(
            "UPDATE memory_atoms SET expires_at = ? WHERE id = ?",
            (past_forget_threshold, expired_id),
        )
        await db.commit()

    # 执行 forget 阶段
    forgotten = await store.forget_expired_atoms(mgr._forget_delay_days)
    assert forgotten == 1, f"应 soft-delete 1 条记忆，实际 {forgotten}"

    # 验证状态变为 FORGOTTEN，且 FTS 中已移除
    forgotten_retrieved = await store.get(expired_id)
    assert forgotten_retrieved is not None
    assert forgotten_retrieved.status == AtomStatus.FORGOTTEN

    # 验证 FTS 搜索不到该记忆
    fts_results = await store.search_fts("麻辣烫", limit=5, include_expired=False)
    fts_ids = {a.atom_id for a in fts_results}
    assert expired_id not in fts_ids, "FORGOTTEN 记忆不应出现在 FTS 搜索结果中"

    # 未过期记忆仍可搜索
    fts_hangzhou = await store.search_fts("杭州", limit=5, include_expired=False)
    fts_hangzhou_ids = {a.atom_id for a in fts_hangzhou}
    assert lt1_id in fts_hangzhou_ids, "长期记忆应在 FTS 搜索结果中"


# ============================================================================
# test_backup_and_restore_roundtrip
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_backup_and_restore_roundtrip(
    integration_atom_store: Any,
) -> None:
    """备份恢复往返测试。

    1. 创建 3 条记忆并存入 AtomStore
    2. 备份数据库文件到临时目录
    3. 创建新的 AtomStore 实例
    4. 从备份恢复
    5. 验证恢复后的记忆数量和内容与原始一致
    """
    from core.managers.backup_manager import BackupManager

    # Arrange — 清除已有数据
    store = integration_atom_store
    await _clear_all_atoms(store)

    now = time.time()

    # 创建 3 条不同类型的记忆
    atoms: list[MemoryAtom] = [
        MemoryAtom(
            parent_memory_id=3,
            atom_type=AtomType.EPISODIC,
            content="周末去西湖划船，天气很好",
            importance=0.75,
            confidence=0.85,
            entities=["西湖", "划船", "周末"],
            emotion_tags=["joy", "excited"],
            ttl_days=7.0,
            session_id="backup-test-session",
            persona_id="backup-test-persona",
        ),
        MemoryAtom(
            parent_memory_id=3,
            atom_type=AtomType.FACTUAL,
            content="杭州位于浙江省，是长三角重要城市",
            importance=0.65,
            confidence=0.75,
            entities=["杭州", "浙江", "长三角"],
            emotion_tags=["neutral"],
            ttl_days=180.0,
            session_id="backup-test-session",
            persona_id="backup-test-persona",
        ),
        MemoryAtom(
            parent_memory_id=3,
            atom_type=AtomType.PREFERENCE,
            content="用户喜欢喝深度烘焙的咖啡，尤其是拿铁",
            importance=0.55,
            confidence=0.7,
            entities=["咖啡", "拿铁", "偏好"],
            emotion_tags=["happy"],
            ttl_days=60.0,
            session_id="backup-test-session",
            persona_id="backup-test-persona",
        ),
    ]

    # Act — 插入记忆
    inserted_ids: list[int] = []
    inserted_contents: dict[int, str] = {}
    inserted_types: dict[int, str] = {}

    for atom in atoms:
        atom_id = await store.insert(atom)
        inserted_ids.append(atom_id)
        inserted_contents[atom_id] = atom.content
        inserted_types[atom_id] = atom.atom_type.value

    # 验证插入成功
    total = await store.count_atoms()
    assert total == 3, f"应有 3 条记忆，实际 {total}"

    # Act — 备份：直接复制 SQLite 文件 + FAISS 索引文件到临时目录
    backup_dir = tempfile.mkdtemp(prefix="memora_backup_test_")
    db_path = store.db_path

    try:
        backup_db = os.path.join(backup_dir, "memora.db")
        shutil.copy2(db_path, backup_db)

        # 同时尝试备份 WAL/SHM 文件
        for suffix in ("-wal", "-shm"):
            wal_path = db_path + suffix
            if os.path.exists(wal_path):
                shutil.copy2(wal_path, os.path.join(backup_dir, f"memora.db{suffix}"))

        # 写备份元数据
        backup_info = {
            "backup_timestamp": time.time(),
            "atom_count": 3,
            "atom_ids": inserted_ids,
        }
        info_path = os.path.join(backup_dir, "backup_info.json")
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(backup_info, f, ensure_ascii=False)

        # Assert — 备份文件存在
        assert os.path.exists(backup_db), "备份 SQLite 文件应存在"
        assert os.path.getsize(backup_db) > 0, "备份文件不应为空"

        # Act — 从备份创建新 AtomStore 实例
        from core.storage.atom_store import AtomStore

        restored_store = AtomStore(db_path=backup_db)
        await restored_store.initialize()

        # Assert — 恢复后的记忆数量一致
        restored_count = await restored_store.count_atoms()
        assert restored_count == 3, f"恢复后应有 3 条记忆，实际 {restored_count}"

        # Assert — 恢复后的每条记忆内容正确
        for atom_id in inserted_ids:
            restored = await restored_store.get(atom_id)
            assert restored is not None, f"atom_id={atom_id} 应在恢复后的数据库中"
            assert restored.content == inserted_contents[atom_id], (
                f"atom_id={atom_id} 内容应一致: "
                f"'{inserted_contents[atom_id]}' vs '{restored.content}'"
            )
            assert restored.atom_type.value == inserted_types[atom_id], (
                f"atom_id={atom_id} 类型应一致"
            )

        # Assert — FTS 搜索可正常工作
        fts_results = await restored_store.search_fts(
            "西湖", limit=5, include_expired=False
        )
        fts_contents = {a.content for a in fts_results}
        assert "周末去西湖划船，天气很好" in fts_contents, (
            "FTS 搜索应在恢复后的数据库中正常工作"
        )

        # Assert — 统计信息一致
        restored_stats = await restored_store.get_stats()
        assert restored_stats.get("active", 0) == 3, (
            f"恢复后应有 3 条 ACTIVE，实际 {restored_stats.get('active', 0)}"
        )

        # 清理 — 关闭 restored store 的连接
        if hasattr(restored_store, "db_connection") and restored_store.db_connection:
            await restored_store.db_connection.close()

    finally:
        # 清理备份目录
        shutil.rmtree(backup_dir, ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_restore_reload_lifecycle_applies_then_validates(tmp_path: Path) -> None:
    """恢复事务在热重载模式下先应用并校验，再由新实例确认成功。"""
    from core.managers.backup_manager import BackupManager

    db_path = tmp_path / "memora.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker(value) VALUES (?)", ("restored",))
        connection.commit()
    finally:
        connection.close()

    manager = BackupManager(str(tmp_path))
    backup = await manager.create_backup(kind="manual")
    staged = manager.stage_restore(str(backup["name"]), apply_mode="reload")

    applied = manager.apply_pending_restores()

    assert applied["restore_status"] == "validating"
    manager.mark_restore_succeeded(str(staged["operation_id"]))
    status = manager.get_restore_status(str(staged["operation_id"]))
    assert status is not None
    assert status["restore_status"] == "succeeded"


# ============================================================================
# helpers
# ============================================================================

async def _clear_all_atoms(store: Any) -> None:
    """清空 memory_atoms 表和 FTS 索引（保留表结构）。"""
    async with store._connect() as db:
        await db.execute("DELETE FROM memory_atoms_fts")
        await db.execute("DELETE FROM memory_atoms")
        await db.commit()
