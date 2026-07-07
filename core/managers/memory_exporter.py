"""记忆导入导出 — JSONL / Markdown 格式，支持去重合并导入。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from typing import Any

import aiofiles

from astrbot.api import logger


class MemoryExporter:
    """记忆导出器 — JSONL / Markdown。"""

    def __init__(
        self,
        get_all_memories_cb: Callable | None = None,
    ) -> None:
        self._get_all = get_all_memories_cb

    @staticmethod
    def _build_jsonl_line(mem: dict[str, Any]) -> str:
        record = {
            "id": mem.get("id"),
            "content": mem.get("text") or mem.get("content", ""),
            "metadata": mem.get("metadata", {}),
            "exported_at": time.time(),
        }
        return json.dumps(record, ensure_ascii=False) + "\n"

    async def export_jsonl(
        self,
        output_path: str,
        session_id: str | None = None,
    ) -> int:
        memories = await self._fetch_memories(session_id)
        await asyncio.to_thread(os.makedirs, os.path.dirname(output_path) or ".", exist_ok=True)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
            for mem in memories:
                await f.write(self._build_jsonl_line(mem))
        count = len(memories)
        logger.info(f"[Export] JSONL: {count} memories → {output_path}")
        return count

    async def export_markdown(
        self,
        output_path: str,
        session_id: str | None = None,
    ) -> int:
        memories = await self._fetch_memories(session_id)
        lines: list[str] = [
            "# Memora Export",
            f"Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Count: {len(memories)}",
            "",
            "---",
            "",
        ]
        for i, mem in enumerate(memories, 1):
            meta = mem.get("metadata", {}) or {}
            content = mem.get("text") or mem.get("content", "")
            topics = meta.get("topics", []) or []
            emotion_tags = meta.get("emotion_tags", []) or []
            importance = meta.get("importance", 0.5)
            created = meta.get("create_time", 0)
            lines.append(f"## Memory #{i}")
            lines.append("")
            lines.append(f"- **Importance**: {importance:.2f}")
            if topics:
                lines.append(f"- **Topics**: {', '.join(str(t) for t in topics)}")
            if emotion_tags:
                lines.append(
                    f"- **Emotions**: {', '.join(str(e) for e in emotion_tags)}"
                )
            if created:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(created)))
                lines.append(f"- **Created**: {ts}")
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")
        await asyncio.to_thread(os.makedirs, os.path.dirname(output_path) or ".", exist_ok=True)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
            await f.write("\n".join(lines))
        logger.info(f"[Export] Markdown: {len(memories)} memories → {output_path}")
        return len(memories)

    async def _fetch_memories(
        self,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        if self._get_all is not None:
            return await self._get_all(session_id) or []
        return []


class MemoryImporter:
    """记忆导入器 — JSONL 去重合并导入。"""

    _DEDUP_BATCH_SIZE: int = 10  # 并发去重搜索的批次大小

    def __init__(
        self,
        add_memory_cb: Callable | None = None,
        search_memories_cb: Callable | None = None,
    ) -> None:
        self._add_memory = add_memory_cb
        self._search = search_memories_cb

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]

    async def import_jsonl(
        self,
        input_path: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"JSONL not found: {input_path}")
        records: list[dict[str, Any]] = []
        async with aiofiles.open(input_path, encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"[Import] skip malformed: {line[:60]}...")

        result = {
            "total": len(records),
            "imported": 0,
            "skipped_duplicate": 0,
            "errors": 0,
        }
        seen_hashes: set[str] = set()

        # 去重：分批并发搜索已有内容
        if self._search is not None:
            valid_records = [r for r in records if r.get("content")]
            batch_size = self._DEDUP_BATCH_SIZE
            for batch_start in range(0, len(valid_records), batch_size):
                batch = valid_records[batch_start : batch_start + batch_size]

                async def _dedup_one(rec):
                    try:
                        content = rec.get("content", "")
                        existing = await self._search(
                            content[:80],
                            k=1,
                            session_id=session_id,
                            persona_id=persona_id,
                            recall_type="passive",
                        )
                        hashes = set()
                        if existing:
                            for ex in existing:
                                ex_content = ex.content or ""
                                hashes.add(self._content_hash(ex_content))
                        return hashes
                    except Exception:
                        return set()

                batch_results = await asyncio.gather(*[_dedup_one(r) for r in batch])
                for hashes in batch_results:
                    seen_hashes.update(hashes)

        for rec in records:
            content = rec.get("content", "")
            if not content or not content.strip():
                result["errors"] += 1
                continue
            h = self._content_hash(content)
            if h in seen_hashes:
                result["skipped_duplicate"] += 1
                continue
            if dry_run:
                result["imported"] += 1
                seen_hashes.add(h)
                continue
            try:
                meta = rec.get("metadata", {}) or {}
                if session_id:
                    meta["session_id"] = session_id
                if persona_id:
                    meta["persona_id"] = persona_id
                meta["imported_at"] = time.time()
                meta["import_source_id"] = rec.get("id")
                if self._add_memory is not None:
                    await self._add_memory(
                        content=content,
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=float(meta.get("importance", 0.5)),
                        metadata=meta,
                    )
                result["imported"] += 1
                seen_hashes.add(h)
            except Exception:
                logger.debug(f"[Import] failed: {content[:60]}...", exc_info=True)
                result["errors"] += 1

        logger.info(
            f"[Import] total={result['total']}, imported={result['imported']}, "
            f"skipped={result['skipped_duplicate']}, errors={result['errors']}"
        )
        return result


__all__ = ["MemoryExporter", "MemoryImporter"]
