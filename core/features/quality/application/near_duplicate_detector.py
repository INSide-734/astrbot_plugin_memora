"""canonical 写入前的确定性近重复检测。

检测器只做 token 集合 Jaccard 打分：不调用 LLM、不查询向量索引、不写
任何状态，因此同一输入在任意时刻给出相同结论。近邻候选由调用方通过
``search_similar`` 端口注入（有界查询），比较范围在 Python 侧收敛到同一
``scope_key`` + ``privacy_level`` + 会话/人格 + 主体边界，绝不把不同
scope 的候选纳入相似度比较。

相似度定为 token 集合 Jaccard，阈值默认 0.85，与 ``semantic_compression``
的同名字段对齐但口径独立；短文本由 ``min_tokens`` 护栏跳过，命中后还要
通过 ``key_facts`` 词集 Jaccard >= 0.5 的事实护栏，避免把“同主题不同事实”
合并掉。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from ....shared.memory_status import is_memory_recallable
from ....shared.text_utils import tokenize_cjk_words

FACT_JACCARD_FLOOR: Final = 0.5
_PARTICIPANT_BOUNDARY_PRIVACY: Final = "confidential"
_PARTICIPANT_BOUNDARY_CHAT_TYPE: Final = "private"


class NearDuplicateVerdict(str, Enum):
    """单次检测的互斥结论。"""

    HIT = "hit"
    MISS = "miss"
    FACT_MISMATCH = "fact_mismatch"


@dataclass(frozen=True, slots=True)
class DedupQuery:
    """近邻检索请求：候选正文、可信会话标识与比较数量上限。"""

    content: str
    session_id: str
    limit: int


@dataclass(frozen=True, slots=True)
class DedupDocument:
    """参与比较的既有 canonical 投影。"""

    memory_id: int
    content: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DedupScope:
    """跨窗口可比较的最小相等集。"""

    session_id: str
    persona_id: str | None
    scope_key: str
    privacy_level: str
    chat_type: str | None
    participant_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class NearDuplicateOutcome:
    """检测结论；``document`` 只在给出最高分候选时存在。

    命中时同时返回检测当时读到的正文快照，调用方在写回前据此复核目标
    canonical 未被改写。
    """

    verdict: NearDuplicateVerdict
    document: DedupDocument | None = None
    score: float = 0.0

    @property
    def memory_id(self) -> int | None:
        """返回命中 canonical 的整数 ID。"""

        return self.document.memory_id if self.document is not None else None


SimilarDocumentSearch = Callable[[DedupQuery], Awaitable[Sequence[DedupDocument]]]


def candidate_scope(
    metadata: Mapping[str, Any],
    *,
    session_id: str,
    persona_id: str | None,
) -> DedupScope | None:
    """构造候选比较作用域；会话身份只采信调用方传入的可信值。

    候选 metadata 中的 ``scope_key``/``privacy_level``/``chat_type`` 由
    resolver 快照写入并经写入侧校验；``session_id``/``persona_id`` 一律
    使用调用方实参，避免模型输出伪造会话边界。字段缺失或类型非法时返回
    ``None``，调用方据此跳过比较。
    """

    return _build_scope(
        scope_key=metadata.get("scope_key"),
        privacy_level=metadata.get("privacy_level"),
        chat_type=metadata.get("chat_type"),
        session_id=session_id,
        persona_id=persona_id,
        participants=metadata.get("participant_ids"),
    )


def stored_scope(metadata: Mapping[str, Any]) -> DedupScope | None:
    """构造既有 canonical 的比较作用域；字段全部来自落库 metadata。"""

    return _build_scope(
        scope_key=metadata.get("scope_key"),
        privacy_level=metadata.get("privacy_level"),
        chat_type=metadata.get("chat_type"),
        session_id=metadata.get("session_id"),
        persona_id=metadata.get("persona_id"),
        participants=metadata.get("participant_ids"),
    )


def same_dedup_scope(incoming: DedupScope, stored: DedupScope) -> bool:
    """判断两侧是否落在同一可比较边界内。

    除 ``scope_key``/``privacy_level`` 相等外，显式复核 ``session_id`` 与
    ``persona_id``；私聊或机密记忆额外要求稳定主体交集非空（沿用注入侧
    provider 隐私预过滤的主体判定口径）。
    """

    if incoming.session_id != stored.session_id:
        return False
    if incoming.persona_id != stored.persona_id:
        return False
    if incoming.scope_key != stored.scope_key:
        return False
    if incoming.privacy_level != stored.privacy_level:
        return False
    if (
        incoming.chat_type
        and stored.chat_type
        and incoming.chat_type != stored.chat_type
    ):
        return False
    if _needs_participant_boundary(incoming):
        return bool(incoming.participant_ids & stored.participant_ids)
    return True


async def detect_near_duplicate(
    *,
    content: str,
    metadata: Mapping[str, Any],
    session_id: str,
    persona_id: str | None,
    search_similar: SimilarDocumentSearch,
    similarity_threshold: float = 0.85,
    candidate_limit: int = 5,
    min_tokens: int = 12,
) -> NearDuplicateOutcome:
    """在注入的近邻端口上返回最高分命中、事实护栏终态或未命中。

    只评估最高分候选：分数达到 ``similarity_threshold`` 后，若两侧
    ``key_facts`` 词集 Jaccard 低于 ``FACT_JACCARD_FLOOR`` 则返回
    ``FACT_MISMATCH``，否则返回 ``HIT``。任一侧 token 数低于
    ``min_tokens``、作用域不完整或无可比候选时返回 ``MISS``。
    """

    candidate_tokens = tokenize_cjk_words(content)
    if len(candidate_tokens) < min_tokens:
        return NearDuplicateOutcome(NearDuplicateVerdict.MISS)
    incoming = candidate_scope(metadata, session_id=session_id, persona_id=persona_id)
    if incoming is None:
        return NearDuplicateOutcome(NearDuplicateVerdict.MISS)

    documents = await search_similar(
        DedupQuery(content=content, session_id=session_id, limit=candidate_limit)
    )
    incoming_tokens = frozenset(candidate_tokens)
    scored: list[tuple[float, DedupDocument]] = []
    for document in documents:
        if not _is_comparable(document, incoming, min_tokens=min_tokens):
            continue
        score = _jaccard(
            incoming_tokens,
            frozenset(tokenize_cjk_words(document.content)),
        )
        if score >= similarity_threshold:
            scored.append((score, document))
    if not scored:
        return NearDuplicateOutcome(NearDuplicateVerdict.MISS)

    # 同分时固定选择最早的 canonical，结论与检索返回顺序无关。
    score, document = max(scored, key=lambda item: (item[0], -item[1].memory_id))
    if _fact_jaccard(metadata, document.metadata) < FACT_JACCARD_FLOOR:
        return NearDuplicateOutcome(
            NearDuplicateVerdict.FACT_MISMATCH,
            document,
            score,
        )
    return NearDuplicateOutcome(NearDuplicateVerdict.HIT, document, score)


def _build_scope(
    *,
    scope_key: Any,
    privacy_level: Any,
    chat_type: Any,
    session_id: Any,
    persona_id: Any,
    participants: Any,
) -> DedupScope | None:
    """校验并规范化作用域字段；任一必需字段非法时返回 ``None``。"""

    normalized_scope = _text(scope_key)
    normalized_privacy = _text(privacy_level)
    normalized_session = _text(session_id)
    if normalized_scope is None or normalized_privacy is None:
        return None
    if normalized_session is None:
        return None
    normalized_persona = _text(persona_id)
    if persona_id is not None and normalized_persona is None:
        return None
    normalized_chat_type = _text(chat_type)
    if chat_type is not None and normalized_chat_type is None:
        return None
    normalized_participants = _participant_ids(participants)
    if normalized_participants is None:
        return None
    return DedupScope(
        session_id=normalized_session,
        persona_id=normalized_persona,
        scope_key=normalized_scope,
        privacy_level=normalized_privacy,
        chat_type=normalized_chat_type,
        participant_ids=normalized_participants,
    )


def _text(value: Any) -> str | None:
    """返回去空白后的非空文本；其他类型或空串返回 ``None``。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _participant_ids(value: Any) -> frozenset[str] | None:
    """规范化稳定参与者集合；缺失返回空集合，类型非法返回 ``None``。"""

    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    normalized: set[str] = set()
    for item in value:
        text = _text(item)
        if text is None:
            return None
        normalized.add(text)
    return frozenset(normalized)


