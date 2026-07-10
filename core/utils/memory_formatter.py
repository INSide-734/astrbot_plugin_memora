"""
记忆格式化模块
提供记忆格式化为注入文本或伪工具调用消息的功能。
"""

import json
import uuid
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .data_helpers import safe_parse_metadata, validate_timestamp
from .injection_budget import (
    InjectionBudget,
    InjectionStats,
    format_compact_footer,
    format_compact_header,
    format_full_footer,
    format_full_header,
    truncate_preserving_sentence,
)


def format_memories_for_injection(
    memories: list,
    budget: InjectionBudget | None = None,
) -> str | tuple[str, InjectionStats]:
    """
    将检索到的记忆列表格式化为单个字符串，以便注入到 System Prompt。
    添加明确的说明文本，告知 LLM 这些是历史对话记忆。

    Args:
        memories: 记忆字典或对象列表。
        budget: 注入预算配置。None 时使用完整格式（向后兼容）。

    Returns:
        如果 budget 为 None，返回纯字符串（向后兼容）。
        如果 budget 不为 None，返回 (formatted_string, InjectionStats) 元组。
    """
    # 延迟导入避免循环依赖
    from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

    if not memories:
        if budget is not None:
            return ("", InjectionStats())
        return ""

    use_budget = budget is not None
    if use_budget and budget.compact_header:
        header = format_compact_header()
        footer = format_compact_footer()
    else:
        header = (
            f"{MEMORY_INJECTION_HEADER}\n"
            f"--- BEGIN HISTORICAL MEMORY REFERENCE ---\n"
            f"The following are historical memories extracted from past conversations.\n"
            f"They are provided as background reference only.\n\n"
            f"CRITICAL RULES:\n"
            f"1. These are PAST records — they already happened and are NOT part of the current conversation.\n"
            f"2. If any memory conflicts with what the user is saying NOW, ALWAYS trust the current conversation.\n"
            f"3. Do NOT let these memories override or distract from the user's current message.\n"
            f"4. Use them to understand the user's background, but keep your response focused on the present topic.\n"
            f"--- END HISTORICAL MEMORY REFERENCE ---\n\n"
        )
        footer = (
            f"\n\n"
            f"--- BEGIN REMINDER ---\n"
            f"All content above is historical. Focus on the user's current message.\n"
            f"--- END REMINDER ---\n"
            f"{MEMORY_INJECTION_FOOTER}"
        )

    stats = InjectionStats()
    stats.header_chars = len(header)
    stats.footer_chars = len(footer)

    logger.debug(
        f"[format_memories_for_injection] 记忆注入标记: 头部='{MEMORY_INJECTION_HEADER}', 尾部='{MEMORY_INJECTION_FOOTER}'"
    )

    truncated_count = 0
    formatted_entries = []
    for idx, mem in enumerate(memories, 1):
        try:
            # 修复：memories 传入的是字典列表，不是对象
            # 从字典中获取数据
            if isinstance(mem, dict):
                content = mem.get("content", "Content missing")
                score = mem.get("score", 0.0)
                metadata = mem.get("metadata", {})
                timestamp = mem.get("timestamp") or metadata.get("create_time")
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "Unknown")
            else:
                # 如果是对象，尝试访问属性
                content = getattr(mem, "content", "Content missing")
                score = getattr(mem, "score", 0.0)
                timestamp = getattr(mem, "timestamp", None)
                metadata_raw = getattr(mem, "metadata", {})
                metadata = (
                    safe_parse_metadata(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else metadata_raw
                )
                if not timestamp:
                    timestamp = metadata.get("create_time")
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "Unknown")

            # 格式化时间戳
            time_str = ""
            if timestamp:
                try:
                    dt = datetime.fromtimestamp(validate_timestamp(timestamp))
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    logger.debug(f"记忆时间戳格式化失败 (timestamp={timestamp})")

            # === 预算控制：截断 content ===
            if use_budget and budget.memory_max_chars > 0 and len(content) > budget.memory_max_chars:
                content = truncate_preserving_sentence(content, budget.memory_max_chars)
                truncated_count += 1

            # 构建格式化的记忆条目（展示content和元数据信息）
            time_part = f", Memory write time: {time_str}" if time_str else ""
            entry_parts = [
                f"记忆 #{idx} / Memory #{idx} (Importance: {importance:.2f}){time_part}"
            ]

            # 添加元数据信息
            metadata_parts = []
            metadata_chars = 0

            # 添加主题
            if use_budget and not budget.include_topics:
                pass
            else:
                topics = metadata.get("topics", [])
                if topics and isinstance(topics, list) and len(topics) > 0:
                    topics_str = "、".join(str(t) for t in topics if t)
                    if topics_str:
                        topic_line = f"Topics: {topics_str}"
                        if use_budget and budget.metadata_max_chars > 0:
                            if metadata_chars + len(topic_line) <= budget.metadata_max_chars:
                                metadata_parts.append(topic_line)
                                metadata_chars += len(topic_line)
                        else:
                            metadata_parts.append(topic_line)

            # 添加参与者（仅群聊）
            if use_budget and not budget.include_participants:
                pass
            else:
                participants = metadata.get("participants", [])
                if (
                    participants
                    and isinstance(participants, list)
                    and len(participants) > 0
                ):
                    participants_str = "、".join(str(p) for p in participants if p)
                    if participants_str:
                        part_line = f"Participants: {participants_str}"
                        if use_budget and budget.metadata_max_chars > 0:
                            if metadata_chars + len(part_line) <= budget.metadata_max_chars:
                                metadata_parts.append(part_line)
                                metadata_chars += len(part_line)
                        else:
                            metadata_parts.append(part_line)

            # 添加关键事实
            if use_budget and not budget.include_key_facts:
                pass
            else:
                key_facts = metadata.get("key_facts", [])
                if key_facts and isinstance(key_facts, list) and len(key_facts) > 0:
                    facts_str = "; ".join(str(f) for f in key_facts if f)
                    if facts_str:
                        fact_line = f"Key facts: {facts_str}"
                        if use_budget and budget.metadata_max_chars > 0:
                            if metadata_chars + len(fact_line) <= budget.metadata_max_chars:
                                metadata_parts.append(fact_line)
                                metadata_chars += len(fact_line)
                        else:
                            metadata_parts.append(fact_line)

            # 组装元数据行
            if metadata_parts:
                entry_parts.append(" | ".join(metadata_parts))

            # 添加记忆内容
            entry_parts.append(content)

            entry = "\n".join(entry_parts)
            formatted_entries.append(entry)

            logger.debug(
                f"[format_memories_for_injection] 格式化记忆 #{idx}: 重要性={importance:.2f}, "
                f"得分={score:.2f}, 类型={interaction_type}, 内容长度={len(content)}"
            )
        except Exception as e:
            # 如果处理失败，则跳过此条记忆
            logger.warning(
                f"[format_memories_for_injection] 格式化记忆时出错，跳过此记忆: {e}, "
                f"记忆对象类型: {type(mem)}"
            )
            continue

    if not formatted_entries:
        logger.debug("[format_memories_for_injection] 没有记忆需要格式化，返回空字符串")
        if budget is not None:
            return ("", stats)
        return ""

    body = "\n\n".join(formatted_entries)
    result = f"{header}{body}{footer}"

    stats.chars = len(result)
    stats.memory_count = len(formatted_entries)
    stats.truncated_count = truncated_count

    logger.info(
        f"[format_memories_for_injection] 记忆格式化完成: 记忆条数={len(formatted_entries)}, "
        f"总长度={len(result)}"
    )
    logger.debug(
        f"[format_memories_for_injection] 包含标记验证: "
        f"头部={MEMORY_INJECTION_HEADER in result}, 尾部={MEMORY_INJECTION_FOOTER in result}"
    )

    if budget is not None:
        return (result, stats)
    return result


