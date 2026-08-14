"""MemoryEngine 写入链的原子准备、成功筛选与质量观测辅助。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable

from astrbot.api import logger

from .atom_lifecycle_manager import dedup_atoms_batch


def prepare_atoms_for_write(
    atoms: Iterable[Any],
    *,
    session_id: str | None,
    persona_id: str | None,
    config: dict[str, Any],
) -> list[Any]:
    """补齐作用域并按配置对同一 canonical 批次去重。"""

    prepared = list(atoms)
    for atom in prepared:
        atom.session_id = atom.session_id or session_id
        atom.persona_id = atom.persona_id or persona_id
    if not bool(config.get("atom_dedup_enabled", True)):
        return prepared
    return dedup_atoms_batch(
        prepared,
        similarity_threshold=float(config.get("atom_dedup_threshold", 0.7)),
    )


def successful_atoms(atoms: Iterable[Any]) -> list[Any]:
    """返回已经获得有效内部 ID 的实际持久化 Atom。"""

    return [atom for atom in atoms if int(getattr(atom, "atom_id", 0) or 0) > 0]


async def reinforce_existing_atoms(manager: Any, atoms: list[Any]) -> None:
    """用新可信证据强化同作用域旧 Atom，普通失败不阻塞 canonical 写入。"""

    if manager is None or not atoms:
        return
    try:
        await manager.run_manual_reinforcement(atoms, similarity_threshold=0.6)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "[MemoryEngine] 原子重复证据强化失败，异常类型=%s",
            exc.__class__.__name__,
        )


def record_quality_samples(
    scorer: Any,
    *,
    doc_id: int,
    content: str,
    metadata: dict[str, Any],
    atoms: list[Any],
) -> None:
    """优先用真实成功 Atom 采样；无 Atom 时保留 canonical 基线采样。"""

    source_type = (
        metadata.get("source_type") or metadata.get("chat_type") or "group_chat"
    )
    if atoms:
        payloads = [
            {
                "content": str(atom.content),
                "source_type": source_type,
                "created_at": float(getattr(atom, "created_at", time.time())),
                "ttl_days": float(getattr(atom, "ttl_days", 30.0)),
                "verified": bool((getattr(atom, "metadata", {}) or {}).get("verified")),
                "importance": float(getattr(atom, "importance", 0.5)),
            }
            for atom in atoms
        ]
    else:
        payloads = [
            {
                "id": doc_id,
                "content": content,
                "source_type": source_type,
                "created_at": metadata.get("create_time", time.time()),
                "ttl_days": metadata.get("ttl_days", metadata.get("ttl", 30.0)),
                "verified": metadata.get("verified", False),
                "importance": metadata.get("importance", 0.5),
            }
        ]

    check_alerts = getattr(scorer, "check_alerts", None)
    for payload in payloads:
        score = scorer.score_atom(payload, context={"metadata": metadata})
        if callable(check_alerts):
            check_alerts(score)


__all__ = [
    "prepare_atoms_for_write",
    "record_quality_samples",
    "reinforce_existing_atoms",
    "successful_atoms",
]
