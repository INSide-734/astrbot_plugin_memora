"""话题分割策略集合：将混合话题的 LLM 输出拆分为独立记忆。"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass
class MemorySegment:
    """可直接入库的独立话题记忆片段。"""

    content: str
    importance: float
    metadata: dict[str, Any] = field(default_factory=dict)
    key_facts: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    atoms: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 抽象策略
# ---------------------------------------------------------------------------


class TopicSegmentationStrategy(ABC):
    """所有话题分割策略的基类。"""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    @abstractmethod
    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        """将结构化 LLM 输出拆分为多个独立话题片段。"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# 策略 A：提示词工程
# ---------------------------------------------------------------------------


class PromptSegmentationStrategy(TopicSegmentationStrategy):
    """解析 LLM 按约定输出的 ``memories[]`` 数组。"""

    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        """把模型输出的 memories[] 转换为保持原顺序的独立片段。"""

        if "memories" in structured_data:
            raw_list: list[dict[str, Any]] = structured_data["memories"]
            logger.debug(f"[提示词分割] LLM 返回了 {len(raw_list)} 条独立记忆")
        else:
            raw_list = [structured_data]
            logger.debug("[提示词分割] 检测到旧格式，已包装为单条记忆")

        segments: list[MemorySegment] = []
        for mem in raw_list:
            if not isinstance(mem, dict):
                continue
            summary = str(mem.get("summary", "") or "")
            key_facts: list[str] = [str(f) for f in (mem.get("key_facts") or []) if f]
            topics: list[str] = [str(t) for t in (mem.get("topics") or []) if t]
            if not summary and not key_facts:
                continue  # 跳过空条目（纯闲聊）

            segments.append(
                MemorySegment(
                    content=summary,
                    metadata=_segment_metadata(mem, key_facts, topics),
                    importance=float(mem.get("importance", 0.5)),
                    key_facts=key_facts,
                    topics=topics,
                )
            )
        return segments


# ---------------------------------------------------------------------------
# 策略 B：向量聚类
# ---------------------------------------------------------------------------


