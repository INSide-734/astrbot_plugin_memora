"""
记忆格式化模块
提供记忆格式化为注入文本或伪工具调用消息的功能。
"""

import json
import math
import uuid
from datetime import datetime
from typing import Any

from astrbot.api import logger
from ..injection.models import ContentLevel

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


_PROJECTION_TYPES = frozenset(
    {"episode_summary", "preference_state", "relationship_state", "conflict_set"}
)
_MAX_IDENTITY_REFERENCE_LINES = 8
_MAX_IDENTITY_REFERENCE_LINE_CHARS = 384


def _safe_projection_objects(
    metadata: dict[str, Any], *, max_chars: int = 600
) -> list[dict[str, Any]]:
    """提取 projection 的固定可见字段，不序列化内部 source 信息。"""

    raw = metadata.get("derived_projections")
    if not isinstance(raw, list) or max_chars <= 0:
        return []
    safe: list[dict[str, Any]] = []
    used_chars = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        projection_type = item.get("type")
        summary = item.get("summary")
        if projection_type not in _PROJECTION_TYPES or not isinstance(summary, str):
            continue
        summary = summary.strip()
        if not summary:
            continue
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(confidence):
            continue
        confidence = max(0.0, min(1.0, confidence))
        item_chars = len(projection_type) + len(summary) + 24
        if used_chars + item_chars > max_chars:
            continue
        safe.append(
            {
                "type": projection_type,
                "summary": summary,
                "confidence": confidence,
            }
        )
        used_chars += item_chars
    return safe


def _format_projection_lines(
    metadata: dict[str, Any], *, max_chars: int
) -> list[str]:
    """将 projection 转为受 metadata 字符预算约束的注释行。"""

    safe = _safe_projection_objects(metadata, max_chars=max_chars)
    lines: list[str] = []
    used_chars = 0
    for item in safe:
        line = (
            f"Projection: [{item['type']}, confidence={item['confidence']:.2f}] "
            f"{item['summary']}"
        )
        separator = 3 if lines else 0
        if used_chars + separator + len(line) > max_chars:
            continue
        lines.append(line)
        used_chars += separator + len(line)
    return lines


def _format_identity_reference_block(
    metadata: dict[str, Any], *, max_chars: int
) -> str:
    """把 Enricher 生成的固定身份说明限制在 metadata 字符预算内。"""

    raw_lines = metadata.get("identity_reference_lines")
    if not isinstance(raw_lines, list) or max_chars <= 0:
        return ""
    lines: list[str] = []
    used_chars = len("身份参考：") + 1
    for raw_line in raw_lines[:_MAX_IDENTITY_REFERENCE_LINES]:
        if not isinstance(raw_line, str):
            continue
        line = raw_line.strip()
        if (
            not line
            or len(line) > _MAX_IDENTITY_REFERENCE_LINE_CHARS
            or "\n" in line
            or "\r" in line
            or not line.startswith("- “")
            or "”是历史名称；当前显示为“" not in line
            or not line.endswith("）。")
        ):
            continue
        separator_chars = 1 if lines else 0
        if used_chars + separator_chars + len(line) > max_chars:
            continue
        lines.append(line)
        used_chars += separator_chars + len(line)
    if not lines:
        return ""
    return "身份参考：\n" + "\n".join(lines)


