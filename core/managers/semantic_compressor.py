"""语义压缩 — 60天+旧记忆按 topic 相似度合并为抽象摘要。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from astrbot.api import logger

_DEFAULT_AGE_DAYS = 60.0
_DEFAULT_SIMILARITY = 0.85
_DEFAULT_BATCH_SIZE = 20

_SEEDS: dict[str, list[str]] = {
    "zh": ["总结", "回顾", "经历", "日常", "聊天"],
    "en": ["summary", "review", "experience", "daily", "chat"],
    "ru": ["итоги", "обзор", "опыт", "повседневное", "общение"],
}


def _resolve_seeds(seed_language: str, bot_language: str = "zh") -> list[str]:
    lang = seed_language
    if lang == "auto":
        lang = bot_language if bot_language in _SEEDS else "zh"
    return _SEEDS.get(lang, _SEEDS["zh"])


class SemanticCompressor:
    """扫描旧记忆，将 topic 重叠 > 50% 的记忆合并为摘要。"""

    def __init__(
        self,
        search_similar_cb: Callable | None = None,
        add_memory_cb: Callable | None = None,
        delete_memory_cb: Callable | None = None,
        age_days: float = _DEFAULT_AGE_DAYS,
        similarity_threshold: float = _DEFAULT_SIMILARITY,
        seed_language: str = "auto",
        bot_language: str = "zh",
    ) -> None:
        self._search_similar = search_similar_cb
        self._add_memory = add_memory_cb
        self._delete_memory = delete_memory_cb
        self._age_days = max(30.0, age_days)
        self._sim_threshold = max(0.7, min(0.98, similarity_threshold))
        self._seeds = _resolve_seeds(seed_language, bot_language)

    async def compress_old_memories(
        self,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> dict[str, int]:
        result = {"merged_groups": 0, "deleted_originals": 0, "new_abstracts": 0}
        if self._search_similar is None or self._add_memory is None:
            return result

        try:
            seeds = self._seeds
            seen_ids: set[int] = set()
            groups: list[list[dict[str, Any]]] = []

            for seed in seeds:
                similar = await self._search_similar(
                    seed,
                    k=_DEFAULT_BATCH_SIZE,
                    session_id=session_id,
                    persona_id=persona_id,
                    recall_type="passive",
                )
                if not similar:
                    continue

                cutoff = time.time() - self._age_days * 86400.0
                old_memories = []
                for r in similar:
                    if r.doc_id in seen_ids:
                        continue
                    meta = r.metadata or {}
                    created = float(meta.get("create_time", 0) or 0)
                    if created > cutoff or created <= 0:
                        continue
                    old_memories.append(
                        {
                            "doc_id": r.doc_id,
                            "content": r.content,
                            "score": r.final_score,
                            "metadata": meta,
                        }
                    )
                    seen_ids.add(r.doc_id)

                if len(old_memories) >= 2:
                    groups.append(old_memories)

            for group in groups:
                if len(group) < 2:
                    continue
                merged = self._cluster_by_topics(group)
                for mg in merged:
                    if len(mg) < 2:
                        continue
                    oids = [m["doc_id"] for m in mg]
                    contents = [m["content"] for m in mg]
                    abstract = self._synthesize_abstract(contents)
                    if not abstract:
                        continue

                    combined_imp = max(
                        (m["metadata"].get("importance", 0.5) for m in mg),
                        default=0.5,
                    )
                    combined_topics: list[str] = []
                    for m in mg:
                        for t in m["metadata"].get("topics", []) or []:
                            if t not in combined_topics:
                                combined_topics.append(t)

                    try:
                        await self._add_memory(
                            content=abstract,
                            session_id=session_id,
                            persona_id=persona_id,
                            importance=combined_imp,
                            metadata={
                                "compressed_from": oids,
                                "compressed_at": time.time(),
                                "compression_count": len(mg),
                                "topics": combined_topics[:10],
                                "is_compressed_abstract": True,
                            },
                        )
                        result["new_abstracts"] += 1
                    except Exception:
                        continue

                    if self._delete_memory is not None:
                        for oid in oids:
                            try:
                                await self._delete_memory(oid)
                                result["deleted_originals"] += 1
                            except Exception:
                                pass
                    result["merged_groups"] += 1

            if result["merged_groups"] > 0:
                logger.info(
                    f"[Compressor] {result['merged_groups']} groups merged, "
                    f"{result['deleted_originals']} deleted, "
                    f"{result['new_abstracts']} abstracts"
                )
        except Exception:
            logger.debug("[Compressor] failed", exc_info=True)
        return result

    @staticmethod
    def _cluster_by_topics(
            memories: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        clusters: list[list[dict[str, Any]]] = []
        used: set[int] = set()
        for i, mem in enumerate(memories):
            if i in used:
                continue
            cluster = [mem]
            used.add(i)
            ti = {t.lower() for t in (mem["metadata"].get("topics", []) or [])}
            if not ti:
                continue
            for j, other in enumerate(memories):
                if j in used:
                    continue
                tj = {t.lower() for t in (other["metadata"].get("topics", []) or [])}
                if not tj:
                    continue
                if len(ti & tj) / max(1, len(ti | tj)) >= 0.5:
                    cluster.append(other)
                    used.add(j)
            if len(cluster) >= 2:
                clusters.append(cluster)
        return clusters

    @staticmethod
    def _synthesize_abstract(contents: list[str]) -> str:
        if not contents:
            return ""
        if len(contents) == 1:
            return contents[0]
        base = contents[0].strip().rstrip("。！？.!?")
        supplements = []
        for c in contents[1:3]:
            c2 = c.strip().rstrip("。！？.!?")
            if len(c2) < len(base) * 0.3:
                supplements.append(c2)
        if supplements:
            return base + "。" + "；".join(supplements) + "。"
        return base + "。"


__all__ = ["SemanticCompressor"]
