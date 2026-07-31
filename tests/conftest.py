"""Memora 插件测试的 Pytest Fixtures。

提供：
- mock_llm_caller：为依赖 LLM 的代码返回预设 JSON 响应
- test_config：带有测试用合适默认值的 ConfigManager
- test_db：内存 SQLite 数据库连接
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# 确保插件根目录在 sys.path 中以便导入
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Mock AstrBot 框架（测试环境中未安装）
# 必须在任何 core.* 导入之前运行 —— Pytest 最先加载 conftest.py。
# ---------------------------------------------------------------------------


def _install_astrbot_mocks() -> None:
    """将 AstrBot 的最小 Mock 模块注入 sys.modules。

    使用真实的 ``types.ModuleType`` 实例，以便 Python 的导入机制
    能够正确解析子模块路径。
    """
    import logging
    from types import ModuleType

    def _mkpkg(name: str) -> ModuleType:
        """创建命名空间包（具有 __path__ 属性）。"""
        mod = ModuleType(name)
        mod.__path__ = []  # type: ignore[attr-defined]
        mod.__package__ = name
        return mod

    def _mkmod(name: str) -> ModuleType:
        mod = ModuleType(name)
        mod.__package__ = name.rsplit(".", 1)[0] if "." in name else name
        return mod

    def _identity_decorator(*args, **kwargs):
        def _decorator(func):
            return func

        return _decorator

    class _CommandGroupStub:
        def __call__(self, func):
            return self

        def command(self, *args, **kwargs):
            return _identity_decorator(*args, **kwargs)

    class _FilterStub:
        PlatformAdapterType = MagicMock(ALL="all")

        @staticmethod
        def command_group(*args, **kwargs):
            return _CommandGroupStub()

        @staticmethod
        def platform_adapter_type(*args, **kwargs):
            return _identity_decorator(*args, **kwargs)

        @staticmethod
        def on_llm_request(*args, **kwargs):
            return _identity_decorator(*args, **kwargs)

        @staticmethod
        def on_llm_response(*args, **kwargs):
            return _identity_decorator(*args, **kwargs)

        @staticmethod
        def after_message_sent(*args, **kwargs):
            return _identity_decorator(*args, **kwargs)

    class _PermissionType:
        ADMIN = "admin"

    # 包层级结构
    for pkg in ("astrbot", "astrbot.api", "astrbot.core", "astrbot.core.agent"):
        if pkg not in sys.modules:
            sys.modules[pkg] = _mkpkg(pkg)

    # astrbot.api — 必须具有 ``logger`` 和 ``sp`` 属性
    sys.modules["astrbot.api"].logger = logging.getLogger("astrbot.test")  # type: ignore[attr-defined]
    sys.modules["astrbot.api"].sp = MagicMock()  # type: ignore[attr-defined]

    # astrbot.api.logger — 模块级日志记录器
    _logmod = _mkmod("astrbot.api.logger")
    _test_logger = logging.getLogger("astrbot.test")
    _test_logger.setLevel(logging.DEBUG)
    _logmod.logger = _test_logger  # type: ignore[attr-defined]
    # 将常用日志方法直接复制到模块上
    for _attr in ("debug", "info", "warning", "error", "exception", "critical"):
        setattr(_logmod, _attr, getattr(_test_logger, _attr))
    sys.modules["astrbot.api.logger"] = _logmod

    # astrbot.api.star — Context、Star、StarTools
    _star = _mkmod("astrbot.api.star")
    _star.Context = MagicMock  # type: ignore[attr-defined]
    _star.Star = MagicMock  # type: ignore[attr-defined]
    _star.StarTools = MagicMock  # type: ignore[attr-defined]
    sys.modules["astrbot.api.star"] = _star

    # astrbot.api.platform — MessageType
    _platform = _mkmod("astrbot.api.platform")
    _platform.MessageType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["astrbot.api.platform"] = _platform

    # astrbot.api.event — AstrMessageEvent、MessageEventResult、filter
    _event = _mkmod("astrbot.api.event")
    _event.AstrMessageEvent = MagicMock  # type: ignore[attr-defined]
    _event.MessageEventResult = MagicMock  # type: ignore[attr-defined]
    _event.filter = _FilterStub()  # type: ignore[attr-defined]
    sys.modules["astrbot.api.event"] = _event

    _event_filter = _mkmod("astrbot.api.event.filter")
    _event_filter.PermissionType = _PermissionType  # type: ignore[attr-defined]
    _event_filter.permission_type = _identity_decorator  # type: ignore[attr-defined]
    sys.modules["astrbot.api.event.filter"] = _event_filter

    # astrbot.api.provider — ProviderRequest、LLMResponse
    _provider = _mkmod("astrbot.api.provider")
    _provider.ProviderRequest = MagicMock  # type: ignore[attr-defined]
    _provider.LLMResponse = MagicMock  # type: ignore[attr-defined]
    sys.modules["astrbot.api.provider"] = _provider

    # astrbot.api.provider.entities — ProviderRequest（别名路径）
    _prov_ent = _mkmod("astrbot.api.provider.entities")
    _prov_ent.ProviderRequest = MagicMock  # type: ignore[attr-defined]
    sys.modules["astrbot.api.provider.entities"] = _prov_ent

    # astrbot.core.agent.message — TextPart
    _agent_msg = _mkmod("astrbot.core.agent.message")
    _text_part = MagicMock()
    _text_part.return_value.mark_as_temp = MagicMock(
        return_value=_text_part.return_value
    )
    _agent_msg.TextPart = _text_part  # type: ignore[attr-defined]
    sys.modules["astrbot.core.agent.message"] = _agent_msg

    # astrbot.core.db — FaissVecDB / 向量数据库（被 vector_retriever 使用）
    # 使用普通对象（非 MagicMock）作为基类 — 验证器继承自
    # 引用了 FaissVecDB 的混入类，而 MagicMock.__setattr__ 会
    # 在初始化期间访问 _mock_methods 从而破坏 MRO。
    sys.modules["astrbot.core.db"] = _mkpkg("astrbot.core.db")
    _db_vec_mod = _mkmod("astrbot.core.db.vec_db")
    sys.modules["astrbot.core.db.vec_db"] = _db_vec_mod
    _faiss_impl = _mkmod("astrbot.core.db.vec_db.faiss_impl")
    sys.modules["astrbot.core.db.vec_db.faiss_impl"] = _faiss_impl
    _vec_db = _mkmod("astrbot.core.db.vec_db.faiss_impl.vec_db")
    _fb = type("FaissVecDB", (object,), {})  # plain class, not MagicMock
    _vec_db.FaissVecDB = _fb  # type: ignore[attr-defined]
    sys.modules["astrbot.core.db.vec_db.faiss_impl.vec_db"] = _vec_db

    # astrbot.core.message.components —（被 message_content_extractor 使用）
    # 使用不同的 MagicMock 子类以确保 isinstance 检查正常工作
    sys.modules["astrbot.core.message"] = _mkpkg("astrbot.core.message")
    _msg_comp = _mkmod("astrbot.core.message.components")
    _msg_comp.Image = type("Image", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.At = type("At", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.AtAll = type("AtAll", (_msg_comp.At,), {})  # type: ignore[attr-defined]
    _msg_comp.Reply = type("Reply", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.Plain = type("Plain", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.Record = type("Record", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.Video = type("Video", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.File = type("File", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.Face = type("Face", (MagicMock,), {})  # type: ignore[attr-defined]
    _msg_comp.Forward = type("Forward", (MagicMock,), {})  # type: ignore[attr-defined]
    sys.modules["astrbot.core.message.components"] = _msg_comp

    # astrbot.core.agent.run_context —（被工具使用，必须支持下标访问）
    _run_ctx = _mkmod("astrbot.core.agent.run_context")
    _cw = type("ContextWrapper", (MagicMock,), {})
    _cw.__class_getitem__ = classmethod(lambda cls, item: cls)  # type: ignore[attr-defined]
    _cw.return_value = MagicMock()
    _run_ctx.ContextWrapper = _cw  # type: ignore[attr-defined]
    sys.modules["astrbot.core.agent.run_context"] = _run_ctx

    # astrbot.core.agent.tool —（被工具使用，必须支持：
    #   - class X(FunctionTool[Context]): 子类语法
    #   - @FunctionTool[...] 装饰器语法
    #   使用普通对象作为基类以避免 MagicMock.__setattr__ 干扰
    _tool_mod = _mkmod("astrbot.core.agent.tool")
    _Ft = type("FunctionTool", (object,), {})
    _Ft.__class_getitem__ = classmethod(lambda cls, item: cls)  # type: ignore[attr-defined]
    _Ft.name = "mock_tool"  # type: ignore[attr-defined]
    _Ft.description = "mock tool"  # type: ignore[attr-defined]
    _tool_mod.FunctionTool = _Ft  # type: ignore[attr-defined]
    _tr_cls = type("ToolExecResult", (str,), {})
    _tool_mod.ToolExecResult = _tr_cls  # type: ignore[attr-defined]
    sys.modules["astrbot.core.agent.tool"] = _tool_mod

    # astrbot.core.astr_agent_context —（被工具使用）
    _aac = _mkmod("astrbot.core.astr_agent_context")
    _aac.AstrAgentContext = MagicMock  # type: ignore[attr-defined]
    sys.modules["astrbot.core.astr_agent_context"] = _aac

    # astrbot.core.provider —（被 plugin_initializer 使用）
    sys.modules["astrbot.core.provider"] = _mkpkg("astrbot.core.provider")
    _prov_mod = _mkmod("astrbot.core.provider.provider")
    _prov_mod.EmbeddingProvider = MagicMock  # type: ignore[attr-defined]
    _prov_mod.Provider = MagicMock  # type: ignore[attr-defined]
    sys.modules["astrbot.core.provider.provider"] = _prov_mod


_install_astrbot_mocks()


# ---------------------------------------------------------------------------
# LLM / Provider Mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_caller() -> AsyncMock:
    """异步可调用对象，返回预设的 JSON 摘要响应。

    在测试中覆盖 ``mock_llm_caller.return_value`` 以自定义输出。
    """
    default_response = json.dumps(
        {
            "summary": "用户和小明讨论了周末去西湖游玩的计划",
            "key_facts": ["周末计划去西湖", "小明想去划船", "用户想爬山"],
            "topics": ["周末计划", "西湖", "游玩"],
            "sentiment": "positive",
            "importance": 0.7,
            "emotional_intensity": 0.65,
        },
        ensure_ascii=False,
    )
    mock = AsyncMock(return_value=default_response)
    return mock


@pytest.fixture
def mock_llm_provider() -> MagicMock:
    """模拟 AstrBot LLM 提供者实例。"""
    provider = MagicMock()
    provider.model_name = "test-model"
    return provider


# ---------------------------------------------------------------------------
# 配置 Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_config_dict() -> dict[str, Any]:
    """适用于核心逻辑单元测试的最小配置字典。"""
    return {
        "recall_engine": {
            "top_k": 5,
            "max_k": 10,
            "query_rewrite_enabled": True,
            "privacy_filter_enabled": True,
            "testing_effect_async": True,
            "testing_effect_top_k": 5,
            "serial_position_enabled": True,
            "spontaneous_recall_enabled": True,
            "spontaneous_recall_probability": 0.06,
            "spontaneous_recall_k": 2,
            "prospective_recall_enabled": True,
            "prospective_lookahead_hours": 24.0,
            "prospective_recall_k": 3,
            "narrative_coherence_enabled": True,
            "interest_boost_enabled": True,
            "session_cache_enabled": True,
            "session_cache_ttl_seconds": 10.0,
            "injection_routing_mode": "manual",
            "injection_manual_preset": "balanced",
            "injection_auto_fallback_preset": "balanced",
            "injection_hybrid_base_preset": "balanced",
            "injection_hybrid_min_preset": "low_cost",
            "injection_hybrid_max_preset": "quality",
            "injection_delivery_override": "auto",
            "injection_preset_overrides_enabled": False,
            "injection_budget_chars": 0,
            "injection_memory_max_chars": 0,
            "injection_metadata_max_chars": 0,
            "auto_remove_injected": True,
            "inject_with_recent_context": False,
        },
        "fusion_strategy": {
            "document_route_weight": 0.65,
            "graph_route_weight": 0.35,
            "dynamic_route_weighting": True,
            "rrf_k": 60,
        },
        "hybrid_scoring": {
            "score_alpha": 0.5,
            "score_beta": 0.25,
            "score_gamma": 0.25,
            "mmr_lambda": 0.7,
        },
        "graph_memory_enabled": False,
        "atom_enabled": False,
        "graph_expansion_hops": 1,
        "filtering_settings": {
            "use_persona_filtering": True,
            "use_session_filtering": True,
        },
        "user_profile": {
            "enabled": True,
            "boost_strength": 0.15,
            "tag_decay_rate": 0.98,
            "min_tag_confidence": 0.1,
        },
        "auto_learning": {
            "enabled": True,
            "learning_rate": 0.01,
            "target_hit_rate_low": 0.3,
            "target_hit_rate_high": 0.7,
            "quality_ema_alpha": 0.2,
        },
        "knowledge_base": {
            "enabled": True,
            "dedup_threshold": 0.85,
            "expire_days": 365,
        },
        "notes": {
            "enabled": True,
            "auto_create_min_length": 50,
            "max_tags": 10,
        },
        "write_reliability": {
            "repair_enabled": True,
            "max_retries": 3,
        },
        "search_cache_enabled": True,
        "search_cache_ttl_seconds": 45.0,
        "search_cache_max_size": 256,
        "decay_rate": 0.01,
        "importance_decay": {
            "daily_decay_rate": 0.01,
        },
    }


@pytest.fixture
def test_config(test_config_dict: dict[str, Any]) -> Any:
    """基于 *test_config_dict* 的 ConfigManager 式对象。

    返回一个简单包装器，使得测试不需要完整的 Pydantic
    验证链，除非它们专门测试配置验证。
    """

    class _TestConfig:
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        def get(self, key: str, default: Any = None) -> Any:
            parts = key.split(".")
            node: Any = self._data
            for p in parts:
                if isinstance(node, dict):
                    node = node.get(p)
                else:
                    return default
            return node if node is not None else default

        @property
        def filtering_settings(self) -> dict[str, Any]:
            return self._data.get("filtering_settings", {})

    return _TestConfig(test_config_dict)


# ---------------------------------------------------------------------------
# 数据库 Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path() -> str:
    """临时 SQLite 数据库路径（基于文件，而非 :memory:）。

    使用真实文件使得测试基于 aiosqlite 的代码更加容易。
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    yield tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# MemoryAtom / 模型辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_atoms() -> list[dict[str, Any]]:
    """覆盖全部五种类型的一小组记忆原子字典。"""
    now = __import__("time").time()
    return [
        {
            "atom_type": "EPISODIC",
            "content": "周末和小明去了西湖划船",
            "importance": 0.75,
            "emotional_intensity": 0.80,
            "topics": ["周末", "西湖", "划船"],
            "emotion_tags": ["joy", "excited"],
            "event_time": now - 86400 * 3,
            "ttl_days": 30.0,
            "reinforcement_count": 2,
        },
        {
            "atom_type": "FACTUAL",
            "content": "西湖是杭州最著名的景点",
            "importance": 0.60,
            "emotional_intensity": 0.40,
            "topics": ["西湖", "杭州", "景点"],
            "emotion_tags": ["neutral"],
            "ttl_days": 180.0,
            "reinforcement_count": 0,
        },
        {
            "atom_type": "PREFERENCE",
            "content": "用户喜欢喝咖啡尤其是拿铁",
            "importance": 0.55,
            "emotional_intensity": 0.50,
            "topics": ["咖啡", "拿铁", "偏好"],
            "emotion_tags": ["happy"],
            "ttl_days": 60.0,
            "reinforcement_count": 1,
        },
        {
            "atom_type": "RELATIONAL",
            "content": "小明是用户的大学室友",
            "importance": 0.70,
            "emotional_intensity": 0.55,
            "topics": ["小明", "室友", "大学"],
            "emotion_tags": ["nostalgic"],
            "ttl_days": 90.0,
            "reinforcement_count": 3,
        },
        {
            "atom_type": "PLANNED",
            "content": "下周三月度项目评审会议",
            "importance": 0.80,
            "emotional_intensity": 0.45,
            "topics": ["会议", "项目", "评审"],
            "emotion_tags": ["neutral"],
            "event_time": now + 86400 * 5,
            "ttl_days": 7.0,
            "reinforcement_count": 0,
        },
    ]


