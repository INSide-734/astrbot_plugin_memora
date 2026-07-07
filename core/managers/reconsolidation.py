"""记忆再巩固 — 召回时可选 LLM 微调记忆内容，保留原始版本。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from astrbot.api import logger


class ReconsolidationManager:
    """管理记忆再巩固流程。每次回忆都是重新存储的机会。"""

    def __init__(
        self,
        update_memory_cb: Callable | None = None,
        get_memory_cb: Callable | None = None,
        llm_caller: Callable | None = None,
        enabled: bool = True,
    ) -> None:
        self._update_memory = update_memory_cb
        self._get_memory = get_memory_cb
        self._llm = llm_caller
        self._enabled = enabled

    _RECONSOLIDATION_PROMPT = (
        "你是记忆编辑器。根据最近上下文对以下长期记忆做轻微修正（保持原意）。"
        "如记忆已准确，直接返回原内容。\n\n"
        "原始记忆: {original}\n"
        "近期上下文: {context}\n\n"
        "返回修正后记忆（仅返回内容，无额外文字）："
    )

    async def maybe_reconsolidate(
        self,
        memory_id: int,
        context: str = "",
    ) -> dict[str, Any] | None:
        if not self._enabled or self._get_memory is None or self._update_memory is None:
            return None
        try:
            memory = await self._get_memory(memory_id)
            if not memory:
                return None
            text = memory.get("text") or memory.get("content", "")
            if not text.strip():
                return None
            meta = memory.get("metadata", {}) or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            # 24h 内不再重复巩固
            last_recon = float(meta.get("last_reconsolidated_at", 0) or 0)
            if time.time() - last_recon < 86400:
                return None

            revised = text
            llm_used = False
            if self._llm is not None and context.strip():
                try:
                    prompt = self._RECONSOLIDATION_PROMPT.format(
                        original=text,
                        context=context[:500],
                    )
                    result = await self._llm(prompt)
                    if result and len(str(result).strip()) >= 10:
                        revised = str(result).strip()
                        llm_used = True
                except Exception:
                    logger.debug("[Reconsolidation] LLM failed", exc_info=True)

            if not llm_used or revised == text:
                return {"memory_id": memory_id, "revised": False, "reason": "unchanged"}

            history: list[dict[str, Any]] = (
                meta.get("reconsolidation_history", []) or []
            )
            if isinstance(history, str):
                try:
                    history = json.loads(history)
                except (json.JSONDecodeError, TypeError):
                    history = []
            history.append(
                {
                    "timestamp": time.time(),
                    "original": text[:200],
                    "revised": revised[:200],
                    "context_snippet": context[:100],
                }
            )

            new_meta = dict(meta)
            new_meta["original_content"] = new_meta.get("original_content") or text
            new_meta["reconsolidation_count"] = (
                int(new_meta.get("reconsolidation_count", 0)) + 1
            )
            new_meta["reconsolidation_history"] = history[-5:]
            new_meta["last_reconsolidated_at"] = time.time()

            await self._update_memory(
                memory_id, {"content": revised, "metadata": new_meta}
            )
            logger.info(
                f"[Reconsolidation] #{memory_id}: "
                f"'{text[:40]}...' → '{revised[:40]}...'"
            )
            return {
                "memory_id": memory_id,
                "revised": True,
                "count": new_meta["reconsolidation_count"],
            }
        except Exception:
            logger.debug(f"[Reconsolidation] #{memory_id} failed", exc_info=True)
            return None


__all__ = ["ReconsolidationManager"]
