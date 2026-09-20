"""MemoryProcessor 候选注入 mixin。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

from ...reflection.application.topic_label_renderer import render_topic_labels

if TYPE_CHECKING:
    from ...reflection.domain.summary_models import TopicCandidateSelection


class MemoryProcessorCandidateMixin:
    """MemoryProcessor 的候选注入 mixin。

    提供将 TopicCandidateSelection 渲染为 Prompt 块并条件注入的能力。
    """

    def _inject_topic_candidates(
        self,
        base_prompt: str,
        candidate_selection: TopicCandidateSelection | None,
    ) -> str:
        """在动态用户 Prompt 注入候选块（带隐私门禁）。

        参数：
            base_prompt: 基线 Prompt（不含候选）
            candidate_selection: 候选选择结果（可为 None）

        返回：
            完整 Prompt（base + 候选块），或 base_prompt（候选为空时）

        保证：
            - production_labels 为空时返回 base_prompt
            - 不修改 system prompt
            - 使用 TopicLabelRenderer 渲染安全候选块
            - 候选块独立于 base_prompt，追加在末尾
            - 隐私门禁：只注入 source_provenance_complete=True 的标签
            - 全部拒绝时降级为 baseline
        """
        if not candidate_selection:
            return base_prompt

        # 隐私门禁：来源证据不完整时降级为 baseline（先于标签判空，
        # 保证拒绝事件可观测；production_labels 亦已按同一条件过滤）
        if not candidate_selection.source_provenance_complete:
            logger.warning(
                "Rejected candidate selection without complete provenance",
                extra={
                    "reason": "incomplete_source_provenance",
                    "candidate_count": len(candidate_selection.labels),
                },
            )
            return base_prompt

        production_labels = candidate_selection.production_labels
        if not production_labels:
            return base_prompt

        # 使用反思专用 renderer 渲染候选块
        rendered = render_topic_labels(
            labels=production_labels,
            max_total_tokens=256,  # selector 已控制，这里设置安全上限
        )

        candidate_block = rendered.rendered_block
        if not candidate_block:
            return base_prompt

        # 追加候选块到动态用户 Prompt
        return f"{base_prompt}\n\n{candidate_block}"