def _needs_participant_boundary(scope: DedupScope) -> bool:
    """私聊或机密记忆必须额外满足主体交集。"""

    return (
        scope.privacy_level == _PARTICIPANT_BOUNDARY_PRIVACY
        or scope.chat_type == _PARTICIPANT_BOUNDARY_CHAT_TYPE
    )


def _is_comparable(
    document: DedupDocument,
    incoming: DedupScope,
    *,
    min_tokens: int,
) -> bool:
    """判断一条候选是否可进入打分：ID、活跃状态、作用域与长度护栏。"""

    memory_id = document.memory_id
    if isinstance(memory_id, bool) or not isinstance(memory_id, int) or memory_id <= 0:
        return False
    if not isinstance(document.metadata, Mapping):
        return False
    if not is_memory_recallable(document.metadata):
        return False
    if len(tokenize_cjk_words(document.content)) < min_tokens:
        return False
    stored = stored_scope(document.metadata)
    if stored is None:
        return False
    return same_dedup_scope(incoming, stored)


def _fact_tokens(metadata: Mapping[str, Any]) -> frozenset[str]:
    """把 ``key_facts`` 文本列表展开为事实词集。"""

    facts = metadata.get("key_facts")
    if not isinstance(facts, (list, tuple)):
        return frozenset()
    tokens: set[str] = set()
    for fact in facts:
        if isinstance(fact, str):
            tokens.update(tokenize_cjk_words(fact))
    return frozenset(tokens)


def _fact_jaccard(incoming: Mapping[str, Any], stored: Mapping[str, Any]) -> float:
    """两侧事实词集 Jaccard；任一侧没有事实 token 时按 0 处理。"""

    return _jaccard(_fact_tokens(incoming), _fact_tokens(stored))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """token 集合 Jaccard；空集按无重叠处理。"""

    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


__all__ = [
    "FACT_JACCARD_FLOOR",
    "DedupDocument",
    "DedupQuery",
    "DedupScope",
    "NearDuplicateOutcome",
    "NearDuplicateVerdict",
    "SimilarDocumentSearch",
    "candidate_scope",
    "detect_near_duplicate",
    "same_dedup_scope",
    "stored_scope",
]
