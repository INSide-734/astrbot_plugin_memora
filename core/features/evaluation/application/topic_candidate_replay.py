"""Topic 候选回放：合成窗口、临时 catalog、多模式对比。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from ....shared.contracts.conversation import Message
from ....shared.summary_source import source_window_digest
from ...memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from ...memory.infrastructure.topic_catalog_store import TopicCatalogStore
from ...reflection.application.topic_candidate_selector import (
    TopicCandidateSelector,
)
from ...reflection.domain.config import CandidateReuseConfig
from ...reflection.domain.summary_models import (
    SourceWindow,
    TopicCandidateContext,
    TopicCandidateSelection,
    normalize_topic_key,
)
from ...reflection.domain.topic_label_renderer import render_topic_labels
from .topic_candidate_evidence import (
    CandidateReplayRecord,
    create_replay_record,
)


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """回放配置；只包含 selector 实际读取的字段。"""

    mode: Literal["off", "observe", "full", "top_k"]
    activation_threshold: int
    fixed_k: int
    max_full_topics: int
    max_full_prompt_tokens: int
    max_query_chars: int
    overfetch_factor: Literal[3]
    metrics_retention_days: int


@dataclass(frozen=True, slots=True)
class SyntheticWindow:
    """合成回放窗口夹具；标注仅留在内存，不代表 Provider 实测。"""

    case_hash: str
    bucket: str
    chat_type: Literal["private", "group"]
    scope_key: str
    privacy_level: Literal["public", "shared", "confidential"]
    resolver_revision: str
    semantic_clusters: tuple[str, ...]
    nonsemantic_occurrences: tuple[str, ...]
    negative_topics: tuple[str, ...]
    fragmentation_count: int | None
    messages: tuple[Message, ...]
    message_seqs: tuple[int, ...]
    window: SourceWindow
    context: TopicCandidateContext


@dataclass(frozen=True, slots=True)
class ReplayCase:
    """单个 case 的回放结果。"""

    case_hash: str
    bucket: str
    chat_type: str
    mode: str
    k: int | None
    record: CandidateReplayRecord | None
    privacy_reject: bool
    execution_status: Literal["success", "degraded", "error", "privacy_reject"] = (
        "error"
    )
    reason_code: str = "replay_unrun"
    rendered_prompt: str = ""  # 仅供内存中的回放检查，不写入证据报告。


def _make_window_hash(session_id: str, start_seq: int, end_seq: int) -> str:
    """生成窗口哈希。"""
    payload = json.dumps(
        {"session_id": session_id, "start_seq": start_seq, "end_seq": end_seq},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def create_temp_catalog(
    documents: Sequence[dict[str, Any]],
) -> tuple[Path, aiosqlite.Connection]:
    """创建临时 catalog 并写入 documents。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="replay_catalog_"))
    catalog_path = temp_dir / "catalog.db"

    db = await aiosqlite.connect(catalog_path)
    try:
        # 创建 documents 表
        await db.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        # 创建 catalog schema
        await create_topic_catalog_schema(db)

        # 设置 active_generation=1, status='ready'
        await db.execute(
            "UPDATE topic_catalog_state SET active_generation=1, status='ready' WHERE id=1"
        )

        # 插入 documents
        for doc in documents:
            await db.execute(
                "INSERT INTO documents (id, text, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    doc["id"],
                    doc["text"],
                    json.dumps(doc["metadata"]),
                    doc.get("created_at", "2026-01-01T00:00:00+00:00"),
                    doc.get("updated_at", "r1"),
                ),
            )

        await db.commit()

        # 调用 replace_memory_mappings 建立映射
        store = TopicCatalogStore(db)
        for doc in documents:
            memory_id = doc["id"]
            await store.replace_memory_mappings(memory_id, 1)

        return catalog_path, db
    except Exception:
        await db.close()
        raise


