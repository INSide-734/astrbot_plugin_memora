"""
发送者名称解析模块
从 AstrBot 事件中提取并规范化发送者名称。

将发送者名称解析逻辑从 ConversationManager 中提取为独立模块，
所有函数为模块级纯函数，不依赖类实例。
"""

_UNKNOWN_SENDER_NAMES = {
    "",
    "unknown",
    "Unknown",
    "none",
    "null",
    "n/a",
    "na",
    "user",
    "user_",
    "tg",
    "未知",
}


def _normalize_sender_name(value) -> str | None:
    """过滤平台占位昵称，保留可读名称。"""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _UNKNOWN_SENDER_NAMES:
        return None
    return text


def _raw_get(obj, key: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _format_raw_user_name(raw_user, sender_id: str | None) -> str | None:
    username = _normalize_sender_name(_raw_get(raw_user, "username"))
    if username:
        return username

    first_name = _normalize_sender_name(_raw_get(raw_user, "first_name"))
    last_name = _normalize_sender_name(_raw_get(raw_user, "last_name"))
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if full_name:
        return full_name

    display_name = _normalize_sender_name(_raw_get(raw_user, "full_name"))
    if display_name:
        return display_name

    raw_id = _normalize_sender_name(_raw_get(raw_user, "id"))
    if raw_id:
        return raw_id

    return _normalize_sender_name(sender_id)


def _iter_raw_sender_candidates(event):
    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    for source in (
        raw_message,
        _raw_get(raw_message, "message"),
        _raw_get(raw_message, "effective_message"),
        _raw_get(raw_message, "callback_query"),
    ):
        raw_user = _raw_get(source, "from_user")
        if raw_user is not None:
            yield raw_user
    effective_user = _raw_get(raw_message, "effective_user")
    if effective_user is not None:
        yield effective_user


def _resolve_sender_name(event, sender_id: str | None) -> str | None:
    sender_name = None
    if hasattr(event, "get_sender_name"):
        sender_name = event.get_sender_name()
    elif hasattr(event, "sender_name"):
        sender_name = event.sender_name

    normalized = _normalize_sender_name(sender_name)

    # Telegram 等平台的回退方案: 如果 sender_name 是占位符
    # (例如 "Unknown")，尝试从原始发送者中提取 first_name + last_name
    if not normalized:
        message_obj = getattr(event, "message_obj", None)
        raw_sender = getattr(message_obj, "sender", None)
        first_name = _normalize_sender_name(_raw_get(raw_sender, "first_name"))
        last_name = _normalize_sender_name(_raw_get(raw_sender, "last_name"))
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if full_name:
            return full_name

    if normalized:
        return normalized

    message_obj = getattr(event, "message_obj", None)
    raw_sender = getattr(message_obj, "sender", None)
    raw_nickname = _normalize_sender_name(_raw_get(raw_sender, "nickname"))
    if raw_nickname:
        return raw_nickname

    for raw_user in _iter_raw_sender_candidates(event):
        candidate = _format_raw_user_name(raw_user, sender_id)
        if candidate:
            return candidate

    return _normalize_sender_name(sender_id)
