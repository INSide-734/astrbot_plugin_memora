"""TopicCandidateSelector 调用 helper，提供失败降级。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..domain.summary_models import (
    TopicCandidateMode,
    TopicCandidateSelection,
)

if TYPE_CHECKING:
    from ..domain.config import CandidateReuseConfig
    from ..domain.summary_models import ClaimedJob, SourceWindow
    from .topic_candidate_selector import TopicCandidateSelector


async def select_with_fallback(
    selector: TopicCandidateSelector | None,
    window: SourceWindow,
    claim: ClaimedJob,
    config: CandidateReuseConfig,
) -> TopicCandidateSelection:
    """执行候选选择，失败时降级为 baseline。

    参数：
        selector: TopicCandidateSelector 实例
        window: 当前总结窗口
        claim: 持有 scope_key/privacy/resolver_revision/chat_type 的 claim
        config: 候选复用配置快照

    返回：
        TopicCandidateSelection - 成功或 baseline

    保证：
        - context 一律取 ``claim.scope_snapshot``（含 scope_reason_code 与
          source_provenance_complete），不在此处猜测字段
        - 任何异常返回 baseline（空候选）
        - 记录失败 reason code
        - CancelledError 必须传播
        - 不阻塞总结主链
    """
    if selector is None:
        return baseline_selection(
            mode=config.mode,
            reason_code="selector_unavailable",
        )
    try:
        context = claim.scope_snapshot

        selection = await selector.select_candidates(
            window=window,
            context=context,
            config=config,
        )
        return selection

    except asyncio.CancelledError:
        raise  # 必须传播，不降级

    except Exception as e:
        # 只记录异常类型名，异常消息可能携带 scope/查询上下文
        logger.warning(
            f"TopicCandidateSelector 失败，降级为 baseline: {type(e).__name__}",
            extra={"reason_code": "selector_failed"},
        )
        return baseline_selection(
            mode=config.mode,
            reason_code="selector_failed",
        )


def baseline_selection(
    mode: TopicCandidateMode | str,
    reason_code: str,
) -> TopicCandidateSelection:
    """构造 baseline（空候选）。

    参数：
        mode: 配置的模式
        reason_code: 失败原因码

    返回：
        空候选的 TopicCandidateSelection
    """
    return TopicCandidateSelection(
        labels=(),
        source_provenance_complete=False,
        mode=mode,
        effective_mode=TopicCandidateMode.OFF,
        catalog_status="unavailable",
        reason_code=reason_code,
        candidate_count=0,
        bm25_hit_count=0,
        recent_fill_count=0,
    )
