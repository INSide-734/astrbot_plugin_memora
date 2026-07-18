"""派生记忆演化对象的 SQLite 持久化。

本 Store 保持证据平面的 ``documents`` 不变；派生行按 source revision
保存，并支持失效和重建。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Iterable

from ..models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    JobClaim,
    JobSpec,
    JobState,
    MemoryEvolutionJob,
    ProjectionSourceView,
    ProjectionBundle,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
    RetrySpec,
    MemorySourceRef,
)
from .base_store import BaseStore


def _dt(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class MemoryEvolutionStore(BaseStore):
    """保存 job、relation、projection 和 source mapping 的本地 Store。"""

    async def _create_tables(self) -> None:
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_evolution_jobs (
              job_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, bucket_key TEXT NOT NULL,
              state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
              source_ids_json TEXT NOT NULL DEFAULT '[]', not_before TEXT NOT NULL,
              lease_until TEXT, worker_token TEXT, idempotency_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error_code TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_memory_evolution_jobs_ready
              ON memory_evolution_jobs(state, not_before, lease_until);
            CREATE TABLE IF NOT EXISTS memory_relations (
              relation_id TEXT PRIMARY KEY, relation_key TEXT NOT NULL UNIQUE,
              source_memory_id INTEGER NOT NULL, target_memory_id INTEGER NOT NULL,
              source_revision TEXT NOT NULL, target_revision TEXT NOT NULL,
              relation_type TEXT NOT NULL, state TEXT NOT NULL, confidence REAL NOT NULL,
              scope_key TEXT NOT NULL, privacy_level TEXT NOT NULL,
              valid_from TEXT, valid_to TEXT, origin_job_id TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_relations_seed
              ON memory_relations(source_memory_id, state, scope_key);
            CREATE INDEX IF NOT EXISTS idx_memory_relations_target
              ON memory_relations(target_memory_id, state, scope_key);
            CREATE TABLE IF NOT EXISTS memory_projections (
              projection_id TEXT PRIMARY KEY, projection_key TEXT NOT NULL UNIQUE,
              projection_type TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              state TEXT NOT NULL, summary TEXT NOT NULL, primary_source_memory_id INTEGER NOT NULL,
              scope_key TEXT NOT NULL, privacy_level TEXT NOT NULL, confidence REAL NOT NULL,
              valid_from TEXT, valid_to TEXT, origin_job_id TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_projections_seed
              ON memory_projections(primary_source_memory_id, state, scope_key);
            CREATE TABLE IF NOT EXISTS memory_projection_sources (
              projection_id TEXT NOT NULL, memory_id INTEGER NOT NULL,
              revision_token TEXT NOT NULL, source_role TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (projection_id, memory_id),
              FOREIGN KEY (projection_id) REFERENCES memory_projections(projection_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_memory_projection_sources_memory
              ON memory_projection_sources(memory_id, projection_id);
            """
        )
        await self.connection.commit()

    async def enqueue_job(self, spec: JobSpec) -> MemoryEvolutionJob:
        now = datetime.now(timezone.utc)
        job_id = spec.job_id or uuid.uuid4().hex
        await self._execute(
            """INSERT INTO memory_evolution_jobs
            (job_id,scope_key,bucket_key,state,attempt_count,source_ids_json,not_before,
             idempotency_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(idempotency_key) DO NOTHING""",
            (job_id, spec.scope_key, spec.bucket_key, JobState.PENDING.value, 0,
             _json_ids(spec.source_ids), _dt(spec.not_before), spec.idempotency_key,
             _dt(now), _dt(now)),
        )
        await self._commit()
        row = await self._fetch_one("SELECT * FROM memory_evolution_jobs WHERE idempotency_key=?", (spec.idempotency_key,))
        return _job(row)

    async def get_job(self, job_id: str) -> MemoryEvolutionJob | None:
        return _job(await self._fetch_one("SELECT * FROM memory_evolution_jobs WHERE job_id=?", (job_id,)))

    async def pending_count(self) -> int:
        return int(await self._fetch_scalar("SELECT COUNT(*) FROM memory_evolution_jobs WHERE state IN (?,?)", (JobState.PENDING.value, JobState.RETRY_WAIT.value)) or 0)

    async def load_sources(
        self,
        memory_ids: Iterable[int],
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """从 canonical documents 读取有限 source 快照，不修改证据平面。"""

        ids = tuple(dict.fromkeys(int(memory_id) for memory_id in memory_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = await self._fetch_all(
            "SELECT id, text, metadata, created_at, updated_at "
            f"FROM documents WHERE id IN ({placeholders})",
            ids,
        )
        sources_by_id: dict[int, MemorySourceRef] = {}
        for row in rows:
            metadata = _metadata_dict(row.get("metadata"))
            revision_token = str(row.get("updated_at") or row.get("created_at") or "").strip()
            occurred_raw = metadata.get("occurred_at") or row.get("created_at") or row.get("updated_at")
            if not revision_token or not occurred_raw:
                continue
            try:
                occurred_at = (
                    occurred_raw
                    if isinstance(occurred_raw, datetime)
                    else datetime.fromisoformat(str(occurred_raw))
                )
            except (TypeError, ValueError):
                continue
            privacy_level = str(metadata.get("privacy_level", "shared"))
            if privacy_level not in {"public", "shared", "confidential"}:
                privacy_level = "shared"
            scope_key = str(
                metadata.get("scope_key")
                or metadata.get("session_id")
                or metadata.get("persona_id")
                or "private:default"
            )
            content = str(row.get("text") or "")[:max(1, max_content_chars)]
            source = MemorySourceRef(
                memory_id=int(row["id"]),
                revision_token=revision_token,
                scope_key=scope_key,
                privacy_level=privacy_level,
                occurred_at=occurred_at,
                content=content,
            )
            sources_by_id[source.memory_id] = source
        return [sources_by_id[memory_id] for memory_id in ids if memory_id in sources_by_id]

    async def claim_job(self, now: datetime, lease_seconds: int, worker_token: str | None = None) -> JobClaim | None:
        token = worker_token or uuid.uuid4().hex
        lease = now.timestamp() + lease_seconds
        lease_dt = datetime.fromtimestamp(lease, tz=timezone.utc)
        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.connection.execute(
                "SELECT * FROM memory_evolution_jobs WHERE state IN (?,?) AND not_before<=? ORDER BY created_at LIMIT 1",
                (JobState.PENDING.value, JobState.RETRY_WAIT.value, _dt(now)),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.connection.rollback()
                return None
            await self.connection.execute(
                "UPDATE memory_evolution_jobs SET state=?, lease_until=?, worker_token=?, attempt_count=attempt_count+1, updated_at=? WHERE job_id=?",
                (JobState.PROCESSING.value, _dt(lease_dt), token, _dt(now), row["job_id"]),
            )
            await self.connection.commit()
            return JobClaim(row["job_id"], token, row["scope_key"], row["bucket_key"], tuple(_loads_ids(row["source_ids_json"])), int(row["attempt_count"] or 0) + 1, lease_dt)
        except BaseException:
            await self.connection.rollback()
            raise

    async def renew_lease(self, job_id: str, worker_token: str, lease_until: datetime) -> bool:
        cur = await self._execute("UPDATE memory_evolution_jobs SET lease_until=?,updated_at=? WHERE job_id=? AND state=? AND worker_token=?", (_dt(lease_until), _dt(datetime.now(timezone.utc)), job_id, JobState.PROCESSING.value, worker_token))
        await self._commit()
        return cur.rowcount == 1

    async def complete_job(self, job_id: str, worker_token: str) -> bool:
        return await self._set_job_state(job_id, worker_token, JobState.COMPLETED)

    async def reject_job(self, job_id: str, worker_token: str, reason_code: str = "rejected") -> bool:
        return await self._set_job_state(job_id, worker_token, JobState.REJECTED, reason_code)

    async def dead_job(self, job_id: str, worker_token: str, reason_code: str) -> bool:
        """将超过重试上限的任务标记为 dead。"""

        return await self._set_job_state(job_id, worker_token, JobState.DEAD, reason_code)

    async def restore_pending(self, job_id: str, worker_token: str) -> bool:
        """取消或暂时中断时把 processing 任务恢复为 pending。"""

        return await self._set_job_state(job_id, worker_token, JobState.PENDING)

    async def retry_job(self, job_id: str, worker_token: str, retry: RetrySpec) -> bool:
        cur = await self._execute("UPDATE memory_evolution_jobs SET state=?,not_before=?,lease_until=NULL,worker_token=NULL,last_error_code=?,updated_at=? WHERE job_id=? AND state=? AND worker_token=?", (JobState.RETRY_WAIT.value, _dt(retry.not_before), retry.reason_code, _dt(datetime.now(timezone.utc)), job_id, JobState.PROCESSING.value, worker_token))
        await self._commit()
        return cur.rowcount == 1

    async def _set_job_state(self, job_id: str, worker_token: str, state: JobState, reason: str | None = None) -> bool:
        cur = await self._execute("UPDATE memory_evolution_jobs SET state=?,lease_until=NULL,worker_token=NULL,last_error_code=?,updated_at=? WHERE job_id=? AND state=? AND worker_token=?", (state.value, reason, _dt(datetime.now(timezone.utc)), job_id, JobState.PROCESSING.value, worker_token))
        await self._commit()
        return cur.rowcount == 1

    async def recover_expired_leases(self, now: datetime) -> int:
        cur = await self._execute("UPDATE memory_evolution_jobs SET state=?,lease_until=NULL,worker_token=NULL,updated_at=? WHERE state=? AND lease_until IS NOT NULL AND lease_until<?", (JobState.PENDING.value, _dt(now), JobState.PROCESSING.value, _dt(now)))
        await self._commit()
        return cur.rowcount

    async def apply_derived_plan(self, plan: DerivedApplyPlan) -> None:
        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            for rel in plan.relations:
                source_revision = rel.source_revision or plan.source_revisions.get(
                    rel.source_memory_id, ""
                )
                target_revision = rel.target_revision or plan.source_revisions.get(
                    rel.target_memory_id, ""
                )
                if not source_revision or not target_revision:
                    raise ValueError("relation source revisions must be non-empty")
                key = ":".join(
                    (
                        str(rel.source_memory_id),
                        source_revision,
                        str(rel.target_memory_id),
                        target_revision,
                        rel.relation_type.value,
                    )
                )
                await self.connection.execute(
                    """INSERT INTO memory_relations
                    (relation_id,relation_key,source_memory_id,target_memory_id,source_revision,target_revision,relation_type,state,confidence,scope_key,privacy_level,valid_from,valid_to,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(relation_key) DO UPDATE SET
                      state=excluded.state,
                      confidence=excluded.confidence,
                      scope_key=excluded.scope_key,
                      privacy_level=excluded.privacy_level,
                      valid_from=excluded.valid_from,
                      valid_to=excluded.valid_to,
                      updated_at=excluded.updated_at""",
                    (
                        rel.relation_id,
                        key,
                        rel.source_memory_id,
                        rel.target_memory_id,
                        source_revision,
                        target_revision,
                        rel.relation_type.value,
                        rel.state.value,
                        rel.confidence,
                        rel.scope_key,
                        rel.privacy_level,
                        _dt(rel.valid_from),
                        _dt(rel.valid_to),
                        now,
                        now,
                    ),
                )
            projection_id_map: dict[str, str] = {}
            for proj in plan.projections:
                projection_sources = tuple(
                    source
                    for source in plan.projection_sources
                    if source.projection_id == proj.projection_id
                )
                primary_sources = tuple(
                    source for source in projection_sources if source.role == "primary"
                )
                if len(primary_sources) != 1:
                    raise ValueError("projection must have exactly one primary source")
                if set(proj.source_memory_ids) != {
                    source.memory_id for source in projection_sources
                }:
                    raise ValueError("projection source mappings must match source ids")
                if proj.projection_type is ProjectionType.CONFLICT_SET:
                    roles = {source.role for source in projection_sources}
                    if not {"conflict_left", "conflict_right"} <= roles:
                        raise ValueError("conflict projection must include both sides")
                primary_source = primary_sources[0].memory_id
                source_key = ",".join(
                    f"{source.memory_id}@{source.revision_token}"
                    for source in sorted(
                        projection_sources,
                        key=lambda item: (item.memory_id, item.revision_token),
                    )
                )
                pkey = f"{proj.projection_type.value}:{proj.scope_key}:{source_key}"
                await self.connection.execute(
                    """INSERT INTO memory_projections
                    (projection_id,projection_key,projection_type,revision,state,summary,primary_source_memory_id,scope_key,privacy_level,confidence,valid_from,valid_to,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(projection_key) DO UPDATE SET
                      revision=memory_projections.revision+1,
                      state=excluded.state,
                      summary=excluded.summary,
                      primary_source_memory_id=excluded.primary_source_memory_id,
                      scope_key=excluded.scope_key,
                      privacy_level=excluded.privacy_level,
                      confidence=excluded.confidence,
                      valid_from=excluded.valid_from,
                      valid_to=excluded.valid_to,
                      updated_at=excluded.updated_at""",
                    (proj.projection_id, pkey, proj.projection_type.value, 1, proj.state.value, proj.summary, primary_source, proj.scope_key, proj.privacy_level, proj.confidence, _dt(proj.valid_from), _dt(proj.valid_to), now, now),
                )
                cursor = await self.connection.execute(
                    "SELECT projection_id FROM memory_projections WHERE projection_key=?",
                    (pkey,),
                )
                stored = await cursor.fetchone()
                if stored is None:
                    raise RuntimeError("projection upsert did not return a stored row")
                projection_id_map[proj.projection_id] = str(stored["projection_id"])
                await self.connection.execute(
                    "DELETE FROM memory_projection_sources WHERE projection_id=?",
                    (str(stored["projection_id"]),),
                )
            for src in plan.projection_sources:
                stored_projection_id = projection_id_map.get(src.projection_id)
                if stored_projection_id is None:
                    raise ValueError("projection source references an unknown projection")
                await self.connection.execute(
                    "INSERT INTO memory_projection_sources"
                    "(projection_id,memory_id,revision_token,source_role,ordinal) "
                    "VALUES (?,?,?,?,?)",
                    (
                        stored_projection_id,
                        src.memory_id,
                        src.revision_token,
                        src.role,
                        src.ordinal,
                    ),
                )
            await self.connection.commit()
        except BaseException:
            await self.connection.rollback()
            raise

    async def active_relations_for_seeds(self, seed_ids: Iterable[int], scope_key: str | None = None, limit: int = 100) -> list[RelationView]:
        ids = tuple(seed_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        params: list = [*ids, *ids]
        clause = (
            f"(source_memory_id IN ({placeholders}) "
            f"OR target_memory_id IN ({placeholders})) AND state=?"
        )
        params.append(DerivedState.ACTIVE.value)
        if scope_key:
            clause += " AND scope_key=?"; params.append(scope_key)
        rows = await self._fetch_all(f"SELECT * FROM memory_relations WHERE {clause} ORDER BY confidence DESC LIMIT ?", (*params, limit))
        return [_relation(r) for r in rows]

    async def active_projections_for_seeds(self, seed_ids: Iterable[int], scope_key: str | None = None, limit: int = 100) -> list[ProjectionView]:
        bundles = await self.active_projection_bundles_for_seeds(
            seed_ids,
            scope_key=scope_key,
            limit=limit,
        )
        return [bundle.projection for bundle in bundles]

    async def active_projection_bundles_for_seeds(
        self,
        seed_ids: Iterable[int],
        *,
        scope_key: str | None = None,
        limit: int = 100,
    ) -> list[ProjectionBundle]:
        """读取命中 seed 的 active projection 及其 source mapping。"""

        ids = tuple(dict.fromkeys(int(seed_id) for seed_id in seed_ids))
        if not ids or limit <= 0:
            return []
        placeholders = ",".join("?" for _ in ids)
        params: list[object] = [*ids, DerivedState.ACTIVE.value]
        clause = f"source.memory_id IN ({placeholders}) AND projection.state=?"
        if scope_key is not None:
            clause += " AND projection.scope_key=?"
            params.append(scope_key)
        rows = await self._fetch_all(
            "SELECT DISTINCT projection.* FROM memory_projections AS projection "
            "JOIN memory_projection_sources AS source "
            "ON source.projection_id=projection.projection_id "
            f"WHERE {clause} "
            "ORDER BY projection.confidence DESC, projection.projection_id ASC LIMIT ?",
            (*params, limit),
        )
        if not rows:
            return []

        projection_ids = tuple(str(row["projection_id"]) for row in rows)
        projection_placeholders = ",".join("?" for _ in projection_ids)
        source_rows = await self._fetch_all(
            "SELECT projection_id,memory_id,revision_token,source_role,ordinal "
            "FROM memory_projection_sources "
            f"WHERE projection_id IN ({projection_placeholders}) "
            "ORDER BY projection_id ASC, ordinal ASC, memory_id ASC",
            projection_ids,
        )
        sources_by_projection: dict[str, list[ProjectionSourceView]] = {
            projection_id: [] for projection_id in projection_ids
        }
        for source_row in source_rows:
            source = ProjectionSourceView(
                str(source_row["projection_id"]),
                int(source_row["memory_id"]),
                str(source_row["revision_token"]),
                str(source_row["source_role"]),
                int(source_row["ordinal"]),
            )
            sources_by_projection[source.projection_id].append(source)

        bundles: list[ProjectionBundle] = []
        for row in rows:
            projection_id = str(row["projection_id"])
            sources = tuple(sources_by_projection.get(projection_id, ()))
            if not sources:
                continue
            projection = _projection(
                row,
                tuple(source.memory_id for source in sources),
            )
            bundles.append(ProjectionBundle(projection, sources))
        return bundles

    async def invalidate_for_source_revision(self, memory_id: int, revision_token: str) -> int:
        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            relation_cursor = await self.connection.execute(
                "UPDATE memory_relations SET state=?,updated_at=? "
                "WHERE state!=? AND ((source_memory_id=? AND source_revision!=?) "
                "OR (target_memory_id=? AND target_revision!=?))",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    DerivedState.INVALIDATED.value,
                    memory_id,
                    revision_token,
                    memory_id,
                    revision_token,
                ),
            )
            projection_cursor = await self.connection.execute(
                "UPDATE memory_projections SET state=?,updated_at=? "
                "WHERE state!=? AND projection_id IN ("
                "SELECT projection_id FROM memory_projection_sources "
                "WHERE memory_id=? AND revision_token!=?"
                ")",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    DerivedState.INVALIDATED.value,
                    memory_id,
                    revision_token,
                ),
            )
            await self.connection.commit()
            return relation_cursor.rowcount + projection_cursor.rowcount
        except BaseException:
            await self.connection.rollback()
            raise


def _json_ids(ids: Iterable[int]) -> str:
    return json.dumps([int(x) for x in ids], separators=(",", ":"))


def _loads_ids(value: str) -> list[int]:
    try: return [int(x) for x in json.loads(value or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError): return []


def _metadata_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _job(row) -> MemoryEvolutionJob | None:
    if not row: return None
    return MemoryEvolutionJob(row["job_id"], row["scope_key"], row["bucket_key"], JobState(row["state"]), int(row["attempt_count"]), _parse(row["not_before"]), _parse(row["lease_until"]), row["idempotency_key"], _parse(row["created_at"]), _parse(row["updated_at"]))


def _relation(row) -> RelationView:
    return RelationView(row["relation_id"], int(row["source_memory_id"]), int(row["target_memory_id"]), RelationType(row["relation_type"]), float(row["confidence"]), row["scope_key"], row["privacy_level"], DerivedState(row["state"]), row["source_revision"], row["target_revision"], _parse(row["valid_from"]), _parse(row["valid_to"]))


def _projection(row, source_ids: tuple[int, ...]) -> ProjectionView:
    return ProjectionView(row["projection_id"], ProjectionType(row["projection_type"]), row["summary"], source_ids, row["scope_key"], row["privacy_level"], float(row["confidence"]), DerivedState(row["state"]), _parse(row["valid_from"]), _parse(row["valid_to"]))


__all__ = ["MemoryEvolutionStore"]