def create_synthetic_windows(
    scenarios: Sequence[dict[str, Any]],
) -> list[SyntheticWindow]:
    """根据脱敏场景创建回放窗口与仅内存可见的人工标注。"""
    windows: list[SyntheticWindow] = []

    for idx, scenario in enumerate(scenarios):
        session_id = f"replay_session_{idx}"
        start_seq = 0
        end_seq = len(scenario["messages"])
        expected_count = end_seq - start_seq
        messages = tuple(
            Message(
                id=i,
                session_id=session_id,
                role=message["role"],
                content=message["content"],
                sender_id=message.get("sender_id", "user"),
                timestamp=1704067200.0 + i,
            )
            for i, message in enumerate(scenario["messages"], start=1)
        )
        message_seqs = tuple(range(1, end_seq + 1))
        digest = source_window_digest(messages, message_seqs)
        window = SourceWindow(
            session_id=session_id,
            start_seq=start_seq,
            end_seq=end_seq,
            expected_count=expected_count,
            source_digest=digest,
            messages=messages,
            message_seqs=message_seqs,
        )

        fragmentation_count = scenario.get("fragmentation_count")
        if fragmentation_count is not None and (
            isinstance(fragmentation_count, bool)
            or not isinstance(fragmentation_count, int)
            or fragmentation_count < 0
        ):
            raise ValueError("fragmentation_count_invalid")

        scope_key = scenario["scope_key"]
        chat_type = scenario["chat_type"]
        privacy_level = scenario["privacy_level"]
        resolver_revision = scenario.get("resolver_revision", "resolver-1")
        context = TopicCandidateContext(
            scope_key=scope_key,
            chat_type=chat_type,
            privacy_level=privacy_level,
            resolver_revision=resolver_revision,
            source_digest=digest,
            session_epoch=1,
            scope_reason_code="scope_resolved",
            source_provenance_complete=True,
        )
        windows.append(
            SyntheticWindow(
                case_hash=_make_window_hash(session_id, start_seq, end_seq),
                bucket=scenario["bucket"],
                chat_type=chat_type,
                scope_key=scope_key,
                privacy_level=privacy_level,
                resolver_revision=resolver_revision,
                semantic_clusters=tuple(scenario.get("semantic_clusters", [])),
                nonsemantic_occurrences=tuple(
                    scenario.get("nonsemantic_occurrences", [])
                ),
                negative_topics=tuple(scenario.get("negative_topics", [])),
                fragmentation_count=fragmentation_count,
                messages=messages,
                message_seqs=message_seqs,
                window=window,
                context=context,
            )
        )
    return windows


@dataclass(frozen=True, slots=True)
class _ReplayAnnotationMetrics:
    """从人工标注与候选标签派生的纯计数指标。"""

    exact_reuse_count: int | None
    exact_topic_count: int | None
    candidate_occurrence_count: int | None
    misreuse_count: int | None
    duplicate_count: int | None
    window_topic_count: int | None
    fragmentation_count: int | None


def _derive_annotation_metrics(
    window: SyntheticWindow,
    labels: tuple[str, ...],
) -> _ReplayAnnotationMetrics:
    """从内存中的人工 occurrence 标注计算记录所需的低敏计数。"""
    candidate_keys = {
        key for label in labels if (key := normalize_topic_key(label)) is not None
    }
    semantic_keys = [
        key
        for topic in window.semantic_clusters
        if (key := normalize_topic_key(topic)) is not None
    ]
    nonsemantic_keys = [
        key
        for topic in window.nonsemantic_occurrences
        if (key := normalize_topic_key(topic)) is not None
    ]
    output_keys = semantic_keys + nonsemantic_keys
    if not output_keys:
        return _ReplayAnnotationMetrics(None, None, None, None, None, None, None)

    candidate_occurrences = sum(key in candidate_keys for key in output_keys)
    duplicate_count = sum(count - 1 for count in Counter(output_keys).values())
    return _ReplayAnnotationMetrics(
        exact_reuse_count=sum(key in candidate_keys for key in semantic_keys),
        exact_topic_count=len(semantic_keys) or None,
        candidate_occurrence_count=candidate_occurrences,
        misreuse_count=sum(key in candidate_keys for key in nonsemantic_keys),
        duplicate_count=duplicate_count,
        window_topic_count=len(output_keys),
        fragmentation_count=window.fragmentation_count,
    )