# ---------------------------------------------------------------------------
# 事件 / 上下文 Mock（用于处理器测试）
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_event() -> MagicMock:
    """模拟 AstrBot 的 ``AstrMessageEvent``。"""
    event = MagicMock()
    event.unified_msg_origin = "test-session-001"
    event.get_message_type.return_value = MagicMock(value="GROUP_MESSAGE")  # type: ignore[union-attr]
    event.session_id = "test-session-001"
    return event


@pytest.fixture
def mock_context() -> MagicMock:
    """模拟 AstrBot 插件的 ``Context``。"""
    ctx = MagicMock()
    ctx.get_using_provider.return_value = None
    ctx.get_registered_llm_tools.return_value = []
    return ctx


# ---------------------------------------------------------------------------
# 性能 / 功能委托 Mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_perf_tracker() -> MagicMock:
    """Mock PerfTracker，用于召回管线计时测试。"""
    tracker = MagicMock()
    tracker.get_perf_data.return_value = {
        "avg_total_ms": 120.5,
        "avg_bm25_ms": 15.0,
        "avg_vector_ms": 45.0,
        "avg_graph_ms": 30.0,
        "avg_rerank_ms": 25.0,
        "std_total_ms": 10.0,
        "count_total_ms": 42,
        "recent": [],
    }
    tracker.record = MagicMock()
    return tracker