class EmbeddingClusteringStrategy(TopicSegmentationStrategy):
    """按向量余弦相似度对 ``key_facts`` 聚类，并按簇拆分记忆片段。"""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        embed_fn: Any = None,
    ) -> None:
        super().__init__(config)
        self._embed_fn = embed_fn
        self._threshold = float(self._config.get("similarity_threshold", 0.5))
        self._min_cluster_size = int(self._config.get("min_cluster_size", 1))
        self._max_clusters = int(self._config.get("max_clusters", 5))

    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        """在每条原始 memory 内分别执行事实向量聚类。"""

        raw_memories = structured_data.get("memories")
        if isinstance(raw_memories, list):
            segments: list[MemorySegment] = []
            for memory in raw_memories:
                if isinstance(memory, dict):
                    segments.extend(await self._segment_single(memory))
            return segments

        return await self._segment_single(structured_data)

    async def _segment_single(
        self,
        structured_data: dict[str, Any],
    ) -> list[MemorySegment]:
        """仅在单个原始 memory 边界内聚类，避免跨参与者合并。"""

        key_facts: list[str] = [
            str(f) for f in (structured_data.get("key_facts") or []) if f
        ]
        if len(key_facts) <= 1:
            return _single_segment(structured_data, key_facts)

        embeddings = await self._compute_embeddings(key_facts)
        clusters = self._cluster(embeddings, key_facts)
        return _build_segments_from_clusters(structured_data, clusters)

    async def _compute_embeddings(self, facts: list[str]) -> list[list[float]]:
        if self._embed_fn is None:
            return _dummy_embeddings(facts)

        try:
            vectors = await self._embed_fn(facts)
            if vectors and len(vectors) == len(facts):
                return [list(v) for v in vectors]
        except Exception:
            logger.warning(
                "[向量聚类分割] 向量调用失败，回退到伪造向量",
                exc_info=True,
            )

        return _dummy_embeddings(facts)

    def _cluster(
        self, embeddings: list[list[float]], facts: list[str]
    ) -> list[list[str]]:
        n = len(facts)
        if n <= 1:
            return [list(facts)]

        sim = _similarity_matrix(embeddings)

        # 基于配置阈值执行贪心式凝聚聚类
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # 合并相似度高于阈值的点对（遍历上三角）
        for i in range(n):
            for j in range(i + 1, n):
                if sim[i][j] >= self._threshold:
                    union(i, j)

        # 收集聚类结果
        groups: dict[int, list[str]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(facts[i])

        clusters = list(groups.values())
        # 按簇大小排序（大的在前），并限制最大簇数
        clusters.sort(key=len, reverse=True)
        if len(clusters) > self._max_clusters:
            # 将超出上限的小簇并入最大簇
            overflow = clusters[self._max_clusters :]
            clusters = clusters[: self._max_clusters]
            for c in overflow:
                clusters[0].extend(c)

        return clusters


# ---------------------------------------------------------------------------
# A+B 混合策略
# ---------------------------------------------------------------------------


class HybridSegmentationStrategy(TopicSegmentationStrategy):
    """先走 Prompt 分割（A），必要时回退到向量聚类（B）。"""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        embed_fn: Any = None,
    ) -> None:
        super().__init__(config)
        self._strategy_a = PromptSegmentationStrategy(config)
        self._strategy_b = EmbeddingClusteringStrategy(config, embed_fn=embed_fn)
        self._fallback_fact_threshold = int(
            self._config.get("hybrid_fallback_fact_threshold", 3)
        )

    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        """优先采用 A 的结果，必要时对唯一 memory 执行 B 回退。"""

        segments = await self._strategy_a.segment(
            structured_data, messages, is_group_chat
        )

        if len(segments) >= 2:
            return segments

        # 检查原始数据是否具备足够多的事实，值得再跑一次聚类
        fallback_data = structured_data
        raw_memories = structured_data.get("memories")
        if (
            isinstance(raw_memories, list)
            and len(raw_memories) == 1
            and isinstance(raw_memories[0], dict)
        ):
            fallback_data = raw_memories[0]
        raw_facts: list[str] = fallback_data.get("key_facts") or []
        if len(raw_facts) < self._fallback_fact_threshold:
            return segments

        logger.info(
            "[混合分割] 策略 A 仅返回 %d 个分段，但存在 %d 条关键事实；"
            "将运行策略 B（向量聚类）作为兜底",
            len(segments),
            len(raw_facts),
        )
        fallback_segments = await self._strategy_b.segment(
            fallback_data,
            messages,
            is_group_chat,
        )
        for segment in fallback_segments:
            segment.metadata["topic_segmentation_fallback_reason"] = (
                "a_single_mixed_facts"
            )
        return fallback_segments


# ---------------------------------------------------------------------------
# 策略 C：话题感知预切块（骨架实现）
# ---------------------------------------------------------------------------


class TopicChunkingStrategy(TopicSegmentationStrategy):
    """在 LLM 调用前，通过相邻消息向量相似度检测话题边界。"""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        embed_fn: Any = None,
    ) -> None:
        super().__init__(config)
        self._embed_fn = embed_fn
        self._threshold = float(self._config.get("topic_shift_threshold", 0.3))
        self._min_chunk_size = int(self._config.get("min_chunk_size", 2))

    async def chunk_messages(self, messages: list) -> list[list]:
        """将消息列表切分为话题相对单一的多个子批次。"""
        if len(messages) <= self._min_chunk_size:
            return [list(messages)]

        # 构造每条消息的文本表示
        texts = [_msg_text(m) for m in messages]
        embeddings = await self._compute_message_embeddings(texts)

        # 找出相邻相似度低于阈值的转折点
        boundaries = [0]
        for i in range(1, len(embeddings)):
            sim = _cosine_sim(embeddings[i - 1], embeddings[i])
            if sim < self._threshold:
                boundaries.append(i)

        # 生成分块
        chunks: list[list] = []
        for start, end in zip(
            boundaries, boundaries[1:] + [len(messages)], strict=False
        ):
            if end - start >= self._min_chunk_size:
                chunks.append(list(messages[start:end]))
            elif chunks:
                # 将过小分块并入前一个分块
                chunks[-1].extend(messages[start:end])
            else:
                chunks.append(list(messages[start:end]))

        logger.debug(
            "[话题切块] %d 条消息 -> %d 个分块（阈值=%.2f）",
            len(messages),
            len(chunks),
            self._threshold,
        )
        return chunks if chunks else [list(messages)]

    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        # 透传：真正的切块在 ReflectionHandler 上游完成
        return _single_segment(structured_data, structured_data.get("key_facts") or [])

    async def _compute_message_embeddings(self, texts: list[str]) -> list[list[float]]:
        if self._embed_fn is None:
            return _dummy_embeddings(texts)
        try:
            vectors = await self._embed_fn(texts)
            if vectors and len(vectors) == len(texts):
                return [list(v) for v in vectors]
        except Exception:
            logger.warning("[话题切块] 向量计算失败，改用伪造向量", exc_info=True)
        return _dummy_embeddings(texts)


