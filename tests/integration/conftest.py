"""Memora 插件集成测试 Fixtures。

提供真实存储后端（SQLite + FAISS）以及 Mock LLM/Embedding
提供者，使集成测试能够演练完整管线而无需运行
AstrBot 实例。

Fixtures（除注明外均为函数作用域）：
- integration_db_path（会话级）—— 会话内共享的临时 SQLite 文件
- integration_faiss —— 真实 FAISS IndexFlatIP（维度=128）
- integration_atom_store —— 已建表的真实 AtomStore
- integration_config —— 基于 test_config_dict 的配置字典
- mock_embedding_fn —— 返回固定 128 维向量的 AsyncMock
- integration_engine —— 完整组装的 MemoryEngine
- preloaded_engine —— 预置 5 个种子原子 + FAISS 向量的引擎
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

# 确保插件根目录在 sys.path 中（可能已由 tests/conftest.py 设置）
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Pytest 配置
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """启用自动 asyncio 模式并注册集成测试的自定义标记。"""
    config.option.asyncio_mode = "auto"
    config.addinivalue_line("markers", "integration: mark test as integration test")


# ---------------------------------------------------------------------------
# 函数作用域数据库路径 — 每个测试隔离以防止数据泄漏
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_db_path() -> str:
    """每个测试独立的临时 SQLite 文件，确保测试之间完全隔离。

    函数作用域可防止测试以随机顺序运行或使用并行执行器
    (pytest-xdist) 时发生数据泄漏。
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    yield tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# FAISS 索引（真实）
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_faiss() -> Any:
    """创建维度为 128 的真实 FAISS IndexFlatIP。

    IndexFlatIP 使用内积相似度，与生产环境中
    文档向量搜索所使用的索引类型一致。
    """
    import faiss

    dim = 128
    index = faiss.IndexFlatIP(dim)
    return index


# ---------------------------------------------------------------------------
# Mock Embedding 函数
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_embedding_fn() -> AsyncMock:
    """返回固定 128 维向量的 AsyncMock。

    测试可覆盖 ``mock_embedding_fn.return_value`` 以自定义输出。
    """
    mock = AsyncMock(return_value=[0.1] * 128)
    return mock


# ---------------------------------------------------------------------------
# 集成测试配置 — 扩展 tests/conftest.py 的 test_config_dict
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_config(test_config_dict: dict[str, Any]) -> dict[str, Any]:
    """集成测试的配置字典。

    扩展单元测试的 test_config_dict，添加集成测试特有的覆盖：
    graph_memory_enabled=True, atom_enabled=True。
    """
    config = dict(test_config_dict)
    config["graph_memory_enabled"] = True
    config["atom_enabled"] = True
    config["graph_memory_atom_enabled"] = True
    return config


# ---------------------------------------------------------------------------
# 真实存储后端
# ---------------------------------------------------------------------------


@pytest.fixture
async def integration_atom_store(integration_db_path: str) -> Any:
    """创建并初始化由会话数据库支持的真实 AtomStore。"""
    from core.storage.atom_store import AtomStore

    store = AtomStore(db_path=integration_db_path)
    await store.initialize()
    return store


# ---------------------------------------------------------------------------
# 完整 MemoryEngine 组装
# ---------------------------------------------------------------------------


@pytest.fixture
async def integration_engine(
    integration_db_path: str,
    integration_faiss: Any,
    integration_config: dict[str, Any],
) -> Any:
    """组装一个使用真实存储和 Mock 提供者的 MemoryEngine。

    引擎使用：
    - 真实 AtomStore + FAISS（共享 SQLite 文件 + IndexFlatIP）
    - Mock LLM 提供者（返回预设 JSON）
    - Mock Embedding 函数（固定向量）
    - 启用 Graph + Atom 子系统的配置

    返回一个完全初始化、可用于 CRUD / 搜索操作的引擎。
    """
    from core.managers.memory_engine import MemoryEngine

    # 构造 Mock LLM 提供者。
    mock_llm = MagicMock()
    mock_llm.model_name = "test-integration-model"

    # 基于 integration_config 构建引擎配置，包含运行时覆盖
    engine_config: dict[str, Any] = dict(integration_config)
    engine_config.update(
        {
            "graph_vector_db": "mock",
            "user_profile": {"enabled": False},
            "auto_learning": {"enabled": False},
            "knowledge_base": {"enabled": False},
            "notes": {"enabled": False},
            "reranker": {"enabled": False},
            "continuity_tracking": {"enabled": False},
            "relationship_tracking": {"enabled": False},
            "reconsolidation": {"enabled": False},
            "anomaly_detection": {"enabled": False},
            "weight_learning": {"enabled": False},
            "trait_evolution": {"enabled": False},
            "export": {"enabled": False},
            "write_reliability": {"repair_enabled": False, "max_retries": 1},
            "search_cache_enabled": False,
            "search_cache_ttl_seconds": 0.0,
            "search_cache_max_size": 0,
            "rrf_k": 60,
        }
    )

    engine = MemoryEngine(
        db_path=integration_db_path,
        faiss_db=integration_faiss,
        graph_vector_db=MagicMock(),
        llm_provider=mock_llm,
        config=engine_config,
    )

    try:
        await engine.initialize()
    except Exception as exc:
        if hasattr(engine, "db_connection") and engine.db_connection:
            await engine.db_connection.close()
        pytest.fail(f"MemoryEngine.initialize() failed: {exc}")

    yield engine

    # 拆卸：干净地关闭引擎
    try:
        await engine.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 预加载引擎 — 存储中已有 5 个种子原子