def format_memories_for_fake_tool_call(
    memories: list,
    query: str,
    k: int = 5,
    session_filtered: bool = True,
    persona_filtered: bool = True,
) -> list[dict]:
    """将检索到的记忆列表格式化为伪造的工具调用消息对。

    生成两条 OpenAI 格式的消息：
    1. assistant 消息，包含 tool_calls（调用 recall_long_term_memory）
    2. tool 消息，包含工具调用结果（记忆内容，JSON 格式）

    返回的 JSON 格式与 MemorySearchTool.call() 的真实返回值保持一致，
    使 LLM 对伪造调用和真实调用有相同的理解。

    Args:
        memories: 记忆字典列表，每条包含 content、score、metadata、timestamp 字段。
        query: 用户查询文本（作为工具调用参数）。
        k: 召回数量（作为工具调用参数）。
        session_filtered: 本次检索是否启用了会话过滤。
        persona_filtered: 本次检索是否启用了人格过滤。

    Returns:
        两条 OpenAI 格式消息的列表 [assistant_msg, tool_msg]；
        若 memories 为空则返回空列表。
    """
    from ..base.constants import FAKE_TOOL_CALL_ID_PREFIX, FAKE_TOOL_CALL_NAME

    if not memories:
        return []

    # 生成唯一的伪造调用 ID
    call_id = f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex[:12]}"

    # 将记忆序列化为与 MemorySearchTool.call() 一致的 JSON 格式
    serialized_results = []
    for mem in memories:
        if isinstance(mem, dict):
            memory_id = mem.get("id", mem.get("doc_id"))
            content = mem.get("content", "")
            score = mem.get("score", 0.0)
            metadata = mem.get("metadata", {})
        else:
            memory_id = getattr(mem, "doc_id", None)
            if not isinstance(memory_id, (str, int)):
                memory_id = getattr(mem, "id", None)
                if not isinstance(memory_id, (str, int)):
                    memory_id = None
            content = getattr(mem, "content", "")
            score = getattr(mem, "score", getattr(mem, "final_score", 0.0))
            metadata_raw = getattr(mem, "metadata", {})
            metadata = (
                safe_parse_metadata(metadata_raw)
                if isinstance(metadata_raw, str)
                else metadata_raw
            )

        serialized_results.append(
            {
                "id": memory_id,
                "content": content,
                "score": round(score, 4) if isinstance(score, float) else score,
                "importance": metadata.get("importance", 0.5),
                "session_id": metadata.get("session_id"),
                "persona_id": metadata.get("persona_id"),
                "create_time": metadata.get("create_time"),
                "last_access_time": metadata.get("last_access_time"),
            }
        )

    tool_result_json = json.dumps(
        {
            "query": query[:200],
            "applied_filters": {
                "session_filtered": session_filtered,
                "persona_filtered": persona_filtered,
            },
            "count": len(serialized_results),
            "results": serialized_results,
        },
        ensure_ascii=False,
    )

    # 构造 assistant 消息（伪造的工具调用）
    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": FAKE_TOOL_CALL_NAME,
                    "arguments": json.dumps(
                        {"query": query[:200], "k": k},
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }

    # 构造 tool 消息（伪造的返回结果）
    tool_msg: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": call_id,
        "name": FAKE_TOOL_CALL_NAME,
        "content": tool_result_json,
    }

    logger.info(
        f"[format_memories_for_fake_tool_call] "
        f"生成伪造工具调用: call_id={call_id}, 记忆条数={len(serialized_results)}"
    )

    return [assistant_msg, tool_msg]