# ---------------------------------------------------------------------------
# 策略 D：两阶段 LLM（骨架实现）
# ---------------------------------------------------------------------------


class TwoStageLLMStrategy(TopicSegmentationStrategy):
    """第一阶段由 LLM 识别话题范围，第二阶段再逐段抽取内容。

    设计说明：`identify_topics()` 才是主要的公开入口，应由处理器上游
    在把批次交给 `MemoryProcessor` 之前调用。下方的 `segment()` 只是
    透传实现，因为两阶段切分已经在上游完成；如果仅对原始数据直接调用
    `segment()`，最终只会得到一个未经切分的结果。
    """

    def __init__(
        self, config: dict[str, Any] | None = None, llm_client: Any = None
    ) -> None:
        super().__init__(config)
        self._llm_client = llm_client
        self._max_topics = int(self._config.get("stage1_max_topics", 5))
        self._parallel_stage2 = _safe_bool(
            self._config.get("enable_parallel_stage2", True)
        )

    async def identify_topics(
        self,
        conversation_text: str,
        system_prompt: str = "",
        *,
        propagate_errors: bool = False,
    ) -> list[dict[str, Any]]:
        """第一阶段返回话题范围，并可让上游预算门观察 Provider 失败。"""
        if self._llm_client is None:
            return []

        prompt = (
            "识别以下对话中的独立话题。对每个话题，标注涉及的消息行号范围 "
            "(line_range 为 [起始行, 结束行]，行号从 1 开始)。\n"
            f"最多识别 {self._max_topics} 个话题。\n\n"
            f"对话:\n{conversation_text}\n\n"
            '输出 JSON 数组: [{"topic": "话题名", "line_range": [1, 5]}, ...]'
        )
        try:
            raw = await self._llm_client.call_llm_with_retry(
                prompt=prompt,
                system_prompt=system_prompt,
                max_retries=1,
            )
            line_count = len(conversation_text.splitlines()) or 1
            return _parse_topic_identification_response(
                raw,
                max_topics=self._max_topics,
                line_count=line_count,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if propagate_errors:
                raise
            logger.warning("[两阶段分割] 第一阶段话题识别失败", exc_info=True)
        return []

    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        return _single_segment(structured_data, structured_data.get("key_facts") or [])


# ---------------------------------------------------------------------------
# 路由器
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, type[TopicSegmentationStrategy]] = {
    "a": PromptSegmentationStrategy,
    "b": EmbeddingClusteringStrategy,
    "c": TopicChunkingStrategy,
    "d": TwoStageLLMStrategy,
    "a_b_hybrid": HybridSegmentationStrategy,
}
STRATEGY_ALIASES = {
    "strategy_a": "a",
    "strategy_b": "b",
    "strategy_c": "c",
    "strategy_d": "d",
}

_FALLBACK_STRATEGY = "a_b_hybrid"


