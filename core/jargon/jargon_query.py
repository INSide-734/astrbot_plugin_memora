"""Jargon 查询服务 — 供 LLM Tool 使用的黑话查询。

提供：
- 按关键词查询黑话含义
- 检查文本中的黑话并生成解释文本（注入 LLM 上下文）
- 带 TTLCache 的查询缓存
- ASCII/非ASCII 分别匹配（英文缩写用 word-boundary regex）
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Any

from astrbot.api import logger

from .jargon_store import JargonStore
from .models import JargonMeaning


class TTLCache:
    """简单的 TTL 缓存实现。

    使用 OrderedDict 维护插入顺序，按需逐出过期条目。
    """

    def __init__(self, maxsize: int = 500, ttl: int = 60) -> None:
        self._maxsize = maxsize
        self._ttl = ttl  # 秒
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        """获取缓存值，过期或不存在返回 None。"""
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """设置缓存值（带 TTL）。

        当缓存满时，逐出最旧的条目（FIFO）。
        """
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._maxsize:
            self._store.popitem(last=False)
        self._store[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        """清除所有缓存。"""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class JargonQueryService:
    """供 LLM Tool 使用的黑话查询服务。

    提供带缓存的查询接口，支持：
    - 按关键词搜索黑话含义
    - 检查文本中是否包含群内黑话并生成解释
    - 列出群组所有已确认黑话

    Usage::

        query_svc = JargonQueryService(store)
        explanation = await query_svc.check_and_explain(text, group_id)
        if explanation:
            # 注入到 LLM 系统提示中
            system_prompt += explanation
    """

    def __init__(self, store: JargonStore) -> None:
        self._store = store
        self._cache = TTLCache(maxsize=500, ttl=60)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def query(
        self, keyword: str, group_id: str, use_cache: bool = True
    ) -> list[dict[str, Any]]:
        """按关键词查询黑话含义。

        Args:
            keyword: 搜索关键词。
            group_id: 群组 ID。
            use_cache: 是否使用缓存（默认 True）。

        Returns:
            匹配的黑话条目列表，每项为字典格式（适合 LLM Tool 返回）。
        """
        cache_key = f"query:{group_id}:{keyword}"
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"[JargonQuery] 缓存命中: {keyword}")
                return cached

        results = await self._store.search(keyword, group_id)
        formatted = [self._meaning_to_dict(m) for m in results]

        self._cache.set(cache_key, formatted)
        return formatted

    async def check_and_explain(
        self, text: str, group_id: str
    ) -> str | None:
        """检查文本中的黑话并返回解释文本。

        该方法检查给定文本中是否包含群组黑话并生成解释，
        返回值可直接注入到 LLM 上下文中。

        Args:
            text: 待检查的文本。
            group_id: 群组 ID。

        Returns:
            黑话解释文本，若文本中无黑话则返回 None。
            格式示例::

                本群黑话参考：
                - "yyds" = "永远的神"（表示极度崇拜或赞扬）
                - "xswl" = "笑死我了"（表示非常好笑）
        """
        if not text or not text.strip():
            return None

        cache_key = f"explain:{group_id}:{hash(text) & 0xFFFFFFFF}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # 获取群组所有已确认黑话
        all_jargon = await self._store.list_by_group(group_id, confirmed_only=True)

        # 只保留 is_jargon=True 的条目
        jargon_entries = [j for j in all_jargon if j.is_jargon]
        if not jargon_entries:
            return None

        # 匹配文本中的黑话
        matched = self._match_jargon_in_text(text, jargon_entries)
        if not matched:
            return None

        # 生成解释文本
        lines = ["本群黑话参考："]
        for jm in matched:
            meaning_text = jm.meaning or "(含义暂未推断)"
            lines.append(f'- "{jm.term}" = "{meaning_text}"')

        result = "\n".join(lines)
        self._cache.set(cache_key, result)
        return result

    async def get_group_jargon(
        self, group_id: str, use_cache: bool = True
    ) -> list[dict[str, Any]]:
        """获取群组所有已确认黑话。

        Args:
            group_id: 群组 ID。
            use_cache: 是否使用缓存。

        Returns:
            黑话条目列表。
        """
        cache_key = f"group:{group_id}"
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        results = await self._store.list_by_group(group_id, confirmed_only=True)
        formatted = [self._meaning_to_dict(m) for m in results]

        self._cache.set(cache_key, formatted)
        return formatted

    async def invalidate_cache(self, group_id: str | None = None) -> None:
        """使缓存失效。

        Args:
            group_id: 若提供则仅清除该群组相关缓存；否则清除全部。
        """
        if group_id is None:
            self._cache.clear()
            logger.debug("[JargonQuery] 已清除全部缓存")
            return

        # 清除特定群组相关缓存
        keys_to_remove = []
        for key in list(self._cache._store.keys()):
            if group_id in key:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._cache._store[key]
        logger.debug(f"[JargonQuery] 已清除群 {group_id} 缓存")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _match_jargon_in_text(
        text: str, entries: list[JargonMeaning]
    ) -> list[JargonMeaning]:
        """在文本中匹配黑话词条。

        分词策略：
        - 纯 ASCII（英文缩写等）：用 word-boundary regex ``\\b{term}\\b``
        - 非 ASCII（中文等）：直接子串匹配

        Args:
            text: 待检查文本。
            entries: 黑话条目列表。

        Returns:
            匹配到的黑话条目列表（去重，按 term 长度降序避免短词误匹配）。
        """
        if not text or not entries:
            return []

        text_lower = text.lower()
        matched: dict[str, JargonMeaning] = {}  # term -> JargonMeaning（去重）

        # 按 term 长度降序排序，避免短词先匹配
        sorted_entries = sorted(entries, key=lambda e: len(e.term), reverse=True)

        for entry in sorted_entries:
            term = entry.term
            if term in matched:
                continue

            if term.isascii():
                # 英文缩写：word-boundary regex
                pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
                if pattern.search(text):
                    matched[term] = entry
            else:
                # 中文等非 ASCII：直接子串匹配
                if term in text:
                    matched[term] = entry

        return list(matched.values())

    @staticmethod
    def _meaning_to_dict(meaning: JargonMeaning) -> dict[str, Any]:
        """将 JargonMeaning 转换为字典格式（供 LLM Tool 返回）。"""
        return {
            "term": meaning.term,
            "group_id": meaning.group_id,
            "meaning": meaning.meaning,
            "confidence": meaning.confidence,
            "is_jargon": meaning.is_jargon,
            "is_confirmed": meaning.is_confirmed,
            "is_global": meaning.is_global,
            "is_complete": meaning.is_complete,
            "count": meaning.count,
        }


__all__ = [
    "JargonQueryService",
    "TTLCache",
]
