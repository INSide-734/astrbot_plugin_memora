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

整段打分没有任何候选达标时，再算一次事实粒度覆盖作为 ``FACT_OVERLAP``
观测信号：候选的每条事实在可比候选的 ``key_facts`` 中取最佳单条 Jaccard，
达标事实（>= ``FACT_MATCH_FLOOR``）占比达到 ``FACT_OVERLAP_RATIO`` 即认为
“部分共享事实”。该信号只用于灰度计数，不改变 HIT/FACT_MISMATCH 判定，
也不参与写回决策。

本模块保持确定性纯计算；可选的语义扩展在
`semantic_duplicate_detector.py`：lexical 仍是第一判据，只有协调器在
MISS/FACT_OVERLAP 之后才调用有界的语义端口，并复用本模块的
``document_is_comparable`` 与 ``fact_guard_passes`` 护栏。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from ....shared.memory_status import is_memory_recallable
from ....shared.text_utils import tokenize_cjk_words

FACT_JACCARD_FLOOR: Final = 0.5
# 事实重叠观测门槛：单条事实达标所需的最佳 Jaccard，以及达标事实占比门槛。
FACT_MATCH_FLOOR: Final = 0.6
FACT_OVERLAP_RATIO: Final = 0.5
_PARTICIPANT_BOUNDARY_PRIVACY: Final = "confidential"
_PARTICIPANT_BOUNDARY_CHAT_TYPE: Final = "private"


class NearDuplicateVerdict(str, Enum):
    """单次检测的互斥结论。"""

    HIT = "hit"
    MISS = "miss"
    FACT_MISMATCH = "fact_mismatch"
    FACT_OVERLAP = "fact_overlap"


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
    """在注入的近邻端口上返回最高分命中、事实护栏/重叠终态或未命中。

    只评估最高分候选：分数达到 ``similarity_threshold`` 后，若两侧
    ``key_facts`` 词集 Jaccard 低于 ``FACT_JACCARD_FLOOR`` 则返回
    ``FACT_MISMATCH``，否则返回 ``HIT``。整段打分没有任何候选达标时，若
    候选事实被某个可比候选覆盖到 ``FACT_OVERLAP_RATIO`` 及以上（单条事实
    门槛 ``FACT_MATCH_FLOOR``）则返回 ``FACT_OVERLAP``，否则返回 ``MISS``。
    任一侧 token 数低于 ``min_tokens``、作用域不完整、无可比候选或候选侧
    没有事实 token 时返回 ``MISS``。
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
    evaluated: list[tuple[float, DedupDocument]] = []
    for document in documents:
        if not document_is_comparable(document, incoming, min_tokens=min_tokens):
            continue
        evaluated.append(
            (
                _jaccard(
                    incoming_tokens,
                    frozenset(tokenize_cjk_words(document.content)),
                ),
                document,
            )
        )
    scored = [item for item in evaluated if item[0] >= similarity_threshold]
    if not scored:
        # 整段无命中：事实粒度覆盖只作观测，不改变写入决策。
        overlap = _best_fact_overlap(metadata, evaluated)
        if overlap is None:
            return NearDuplicateOutcome(NearDuplicateVerdict.MISS)
        overlap_document, overlap_score = overlap
        return NearDuplicateOutcome(
            NearDuplicateVerdict.FACT_OVERLAP,
            overlap_document,
            overlap_score,
        )

    # 同分时固定选择最早的 canonical，结论与检索返回顺序无关。
    score, document = max(scored, key=lambda item: (item[0], -item[1].memory_id))
    if not fact_guard_passes(metadata, document.metadata):
        return NearDuplicateOutcome(
            NearDuplicateVerdict.FACT_MISMATCH,
            document,
            score,
        )
    return NearDuplicateOutcome(NearDuplicateVerdict.HIT, document, score)


def fact_guard_passes(incoming: Mapping[str, Any], stored: Mapping[str, Any]) -> bool:
    """两侧 ``key_facts`` 词集 Jaccard 达到事实护栏门槛。

    语义扩展与 lexical 命中判定共用同一道护栏：任一侧没有事实 token 时按
    0 处理，因此只有词面事实确实重合的候选才能通过。
    """

    return _fact_jaccard(incoming, stored) >= FACT_JACCARD_FLOOR


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


def document_is_comparable(
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


def _best_fact_overlap(
    metadata: Mapping[str, Any],
    evaluated: Sequence[tuple[float, DedupDocument]],
) -> tuple[DedupDocument, float] | None:
    """在整段未达标的可比候选中选事实覆盖最高的一个。

    返回 ``(候选, 该候选的整段分数)``；候选侧没有事实 token、或没有任何
    候选达到 ``FACT_OVERLAP_RATIO`` 时返回 ``None``。同分时固定选择最早的
    canonical，结论与检索返回顺序无关。
    """

    incoming_facts = _fact_token_sets(metadata)
    if not incoming_facts:
        return None
    candidates: list[tuple[float, float, DedupDocument]] = []
    for score, document in evaluated:
        coverage = _fact_coverage(incoming_facts, document.metadata)
        if coverage >= FACT_OVERLAP_RATIO:
            candidates.append((coverage, score, document))
    if not candidates:
        return None
    _, score, document = max(
        candidates,
        key=lambda item: (item[0], -item[2].memory_id),
    )
    return document, score


def _fact_coverage(
    incoming_facts: Sequence[frozenset[str]],
    stored: Mapping[str, Any],
) -> float:
    """候选事实中被目标事实覆盖的占比。

    单条候选事实的覆盖度取它与目标 ``key_facts`` 中某一条的最佳 Jaccard；
    达到 ``FACT_MATCH_FLOOR`` 才计入分子。目标侧没有任何事实 token 时按
    无覆盖处理。
    """

    stored_facts = _fact_token_sets(stored)
    if not stored_facts:
        return 0.0
    matched = 0
    for fact in incoming_facts:
        best = max((_jaccard(fact, other) for other in stored_facts), default=0.0)
        if best >= FACT_MATCH_FLOOR:
            matched += 1
    return matched / len(incoming_facts)


def _fact_token_sets(metadata: Mapping[str, Any]) -> list[frozenset[str]]:
    """把 ``key_facts`` 展开为逐条事实的词集；空白或无 token 的条目忽略。"""

    facts = metadata.get("key_facts")
    if not isinstance(facts, (list, tuple)):
        return []
    token_sets: list[frozenset[str]] = []
    for fact in facts:
        if not isinstance(fact, str):
            continue
        tokens = frozenset(tokenize_cjk_words(fact))
        if tokens:
            token_sets.append(tokens)
    return token_sets


def _fact_tokens(metadata: Mapping[str, Any]) -> frozenset[str]:
    """把 ``key_facts`` 文本列表展开为事实词集。"""

    tokens: set[str] = set()
    for fact in _fact_token_sets(metadata):
        tokens.update(fact)
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
    "FACT_MATCH_FLOOR",
    "FACT_OVERLAP_RATIO",
    "DedupDocument",
    "DedupQuery",
    "DedupScope",
    "NearDuplicateOutcome",
    "NearDuplicateVerdict",
    "SimilarDocumentSearch",
    "candidate_scope",
    "detect_near_duplicate",
    "document_is_comparable",
    "fact_guard_passes",
    "same_dedup_scope",
    "stored_scope",
]