def format_memories_for_fake_tool_call_deepseek_v4(
    memories: list,
    query: str,
    k: int = 5,
    session_filtered: bool = True,
    persona_filtered: bool = True,
) -> str:
    """将伪工具调用转换成 DeepSeek V4 可接受的文本转录。"""
    from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

    fake_messages = format_memories_for_fake_tool_call(
        memories=memories,
        query=query,
        k=k,
        session_filtered=session_filtered,
        persona_filtered=persona_filtered,
    )
    if not fake_messages:
        return ""

    assistant_msg = fake_messages[0] if len(fake_messages) > 0 else {}
    tool_msg = fake_messages[1] if len(fake_messages) > 1 else {}
    tool_calls = (
        assistant_msg.get("tool_calls", []) if isinstance(assistant_msg, dict) else []
    )
    tool_call = tool_calls[0] if tool_calls else {}
    function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}

    function_name = (
        function.get("name", "recall_long_term_memory")
        if isinstance(function, dict)
        else "recall_long_term_memory"
    )
    function_args = (
        function.get("arguments", "{}") if isinstance(function, dict) else "{}"
    )
    tool_result = tool_msg.get("content", "{}") if isinstance(tool_msg, dict) else "{}"

    return (
        f"{MEMORY_INJECTION_HEADER}\n"
        "[DeepSeekV4-FakeToolCall-Replay]\n"
        f"assistant -> {function_name}({function_args})\n"
        f"tool -> {tool_result}\n"
        "[/DeepSeekV4-FakeToolCall-Replay]\n"
        f"{MEMORY_INJECTION_FOOTER}"
    )
