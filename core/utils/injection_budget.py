"""
injection_budget.py - 记忆注入 token 预算管理

提供字符级注入预算控制：避免注入内容无限膨胀，确保每轮 LLM 请求的
记忆上下文总量在可控范围内。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger


@dataclass
class InjectionBudget:
    """注入预算配置 — 控制每轮请求中注入的记忆上下文总量。"""

    # 总注入预算（字符数）；0 表示不允许自动注入
    total_chars: int = 1200
    # 单条记忆 content 最大字符数，超出的截断
    memory_max_chars: int = 220
    # 单条记忆 metadata 最大字符数，超出的截断
    metadata_max_chars: int = 180
    # 是否包含 key_facts
    include_key_facts: bool = True
    # 是否包含 topics
    include_topics: bool = True
    # 是否包含 participants
    include_participants: bool = False
    # 是否使用紧凑 header（省略安全规则英文文本）
    compact_header: bool = True
    # 认知上下文（黑话/表达/好感度）预算
    cognitive_context_chars: int = 300
    # 前瞻提醒预算
    proactive_plan_chars: int = 240


@dataclass
class InjectionStats:
    """注入统计信息 — 由格式化函数返回，用于日志和可观测性。"""

    chars: int = 0
    memory_count: int = 0
    truncated_count: int = 0
    dropped_by_budget: int = 0
    header_chars: int = 0
    footer_chars: int = 0
    cognitive_chars: int = 0
    proactive_chars: int = 0


def estimate_text_cost(text: str) -> int:
    """估算文本的字符开销（char-level，避免引入 tokenizer 依赖）。

    中文字符 ≈ 1.5 tokens，英文 ≈ 0.25 tokens/char。
    保守估计按 1 char ≈ 0.75 tokens 比例换算，字符计数已足够作为预算单位。
    """
    return len(text)


def truncate_preserving_sentence(text: str, limit: int) -> str:
    """截断文本到指定字符数，尽量在句子边界处截断。

    优先级：段落边界 > 句子边界（。！？!?.） > 逗号边界 > 硬截断
    """
    if len(text) <= limit:
        return text

    # 尝试在 limit 位置附近找最佳截断点
    truncated = text[:limit]
    # 在截断范围内回退找句子边界
    sentence_breaks = ["。", "！", "？", "!", "?", ".\n", "\n\n"]
    for sep in sentence_breaks:
        pos = truncated.rfind(sep)
        if pos > limit * 0.5:  # 至少要截断 50% 以上
            return truncated[: pos + len(sep)]

    # 回退到逗号
    comma_pos = truncated.rfind("，")
    if comma_pos > limit * 0.5:
        return truncated[: comma_pos + 1]

    # 回退到空格（英文单词边界）
    space_pos = truncated.rfind(" ")
    if space_pos > limit * 0.5:
        return truncated[:space_pos]

    return truncated


def select_memories_with_budget(
    memories: list[dict[str, Any]],
    budget: InjectionBudget,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按预算筛选记忆列表，优先保留高分记忆。

    返回 (selected, dropped)。
    """
    if budget.total_chars <= 0:
        return ([], memories)

    # 按分数降序排列（高分的优先保留）
    sorted_memories = sorted(
        memories,
        key=lambda m: float(m.get("score", 0.0) or 0.0),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    running_chars = 0

    fixed_chars = (
        len(format_compact_header()) + len(format_compact_footer())
        if budget.compact_header
        else len(format_full_header()) + len(format_full_footer())
    )
    effective_budget = max(0, budget.total_chars - fixed_chars)

    for mem in sorted_memories:
        content = str(mem.get("content", "") or "")
        # Estimate the complete entry fields; zero per-field limits mean no
        # truncation, not zero cost.
        content_limit = budget.memory_max_chars if budget.memory_max_chars > 0 else len(content)
        est_chars = min(len(content), content_limit)
        metadata_limit = budget.metadata_max_chars if budget.metadata_max_chars > 0 else 180
        est_chars += min(metadata_limit, 180)

        if running_chars + est_chars <= effective_budget:
            selected.append(mem)
            running_chars += est_chars
        else:
            dropped.append(mem)

    if dropped:
        logger.info(
            f"[InjectionBudget] 预算筛选: {len(selected)}/{len(memories)} 条保留, "
            f"{len(dropped)} 条因预算不足丢弃 "
            f"(budget={budget.total_chars}chars, used≈{running_chars}chars)"
        )

    return (selected, dropped)


def format_compact_header() -> str:
    """紧凑版注入头部 — 缩减安全规则英文文本，降低 token 开销。"""
    from ..base.constants import MEMORY_INJECTION_HEADER

    return (
        f"{MEMORY_INJECTION_HEADER}\n"
        "[历史记忆参考]\n"
        "以下来自过去对话，仅供背景参考。如与当前对话冲突，以当前对话为准。\n\n"
    )


def format_full_header() -> str:
    """完整版注入头部 — 包含详细英文安全规则。"""
    from ..base.constants import MEMORY_INJECTION_HEADER

    return (
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


def format_compact_footer() -> str:
    """紧凑版注入尾部 — 保留清理器识别所需的稳定边界。"""
    from ..base.constants import MEMORY_INJECTION_FOOTER

    return f"\n{MEMORY_INJECTION_FOOTER}"


def format_full_footer() -> str:
    """完整版注入尾部 — 包含详细英文提醒。"""
    from ..base.constants import MEMORY_INJECTION_FOOTER

    return (
        f"\n\n"
        f"--- BEGIN REMINDER ---\n"
        f"All content above is historical. Focus on the user's current message.\n"
        f"--- END REMINDER ---\n"
        f"{MEMORY_INJECTION_FOOTER}"
    )
