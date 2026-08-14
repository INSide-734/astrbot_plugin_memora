"""AstrBot 上下文辅助：人格 ID 解析与时区时间。"""

from __future__ import annotations

from datetime import datetime

import pytz
from astrbot.api import logger, sp
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context


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


def get_now_datetime(tz_str: str = "Asia/Shanghai") -> datetime:
    """
    获取当前时间，并根据指定的时区设置时区。

    参数：
        tz_str: 时区字符串，默认为 "Asia/Shanghai"。

    返回：
        带有时区信息的当前时间。
    """
    # 如果传入的是 Context 对象，则使用从上下文获取时间的方法
    if isinstance(tz_str, Context):
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
    "get_persona_id",
    "get_now_datetime",
    "get_now_datetime_from_context",
]
