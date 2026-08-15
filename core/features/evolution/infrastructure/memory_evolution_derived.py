"""Memory Evolution 派生 relation/projection 的校验、读取和清理。"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import Iterable

from ..domain.models import (
    DerivedApplyPlan,
    DerivedState,
    ProjectionBundle,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationView,
)
from .memory_evolution_derived_helpers import (
    _PRIVACY_ORDER,
    _dt,
    _metadata_dict,
    _parse,
    _privacy_allowed,
    _projection,
    _relation,
)


def _serialized_write(method):
    """串行化共享 SQLite 连接上的写操作，并在取消时释放锁。"""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        """将派生事务交给 Store 的局部串行与全局重试边界。"""

        return await self._run_serialized_write(lambda: method(self, *args, **kwargs))

    return wrapper


class MemoryEvolutionDerivedMixin:
    """提供 relation/projection 的 canonical 校验、读取和维护操作。"""

    async def _validate_plan_sources(self, plan: DerivedApplyPlan) -> None:
        """在写入派生对象前核对 canonical source 的存在、revision 和边界。

        Evolution Store 在测试中可以单独使用，因此当 ``documents`` 表尚未
        创建时保留原有的纯派生存储行为；正式运行时该表由 SchemaManager
        先创建，所有 relation/projection 写入都会经过这里的 canonical 校验。
        """

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        table_exists = await self._fetch_scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
        )
        if not table_exists:
            return

        expected_revisions: dict[int, str] = dict(plan.source_revisions)
        expected_scopes: dict[int, set[str]] = {}
        expected_privacy: dict[int, set[str]] = {}

        def add_expectation(
            memory_id: int,
            revision: str,
            scope_key: str | None = None,
            privacy_level: str | None = None,
        ) -> None:
            """合并单个派生引用对 canonical source 的边界要求。"""

            if revision:
                previous = expected_revisions.get(memory_id)
                if previous and previous != revision:
                    raise ValueError("source_revision_mismatch")
                expected_revisions[memory_id] = revision
            if scope_key:
                expected_scopes.setdefault(memory_id, set()).add(scope_key)
            if privacy_level:
                expected_privacy.setdefault(memory_id, set()).add(privacy_level)

        for relation in plan.relations:
            source_revision = relation.source_revision or plan.source_revisions.get(
                relation.source_memory_id, ""
            )
            target_revision = relation.target_revision or plan.source_revisions.get(
                relation.target_memory_id, ""
            )
            add_expectation(
                relation.source_memory_id,
                source_revision,
                relation.scope_key,
                relation.privacy_level,
            )
            add_expectation(
                relation.target_memory_id,
                target_revision,
                relation.scope_key,
                relation.privacy_level,
            )
        for source in plan.projection_sources:
            projection = next(
                (
                    item
                    for item in plan.projections
                    if item.projection_id == source.projection_id
                ),
                None,
            )
            add_expectation(
                source.memory_id,
                source.revision_token,
                projection.scope_key if projection else None,
                projection.privacy_level if projection else None,
            )

        if not expected_revisions:
            return
        ids = tuple(expected_revisions)
        placeholders = ",".join("?" for _ in ids)
        rows = await self._fetch_all(
            "SELECT id, metadata, created_at, updated_at FROM documents "
            f"WHERE id IN ({placeholders})",
            ids,
        )
        rows_by_id = {int(row["id"]): row for row in rows}
        for memory_id, expected_revision in expected_revisions.items():
            row = rows_by_id.get(memory_id)
            if row is None:
                raise ValueError("source_memory_not_found")
            actual_revision = str(
                row.get("updated_at") or row.get("created_at") or ""
            ).strip()
            if expected_revision and actual_revision != expected_revision:
                raise ValueError("source_revision_mismatch")
            metadata = _metadata_dict(row.get("metadata"))
            actual_scope = str(
                metadata.get("scope_key")
                or metadata.get("session_id")
                or metadata.get("persona_id")
                or "private:default"
            )
            expected_scope_values = expected_scopes.get(memory_id, set())
            if expected_scope_values and actual_scope not in expected_scope_values:
                raise ValueError("source_scope_mismatch")
            actual_privacy = str(metadata.get("privacy_level", "shared"))
            if actual_privacy not in {"public", "shared", "confidential"}:
                actual_privacy = "shared"
            for requested_privacy in expected_privacy.get(memory_id, set()):
                if not _privacy_allowed(actual_privacy, requested_privacy):
                    raise ValueError("source_privacy_mismatch")

    async def active_relations_for_seeds(
        self, seed_ids: Iterable[int], scope_key: str | None = None, limit: int = 100
    ) -> list[RelationView]:
        """按 seed、scope 和置信度读取 active relation。"""

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
            clause += " AND scope_key=?"
            params.append(scope_key)
        rows = await self._fetch_all(
            f"SELECT * FROM memory_relations WHERE {clause} ORDER BY confidence DESC LIMIT ?",
            (*params, limit),
        )
        return [_relation(r) for r in rows]

    async def active_projections_for_seeds(
        self, seed_ids: Iterable[int], scope_key: str | None = None, limit: int = 100
    ) -> list[ProjectionView]:
        """按 seed 和 scope 读取 active projection 视图。"""

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

        ids = tuple(
            dict.fromkeys(
                seed_id
                for seed_id in seed_ids
                if isinstance(seed_id, int)
                and not isinstance(seed_id, bool)
                and seed_id >= 0
            )
        )
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
            ",occurred_at,valid_from,valid_to "
            "FROM memory_projection_sources "
            f"WHERE projection_id IN ({projection_placeholders}) "
            "ORDER BY projection_id ASC, ordinal ASC, memory_id ASC",
            projection_ids,
        )
        sources_by_projection: dict[str, list[ProjectionSourceView]] = {
            projection_id: [] for projection_id in projection_ids
        }
        invalid_projection_ids: set[str] = set()
        for source_row in source_rows:
            projection_id = str(source_row["projection_id"])
            try:
                source = ProjectionSourceView(
                    projection_id,
                    int(source_row["memory_id"]),
                    str(source_row["revision_token"]),
                    str(source_row["source_role"]),
                    int(source_row["ordinal"]),
                    _parse(source_row["occurred_at"]),
                    _parse(source_row["valid_from"]),
                    _parse(source_row["valid_to"]),
                )
            except (TypeError, ValueError, OverflowError):
                invalid_projection_ids.add(projection_id)
                continue
            sources_by_projection[source.projection_id].append(source)

        bundles: list[ProjectionBundle] = []
        for row in rows:
            projection_id = str(row["projection_id"])
            if projection_id in invalid_projection_ids:
                continue
            sources = tuple(sources_by_projection.get(projection_id, ()))
            if not sources:
                continue
            projection = _projection(
                row,
                tuple(source.memory_id for source in sources),
            )
            bundles.append(ProjectionBundle(projection, sources))
        return bundles

    @_serialized_write
    async def invalidate_for_source_revision(
        self, memory_id: int, revision_token: str
    ) -> int:
        """失效引用指定 canonical source 旧 revision 的派生对象。"""

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            relation_cursor = await self.connection.execute(
                "UPDATE memory_relations SET state=?,invalid_at=?,updated_at=? "
                "WHERE state!=? AND ((source_memory_id=? AND source_revision!=?) "
                "OR (target_memory_id=? AND target_revision!=?))",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    now,
                    DerivedState.INVALIDATED.value,
                    memory_id,
                    revision_token,
                    memory_id,
                    revision_token,
                ),
            )
            projection_rows = await self._fetch_all(
                "SELECT projection_id,primary_source_memory_id,projection_type "
                "FROM memory_projections WHERE state!=? AND projection_id IN ("
                "SELECT projection_id FROM memory_projection_sources "
                "WHERE memory_id=? AND revision_token!=? )",
                (DerivedState.INVALIDATED.value, memory_id, revision_token),
            )
            await self.connection.execute(
                "DELETE FROM memory_projection_sources "
                "WHERE memory_id=? AND revision_token!=?",
                (memory_id, revision_token),
            )
            invalidated_projection_ids: set[str] = set()
            for row in projection_rows:
                projection_id = str(row["projection_id"])
                remaining = await self._fetch_scalar(
                    "SELECT COUNT(*) FROM memory_projection_sources WHERE projection_id=?",
                    (projection_id,),
                )
                primary_exists = await self._fetch_scalar(
                    "SELECT 1 FROM memory_projection_sources WHERE projection_id=? AND memory_id=?",
                    (projection_id, int(row["primary_source_memory_id"])),
                )
                role_rows = await self._fetch_all(
                    "SELECT source_role FROM memory_projection_sources WHERE projection_id=?",
                    (projection_id,),
                )
                roles = {str(item["source_role"]) for item in role_rows}
                if (
                    not remaining
                    or not primary_exists
                    or str(row["projection_type"])
                    == ProjectionType.SEMANTIC_SUMMARY.value
                    or (
                        str(row["projection_type"]) == ProjectionType.CONFLICT_SET.value
                        and not {"conflict_left", "conflict_right"} <= roles
                    )
                ):
                    invalidated_projection_ids.add(projection_id)
            projection_count = 0
            if invalidated_projection_ids:
                placeholders = ",".join("?" for _ in invalidated_projection_ids)
                projection_cursor = await self.connection.execute(
                    "UPDATE memory_projections SET state=?,invalid_at=?,updated_at=? "
                    f"WHERE projection_id IN ({placeholders})",
                    (
                        DerivedState.INVALIDATED.value,
                        now,
                        now,
                        *invalidated_projection_ids,
                    ),
                )
                projection_count = int(projection_cursor.rowcount or 0)
            await self.connection.commit()
            return int(relation_cursor.rowcount or 0) + projection_count
        except BaseException:
            await self.connection.rollback()
            raise

    @_serialized_write
    async def invalidate_for_deleted_source(self, memory_id: int) -> int:
        """在 canonical 删除提交后让引用该 source 的派生对象立即不可见。

        relation 直接标记为 invalidated。Projection 先移除已删除 source 的
        mapping；普通类型仅在失去 primary 或全部 mapping 时整体失效。
        ``semantic_summary`` 合成自全部来源，任一 mapping 删除都使整条摘要失效。
        """

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            relation_cursor = await self.connection.execute(
                "UPDATE memory_relations SET state=?,invalid_at=?,updated_at=? "
                "WHERE state!=? AND (source_memory_id=? OR target_memory_id=?)",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    now,
                    DerivedState.INVALIDATED.value,
                    memory_id,
                    memory_id,
                ),
            )
            projection_rows = await self._fetch_all(
                "SELECT projection_id,primary_source_memory_id,projection_type "
                "FROM memory_projections WHERE state!=? AND projection_id IN ("
                "SELECT projection_id FROM memory_projection_sources WHERE memory_id=?"
                ")",
                (DerivedState.INVALIDATED.value, memory_id),
            )
            await self.connection.execute(
                "DELETE FROM memory_projection_sources WHERE memory_id=?",
                (memory_id,),
            )
            invalidated_projection_ids = {
                str(row["projection_id"])
                for row in projection_rows
                if int(row["primary_source_memory_id"]) == memory_id
                or str(row["projection_type"]) == ProjectionType.SEMANTIC_SUMMARY.value
            }
            for row in projection_rows:
                projection_id = str(row["projection_id"])
                if projection_id in invalidated_projection_ids:
                    continue
                mapping_count = await self._fetch_scalar(
                    "SELECT COUNT(*) FROM memory_projection_sources WHERE projection_id=?",
                    (projection_id,),
                )
                if not mapping_count:
                    invalidated_projection_ids.add(projection_id)
            projection_count = 0
            if invalidated_projection_ids:
                placeholders = ",".join("?" for _ in invalidated_projection_ids)
                cursor = await self.connection.execute(
                    "UPDATE memory_projections SET state=?,invalid_at=?,updated_at=? "
                    f"WHERE projection_id IN ({placeholders})",
                    (
                        DerivedState.INVALIDATED.value,
                        now,
                        now,
                        *invalidated_projection_ids,
                    ),
                )
                projection_count = int(cursor.rowcount or 0)
            await self.connection.commit()
            return int(relation_cursor.rowcount or 0) + projection_count
        except BaseException:
            await self.connection.rollback()
            raise

    @_serialized_write
    async def rollback_job(self, job_id: str) -> int:
        """回滚指定 evolution job 产生且仍归属于该 job 的派生对象。"""

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            relation_cursor = await self.connection.execute(
                "UPDATE memory_relations SET state=?,invalid_at=?,updated_at=? "
                "WHERE origin_job_id=? AND state!=?",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    now,
                    job_id,
                    DerivedState.INVALIDATED.value,
                ),
            )
            projection_rows = await self._fetch_all(
                "SELECT projection_id FROM memory_projections "
                "WHERE origin_job_id=? AND state!=?",
                (job_id, DerivedState.INVALIDATED.value),
            )
            projection_ids = tuple(str(row["projection_id"]) for row in projection_rows)
            projection_count = 0
            if projection_ids:
                placeholders = ",".join("?" for _ in projection_ids)
                await self.connection.execute(
                    "DELETE FROM memory_projection_sources "
                    f"WHERE projection_id IN ({placeholders})",
                    projection_ids,
                )
                cursor = await self.connection.execute(
                    "UPDATE memory_projections SET state=?,invalid_at=?,updated_at=? "
                    f"WHERE projection_id IN ({placeholders})",
                    (DerivedState.INVALIDATED.value, now, now, *projection_ids),
                )
                projection_count = int(cursor.rowcount or 0)
            await self.connection.commit()
            return int(relation_cursor.rowcount or 0) + projection_count
        except BaseException:
            await self.connection.rollback()
            raise

    @_serialized_write
    async def invalidate_all_derived(self) -> dict[str, int]:
        """为全量重建失效旧 relation/projection，并清空旧 source mapping。"""

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            relation_cursor = await self.connection.execute(
                "UPDATE memory_relations SET state=?,invalid_at=?,updated_at=? WHERE state!=?",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    now,
                    DerivedState.INVALIDATED.value,
                ),
            )
            projection_cursor = await self.connection.execute(
                "UPDATE memory_projections SET state=?,invalid_at=?,updated_at=? WHERE state!=?",
                (
                    DerivedState.INVALIDATED.value,
                    now,
                    now,
                    DerivedState.INVALIDATED.value,
                ),
            )
            mapping_cursor = await self.connection.execute(
                "DELETE FROM memory_projection_sources"
            )
            await self.connection.commit()
            return {
                "relations_invalidated": int(relation_cursor.rowcount or 0),
                "projections_invalidated": int(projection_cursor.rowcount or 0),
                "projection_sources_removed": int(mapping_cursor.rowcount or 0),
            }
        except BaseException:
            await self.connection.rollback()
            raise

    @_serialized_write
    async def cleanup_orphaned_derived(self) -> int:
        """标记缺失或陈旧 canonical source 的 relation/projection，并清理 mapping。"""

        if not self.connection:
            raise RuntimeError("MemoryEvolutionStore 未初始化 -- 先调用 initialize()")
        table_exists = await self._fetch_scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
        )
        if not table_exists:
            return 0
        now = _dt(datetime.now(timezone.utc))
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            canonical_rows = await self._fetch_all(
                "SELECT id,metadata,created_at,updated_at FROM documents"
            )
            canonical = {int(row["id"]): row for row in canonical_rows}

            def source_valid(
                memory_id: int,
                expected_revision: str,
                scope_key: str,
                privacy_level: str,
            ) -> bool:
                """判断 source 当前是否仍满足派生对象记录的读取契约。"""

                row = canonical.get(memory_id)
                if row is None:
                    return False
                actual_revision = str(
                    row.get("updated_at") or row.get("created_at") or ""
                ).strip()
                if expected_revision and actual_revision != expected_revision:
                    return False
                metadata = _metadata_dict(row.get("metadata"))
                actual_scope = str(
                    metadata.get("scope_key")
                    or metadata.get("session_id")
                    or metadata.get("persona_id")
                    or "private:default"
                )
                if actual_scope != scope_key:
                    return False
                actual_privacy = str(metadata.get("privacy_level", "shared"))
                if actual_privacy not in _PRIVACY_ORDER:
                    actual_privacy = "shared"
                return _privacy_allowed(actual_privacy, privacy_level)

            relation_rows = await self._fetch_all(
                "SELECT relation_id,source_memory_id,target_memory_id,"
                "source_revision,target_revision,scope_key,privacy_level,state "
                "FROM memory_relations WHERE state!=?",
                (DerivedState.INVALIDATED.value,),
            )
            invalid_relation_ids = [
                str(row["relation_id"])
                for row in relation_rows
                if not source_valid(
                    int(row["source_memory_id"]),
                    str(row["source_revision"]),
                    str(row["scope_key"]),
                    str(row["privacy_level"]),
                )
                or not source_valid(
                    int(row["target_memory_id"]),
                    str(row["target_revision"]),
                    str(row["scope_key"]),
                    str(row["privacy_level"]),
                )
            ]
            relation_count = 0
            if invalid_relation_ids:
                placeholders = ",".join("?" for _ in invalid_relation_ids)
                cursor = await self.connection.execute(
                    "UPDATE memory_relations SET state=?,invalid_at=?,updated_at=? "
                    f"WHERE relation_id IN ({placeholders})",
                    (
                        DerivedState.INVALIDATED.value,
                        now,
                        now,
                        *invalid_relation_ids,
                    ),
                )
                relation_count = int(cursor.rowcount or 0)

            projection_rows = await self._fetch_all(
                "SELECT projection_id,primary_source_memory_id,projection_type,"
                "scope_key,privacy_level,state "
                "FROM memory_projections WHERE state!=?",
                (DerivedState.INVALIDATED.value,),
            )
            projection_invalid: set[str] = set()
            mapping_rows = await self._fetch_all(
                "SELECT projection_id,memory_id,revision_token FROM memory_projection_sources"
            )
            mappings_by_projection: dict[str, list[dict]] = {}
            for row in mapping_rows:
                mappings_by_projection.setdefault(str(row["projection_id"]), []).append(
                    row
                )
            removed_mapping_count = 0
            for projection in projection_rows:
                projection_id = str(projection["projection_id"])
                mappings = mappings_by_projection.get(projection_id, [])
                mapping_removed = False
                for mapping in mappings:
                    if not source_valid(
                        int(mapping["memory_id"]),
                        str(mapping["revision_token"]),
                        str(projection["scope_key"]),
                        str(projection["privacy_level"]),
                    ):
                        await self.connection.execute(
                            "DELETE FROM memory_projection_sources "
                            "WHERE projection_id=? AND memory_id=?",
                            (projection_id, int(mapping["memory_id"])),
                        )
                        removed_mapping_count += 1
                        mapping_removed = True
                remaining = await self._fetch_scalar(
                    "SELECT COUNT(*) FROM memory_projection_sources WHERE projection_id=?",
                    (projection_id,),
                )
                primary_exists = await self._fetch_scalar(
                    "SELECT 1 FROM memory_projection_sources "
                    "WHERE projection_id=? AND memory_id=?",
                    (projection_id, int(projection["primary_source_memory_id"])),
                )
                if (
                    not remaining
                    or not primary_exists
                    or (
                        mapping_removed
                        and str(projection["projection_type"])
                        == ProjectionType.SEMANTIC_SUMMARY.value
                    )
                ):
                    projection_invalid.add(projection_id)
            projection_count = 0
            if projection_invalid:
                placeholders = ",".join("?" for _ in projection_invalid)
                cursor = await self.connection.execute(
                    "UPDATE memory_projections SET state=?,invalid_at=?,updated_at=? "
                    f"WHERE projection_id IN ({placeholders})",
                    (DerivedState.INVALIDATED.value, now, now, *projection_invalid),
                )
                projection_count = int(cursor.rowcount or 0)
            await self.connection.commit()
            return relation_count + projection_count + removed_mapping_count
        except BaseException:
            await self.connection.rollback()
            raise


__all__ = ["MemoryEvolutionDerivedMixin"]
