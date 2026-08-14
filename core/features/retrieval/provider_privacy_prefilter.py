"""外部重排 Provider 前的候选隐私边界。"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from ...shared.mmr import apply_mmr
from .rrf_fusion import HybridResult

_PRIVACY_LEVELS = frozenset({"public", "shared", "confidential"})
_PROVIDER_ALLOWED_ROLES = frozenset(
    {
        "user",
        "assistant",
        "primary",
        "supporting",
        "conflict_left",
        "conflict_right",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderPrivacyContext:
    """保存一次 Provider 重排所需的最小授权上下文。"""

    chat_type: str
    scope_key: str
    stable_user_id: str | None = None

    def __post_init__(self) -> None:
        """拒绝无法形成确定权限边界的请求上下文。"""

        if self.chat_type not in {"private", "group"}:
            raise ValueError("chat_type 必须是 private 或 group")
        if not isinstance(self.scope_key, str) or not self.scope_key.strip():
            raise ValueError("scope_key 必须是非空字符串")
        if self.stable_user_id is not None and (
            not isinstance(self.stable_user_id, str) or not self.stable_user_id.strip()
        ):
            raise ValueError("stable_user_id 必须是非空字符串或 None")


@dataclass(frozen=True, slots=True)
class ProviderPrefilterResult:
    """返回安全候选和不含业务标识的过滤计数。"""

    candidates: list[HybridResult]
    input_count: int
    allowed_count: int
    filtered_count: int


class ProviderPrivacyPrefilter:
    """在候选正文离开本地进程前执行 fail-closed 权限校验。"""

    def filter(
        self,
        candidates: list[HybridResult],
        context: ProviderPrivacyContext,
    ) -> ProviderPrefilterResult:
        """按 privacy、scope、稳定身份和 role 筛选 Provider 候选。"""

        allowed = [
            candidate
            for candidate in candidates
            if _candidate_is_allowed(candidate, context)
        ]
        input_count = len(candidates)
        return ProviderPrefilterResult(
            candidates=allowed,
            input_count=input_count,
            allowed_count=len(allowed),
            filtered_count=input_count - len(allowed),
        )


async def rerank_with_provider_boundary(
    candidates: list[HybridResult],
    k: int,
    *,
    query: str,
    reranker: Any,
    strategy: str,
    prefilter: ProviderPrivacyPrefilter,
    context: ProviderPrivacyContext,
    strict_mode: bool,
    mmr_lambda: float,
) -> list[HybridResult]:
    """在必要时预过滤后重排，并对边界故障执行纯本地降级。"""

    baseline = _score_sorted(candidates)
    rerank_candidates = candidates
    if _requires_provider_prefilter(strategy):
        try:
            outcome = prefilter.filter(candidates, context)
            rerank_candidates = outcome.candidates
        except asyncio.CancelledError:
            raise
        except Exception:
            if strict_mode:
                return baseline
            return _local_mmr_with_backfill(baseline, k, mmr_lambda)

    fallback = _score_sorted(rerank_candidates)
    try:
        reranked = reranker.rerank(rerank_candidates, k, query=query)
        if inspect.isawaitable(reranked):
            reranked = await reranked
        if not isinstance(reranked, list):
            return fallback
        returned_ids = {item.doc_id for item in reranked}
        return reranked + [item for item in fallback if item.doc_id not in returned_ids]
    except asyncio.CancelledError:
        raise
    except Exception:
        return fallback


def _candidate_is_allowed(
    candidate: HybridResult,
    context: ProviderPrivacyContext,
) -> bool:
    """对单个不可信候选执行权限字段类型和值校验。"""

    if not isinstance(candidate.metadata, dict):
        return False
    metadata = candidate.metadata
    privacy_level = metadata.get("privacy_level")
    if not isinstance(privacy_level, str) or privacy_level not in _PRIVACY_LEVELS:
        return False
    if context.chat_type == "group" and privacy_level == "confidential":
        return False

    candidate_scope = _candidate_scope(metadata)
    if not isinstance(candidate_scope, str) or candidate_scope != context.scope_key:
        return False

    role = metadata.get("role")
    if role is not None and (
        not isinstance(role, str) or role not in _PROVIDER_ALLOWED_ROLES
    ):
        return False

    participant_ids = _participant_ids(metadata)
    if participant_ids is False:
        return False
    if context.chat_type == "private":
        if (
            not isinstance(participant_ids, frozenset)
            or context.stable_user_id is None
            or context.stable_user_id not in participant_ids
        ):
            return False
    return True


def _candidate_scope(metadata: dict[str, Any]) -> str | bool | None:
    """提取候选主作用域；缺失、非法或来源冲突时安全拒绝。"""

    scopes: dict[str, str] = {}
    for key in ("scope_key", "session_id", "persona_id"):
        value = metadata.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            return False
        scopes[key] = value
    if not scopes:
        return None

    explicit_scope = scopes.get("scope_key")
    if explicit_scope is not None:
        source_scope = scopes.get("session_id") or scopes.get("persona_id")
        if source_scope is not None and source_scope != explicit_scope:
            return False
        return explicit_scope
    return scopes.get("session_id") or scopes.get("persona_id")


def _participant_ids(
    metadata: dict[str, Any],
) -> frozenset[str] | bool | None:
    """规范化稳定参与者集合；缺失返回 ``None``，非法返回 ``False``。"""

    value = metadata.get("participant_ids")
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return False
    normalized: set[str] = set()
    for participant_id in value:
        if not isinstance(participant_id, str) or not participant_id.strip():
            return False
        normalized.add(participant_id)
    return frozenset(normalized)


def _requires_provider_prefilter(strategy: str) -> bool:
    """除显式本地 MMR 外，所有重排策略都先执行 Provider 边界。"""

    return str(strategy or "").strip().casefold() != "mmr"


def _score_sorted(candidates: list[HybridResult]) -> list[HybridResult]:
    """按稳定分数与本地 ID 生成不修改输入的基础顺序。"""

    return sorted(candidates, key=lambda item: (-item.final_score, item.doc_id))


def _local_mmr_with_backfill(
    candidates: list[HybridResult],
    k: int,
    mmr_lambda: float,
) -> list[HybridResult]:
    """执行纯本地 MMR，并把未入选候选追加给最终过滤回填。"""

    selected = apply_mmr(candidates, k, max(0.0, min(1.0, mmr_lambda)))
    selected_ids = {item.doc_id for item in selected}
    return selected + [item for item in candidates if item.doc_id not in selected_ids]


def filter_confidential_from_group(
    results: list[HybridResult],
    chat_type: str,
) -> list[HybridResult]:
    """群聊场景过滤机密记忆（私聊秘密不在群聊暴露）。

    向后兼容：没有 privacy_level 的记忆视为 "shared"（都可访问）。
    """
    if chat_type != "group":
        return results
    return [
        r
        for r in results
        if (r.metadata or {}).get("privacy_level", "shared") != "confidential"
    ]


__all__ = [
    "ProviderPrefilterResult",
    "ProviderPrivacyContext",
    "ProviderPrivacyPrefilter",
    "filter_confidential_from_group",
    "rerank_with_provider_boundary",
]