@pytest.fixture
def mock_feature_delegation() -> MagicMock:
    """Mock FeatureDelegation — 模拟无伴侣插件激活的状态。"""
    fd = MagicMock()
    fd.should_delegate_jargon.return_value = False
    fd.should_delegate_expression.return_value = False
    fd.should_delegate_affection.return_value = False
    fd.should_delegate_reply.return_value = False
    fd.should_skip_persona_processing.return_value = False
    fd.should_skip_style_extraction.return_value = False
    fd.self_learning_plugin.return_value = None
    fd.chatplus_plugin.return_value = None
    fd.log_status = MagicMock()
    fd.get_delegation_status.return_value = {
        "self_learning_active": False,
        "self_learning_label": None,
        "chatplus_active": False,
        "chatplus_label": None,
        "delegated_jargon": False,
        "delegated_expression": False,
        "delegated_affection": False,
        "delegated_reply": False,
    }
    return fd


@pytest.fixture
def mock_feature_delegation_with_self_learning() -> MagicMock:
    """Mock FeatureDelegation — 模拟 self_learning 插件激活的状态。"""
    fd = MagicMock()
    fd.should_delegate_jargon.return_value = True
    fd.should_delegate_expression.return_value = True
    fd.should_delegate_affection.return_value = True
    fd.should_delegate_reply.return_value = False
    fd.should_skip_persona_processing.return_value = True
    fd.should_skip_style_extraction.return_value = True
    fd.self_learning_plugin.return_value = MagicMock()  # not None = active
    fd.chatplus_plugin.return_value = None
    fd.log_status = MagicMock()
    fd.get_delegation_status.return_value = {
        "self_learning_active": True,
        "self_learning_label": "SelfLearning",
        "chatplus_active": False,
        "chatplus_label": None,
        "delegated_jargon": True,
        "delegated_expression": True,
        "delegated_affection": True,
        "delegated_reply": False,
    }
    return fd


