"""记忆原子周期性生命周期管理器。

v2.6: 新增同批次原子去重 (_dedup_atoms_batch) 和冷存储迁移 (migrate_to_cold)。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import Any

from astrbot.api import logger

from ..storage.atom_store import AtomStore


def dedup_atoms_batch(
    atoms: list,
    similarity_threshold: float = 0.7,
) -> list:
    """合并内容高度相似的原子，保留置信度更高的。

    使用 Jaccard 相似度（基于词集或 CJK bigram）检测重复。
    用于同一批次原子插入前的去重。

    Args:
        atoms: MemoryAtom 列表。
        similarity_threshold: Jaccard 相似度阈值（默认 0.7）。

    Returns:
        去重后的原子列表。
    """
    if len(atoms) <= 1:
        return list(atoms)

    kept: list = []
    for atom in atoms:
        is_dup = False
        content = str(getattr(atom, "content", "")).lower()
        tokens = set(content.split())
        # 短文本使用 CJK bigram 作为补充 token
        if len(tokens) < 3:
            chars = content.replace(" ", "")
            if len(chars) >= 4:
                tokens = {chars[i : i + 2] for i in range(len(chars) - 1)}

        for i, existing in enumerate(kept):
            ex_content = str(getattr(existing, "content", "")).lower()
            ex_tokens = set(ex_content.split())
            if len(ex_tokens) < 3:
                chars = ex_content.replace(" ", "")
                if len(chars) >= 4:
                    ex_tokens = {chars[i : i + 2] for i in range(len(chars) - 1)}

            if len(tokens) < 2 or len(ex_tokens) < 2:
                continue

            jaccard = len(tokens & ex_tokens) / max(1, len(tokens | ex_tokens))
            if jaccard >= similarity_threshold:
                # 保留置信度更高的
                new_conf = float(getattr(atom, "confidence", 0.7))
                old_conf = float(getattr(existing, "confidence", 0.7))
                if new_conf > old_conf:
                    kept[i] = atom
                is_dup = True
                break

        if not is_dup:
            kept.append(atom)

    if len(atoms) > len(kept):
        logger.debug(
            f"[AtomLifecycle] 同批次去重: {len(atoms)} → {len(kept)} "
            f"(移除 {len(atoms) - len(kept)} 条重复)"
        )

    return kept


class AtomLifecycleManager:
    """调度并执行原子生命周期维护任务。"""

    def __init__(
        self,
        atom_store: AtomStore,
        config: dict[str, Any] | None = None,
    ):
        self.atom_store = atom_store
        self.config = config or {}
        self._maintenance_interval_hours = float(
            self.config.get("atom_maintenance_interval_hours", 24.0)
        )
        self._forget_delay_days = float(self.config.get("atom_forget_delay_days", 7.0))
        self._purge_delay_days = float(
            self.config.get(
                "atom_purge_delay_days",
                max(self._forget_delay_days * 4.0, 30.0),
            )
        )
        # v2.6 冷存储配置
        self._cold_storage_enabled = bool(
            self.config.get("atom_cold_storage_enabled", True)
        )
        self._cold_days_threshold = float(
            self.config.get("atom_cold_days_threshold", 14.0)
        )
        self._cold_max_importance = float(
            self.config.get("atom_cold_max_importance", 0.4)
        )
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """在后台启动周期性维护。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._maintenance_loop())

    async def stop(self) -> None:
        """取消维护循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _maintenance_loop(self) -> None:
        while self._running:
            try:
                await self.run_maintenance()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("[AtomLifecycle] 维护任务异常", exc_info=True)
                await asyncio.sleep(60.0)
                continue
            await asyncio.sleep(self._maintenance_interval_hours * 3600.0)

    async def run_maintenance(self) -> dict[str, int]:
        """执行一次完整的维护周期，返回每项操作的数量。"""
        result: dict[str, int] = {}

        # 1. 使过期原子失效
        expired = await self.atom_store.expire_stale_atoms()
        result["expired"] = expired

        # 2. 软删除旧过期原子：保留元数据但从 FTS 中移除
        forgotten = await self.atom_store.forget_expired_atoms(self._forget_delay_days)
        result["forgotten"] = forgotten

        # 3. 物理清除更久远的被遗忘原子以限制长期存储
        purged = await self.atom_store.cleanup_forgotten(self._purge_delay_days)
        result["purged"] = purged

        # 4. v2.6: 冷存储迁移 — 将长期未被访问的低重要性原子标记为 COLD
        if self._cold_storage_enabled:
            cold_migrated = await self.atom_store.migrate_to_cold(
                cold_days_threshold=self._cold_days_threshold,
                max_importance=self._cold_max_importance,
            )
            result["cold_migrated"] = cold_migrated

        return result

    async def run_manual_reinforcement(
        self,
        new_atoms: list,
        similarity_threshold: float = 0.6,
    ) -> int:
        """尝试查找并增强与新原子相似的已有原子。

        为效率考虑，使用基于内容的简单重叠度（词集 Jaccard 相似度）。
        返回被增强的原子数量。
        """
        if not new_atoms:
            return 0

        reinforced = 0
        for new_atom in new_atoms:
            content = str(new_atom.content)
            new_tokens = set(content.lower().split())
            # CJK 或短文本使用字符 bigram 作为备选 token
            if len(new_tokens) < 3:
                chars = content.replace(" ", "")
                if len(chars) >= 4:
                    new_tokens = {chars[i : i + 2] for i in range(len(chars) - 1)}

            if len(new_tokens) < 2:
                continue

            search_query = " ".join(list(new_tokens)[:8])
            existing = await self.atom_store.search_fts(
                search_query,
                limit=5,
                session_id=getattr(new_atom, "session_id", None),
                persona_id=getattr(new_atom, "persona_id", None),
                include_expired=False,
            )
            source_filter = getattr(self.atom_store, "filter_current_sources", None)
            if callable(source_filter):
                filtered = source_filter(existing)
                if inspect.isawaitable(filtered):
                    filtered = await filtered
                if isinstance(filtered, list):
                    existing = filtered
            for ex in existing:
                ex_content = ex.content.lower()
                ex_tokens = set(ex_content.split())
                if len(ex_tokens) < 2:
                    ex_tokens = (
                        {ex_content[i : i + 2] for i in range(len(ex_content) - 1)}
                        if len(ex_content) >= 4
                        else set()
                    )
                if not ex_tokens or not new_tokens:
                    continue
                jaccard = len(new_tokens & ex_tokens) / max(
                    1, len(new_tokens | ex_tokens)
                )
                if jaccard >= similarity_threshold:
                    await self.atom_store.reinforce(
                        ex.atom_id,
                        new_confidence=float(getattr(new_atom, "confidence", 0.7)),
                    )
                    reinforced += 1
                    break

        return reinforced


__all__ = ["AtomLifecycleManager"]
