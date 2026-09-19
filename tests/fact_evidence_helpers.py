"""Synthetic, relocatable message evidence for focused memory contract fixtures."""

from __future__ import annotations

import hashlib
from typing import Any


def source_evidence(
    content: str = "A synthetic user fact.",
    *,
    message_id: int = 1,
    message_seq: int = 1,
    role: str = "user",
) -> list[dict[str, Any]]:
    return [
        {
            "message_index": 0,
            "message_id": message_id,
            "message_seq": message_seq,
            "role": role,
            "start": 0,
            "end": len(content),
            "message_fingerprint": hashlib.sha256(
                f"{role}\0{content}".encode()
            ).hexdigest(),
            "inferred": False,
        }
    ]


def fact_evidence(facts: list[str]) -> list[list[dict[str, Any]]]:
    return [
        source_evidence(
            fact or "Empty fact source", message_id=index + 1, message_seq=index + 1
        )
        for index, fact in enumerate(facts)
    ]


def candidate_evidence_metadata(content: str, **fields: Any) -> dict[str, Any]:
    """构造带逐事实用户来源证据的召回候选 metadata，供注入夹具复用。"""

    reference = source_evidence(content)[0]
    return {
        **fields,
        "key_facts": [content],
        "fact_source_evidence": [[reference]],
        "source_evidence": [reference],
    }