def _build_offline_selector_config(
    replay_config: ReplayConfig,
) -> CandidateReuseConfig:
    """非 oracle 回放使用真实配置校验，包括 K24 与线上预算约束。"""
    return CandidateReuseConfig(
        mode=replay_config.mode,
        activation_threshold=replay_config.activation_threshold,
        fixed_k=replay_config.fixed_k,
        max_full_topics=replay_config.max_full_topics,
        max_full_prompt_tokens=replay_config.max_full_prompt_tokens,
        max_query_chars=replay_config.max_query_chars,
        overfetch_factor=replay_config.overfetch_factor,
        metrics_retention_days=replay_config.metrics_retention_days,
    )


async def _canonical_snapshot(
    catalog_store: TopicCatalogStore,
    window: SyntheticWindow,
) -> tuple[int, tuple[str, ...]]:
    """离线 oracle 直接读 canonical 单快照并复用来源校验，不依赖派生目录。"""
    db = catalog_store.db_connection
    if db is None:
        raise ValueError("catalog_unavailable")
    cursor = await db.execute(
        """
        SELECT state.status, state.active_generation,
               document.id, document.metadata, document.created_at, document.updated_at
        FROM topic_catalog_state AS state LEFT JOIN documents AS document ON 1=1
        WHERE state.id=1 ORDER BY document.id
        """
    )
    rows = await cursor.fetchall()
    await cursor.close()
    if (
        not rows
        or rows[0][0] != "ready"
        or type(rows[0][1]) is not int
        or rows[0][1] <= 0
    ):
        raise ValueError("catalog_not_ready")
    labels: dict[str, str] = {}
    for row in rows:
        if row[2] is None:
            continue
        source = catalog_store._source_from_values(row[2], row[3], row[4], row[5])
        if source is not None and (
            source.scope_key == window.scope_key
            and source.privacy_level == window.privacy_level
            and source.chat_type == window.chat_type
            and source.resolver_revision == window.resolver_revision
        ):
            for key, label in source.topics:
                labels.setdefault(key, label)
    return rows[0][1], tuple(labels[key] for key in sorted(labels))