class TopicSegmentationRouter:
    """选择并实例化当前配置的话题分割策略。"""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        embed_fn: Any = None,
        llm_client: Any = None,
    ) -> None:
        strategy_key = (config or {}).get(
            "topic_segmentation.strategy", _FALLBACK_STRATEGY
        )
        strategy_key = STRATEGY_ALIASES.get(strategy_key, strategy_key)
        cls = STRATEGY_REGISTRY.get(strategy_key)
        if cls is None:
            logger.warning(
                "[话题分割路由] 未知策略 '%s'，将回退到 '%s'",
                strategy_key,
                _FALLBACK_STRATEGY,
            )
            strategy_key = _FALLBACK_STRATEGY
            cls = STRATEGY_REGISTRY[_FALLBACK_STRATEGY]

        # 提取各策略自己的配置子段
        prefix_map = {
            "a": "",
            "b": "strategy_b.",
            "c": "strategy_c.",
            "d": "strategy_d.",
            "a_b_hybrid": "",
        }
        prefix = prefix_map.get(strategy_key, "")
        strategy_config = {
            k.replace(f"topic_segmentation.{prefix}", ""): v
            for k, v in (config or {}).items()
            if k.startswith(f"topic_segmentation.{prefix}")
        }
        for k, v in (config or {}).items():
            if not k.startswith("topic_segmentation."):
                continue
            short_key = k.replace("topic_segmentation.", "")
            if "." not in short_key and short_key not in {"strategy", "enabled"}:
                strategy_config.setdefault(short_key, v)

        if strategy_key in ("b", "a_b_hybrid"):
            if strategy_key == "a_b_hybrid":
                # 同时合并 strategy_b.* 子配置，便于向量聚类策略
                # 直接读取去前缀后的 similarity_threshold 等键。
                for k, v in (config or {}).items():
                    if k.startswith("topic_segmentation.strategy_b."):
                        strategy_config[
                            k.replace("topic_segmentation.strategy_b.", "")
                        ] = v
            self._strategy = cls(strategy_config, embed_fn=embed_fn)
        elif strategy_key == "d":
            self._strategy = cls(strategy_config, llm_client=llm_client)
        elif strategy_key == "c":
            self._strategy = cls(strategy_config, embed_fn=embed_fn)
        else:
            self._strategy = cls(strategy_config)

        self._strategy_key = strategy_key
        logger.info(
            "[话题分割路由] 当前启用策略：%s（%s）",
            strategy_key,
            self._strategy.name,
        )

    @property
    def strategy(self) -> TopicSegmentationStrategy:
        return self._strategy

    @property
    def strategy_key(self) -> str:
        return self._strategy_key

    async def segment(
        self,
        structured_data: dict[str, Any],
        messages: list | None = None,
        is_group_chat: bool = False,
    ) -> list[MemorySegment]:
        """调用当前策略并附加不含正文的稳定决策观测字段。"""

        segments = await self._strategy.segment(
            structured_data,
            messages,
            is_group_chat,
        )
        input_count = _memory_input_count(structured_data)
        output_count = len(segments)
        fallback_reason = ""
        for segment in segments:
            segment.metadata["topic_segmentation_strategy"] = self._strategy_key
            segment.metadata["topic_segmentation_input_count"] = input_count
            segment.metadata["topic_segmentation_output_count"] = output_count
            if not fallback_reason:
                fallback_reason = str(
                    segment.metadata.get("topic_segmentation_fallback_reason") or ""
                )
        logger.info(
            "[话题分割路由] strategy=%s, fallback_reason=%s, input_count=%d, output_count=%d",
            self._strategy_key,
            fallback_reason or "none",
            input_count,
            output_count,
        )
        return segments


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _safe_bool(value: object, default: bool = True) -> bool:
    """安全地将配置值转换为布尔值，兼容 `"False"` / `"0"` 等字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _parse_topic_identification_response(
    raw: str,
    *,
    max_topics: int,
    line_count: int,
) -> list[dict[str, Any]]:
    """从 JSON 数组、包装对象或代码块解析策略 D 的话题范围。"""
    text = str(raw or "").strip()
    if not text:
        return []

    code_match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.I)
    if code_match:
        text = code_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        array_match = re.search(r"\[[\s\S]*\]", text)
        if not array_match:
            return []
        try:
            parsed = json.loads(array_match.group(0))
        except (json.JSONDecodeError, TypeError):
            return []

    if isinstance(parsed, dict):
        parsed = parsed.get("topics", [])
    if not isinstance(parsed, list):
        return []

    normalized: list[dict[str, Any]] = []
    max_line = max(1, int(line_count or 1))
    for item in parsed:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        line_range = item.get("line_range")
        if (
            not topic
            or not isinstance(line_range, (list, tuple))
            or len(line_range) != 2
        ):
            continue
        try:
            start = int(line_range[0])
            end = int(line_range[1])
        except (TypeError, ValueError):
            continue
        if start > end:
            continue
        start = max(1, min(max_line, start))
        end = max(1, min(max_line, end))
        if end < start:
            continue
        normalized.append({"topic": topic, "line_range": [start, end]})

    normalized.sort(key=lambda item: (item["line_range"][0], item["line_range"][1]))
    return normalized[: max(0, max_topics)]


def _msg_text(msg: Any) -> str:
    """尽力从 Message 对象中提取文本内容。"""
    # 若可用，优先委托给标准内容提取器
    if hasattr(msg, "content_to_text"):
        try:
            result = msg.content_to_text()
            if result and isinstance(result, str) and result.strip():
                return result.strip()
        except Exception:
            pass
    for attr in ("content", "text", "message"):
        val = getattr(msg, attr, None)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return str(msg)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        if len(a) != len(b) and a and b:
            logger.warning(
                "[余弦相似度] 向量维度不一致：len=%d vs len=%d；返回 0.0",
                len(a),
                len(b),
            )
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _similarity_matrix(embeddings: list[list[float]]) -> list[list[float]]:
    n = len(embeddings)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0
        for j in range(i + 1, n):
            s = _cosine_sim(embeddings[i], embeddings[j])
            mat[i][j] = s
    return mat


def _dummy_embeddings(texts: list[str]) -> list[list[float]]:
    """在缺少 `embed_fn` 时使用的兜底方案：基于哈希生成伪向量。

    这些向量不具备真实语义，只是为了让聚类流程仍能继续运行。
    调用方在走到该路径时应记录告警日志。
    """
    import hashlib

    dim = 64
    out: list[list[float]] = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        # 取前 dim 个字节并做归一化
        vec = [((b / 255.0) * 2.0 - 1.0) for b in h[:dim]]
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        out.append(vec)
    return out


def _build_segments_from_clusters(
    data: dict[str, Any], clusters: list[list[str]]
) -> list[MemorySegment]:
    """按聚类结果为每个簇构建一个 `MemorySegment`。"""
    segments: list[MemorySegment] = []
    for i, facts in enumerate(clusters):
        if not facts:
            continue
        prefix = f"[话题{i + 1}] " if len(clusters) > 1 else ""
        summary = prefix + "；".join(facts)
        topics = [str(topic) for topic in (data.get("topics") or []) if topic]
        metadata = _segment_metadata(data, facts, topics)
        segments.append(
            MemorySegment(
                content=summary,
                metadata=metadata,
                importance=float(data.get("importance", 0.5)),
                key_facts=facts,
                topics=topics,
            )
        )
    return segments


def _single_segment(data: dict[str, Any], key_facts: list[str]) -> list[MemorySegment]:
    """将单话题的 LLM 输出包装成只含一个元素的列表。"""
    summary = str(data.get("summary", "") or "")
    if not summary and not key_facts:
        return []
    topics = [str(topic) for topic in (data.get("topics") or []) if topic]
    return [
        MemorySegment(
            content=summary,
            metadata=_segment_metadata(data, key_facts, topics),
            importance=float(data.get("importance", 0.5)),
            key_facts=key_facts,
            topics=topics,
        )
    ]


def _segment_metadata(
    data: dict[str, Any],
    key_facts: list[str],
    topics: list[str],
) -> dict[str, Any]:
    """复制分段允许继承的内容元数据，不推断身份或作用域。"""

    metadata: dict[str, Any] = {
        "topics": list(topics),
        "key_facts": list(key_facts),
        "sentiment": data.get("sentiment", "neutral"),
        "emotion_tags": data.get("emotion_tags") or [],
        "causal_relations": data.get("causal_relations") or [],
        "participants": data.get("participants") or [],
        "schema_version": "v3",
    }
    for key in ("source_refs", "atom_type", "confidence"):
        if data.get(key) is not None:
            metadata[key] = data[key]
    return metadata


def _memory_input_count(structured_data: dict[str, Any]) -> int:
    """返回 Router 接收的原始 memory 数量，仅用于低敏计数观测。"""

    raw_memories = structured_data.get("memories")
    if isinstance(raw_memories, list):
        return len([item for item in raw_memories if isinstance(item, dict)])
    return 1