@pytest.fixture
def mock_feature_delegation_with_chatplus() -> MagicMock:
    """Mock FeatureDelegation — 模拟 GroupChatPlus 插件激活的状态。"""
    fd = MagicMock()
    fd.should_delegate_jargon.return_value = False
    fd.should_delegate_expression.return_value = False
    fd.should_delegate_affection.return_value = False
    fd.should_delegate_reply.return_value = True
    fd.should_skip_persona_processing.return_value = False
    fd.should_skip_style_extraction.return_value = False
    fd.self_learning_plugin.return_value = None
    fd.chatplus_plugin.return_value = MagicMock()  # not None = active
    fd.log_status = MagicMock()
    fd.get_delegation_status.return_value = {
        "self_learning_active": False,
        "self_learning_label": None,
        "chatplus_active": True,
        "chatplus_label": "ChatPlus",
        "delegated_jargon": False,
        "delegated_expression": False,
        "delegated_affection": False,
        "delegated_reply": True,
    }
    return fd


@pytest.fixture
def mock_monitored_context():
    """在测试期间启用调试监控，然后恢复默认设置。"""
    from core.monitoring.instrumentation import set_debug_mode, set_trace_enabled

    set_debug_mode(True)
    set_trace_enabled(True)
    yield
    set_debug_mode(False)
    set_trace_enabled(False)