async def replay_window(
    window: SyntheticWindow,
    selector: TopicCandidateSelector,
    configs: dict[str, ReplayConfig],
    *,
    catalog_store: TopicCatalogStore,
) -> list[ReplayCase]:
    """执行真实 selector/独立 canonical oracle；失败与未实测字段绝不伪装成功。"""
    results: list[ReplayCase] = []
    try:
        generation, canonical_labels = await _canonical_snapshot(catalog_store, window)
        catalog_count: int | None = len(canonical_labels)
    except asyncio.CancelledError:
        raise
    except Exception:
        generation, canonical_labels, catalog_count = 0, (), None

    for config_name, replay_config in configs.items():
        started = time.perf_counter()
        status: Literal["success", "degraded", "error"] = "error"
        rendered_prompt = ""
        labels: tuple[str, ...] = ()
        selection = TopicCandidateSelection()
        annotation = _ReplayAnnotationMetrics(None, None, None, None, None, None, None)
        try:
            if catalog_count is None:
                raise ValueError("catalog_snapshot_unavailable")
            if config_name == "strict_full":
                if replay_config.mode != "full":
                    raise ValueError("strict_full_requires_full_mode")
                rendered = render_topic_labels(
                    canonical_labels,
                    max_total_tokens=0,
                    max_labels=max(1, len(canonical_labels)),
                    max_chars=max(
                        1,
                        sum(
                            len(json.dumps(label, ensure_ascii=False)) + 2
                            for label in canonical_labels
                        ),
                    ),
                )
                rendered_prompt = rendered.rendered_block
                selection = TopicCandidateSelection(
                    labels=canonical_labels,
                    source_provenance_complete=True,
                    mode="full",
                    effective_mode="full",
                    catalog_status="ready",
                    reason_code="strict_full_success",
                    candidate_count=len(canonical_labels),
                    selector_duration_ms=(time.perf_counter() - started) * 1000,
                )
                status = "success"
            else:
                selection = await selector.select_candidates(
                    window.window,
                    window.context,
                    _build_offline_selector_config(replay_config),
                )
                rendered_prompt = selection.render_prompt()
                succeeded = (
                    replay_config.mode == "off" and selection.reason_code == "mode_off"
                ) or (
                    selection.source_provenance_complete is True
                    and selection.effective_mode in ("full", "top_k")
                    and not selection.budget_reason
                    and selection.reason_code
                    in ("full_success", "top_k_success", "fill_only_success")
                )
                status = "success" if succeeded else "degraded"
            # renderer 的 quoted-item 是唯一模型可见集合，不能用未渲染标签作标注分母。
            labels = tuple(
                json.loads(line[2:])
                for line in rendered_prompt.splitlines()
                if line.startswith("- ")
            )
            if replay_config.mode != "off" and status == "success":
                annotation = _derive_annotation_metrics(window, labels)
            reason_code = selection.reason_code
        except asyncio.CancelledError:
            raise
        except Exception:
            status = "error"
            reason_code = "replay_failed"
            rendered_prompt = ""
            labels = ()

        negative_keys = {normalize_topic_key(topic) for topic in window.negative_topics}
        safety_violations = sum(
            normalize_topic_key(label) in negative_keys for label in labels
        )
        record = create_replay_record(
            case_hash=window.case_hash,
            scale_bucket=window.bucket,
            chat_type=window.chat_type,
            catalog_generation=generation,
            catalog_topic_count=catalog_count,
            variant=config_name,
            source_window_digest=window.window.source_digest,
            candidate_count=len(labels),
            bm25_hit_count=selection.bm25_hit_count,
            recent_fill_count=selection.recent_fill_count,
            identity_drop_count=selection.identity_drop_count,
            safety_violation_count=safety_violations,
            exact_reuse_count=annotation.exact_reuse_count,
            exact_topic_count=annotation.exact_topic_count,
            candidate_occurrence_count=annotation.candidate_occurrence_count,
            misreuse_count=annotation.misreuse_count,
            duplicate_count=annotation.duplicate_count,
            window_topic_count=annotation.window_topic_count,
            fragmentation_count=annotation.fragmentation_count,
            token_availability="unavailable",
            estimated_tokens=None,
            selector_duration_ms=(time.perf_counter() - started) * 1000,
            # selector-only 回放没有实际 Provider 调用，场景中同名注解不构成测量。
            provider_duration_ms=None,
            reason_code=reason_code,
            is_negative_window=bool(window.negative_topics),
            execution_status=status,
        )
        results.append(
            ReplayCase(
                case_hash=window.case_hash,
                bucket=window.bucket,
                chat_type=window.chat_type,
                mode=config_name,
                k=replay_config.fixed_k if replay_config.mode == "top_k" else None,
                record=record,
                privacy_reject=False,
                execution_status=status,
                reason_code=reason_code,
                rendered_prompt=rendered_prompt,
            )
        )
    return results


async def batch_replay(
    windows: Sequence[SyntheticWindow],
    selector: TopicCandidateSelector,
    configs: dict[str, ReplayConfig],
    *,
    catalog_store: TopicCatalogStore,
) -> list[ReplayCase]:
    """批量回放所有窗口。"""
    all_results: list[ReplayCase] = []

    for window in windows:
        cases = await replay_window(
            window, selector, configs, catalog_store=catalog_store
        )
        all_results.extend(cases)

    return all_results


__all__ = [
    "ReplayConfig",
    "SyntheticWindow",
    "ReplayCase",
    "create_temp_catalog",
    "create_synthetic_windows",
    "replay_window",
    "batch_replay",
]
