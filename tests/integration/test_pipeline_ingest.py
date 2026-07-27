"""管线 1：摄入-存储全流程集成测试。

覆盖管线：
  用户消息 → ConversationManager.store → MemoryProcessor (LLM抽取) →
    TopicSplitter (话题分割) → AtomStore.insert (SQLite) + FAISS.add (向量索引)

测试场景：
  - 单条消息摄入全流程（单话题 JSON）
  - 多话题消息话题分割（3 条独立记忆）
  - 空 LLM 响应优雅降级
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

# ============================================================================
# 管线 1.1：单条消息摄入全流程
# ============================================================================


class TestPipelineIngest:
    """摄入-存储管线集成测试。"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _single_topic_llm_output() -> dict[str, Any]:
        """模拟 LLM 返回的单话题 JSON（1 条记忆 + 3 个 key_facts）。"""
        return {
            "summary": "周末和小明去了西湖划船，天气很好",
            "key_facts": [
                "周末去了西湖",
                "和小明一起划船",
                "天气很好适合户外活动",
            ],
            "topics": ["西湖", "划船", "周末"],
            "sentiment": "positive",
            "importance": 0.75,
            "emotional_intensity": 0.80,
            "emotion_tags": ["joy", "excited"],
        }

    @staticmethod
    def _multi_topic_llm_output() -> dict[str, Any]:
        """模拟 LLM 返回的多话题 JSON（3 条独立记忆，不同 topics）。"""
        return {
            "memories": [
                {
                    "summary": "讨论了周末滑雪计划，张三提议去长白山",
                    "key_facts": [
                        "张三提议周末去长白山滑雪",
                        "李四确认可以参加",
                        "计划周五晚上出发",
                    ],
                    "topics": ["滑雪", "长白山", "出行计划"],
                    "importance": 0.8,
                    "sentiment": "positive",
                    "emotion_tags": ["兴奋", "期待"],
                    "causal_relations": [],
                    "participants": ["张三", "李四", "用户"],
                },
                {
                    "summary": "项目A的测试覆盖率不足，需要补充单元测试",
                    "key_facts": [
                        "项目A测试覆盖率仅45%",
                        "周五前需要达到80%",
                        "王五负责写测试",
                    ],
                    "topics": ["项目A", "测试", "工作"],
                    "importance": 0.9,
                    "sentiment": "neutral",
                    "emotion_tags": ["焦虑"],
                    "causal_relations": [],
                    "participants": ["王五", "用户"],
                },
                {
                    "summary": "用户最近失眠，尝试了褪黑素但效果一般",
                    "key_facts": [
                        "用户最近连续三天失眠",
                        "尝试褪黑素效果不佳",
                        "医生建议规律作息",
                    ],
                    "topics": ["失眠", "健康", "褪黑素"],
                    "importance": 0.7,
                    "sentiment": "negative",
                    "emotion_tags": ["疲惫", "担忧"],
                    "causal_relations": [],
                    "participants": ["用户"],
                },
            ]
        }

    async def _count_atoms(self, atom_store: Any) -> int:
        """查询 AtomStore 中当前记忆原子总数。"""
        async with atom_store._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memory_atoms")
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def _insert_segment_via_store(
        self,
        atom_store: Any,
        faiss_db: Any,
        mock_embedding: AsyncMock,
        segment: Any,
        session_id: str = "test-session",
        persona_id: str = "test-persona",
    ) -> int:
        """将 MemorySegment 写入 AtomStore + FAISS 并返回 atom_id。

        这是对 MemoryProcessor._storage_task 内部写入逻辑的模拟 —
        用 MemorySegment 构造 MemoryAtom，调用 AtomStore.insert()，
        并维护 FAISS 向量索引。
        """
        from core.models.memory_atom import AtomType, MemoryAtom

        now = time.time()
        atom = MemoryAtom(
            parent_memory_id=0,
            atom_type=AtomType.EPISODIC,
            content=segment.content,
            entities=segment.topics if hasattr(segment, "topics") else [],
            importance=segment.importance if hasattr(segment, "importance") else 0.5,
            emotion_tags=segment.metadata.get("emotion_tags", [])
            if hasattr(segment, "metadata")
            else [],
            metadata=segment.metadata if hasattr(segment, "metadata") else {},
            session_id=session_id,
            persona_id=persona_id,
            event_time=now,
        )

        # 写入 SQLite
        atom_id = await atom_store.insert(atom)

        # 写入 FAISS 向量索引
        tag_text = json.dumps(atom.entities, ensure_ascii=False)
        embedding = await mock_embedding(atom.content + " " + tag_text)
        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        faiss_db.add(vec)

        return atom_id

    # ------------------------------------------------------------------
    # 1.1  test_full_ingest_single_message_flow
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_ingest_single_message_flow(
        self,
        integration_engine: Any,
        mock_embedding_fn: AsyncMock,
    ):
        """模拟单条消息摄入全流程：LLM → Segment → AtomStore + FAISS。

        验证：
        - AtomStore 中新增 1 条记录
        - FAISS 向量索引条目数 = AtomStore 记录数
        - 记忆内容正确（content、topics、key_facts）
        """
        # --- Arrange ---
        engine = integration_engine
        atom_store = engine.atom_store
        faiss_db = engine.faiss_db

        if atom_store is None:
            pytest.skip("AtomStore 不可用 — atom 子系统可能已禁用")

        # 记录摄入前的初始状态
        before_count = await self._count_atoms(atom_store)

        # 模拟 LLM 输出：单话题记忆
        llm_output = self._single_topic_llm_output()

        # 用 Strategy A 解析
        from core.processors.topic_splitter import PromptSegmentationStrategy

        strat = PromptSegmentationStrategy()

        # --- Act ---
        segments = await strat.segment(llm_output)

        # 写入每个 segment 到 AtomStore + FAISS
        session_id = "ingest-single-session"
        persona_id = "ingest-single-persona"
        inserted_ids: list[int] = []

        for seg in segments:
            seg.metadata["source_window"] = {
                "session_id": session_id,
                "start_index": 0,
                "end_index": 1,
                "message_count": 1,
            }
            seg.metadata["sentiment"] = llm_output.get("sentiment", "")
            seg.metadata["emotion_tags"] = llm_output.get("emotion_tags", [])
            seg.metadata["schema_version"] = "v3"

            atom_id = await self._insert_segment_via_store(
                atom_store,
                faiss_db,
                mock_embedding_fn,
                seg,
                session_id=session_id,
                persona_id=persona_id,
            )
            inserted_ids.append(atom_id)

        # --- Assert ---
        after_count = await self._count_atoms(atom_store)
        after_faiss = faiss_db.ntotal

        # 验证：AtomStore 中新增 1 条记录
        assert after_count - before_count == 1, (
            f"预期新增 1 条原子，实际从 {before_count} 增长到 {after_count}"
        )

        # 验证：FAISS 向量索引条目数 = AtomStore 记录数
        assert after_faiss == after_count, (
            f"FAISS ntotal ({after_faiss}) 应与 AtomStore 记录数 ({after_count}) 一致"
        )

        # 验证：记忆内容正确
        assert len(inserted_ids) == 1
        atom = await atom_store.get_raw(inserted_ids[0])
        assert atom is not None

        assert atom.content == "周末和小明去了西湖划船，天气很好"
        assert "西湖" in atom.entities
        assert "划船" in atom.entities
        assert atom.session_id == session_id
        assert atom.persona_id == persona_id

        # 验证：metadata 正确传递
        meta = (
            json.loads(atom.metadata)
            if isinstance(atom.metadata, str)
            else atom.metadata
        )
        assert meta.get("sentiment") == "positive"
        assert meta.get("schema_version") == "v3"

    # ------------------------------------------------------------------
    # 1.2  test_ingest_multi_topic_message_splits_correctly
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ingest_multi_topic_message_splits_correctly(
        self,
        integration_engine: Any,
        mock_embedding_fn: AsyncMock,
    ):
        """模拟多话题消息摄入：LLM 返回 3 条独立记忆 → 话题分割 → 存储。

        验证：
        - AtomStore 中有 3 条新记录
        - 每条记录的 topics 字段不重叠（滑雪 vs 测试 vs 健康）
        - FAISS 索引与 AtomStore 记录数一致
        """
        # --- Arrange ---
        engine = integration_engine
        atom_store = engine.atom_store
        faiss_db = engine.faiss_db

        if atom_store is None:
            pytest.skip("AtomStore 不可用 — atom 子系统可能已禁用")

        before_count = await self._count_atoms(atom_store)
        before_faiss = faiss_db.ntotal

        llm_output = self._multi_topic_llm_output()

        from core.processors.topic_splitter import PromptSegmentationStrategy

        strat = PromptSegmentationStrategy()

        # --- Act ---
        segments = await strat.segment(llm_output)

        # 应分割成 3 段
        assert len(segments) == 3, f"预期 3 个 segment，实际得到 {len(segments)}"

        session_id = "ingest-multi-session"
        persona_id = "ingest-multi-persona"
        inserted_ids: list[int] = []

        for seg in segments:
            atom_id = await self._insert_segment_via_store(
                atom_store,
                faiss_db,
                mock_embedding_fn,
                seg,
                session_id=session_id,
                persona_id=persona_id,
            )
            inserted_ids.append(atom_id)

        # --- Assert ---
        after_count = await self._count_atoms(atom_store)
        after_faiss = faiss_db.ntotal

        # 验证：AtomStore 中有 3 条新记录
        assert after_count - before_count == 3, (
            f"预期新增 3 条原子，实际从 {before_count} 增长到 {after_count}"
        )

        # 验证：FAISS 增量 = AtomStore 增量（FAISS 函数级隔离，SQLite session 级共享）
        assert (after_faiss - before_faiss) == (after_count - before_count), (
            f"FAISS 增量 {after_faiss - before_faiss} 应与 AtomStore 增量 {after_count - before_count} 一致"
        )

        # 验证：每条记录内容正确，topics 不重叠
        atoms = []
        for aid in inserted_ids:
            atom = await atom_store.get_raw(aid)
            assert atom is not None
            atoms.append(atom)

        # 滑雪话题
        assert "滑雪" in atoms[0].entities
        assert "长白山" in atoms[0].entities
        assert atoms[0].content == "讨论了周末滑雪计划，张三提议去长白山"

        # 测试/工作话题 — 不应包含滑雪相关内容
        assert "项目A" in atoms[1].entities or any(
            "项目A" in e for e in atoms[1].entities
        )
        assert "测试" in atoms[1].entities or any(
            "测试" in e for e in atoms[1].entities
        )
        assert "滑雪" not in atoms[1].entities
        assert "长白山" not in atoms[1].entities

        # 健康话题 — 不应包含滑雪或工作相关内容
        assert "失眠" in atoms[2].entities or any(
            "失眠" in e for e in atoms[2].entities
        )
        assert "健康" in atoms[2].entities or any(
            "健康" in e for e in atoms[2].entities
        )
        assert "滑雪" not in atoms[2].entities
        assert "项目A" not in atoms[2].entities

    # ------------------------------------------------------------------
    # 1.3  test_ingest_empty_llm_response_handles_gracefully
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ingest_empty_llm_response_handles_gracefully(
        self,
        integration_engine: Any,
        mock_embedding_fn: AsyncMock,
    ):
        """异常场景：LLM 返回空 JSON `{}` 时不抛异常，无新增记录。

        验证：
        - 不抛异常
        - AtomStore 无新增记录
        """
        # --- Arrange ---
        engine = integration_engine
        atom_store = engine.atom_store

        if atom_store is None:
            pytest.skip("AtomStore 不可用 — atom 子系统可能已禁用")

        before_count = await self._count_atoms(atom_store)

        # 模拟 LLM 返回空 JSON
        empty_output: dict[str, Any] = {}

        from core.processors.topic_splitter import PromptSegmentationStrategy

        strat = PromptSegmentationStrategy()

        # --- Act ---
        # 空输入不应抛异常
        segments = await strat.segment(empty_output)

        # --- Assert ---
        # 空 {} 应返回 0 个 segments
        assert len(segments) == 0, (
            f"空 LLM 输出应返回 0 个 segment，实际返回 {len(segments)}"
        )

        # 不写入任何数据
        after_count = await self._count_atoms(atom_store)
        assert after_count == before_count, (
            f"空 LLM 输出不应新增记录，预期 {before_count}，实际 {after_count}"
        )

    # ------------------------------------------------------------------
    # 1.4  test_ingest_single_atom_entity_roundtrip
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ingest_single_atom_entity_roundtrip(
        self,
        integration_engine: Any,
        mock_embedding_fn: AsyncMock,
    ):
        """验证记忆原子写入后可完整读出，字段无损失（SQLite JSON 序列化往返）。"""
        # --- Arrange ---
        engine = integration_engine
        atom_store = engine.atom_store
        faiss_db = engine.faiss_db

        if atom_store is None:
            pytest.skip("AtomStore 不可用 — atom 子系统可能已禁用")

        from core.models.memory_atom import AtomType, MemoryAtom

        # 构造一个覆盖所有持久化字段的原子
        # 注意：emotion_tags 不在 DB schema 中，不是持久化字段；通过 metadata 传递
        now = time.time()
        atom = MemoryAtom(
            parent_memory_id=42,
            atom_type=AtomType.FACTUAL,
            content="西湖是杭州最著名的景点，被列为世界文化遗产",
            entities=["西湖", "杭州", "文化遗产"],
            importance=0.70,
            confidence=0.85,
            session_id="roundtrip-session",
            persona_id="roundtrip-persona",
            event_time=now - 86400 * 7,
            ttl_days=180.0,
            metadata={
                "sentiment": "neutral",
                "schema_version": "v3",
                "source": "knowledge_base",
                "emotion_tags": ["neutral"],
            },
        )

        # --- Act ---
        # 写入
        atom_id = await atom_store.insert(atom)

        # 写入 FAISS
        tag_text = json.dumps(atom.entities, ensure_ascii=False)
        embedding = await mock_embedding_fn(atom.content + " " + tag_text)
        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        faiss_db.add(vec)

        # --- Assert ---
        # FAISS 更新
        assert faiss_db.ntotal > 0

        # 读回
        retrieved = await atom_store.get_raw(atom_id)
        assert retrieved is not None
        assert retrieved.atom_id == atom_id
        assert retrieved.parent_memory_id == 42
        assert retrieved.atom_type == AtomType.FACTUAL
        assert retrieved.content == "西湖是杭州最著名的景点，被列为世界文化遗产"
        assert retrieved.entities == ["西湖", "杭州", "文化遗产"]
        assert retrieved.importance == 0.70
        assert retrieved.confidence == 0.85
        assert retrieved.session_id == "roundtrip-session"
        assert retrieved.persona_id == "roundtrip-persona"
        # TTL 由 _prepare_atom_for_insert 按照 atom_type + importance 重新计算，
        # 不会原样保留构造值（FACTUAL base_ttl=180, importance=0.7 → TTL > 180）
        assert retrieved.ttl_days > 0

        # metadata 往返正确
        meta = (
            json.loads(retrieved.metadata)
            if isinstance(retrieved.metadata, str)
            else retrieved.metadata
        )
        assert meta.get("sentiment") == "neutral"
        assert meta.get("schema_version") == "v3"
        assert meta.get("source") == "knowledge_base"
