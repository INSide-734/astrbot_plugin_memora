"""R4: 矛盾检测与更新 — 写入时检测冲突记忆并标记 SUPERSEDED。

认知原理：当新信息与旧记忆矛盾（如"喜欢咖啡" vs "戒咖啡三个月"），
大脑不会删除旧记忆，而是标记为"已过时"并在检索时降权。
"""

from __future__ import annotations

from collections.abc import Callable

from astrbot.api import logger


class ContradictionDetector:
    """写入前搜索候选冲突记忆，判断是否需要标记为 SUPERSEDED。

    用法:
        detector = ContradictionDetector(search_fn, update_fn)
        superseded = await detector.check_and_mark(
            new_content="我已经戒咖啡三个月了",
            new_topics=["咖啡", "饮食"],
            session_id="...",
        )
    """

    # 矛盾检测的相似度阈值（Jaccard token overlap）
    JACCARD_CONFLICT_THRESHOLD = 0.40

    def __init__(
        self,
        search_fn: Callable | None = None,
        update_fn: Callable | None = None,
        enabled: bool = True,
        max_conflicts: int = 5,
    ) -> None:
        """
        Args:
            search_fn: async (query, k, session_id) -> list of dicts with "id", "text", "metadata"
            update_fn: async (memory_id, updates_dict) -> bool
            enabled: 是否启用矛盾检测
            max_conflicts: 最多返回多少候选冲突
        """
        self._search = search_fn
        self._update = update_fn
        self._enabled = enabled
        self._max_conflicts = max_conflicts

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def check_and_mark(
        self,
        new_content: str,
        new_topics: list[str],
        session_id: str | None = None,
    ) -> list[int]:
        """检测并标记与新记忆矛盾的旧记忆。

        Args:
            new_content: 新记忆的文本内容
            new_topics: 新记忆的主题标签
            session_id: 会话 ID（限定搜索范围）

        Returns:
            被标记为 SUPERSEDED 的记忆 ID 列表
        """
        if not self._enabled or self._search is None:
            return []

        if not new_content.strip() or not new_topics:
            return []

        try:
            # 用 topic 关键词搜索候选冲突
            search_query = " ".join(new_topics[:5])
            candidates = await self._search(
                search_query,
                k=self._max_conflicts,
                session_id=session_id,
            )

            if not candidates:
                return []

            new_tokens = set(_tokenize(new_content))

            superseded_ids: list[int] = []
            for candidate in candidates:
                candidate_text = str(
                    candidate.get("text") or candidate.get("content") or ""
                )
                if not candidate_text.strip():
                    continue

                candidate_tokens = set(_tokenize(candidate_text))
                jaccard = _jaccard(new_tokens, candidate_tokens)

                if jaccard >= self.JACCARD_CONFLICT_THRESHOLD:
                    candidate_id = int(
                        candidate.get("id")
                        or candidate.get("doc_id")
                        or candidate.get("memory_id")
                        or 0
                    )
                    if candidate_id <= 0:
                        continue

                    # 检查情感/立场是否矛盾（简单启发式：否定词 + 相同 topic）
                    has_contradiction = _detect_semantic_contradiction(
                        new_content, candidate_text
                    )
                    if not has_contradiction:
                        continue

                    # 标记旧记忆为 SUPERSEDED
                    if self._update is not None:
                        try:
                            meta = candidate.get("metadata", {}) or {}
                            if isinstance(meta, str):
                                import json

                                try:
                                    meta = json.loads(meta)
                                except (json.JSONDecodeError, TypeError):
                                    meta = {}
                            meta["superseded_by"] = new_content[:200]
                            meta["superseded_at"] = _now_iso()

                            await self._update(candidate_id, {"metadata": meta})
                            superseded_ids.append(candidate_id)
                            logger.info(
                                f"[ContradictionDetector] 标记 SUPERSEDED: "
                                f"id={candidate_id}, jaccard={jaccard:.3f}"
                            )
                        except Exception:
                            logger.debug(
                                f"[ContradictionDetector] 更新 id={candidate_id} 失败",
                                exc_info=True,
                            )

            return superseded_ids

        except Exception:
            logger.debug("[ContradictionDetector] 矛盾检测失败", exc_info=True)
            return []


def _tokenize(text: str) -> list[str]:
    """Delegate to shared CJK tokenizer.

    See :func:`core.utils.text_utils.tokenize_cjk_words`.
    """
    from ..utils.text_utils import tokenize_cjk_words

    return tokenize_cjk_words(text)


def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard 相似度。"""
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / max(1, len(set_a | set_b))


def _detect_semantic_contradiction(
    new_text: str,
    old_text: str,
) -> bool:
    """简单启发式矛盾检测：否定词 + 相同主题 = 可能矛盾。

    更精确的判断应使用 LLM，这里作为低延迟预筛选。
    """
    negation_words = [
        "不",
        "没",
        "别",
        "戒",
        "停止",
        "放弃",
        "不再",
        "改",
        "换",
        "取消",
        "拒绝",
        "否",
    ]
    affirmative_words = [
        "喜欢",
        "爱",
        "想要",
        "经常",
        "一直",
        "总是",
        "是",
        "有",
        "会",
        "可以",
    ]

    new_has_negation = any(w in new_text for w in negation_words)
    old_has_affirm = any(w in old_text for w in affirmative_words)

    old_has_negation = any(w in old_text for w in negation_words)
    new_has_affirm = any(w in new_text for w in affirmative_words)

    # 一方有否定 + 另一方有肯定 = 潜在矛盾
    return (new_has_negation and old_has_affirm) or (
        old_has_negation and new_has_affirm
    )


def _now_iso() -> str:
    """ISO 时间戳字符串。"""
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


__all__ = ["ContradictionDetector"]
