#!/usr/bin/env python3
"""真实 SQLite/FAISS + GraphStore/AtomStore 的合成效果门场景。

使用真实 ``MemoryEngine`` 写入 canonical、FTS、FAISS、图与 Atom，然后通过
真实的 DualRouteRetriever / RouteExecutionCoordinator / MMR / select_candidates
执行七类样本。可通过 ``frozen_dir`` 复用已冻结的数据库与向量索引，保证
manifest 的 DB 哈希跨运行稳定。
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB  # noqa: E402

from core.features.backup.infrastructure.snapshot import (  # noqa: E402
    sha256_file,
    snapshot_sqlite,
)
from core.features.evaluation.application.retrieval_quality import (  # noqa: E402
    EvaluationCase,
)
from core.features.memory.application.memory_engine import MemoryEngine  # noqa: E402
from core.features.memory.domain.memory_atom import (  # noqa: E402
    AtomType,
    MemoryAtom,
)

CATEGORIES = (
    "positive",
    "negative",
    "ambiguous",
    "cross_scope",
    "relation",
    "multi_hop",
    "long_payload",
)
_CASES_PER_CATEGORY = 10
_SCOPE_KEY = "scope:test"
_PRIVACY_LEVEL = "confidential"
_SESSION_ID = "eval-session"
_PERSONA_ID = "eval-persona"
_EMBEDDING_DIM = 64
_FROZEN_FILES = (
    "memora.db",
    "documents.faiss",
    "graph.db",
    "graph.faiss",
    "cases.json",
)


class _DeterministicEmbeddingProvider:
    """真实 FAISS 使用的确定性 embedding 提供者。"""

    def get_dim(self) -> int:
        return _EMBEDDING_DIM

    async def get_embedding(self, text: str) -> list[float]:
        return _embedding(text)

    async def get_embeddings_batch(
        self,
        texts: list[str],
        **_kwargs: Any,
    ) -> list[list[float]]:
        return [_embedding(text) for text in texts]


@dataclass
class SyntheticScenario:
    """真实存储场景及其清理边界。"""

    engine: MemoryEngine
    cases: list[EvaluationCase]
    db_snapshot_hash: str
    reused_frozen_db: bool
    _workdir: tempfile.TemporaryDirectory[str]
    _document_faiss: FaissVecDB
    _graph_faiss: FaissVecDB

    async def close(self) -> None:
        """关闭引擎与向量库，并删除临时目录。"""

        try:
            await self.engine.close()
        finally:
            for faiss_db in (self._document_faiss, self._graph_faiss):
                try:
                    await faiss_db.close()
                except Exception:
                    pass
            self._workdir.cleanup()


async def build_synthetic_scenario(
    frozen_dir: Path | None = None,
) -> SyntheticScenario:
    """构造或复用真实存储场景，并返回七类样本。"""

    workdir = tempfile.TemporaryDirectory(prefix="memora-effect-gate-")
    root = Path(workdir.name)
    db_path = root / "memora.db"
    db_snapshot_hash = ""
    reuse = frozen_dir is not None and _frozen_ready(frozen_dir)
    if reuse:
        assert frozen_dir is not None
        for name in _FROZEN_FILES[:-1]:
            shutil.copy2(frozen_dir / name, root / name)
        cases = _load_cases(frozen_dir / "cases.json")
        db_snapshot_hash = sha256_file(frozen_dir / "memora.db")
    else:
        cases = []

    document_faiss = FaissVecDB(
        str(db_path),
        str(root / "documents.faiss"),
        _DeterministicEmbeddingProvider(),  # type: ignore[arg-type]
    )
    graph_faiss = FaissVecDB(
        str(root / "graph.db"),
        str(root / "graph.faiss"),
        _DeterministicEmbeddingProvider(),  # type: ignore[arg-type]
    )
    await document_faiss.initialize()
    await graph_faiss.initialize()
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=document_faiss,
        graph_vector_db=graph_faiss,
        llm_provider=None,
        config=_config(),
    )
    await engine.initialize()

    if not reuse:
        cases = await _seed_cases(engine)
        if frozen_dir is not None:
            frozen_dir.mkdir(parents=True, exist_ok=True)
            snapshot_sqlite(db_path, frozen_dir / "memora.db")
            for name in _FROZEN_FILES[1:-1]:
                shutil.copy2(root / name, frozen_dir / name)
            _dump_cases(frozen_dir / "cases.json", cases)
            db_snapshot_hash = sha256_file(frozen_dir / "memora.db")
        else:
            snapshot = snapshot_sqlite(db_path, root / "snapshot.db")
            db_snapshot_hash = snapshot.sha256

    return SyntheticScenario(
        engine=engine,
        cases=cases,
        db_snapshot_hash=db_snapshot_hash,
        reused_frozen_db=reuse,
        _workdir=workdir,
        _document_faiss=document_faiss,
        _graph_faiss=graph_faiss,
    )


async def _seed_cases(engine: MemoryEngine) -> list[EvaluationCase]:
    """写入七类 canonical/Atom/图数据并返回样本。"""

    cases: list[EvaluationCase] = []
    for category_index, category in enumerate(CATEGORIES):
        for index in range(_CASES_PER_CATEGORY):
            case_id = f"case-{category_index * _CASES_PER_CATEGORY + index:03d}"
            token = _token(category, index)
            if category in {"relation", "multi_hop", "cross_scope"}:
                content = f"linked{category.replace('_', '')}{index}"
                scope_key = "scope:other" if category == "cross_scope" else _SCOPE_KEY
                memory_id = await engine.add_memory(
                    content,
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(
                        content,
                        scope_key=scope_key,
                        topics=[token],
                    ),
                    atoms=[],
                )
                relevant = {str(memory_id)}
            elif category == "negative":
                content = token
                await engine.add_memory(
                    content,
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(content),
                    atoms=[],
                )
                relevant = {"__no_relevant__"}
            elif category == "ambiguous":
                content = token
                memory_id = await engine.add_memory(
                    content,
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(content),
                    atoms=[],
                )
                distractor_id = await engine.add_memory(
                    f"{token} noise",
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(f"{token} noise"),
                    atoms=[],
                )
                relevant = {str(memory_id), str(distractor_id)}
            elif category == "long_payload":
                content = f"{token} " + "payload " * 800
                memory_id = await engine.add_memory(
                    content,
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(content),
                    atoms=[],
                )
                relevant = {str(memory_id)}
            elif category == "positive" and index % 2 == 0:
                # 文档正文不含查询 token，命中主要来自 Atom 证据。
                content = f"atomfiller{index}"
                memory_id = await engine.add_memory(
                    content,
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(content),
                    atoms=[_atom(token)],
                )
                relevant = {str(memory_id)}
            else:
                content = token
                memory_id = await engine.add_memory(
                    content,
                    session_id=_SESSION_ID,
                    persona_id=_PERSONA_ID,
                    importance=0.8,
                    metadata=_metadata(content),
                    atoms=[],
                )
                relevant = {str(memory_id)}

            metadata: dict[str, Any] = {
                "categories": [category],
                "chat_type": "private",
                "scope_key": _SCOPE_KEY,
                "privacy_level": _PRIVACY_LEVEL,
                "query_intent": {
                    "intent": (
                        "relationship"
                        if category == "relation"
                        else "temporal"
                        if category == "multi_hop"
                        else "contextual"
                        if category == "ambiguous"
                        else "default"
                    )
                },
            }
            if category == "negative":
                metadata["expected_no_hit"] = True
            else:
                metadata["required_key_facts"] = [content]
            cases.append(
                EvaluationCase(
                    case_id=case_id,
                    query=token,
                    relevant_doc_ids=relevant,
                    metadata=metadata,
                )
            )
    return cases


def _frozen_ready(directory: Path) -> bool:
    return all((directory / name).is_file() for name in _FROZEN_FILES)


def _dump_cases(path: Path, cases: list[EvaluationCase]) -> None:
    payload = [
        {
            "case_id": case.case_id,
            "query": case.query,
            "relevant_doc_ids": sorted(case.relevant_doc_ids),
            "metadata": case.metadata,
        }
        for case in cases
    ]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _load_cases(path: Path) -> list[EvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvaluationCase(
            case_id=str(item["case_id"]),
            query=str(item["query"]),
            relevant_doc_ids={str(value) for value in item["relevant_doc_ids"]},
            metadata=dict(item.get("metadata") or {}),
        )
        for item in payload
    ]


def _config() -> dict[str, Any]:
    return {
        "graph_memory_enabled": True,
        "graph_route_weight": 0.35,
        "document_route_weight": 0.65,
        "cross_route_bonus": 0.08,
        "dynamic_route_weighting": True,
        "graph_retrieval_enabled": True,
        "atom_route_weight": 0.25,
        "atom_enabled": True,
        "graph_memory_atom_enabled": True,
        "graph_expansion_hops": 1,
        "graph_expansion_limit": 24,
        "reranker.enabled": False,
        "reranker.strategy": "mmr",
        "reranker.mmr_lambda": 0.7,
        "reranker.embedding_similarity_lambda": 0.7,
        "security.strict_mode": False,
        "memory_evolution": {"enabled": False, "mode": "disabled"},
        "user_profile": {"enabled": False},
        "auto_learning": {"enabled": False},
        "knowledge_base": {"enabled": False},
        "notes": {"enabled": False},
        "continuity_tracking": {"enabled": False},
        "reconsolidation": {"enabled": False},
        "anomaly_detection": {"enabled": False},
        "export": {"enabled": False},
        "write_reliability": {"repair_enabled": False, "max_retries": 1},
        "semantic_compression": {"enabled": False},
        "search_cache_enabled": False,
        "search_cache_max_size": 0,
        "rrf_k": 60,
    }


def _metadata(
    content: str,
    *,
    scope_key: str = _SCOPE_KEY,
    topics: list[str] | None = None,
) -> dict[str, Any]:
    evidence = _evidence(content)
    metadata: dict[str, Any] = {
        "key_facts": [content],
        "fact_source_evidence": [[evidence]],
        "source_evidence": [evidence],
        "scope_key": scope_key,
        "privacy_level": _PRIVACY_LEVEL,
        "session_id": _SESSION_ID,
        "persona_id": _PERSONA_ID,
        "memory_status": "active",
        "importance": 0.8,
    }
    if topics:
        metadata["topics"] = list(topics)
    return metadata


def _atom(content: str) -> MemoryAtom:
    return MemoryAtom(
        parent_memory_id=0,
        atom_type=AtomType.FACTUAL,
        content=content,
        source_evidence=[_evidence(content)],
        importance=0.8,
        session_id=_SESSION_ID,
        persona_id=_PERSONA_ID,
    )


def _evidence(content: str) -> dict[str, Any]:
    return {
        "message_index": 0,
        "message_id": 1,
        "message_seq": 1,
        "role": "user",
        "start": 0,
        "end": len(content),
        "message_fingerprint": hashlib.sha256(f"user\0{content}".encode()).hexdigest(),
        "inferred": False,
    }


def _token(category: str, index: int) -> str:
    return f"mem{category.replace('_', '')}{index}"


def _embedding(text: str) -> list[float]:
    vector = [0.0] * _EMBEDDING_DIM
    for token in str(text).casefold().replace("-", " ").replace("_", " ").split():
        vector[
            int(hashlib.sha256(token.encode()).hexdigest(), 16) % _EMBEDDING_DIM
        ] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
