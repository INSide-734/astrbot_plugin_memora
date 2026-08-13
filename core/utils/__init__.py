"""
utils 子模块
"""

import re
from datetime import datetime

import pytz
from astrbot.api import logger, sp
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..features.injection.application.memory_formatter import (
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
)
from ..features.recall.processors.text_processor import TextProcessor
from ..shared.cache_manager import CacheManager, get_cache_manager
from ..shared.data_helpers import (
    OperationContext,
    retry_on_failure,
    safe_parse_metadata,
    safe_serialize_metadata,
    validate_timestamp,
)
from ..shared.json_utils import (
    _convert_single_quotes,
    clean_control_characters,
    clean_markdown_blocks,
    detect_llm_provider,
    extract_json_content,
    fix_common_json_errors,
    remove_thinking_content,
    safe_parse_llm_json,
)
from .diversity_manager import (
    EXPRESSION_VARIATIONS,
    LANGUAGE_STYLES,
    RESPONSE_PATTERNS,
    TEMPERATURE_RANGES,
    HomogeneityReport,
    ResponseDiversityManager,
    VariationComposition,
)
from .stopwords_manager import StopwordsManager, get_stopwords_manager
from .style_analyzer import StyleAnalyzer, StyleEvolution, StyleProfile
from .task_scheduler import TaskScheduler, get_task_scheduler


async def get_persona_id(context: Context, event: AstrMessageEvent) -> str | None:
    """
    获取当前会话的人格 ID，与 AstrBot 主流程保持完全一致的三级优先级：
      1. session_service_config（最高，由 /persona 等命令写入）
      2. conversation.persona_id（会话级绑定）
      3. 全局默认人格（最低）
    """
    try:
        umo = event.unified_msg_origin

        # 优先级 1：session_service_config（与 _ensure_persona_and_skills 一致）
        session_persona_id: str | None = (
            await sp.get_async(
                scope="umo",
                scope_id=umo,
                key="session_service_config",
                default={},
            )
        ).get("persona_id")

        if session_persona_id:
            logger.debug(
                f"[get_persona_id] [{umo}] 使用 session_service_config 人格: {session_persona_id}"
            )
            return session_persona_id

        # 优先级 2：conversation.persona_id
        session_id = await context.conversation_manager.get_curr_conversation_id(umo)
        if session_id is None:
            logger.debug(f"[get_persona_id] [{umo}] 无当前会话，跳至默认人格")
        else:
            conversation = await context.conversation_manager.get_conversation(
                umo, session_id
            )
            persona_id = conversation.persona_id if conversation else None

            logger.debug(
                f"[get_persona_id] [{umo}] 会话={session_id}, "
                f"会话人格={persona_id or '未设置'}"
            )

            if persona_id == "[%None]":
                # 明确设置为无人格
                logger.debug(f"[get_persona_id] [{umo}] 会话明确设置为无人格")
                return None

            if persona_id:
                logger.info(f"[get_persona_id] [{umo}] 最终使用人格: {persona_id}")
                return persona_id

        # 优先级 3：全局默认人格
        default_persona = await context.persona_manager.get_default_persona_v3(umo=umo)
        persona_id = default_persona["name"] if default_persona else None
        logger.debug(f"[get_persona_id] [{umo}] 使用默认人格: {persona_id or '未设置'}")
        logger.info(f"[get_persona_id] [{umo}] 最终使用人格: {persona_id or '无'}")
        return persona_id
    except Exception as e:
        logger.debug(f"获取人格ID失败: {e}")
        return None


def extract_json_from_response(text: str) -> str:
    """
    从可能包含 Markdown 代码块的文本中提取纯 JSON 字符串。
    """
    # 查找被 ```json ... ``` 或 ``` ... ``` 包围的内容
    match = re.search(r"```(json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        # 返回捕获组中的 JSON 部分
        return match.group(2)

    # 如果没有找到代码块，假设整个文本就是 JSON（可能需要去除首尾空格）
    return text.strip()


def get_now_datetime(tz_str: str = "Asia/Shanghai") -> datetime:
    """
    获取当前时间，并根据指定的时区设置时区。

    参数：
        tz_str: 时区字符串，默认为 "Asia/Shanghai"。

    返回：
        带有时区信息的当前时间。
    """
    # 如果传入的是 Context 对象，则使用从上下文获取时间的方法
    # 检查传入的是否是 Context 对象
    if isinstance(tz_str, Context):
        # 如果是 Context 对象，调用专门的函数处理
        return get_now_datetime_from_context(tz_str)

    try:
        timezone = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        # 如果时区无效，则使用默认值
        logger.warning(f"无效的时区: {tz_str}，使用默认时区 Asia/Shanghai")
        timezone = pytz.timezone("Asia/Shanghai")

    return datetime.now(timezone)


def get_now_datetime_from_context(context: Context) -> datetime:
    """
    从上下文中获取当前时间，根据插件配置设置时区。

    参数：
        context: AstrBot 上下文对象。

    返回：
        带有时区信息的当前时间。
    """
    try:
        # 尝试从配置中获取时区
        if hasattr(context, "plugin_config"):
            config = getattr(context, "plugin_config", {})
            if isinstance(config, dict):
                tz_str = config.get("timezone_settings", {}).get(
                    "timezone", "Asia/Shanghai"
                )
                return get_now_datetime(tz_str)
        # 如果配置不存在，则使用默认值
        return get_now_datetime()
    except (AttributeError, KeyError):
        # 如果配置不存在，则使用默认值
        return get_now_datetime()


__all__ = [
    "StopwordsManager",
    "get_stopwords_manager",
    "TextProcessor",
    "safe_parse_metadata",
    "safe_serialize_metadata",
    "validate_timestamp",
    "retry_on_failure",
    "OperationContext",
    "get_persona_id",
    "extract_json_from_response",
    "get_now_datetime",
    "get_now_datetime_from_context",
    "format_memories_for_injection",
    "format_memories_for_fake_tool_call",
    "format_memories_for_fake_tool_call_deepseek_v4",
    # JSON 工具
    "safe_parse_llm_json",
    "remove_thinking_content",
    "clean_markdown_blocks",
    "clean_control_characters",
    "extract_json_content",
    "fix_common_json_errors",
    "_convert_single_quotes",
    "detect_llm_provider",
    # 缓存管理
    "CacheManager",
    "get_cache_manager",
    # 任务调度
    "TaskScheduler",
    "get_task_scheduler",
    # 多样性管理
    "ResponseDiversityManager",
    "HomogeneityReport",
    "VariationComposition",
    "LANGUAGE_STYLES",
    "RESPONSE_PATTERNS",
    "EXPRESSION_VARIATIONS",
    "TEMPERATURE_RANGES",
    # 风格分析
    "StyleAnalyzer",
    "StyleProfile",
    "StyleEvolution",
]