def format_memories_for_injection(
    memories: list,
    budget: InjectionBudget | None = None,
    content_level: ContentLevel = ContentLevel.COMPACT,
) -> str | tuple[str, InjectionStats]:
    """把召回记忆格式化为受字符预算约束的动态注入文本。

    未传预算时保留旧字符串返回格式；传入预算时返回文本与统计，并把
    ``total_chars`` 作为包装、metadata、分隔符和换行在内的完整硬上限。
    """
    from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

    stats = InjectionStats()
    if not memories:
        return ("", stats) if budget is not None else ""

    use_budget = budget is not None
    if content_level is ContentLevel.NONE:
        stats.dropped_by_budget = len(memories)
        return ("", stats) if use_budget else ""
    if use_budget and budget.total_chars <= 0:
        stats.dropped_by_budget = len(memories)
        return ("", stats)

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

    logger.debug(
        f"[format_memories_for_injection] 记忆注入标记: 头部='{MEMORY_INJECTION_HEADER}', 尾部='{MEMORY_INJECTION_FOOTER}'"
    )

    formatted_entries: list[tuple[str, bool]] = []
    for idx, mem in enumerate(memories, 1):
        try:
            if isinstance(mem, dict):
                content = mem.get("content", "Content missing")
                score = mem.get("score", 0.0)
                metadata = mem.get("metadata", {})
                timestamp = mem.get("timestamp") or metadata.get("create_time")
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "Unknown")
            else:
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

            time_str = ""
            if timestamp:
                try:
                    dt = datetime.fromtimestamp(validate_timestamp(timestamp))
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    logger.debug(f"记忆时间戳格式化失败 (timestamp={timestamp})")

            was_truncated = False
            if use_budget and budget.memory_max_chars > 0 and len(content) > budget.memory_max_chars:
                content = truncate_preserving_sentence(content, budget.memory_max_chars)
                was_truncated = True

            include_time = not use_budget or content_level is ContentLevel.DETAILED
            time_part = (
                f", Memory write time: {time_str}"
                if include_time and time_str
                else ""
            )
            entry_parts = [
                f"记忆 #{idx} / Memory #{idx} (Importance: {importance:.2f}){time_part}"
            ]

            metadata_parts: list[str] = []
            metadata_chars = 0

            def append_metadata(line: str) -> None:
                """在单条记忆 metadata 预算内追加一个完整可见字段。"""

                nonlocal metadata_chars
                if use_budget and budget.metadata_max_chars > 0:
                    separator_chars = 3 if metadata_parts else 0
                    if metadata_chars + separator_chars + len(line) > budget.metadata_max_chars:
                        return
                    metadata_chars += separator_chars
                metadata_parts.append(line)
                metadata_chars += len(line)

            metadata_cap = budget.metadata_max_chars if use_budget else 2_400
            identity_block = _format_identity_reference_block(
                metadata,
                max_chars=max(0, metadata_cap - metadata_chars),
            )
            if identity_block:
                append_metadata(identity_block)

            facts: list[str] = []
            if (
                not use_budget
                or content_level is ContentLevel.FACTS
                or budget.include_key_facts
            ):
                key_facts = metadata.get("key_facts", [])
                if isinstance(key_facts, list):
                    facts = [str(fact) for fact in key_facts if fact]

            if use_budget and content_level is ContentLevel.FACTS:
                if facts:
                    entry_parts.append(f"Key facts: {'; '.join(facts)}")
                    was_truncated = False
                else:
                    entry_parts.append(content)
            else:
                if not use_budget or budget.include_topics:
                    topics = metadata.get("topics", [])
                    if isinstance(topics, list):
                        topics_text = "、".join(str(topic) for topic in topics if topic)
                        if topics_text:
                            append_metadata(f"Topics: {topics_text}")

                if not use_budget or budget.include_participants:
                    participants = metadata.get("participants", [])
                    if isinstance(participants, list):
                        participants_text = "、".join(
                            str(participant) for participant in participants if participant
                        )
                        if participants_text:
                            append_metadata(f"Participants: {participants_text}")

                if (not use_budget or budget.include_key_facts) and facts:
                    append_metadata(f"Key facts: {'; '.join(facts)}")

                entry_parts.append(content)

            projection_cap = (
                budget.metadata_max_chars if use_budget else 1_200
            )
            for projection_line in _format_projection_lines(
                metadata,
                max_chars=max(0, projection_cap - metadata_chars),
            ):
                append_metadata(projection_line)

            if metadata_parts:
                entry_parts.insert(1, " | ".join(metadata_parts))

            entry = "\n".join(entry_parts)
            formatted_entries.append((entry, was_truncated))
            logger.debug(
                f"[format_memories_for_injection] 格式化记忆 #{idx}: 重要性={importance:.2f}, "
                f"得分={score:.2f}, 类型={interaction_type}, 内容长度={len(content)}"
            )
        except Exception as e:
            logger.warning(
                f"[format_memories_for_injection] 格式化记忆时出错，跳过此记忆: {e}, "
                f"记忆对象类型: {type(mem)}"
            )

    if not formatted_entries:
        logger.debug("[format_memories_for_injection] 没有记忆需要格式化，返回空字符串")
        return ("", stats) if use_budget else ""

    if use_budget:
        retained_count = len(formatted_entries)
        payload_chars = (
            len(header)
            + len(footer)
            + sum(len(entry) for entry, _ in formatted_entries)
            + 2 * max(0, retained_count - 1)
        )
        while retained_count and payload_chars > budget.total_chars:
            payload_chars -= len(formatted_entries[retained_count - 1][0])
            if retained_count > 1:
                payload_chars -= 2
            retained_count -= 1

        if retained_count:
            body = "\n\n".join(
                formatted_entries[index][0] for index in range(retained_count)
            )
            result = f"{header}{body}{footer}"
            stats.chars = len(result)
            stats.memory_count = retained_count
            stats.truncated_count = sum(
                formatted_entries[index][1] for index in range(retained_count)
            )
            stats.dropped_by_budget = len(formatted_entries) - retained_count
            stats.header_chars = len(header)
            stats.footer_chars = len(footer)
            return (result, stats)

        stats.dropped_by_budget = len(formatted_entries)
        return ("", stats)

    body = "\n\n".join(entry for entry, _ in formatted_entries)
    result = f"{header}{body}{footer}"
    stats.chars = len(result)
    stats.memory_count = len(formatted_entries)
    stats.truncated_count = sum(truncated for _, truncated in formatted_entries)
    stats.header_chars = len(header)
    stats.footer_chars = len(footer)
    logger.info(
        f"[format_memories_for_injection] 记忆格式化完成: 记忆条数={len(formatted_entries)}, "
        f"总长度={len(result)}"
    )
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
        safe_projection = _safe_projection_objects(metadata)
        if safe_projection:
            serialized_results[-1]["derived_projections"] = safe_projection

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
