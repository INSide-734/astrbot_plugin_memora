"""召回结果的时间线叙事编排。"""

from __future__ import annotations

from ....shared.number_utils import safe_float
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

    @staticmethod
    def _result_timestamp(result: HybridResult) -> float | None:
        """按 create_time → timestamp 读取正数时间戳，无有效值时返回 ``None``。"""

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        for key in ("create_time", "timestamp"):
            raw_value = metadata.get(key)
            if raw_value is None:
                continue
            parsed = safe_float(raw_value, 0.0)
            if parsed > 0:
                return parsed
        return None

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
        sorted_results = sorted(results, key=lambda r: self._result_timestamp(r) or 0.0)

        # 2. 按 topic 聚类：相邻同 topic 的记忆归为一组，并保留段内首/末时间
        segments: list[tuple[str | None, list[str], float | None, float | None]] = []
        current_topic: str | None = None
        current_texts: list[str] = []
        current_first: float | None = None
        current_last: float | None = None

        for r in sorted_results:
            meta = r.metadata or {}
            topics = meta.get("topics", []) or []
            primary_topic = topics[0] if topics else None
            text = (r.content or "").strip()
            if not text:
                continue
            timestamp = self._result_timestamp(r)

            if primary_topic == current_topic and current_texts:
                current_texts.append(text)
                if timestamp is not None:
                    current_last = timestamp
            else:
                if current_texts:
                    segments.append(
                        (current_topic, current_texts, current_first, current_last)
                    )
                current_topic = primary_topic
                current_texts = [text]
                current_first = timestamp
                current_last = timestamp

        if current_texts:
            segments.append((current_topic, current_texts, current_first, current_last))

        # 3. 拼接过渡短语
        parts: list[str] = []
        prev_time: float | None = None

        for i, (_topic, texts, first_ts, last_ts) in enumerate(segments):
            if i == 0:
                parts.append(self._TRANSITIONS["introduction"])
            elif (
                prev_time is not None
                and first_ts is not None
                and abs(first_ts - prev_time) / 86400.0 > 7
            ):
                parts.append(self._TRANSITIONS["time_jump"])
            else:
                parts.append(self._TRANSITIONS["topic_switch"])

            # 同 topic 下多条记忆用 "还有，" 连接
            for j, text in enumerate(texts):
                parts.append(text.rstrip("。！？.!?") + "。")
                if j < len(texts) - 1:
                    parts.append(self._TRANSITIONS["same_topic"])

            # 更新 prev_time：上一段时间取当前段最后一条记忆的时间
            prev_time = last_ts

        # 4. 截断到 max_length，保持句子完整
        narrative = "".join(parts)
        if len(narrative) > max_length:
            cutoff = narrative.rfind("。", 0, max_length)
            narrative = (
                narrative[: cutoff + 1] if cutoff > 0 else narrative[:max_length]
            )

        return narrative
