"""召回结果的时间线叙事编排。"""

from __future__ import annotations

from ...retrieval.rrf_fusion import HybridResult


class RetrievalNarrativeMixin:
    """为 RetrievalOptimizer 提供连贯叙事格式化。"""

    # 过渡短语映射
    _TRANSITIONS: dict[str, str] = {
        "same_topic": "还有，",
        "topic_switch": "另外，",
        "time_jump": "那之后，",
        "introduction": "我记得：",
    }

    def arrange_narrative(
        self,
        results: list[HybridResult],
        max_length: int = 500,
    ) -> str:
        """R5: 将平铺记忆列表转为时间线排序 + topic 聚类 + 过渡短语的连贯叙事。

        参数:
            results: 检索结果列表
            max_length: 输出最大字符数（截断点以完整句子为界）

        返回:
            格式化叙事字符串，如 "我记得：xxx。还有，yyy。那之后，zzz。"
        """
        if not results:
            return ""

        # 1. 按时间线排序（优先 create_time，其次 timestamp）
        def _sort_key(r: HybridResult) -> float:
            meta = r.metadata or {}
            ts = meta.get("create_time") or meta.get("timestamp") or 0.0
            try:
                return float(ts)
            except (TypeError, ValueError):
                return 0.0

        sorted_results = sorted(results, key=_sort_key)

        # 2. 按 topic 聚类：相邻同 topic 的记忆归为一组
        segments: list[tuple[str | None, list[str]]] = []
        current_topic: str | None = None
        current_texts: list[str] = []

        for r in sorted_results:
            meta = r.metadata or {}
            topics = meta.get("topics", []) or []
            primary_topic = topics[0] if topics else None
            text = (r.content or "").strip()
            if not text:
                continue

            if primary_topic == current_topic and current_texts:
                current_texts.append(text)
            else:
                if current_texts:
                    segments.append((current_topic, current_texts))
                current_topic = primary_topic
                current_texts = [text]

        if current_texts:
            segments.append((current_topic, current_texts))

        # 3. 拼接过渡短语
        parts: list[str] = []
        prev_time: float | None = None

        for i, (_topic, texts) in enumerate(segments):
            if i == 0:
                parts.append(self._TRANSITIONS["introduction"])
            else:
                # 判断时间跳跃（> 7 天）
                first_ts = None
                if i < len(sorted_results):
                    try:
                        first_ts = float(
                            (sorted_results[i].metadata or {}).get("create_time")
                            or (sorted_results[i].metadata or {}).get("timestamp")
                            or 0
                        )
                    except (TypeError, ValueError):
                        first_ts = None
                if prev_time is not None and first_ts is not None:
                    gap_days = abs(first_ts - prev_time) / 86400.0
                    if gap_days > 7:
                        parts.append(self._TRANSITIONS["time_jump"])
                    else:
                        parts.append(self._TRANSITIONS["topic_switch"])
                else:
                    parts.append(self._TRANSITIONS["topic_switch"])

            # 同 topic 下多条记忆用 "还有，" 连接
            for j, text in enumerate(texts):
                parts.append(text.rstrip("。！？.!?") + "。")
                if j < len(texts) - 1:
                    parts.append(self._TRANSITIONS["same_topic"])

            # 更新 prev_time
            try:
                prev_time = float(
                    (
                        sorted_results[min(i + 1, len(sorted_results) - 1)].metadata
                        or {}
                    ).get("create_time")
                    or 0
                )
            except (TypeError, ValueError):
                prev_time = None

        # 4. 截断到 max_length，保持句子完整
        narrative = "".join(parts)
        if len(narrative) > max_length:
            cutoff = narrative.rfind("。", 0, max_length)
            narrative = (
                narrative[: cutoff + 1] if cutoff > 0 else narrative[:max_length]
            )

        return narrative