# ---------------------------------------------------------------------------

_PRELOAD_ATOMS: list[dict[str, Any]] = [
    {
        "atom_type": "episodic",
        "content": "周末和小明去了西湖划船，天气很好",
        "importance": 0.75,
        "emotional_intensity": 0.80,
        "emotion_tags": ["joy", "excited"],
        "topics": ["西湖", "划船", "周末"],
    },
    {
        "atom_type": "factual",
        "content": "西湖是杭州最著名的景点，被列为世界文化遗产",
        "importance": 0.70,
        "emotional_intensity": 0.40,
        "emotion_tags": ["neutral"],
        "topics": ["西湖", "杭州", "文化遗产"],
    },
    {
        "atom_type": "preference",
        "content": "用户喜欢喝深度烘焙的咖啡，尤其是拿铁",
        "importance": 0.55,
        "emotional_intensity": 0.50,
        "emotion_tags": ["happy"],
        "topics": ["咖啡", "拿铁", "偏好"],
    },
    {
        "atom_type": "relational",
        "content": "小明是用户的大学室友，现在在同一家公司工作",
        "importance": 0.70,
        "emotional_intensity": 0.55,
        "emotion_tags": ["nostalgic", "friendly"],
        "topics": ["小明", "室友", "同事"],
    },
    {
        "atom_type": "planned",
        "content": "下周三月度项目评审会议需要准备PPT",
        "importance": 0.80,
        "emotional_intensity": 0.45,
        "emotion_tags": ["neutral"],
        "topics": ["会议", "评审", "PPT"],
        "event_time_offset_days": 5,
    },
]


@pytest.fixture
async def preloaded_engine(
    integration_engine: Any,
    mock_embedding_fn: AsyncMock,
) -> Any:
    """返回一个预填充了 5 个不同类型记忆原子的引擎。

    这些原子涵盖全部五种类型（EPISODIC、FACTUAL、PREFERENCE、
    RELATIONAL、PLANNED），其向量已添加到 FAISS 索引中。
    """
    from core.models.memory_atom import AtomType, MemoryAtom

    engine = integration_engine
    atom_store = engine.atom_store
    faiss_db = engine.faiss_db

    if atom_store is None:
        pytest.skip("AtomStore not available — graph/atom subsystem may be disabled")

    now = time.time()
    inserted_ids: list[int] = []

    for entry in _PRELOAD_ATOMS:
        event_time = None
        if "event_time_offset_days" in entry:
            event_time = now + entry.pop("event_time_offset_days") * 86400.0

        metadata = {
            "emotional_intensity": entry.get("emotional_intensity", 0.5),
        }
        atom = MemoryAtom(
            parent_memory_id=0,
            atom_type=AtomType(entry["atom_type"]),
            content=entry["content"],
            importance=entry.get("importance", 0.5),
            emotion_tags=entry.get("emotion_tags", []),
            entities=entry.get("topics", []),
            session_id="integration-test-session",
            persona_id="integration-test-persona",
            event_time=event_time,
            metadata=metadata,
        )

        # 通过 AtomStore 插入（绕过引擎 CRUD 以便直接控制）
        atom_id = await atom_store.insert(atom)
        inserted_ids.append(atom_id)

        # 构建 FAISS 向量并添加到索引
        tag_text = json.dumps(entry.get("topics", []), ensure_ascii=False)
        embedding = await mock_embedding_fn(atom.content + " " + tag_text)
        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        faiss_db.add(vec)

    engine._preloaded_ids = inserted_ids
    return engine
