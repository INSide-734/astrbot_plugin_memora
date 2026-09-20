"""事实文本单 owner 的纯判定：规范化、正文包含判定与对齐三态。

``documents.text`` 是唯一权威事实文本；``key_facts`` 与 ``fact_source_evidence``
只是同一表示的准入元数据（v2 表示即 ``"；".join(key_facts)``）。正文被改写后
残留的旧事实不能继续作为派生准入条件：图抽取、注入选择与近重复 merge 都必须
先用这里的判定确认条目仍属于当前正文，再决定消费或按「无事实元数据」回落。

本模块是纯函数：不访问数据库、不写日志、不修改输入、不抛业务异常。判定结果
只表达「对齐 / 不一致 / 不可判定」，是否回落、是否拒绝由调用方按各自平面决定。
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Any

__all__ = [
    "FactTextAlignment",
    "fact_evidence_paired",
    "fact_in_content",
    "facts_aligned",
    "normalize_fact",
]


class FactTextAlignment(StrEnum):
    """事实元数据与当前正文的关系。

    - ``ALIGNED``：记录的事实条目仍出现在当前正文中。
    - ``MISALIGNED``：存在事实已不在正文中（典型为「新正文 + 旧事实」）。
    - ``UNDETERMINABLE``：没有可判定的表示（未记录事实、事实与证据未一一对应、
      或当前正文缺失），调用方按「无事实元数据」处理，不能当成对齐。
    """

    ALIGNED = "aligned"
    MISALIGNED = "misaligned"
    UNDETERMINABLE = "undeterminable"


def normalize_fact(text: str) -> str:
    """把事实文本规范化为比较键（NFKC + casefold + 空白折叠）。"""

    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def fact_in_content(content: Any, fact: Any) -> bool:
    """判断规范化事实条目是否仍出现在当前正文中；非法或空输入返回 ``False``。"""

    if not isinstance(content, str) or not isinstance(fact, str):
        return False
    normalized = normalize_fact(fact)
    if not normalized:
        return False
    return normalized in normalize_fact(content)


def fact_evidence_paired(key_facts: Any, fact_source_evidence: Any) -> bool:
    """判断 ``key_facts`` 与 ``fact_source_evidence`` 是否为合法的一一对应。

    只校验结构：非空字符串事实列表 + 同长度证据列表。证据组本身是否携带用户
    来源由 ``has_user_source_evidence`` 判定，不属于文本对齐职责。
    """

    return (
        isinstance(key_facts, list)
        and bool(key_facts)
        and all(isinstance(fact, str) and fact.strip() for fact in key_facts)
        and isinstance(fact_source_evidence, list)
        and len(fact_source_evidence) == len(key_facts)
    )


def facts_aligned(
    content: Any, key_facts: Any, fact_source_evidence: Any
) -> FactTextAlignment:
    """判定事实元数据与当前正文的关系（三态）。

    ``content`` 为当前 canonical 正文；``key_facts``/``fact_source_evidence``
    为 metadata 中记录的事实表示。事实表示不可用时返回 ``UNDETERMINABLE``，
    调用方按「无事实元数据」处理。
    """

    if not fact_evidence_paired(key_facts, fact_source_evidence):
        return FactTextAlignment.UNDETERMINABLE
    if not isinstance(content, str) or not content.strip():
        return FactTextAlignment.UNDETERMINABLE
    if all(fact_in_content(content, fact) for fact in key_facts):
        return FactTextAlignment.ALIGNED
    return FactTextAlignment.MISALIGNED
