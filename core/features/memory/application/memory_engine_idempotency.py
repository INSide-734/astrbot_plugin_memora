"""MemoryEngine 的 canonical 幂等预检与文档写入恢复协调。"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from astrbot.api import logger

from ....shared.summary_source_fence import SummarySourceFence
from ...observability.application.memory_write_timing import (
    measure_memory_write_stage,
    observe_memory_write,
)
from ...retrieval.bm25_retriever import BM25Retriever
from ...retrieval.vector_retriever import VectorRetriever, fit_embedding_text
from ..infrastructure.canonical_idempotency import (
    find_canonical_memory_id_by_idempotency_key,
    find_canonical_memory_id_by_merged_idempotency_key,
    normalize_canonical_idempotency_key,
)
from ..infrastructure.write_op_repair import (
    CONTENT_PREVIEW_LIMIT,
    ledger_content_digest,
)
from .memory_engine_atom_support import reinforce_existing_atoms


class DocumentWriteOutcome(NamedTuple):
    """文档阶段结果：已提交的 canonical ID 与派生索引状态。"""

    doc_id: int
    owner_reused: bool
    index_degraded: bool


class _StagedIndexPorts(NamedTuple):
    """真实检索器暴露的 canonical/FAISS/FTS 分段写入端口。"""

    document_storage: Any
    embedding_provider: Any
    embedding_storage: Any
    fts_add: Callable[..., Any]


class MemoryEngineIdempotencyMixin:
    """承载 canonical 幂等键预检、文档写入及竞态恢复。"""

    @observe_memory_write
    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        *,
        source_fence: SummarySourceFence | None = None,
    ) -> int:
        """规范化幂等键后提交 canonical，并校验可选总结来源 fence。"""

        normalized_metadata = dict(metadata or {})
        idempotency_key = self._canonical_idempotency_key(normalized_metadata)
        if idempotency_key:
            normalized_metadata["idempotency_key"] = idempotency_key
        if source_fence is not None:
            await self._validate_summary_source_fence(source_fence)
            normalized_metadata.update(
                {
                    "source_epoch": source_fence.session_epoch,
                    "source_digest": source_fence.source_digest,
                    "source_fence_generation": source_fence.worker_generation,
                    "source_fence": source_fence.opaque_token,
                    "summary_source_orphan": True,
                    "summary_source_pending": True,
                }
            )
            if source_fence.has_exact_scope:
                for field_name, value in (
                    ("scope_key", source_fence.scope_key),
                    ("privacy_level", source_fence.privacy_level),
                    ("resolver_revision", source_fence.resolver_revision),
                ):
                    if normalized_metadata.get(field_name) not in (None, value):
                        raise RuntimeError("summary_scope_mismatch")
                    normalized_metadata[field_name] = value
                normalized_metadata["source_provenance_complete"] = True
            else:
                for field_name in (
                    "scope_key",
                    "privacy_level",
                    "resolver_revision",
                ):
                    normalized_metadata.pop(field_name, None)
                normalized_metadata["source_provenance_complete"] = False

        if not idempotency_key:
            memory_id = await self._add_memory_unchecked(
                content,
                session_id=session_id,
                persona_id=persona_id,
                importance=importance,
                metadata=normalized_metadata or None,
                atoms=atoms,
                summary_source_staged=source_fence is not None,
            )
        else:
            lock = self._get_canonical_idempotency_lock()
            async with lock:
                existing_id = await self.find_memory_id_by_idempotency_key(
                    idempotency_key
                )
                if existing_id is not None:
                    memory_id = existing_id
                else:
                    memory_id = await self._add_memory_unchecked(
                        content,
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=importance,
                        metadata=normalized_metadata,
                        atoms=atoms,
                        summary_source_staged=source_fence is not None,
                    )
        if source_fence is None:
            return memory_id
        await self._accept_summary_source_write(memory_id, source_fence)
        return memory_id

    async def _accept_summary_source_write(
        self, memory_id: int, source_fence: SummarySourceFence
    ) -> None:
        """来源 fence 仍有效时接受 canonical，并统一收尾派生与账本。"""

        metadata = await self._load_summary_source_metadata(memory_id)
        if metadata is not None and self._is_summary_source_accepted(metadata):
            # 已接受 owner 的重试：只允许同一来源关系幂等复用，绝不改回 orphan。
            if not self._matches_accepted_source(metadata, source_fence):
                raise RuntimeError("summary_source_mismatch")
            return
        if not await self._summary_source_fence_is_active(source_fence):
            if await self._set_summary_source_orphan(
                memory_id, True, source_fence=source_fence
            ):
                # 只有确实由本次拒绝翻转了状态，才收口该行的账本终态；
                # 已被其他 claim 接受的行保持可召回，不能连带标记失败。
                await self._reject_summary_source_op(memory_id)
            raise RuntimeError("summary_source_fenced")
        if metadata is None:
            # 状态不可读时不推断为已接受，也不为无效来源留下可召回行。
            raise RuntimeError("summary_source_activation_failed")
        if not await self._activate_summary_source(memory_id, source_fence):
            # 激活 CAS 失败可能是并发接受先到：只认同一来源关系的已接受状态。
            current = await self._load_summary_source_metadata(memory_id)
            if (
                current is not None
                and self._is_summary_source_accepted(current)
                and self._matches_accepted_source(current, source_fence)
            ):
                return
            raise RuntimeError("summary_source_activation_failed")
        await self._finalize_summary_source_write(memory_id)

    async def _load_summary_source_metadata(
        self, memory_id: int
    ) -> dict[str, Any] | None:
        """读取 canonical metadata；缺失或不可解析时返回 ``None``。"""

        memory = await self.get_memory(memory_id)
        if not isinstance(memory, dict):
            return None
        metadata: Any = memory.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                return None
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _is_summary_source_accepted(metadata: dict[str, Any]) -> bool:
        """判断 canonical 是否已脱离暂存状态；缺省字段的旧行视为已接受。"""

        return not metadata.get("summary_source_orphan") and not metadata.get(
            "summary_source_pending"
        )

    async def is_memory_source_accepted(self, memory_id: int) -> bool:
        """判断 canonical 是否已可正常召回，供幂等 owner 复核使用。

        暂存（``summary_source_pending``）、已拒绝（``summary_source_orphan``）
        和行缺失都返回 ``False``：这些 owner 不能作为幂等成功结果推进调用方
        cursor，也不需要重新接受。
        """

        metadata = await self._load_summary_source_metadata(memory_id)
        if metadata is None:
            return False
        return self._is_summary_source_accepted(metadata)

    @staticmethod
    def _matches_accepted_source(
        metadata: dict[str, Any], source_fence: SummarySourceFence
    ) -> bool:
        """核对已接受 owner 记录的来源关系与本次幂等请求是否一致。"""

        for field, expected in (
            ("source_epoch", source_fence.session_epoch),
            ("source_digest", source_fence.source_digest),
            ("source_fence_generation", source_fence.worker_generation),
            ("source_fence", source_fence.opaque_token),
        ):
            recorded = metadata.get(field)
            if recorded is None or str(recorded) != str(expected):
                return False
        if not source_fence.has_exact_scope:
            return True
        for field, expected in (
            ("scope_key", source_fence.scope_key),
            ("privacy_level", source_fence.privacy_level),
            ("resolver_revision", source_fence.resolver_revision),
        ):
            recorded = metadata.get(field)
            if recorded is not None and str(recorded) != str(expected):
                return False
        return True

    async def _reject_summary_source_op(self, memory_id: int) -> None:
        """把本次未接受来源的 add 操作收口为可见的拒绝终态。"""

        journal = getattr(self, "_write_journal", None)
        finder = getattr(journal, "find_open_add_op", None)
        if not callable(finder):
            return
        try:
            op = await finder(int(memory_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        if not isinstance(op, dict):
            return
        await journal.advance_op(
            int(op["op_id"]),
            "source_rejected",
            status="failed",
            memory_id=int(memory_id),
            error="summary_source_fenced",
        )

    async def _finalize_summary_source_write(self, memory_id: int) -> None:
        """在来源 fence 仍有效后补运行 canonical 派生和观测钩子。"""

        source = await self.get_memory(memory_id)
        if source is None:
            await self._set_summary_source_orphan(memory_id, True)
            raise RuntimeError("summary_source_activation_failed")
        content = str(source.get("text") or source.get("content") or "")
        metadata = source.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        journal = getattr(self, "_write_journal", None)
        finalize = getattr(journal, "finalize_add_derivation", None)
        if callable(finalize):
            # 接受后按当前 canonical 补建图产物，并收口本次 add 的同一账本操作。
            # 图失败只进入修复队列，不改变已接受的 canonical，也不重复业务副作用。
            await finalize(memory_id)
        atoms = await self._load_finalized_source_atoms(memory_id)
        await reinforce_existing_atoms(
            getattr(self, "atom_lifecycle_manager", None),
            atoms,
        )
        self._create_tracked_task(
            self._retrieval.apply_interference(memory_id, content)
        )
        self._create_tracked_task(self._retrieval.extract_triggers(content, memory_id))
        sse = getattr(self, "sse", None)
        if sse is not None:
            self._create_tracked_task(
                sse.publish(
                    "memory_created", {"doc_id": memory_id, "content": content[:200]}
                )
            )
        self._record_add_memory_observability(
            doc_id=memory_id,
            content=content,
            metadata=metadata,
            atoms=atoms,
            duration_s=0.0,
        )
        await self._schedule_evolution_after_write(memory_id)
        self._schedule_domain_proposals_after_write(memory_id)

    async def _load_finalized_source_atoms(self, memory_id: int) -> list[Any]:
        """读取已接受来源的真实原子用于质量采样；失败只降级为空采样。"""

        atom_store = getattr(self, "atom_store", None)
        loader = getattr(atom_store, "get_by_parent", None)
        if not callable(loader) or not bool(getattr(self, "atom_enabled", True)):
            return []
        try:
            atoms = await loader(int(memory_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            return []
        return list(atoms) if isinstance(atoms, (list, tuple)) else []

    def set_summary_source_validator(
        self,
        validator: Callable[[SummarySourceFence], bool | Awaitable[bool]],
    ) -> None:
        """注入总结来源 fence 验证器，供组合根绑定 ConversationStore。"""

        self._summary_source_validator = validator

    async def _summary_source_fence_is_active(
        self, source_fence: SummarySourceFence
    ) -> bool:
        """调用持久化来源验证器，异常和非法结果均视为失效。"""

        validator = getattr(self, "_summary_source_validator", None)
        if not callable(validator):
            return False
        try:
            result = validator(source_fence)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return result is True

    async def _validate_summary_source_fence(
        self, source_fence: SummarySourceFence
    ) -> None:
        """在 canonical 外部副作用前执行 fail-closed 来源校验。"""

        if not isinstance(source_fence, SummarySourceFence):
            raise RuntimeError("summary_source_fenced")
        if not await self._summary_source_fence_is_active(source_fence):
            raise RuntimeError("summary_source_fenced")

    async def _set_summary_source_orphan(
        self,
        memory_id: int,
        orphan: bool,
        source_fence: SummarySourceFence | None = None,
    ) -> bool:
        """切换总结来源暂存标记，且不推进 canonical source revision。

        携带 ``source_fence`` 时要求该行仍处于暂存状态且记录的 claim token
        一致，避免旧 claim 的拒绝把已被其他 claim 接受的来源改回 orphan。
        """

        return await self._write_summary_source_state(
            memory_id,
            orphan=orphan,
            op_step=None,
            source_fence=source_fence,
            require_pending=source_fence is not None,
        )

    async def _activate_summary_source(
        self, memory_id: int, source_fence: SummarySourceFence
    ) -> bool:
        """接受总结来源，并在同一 canonical 事务内登记待派生收尾意图。"""

        return await self._write_summary_source_state(
            memory_id,
            orphan=False,
            op_step="derived_pending",
            source_fence=source_fence,
            require_pending=True,
        )

    async def _write_summary_source_state(
        self,
        memory_id: int,
        *,
        orphan: bool,
        op_step: str | None,
        source_fence: SummarySourceFence | None = None,
        require_pending: bool = False,
    ) -> bool:
        """在单个 canonical 事务内更新来源状态标记与可选 add 账本步骤。

        激活与 ``derived_pending`` 意图必须同事务提交：先清 pending 再尽力写账本
        会留下“可召回但无收尾记录”的窗口。两者都改 metadata/账本列，不推进
        ``documents.updated_at``，因此既不推进 revision，也不失效派生对象。
        携带 ``source_fence`` 时按记录的 claim token 和暂存状态做 CAS，
        事务已在别处开启时直接拒绝，避免回滚外层未提交写入。
        """

        connection = getattr(self, "db_connection", None)
        if connection is None:
            return False
        if getattr(connection, "in_transaction", False):
            # 不参与嵌套事务：调用方应在事务边界外重试，而不是被 rollback 丢弃。
            return False
        began = False
        try:
            await connection.execute("BEGIN IMMEDIATE")
            began = True
            cursor = await connection.execute(
                "SELECT metadata FROM documents WHERE id=?", (int(memory_id),)
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                await connection.rollback()
                return False
            raw_metadata = row[0]
            if isinstance(raw_metadata, str):
                try:
                    current = json.loads(raw_metadata)
                except (TypeError, json.JSONDecodeError):
                    await connection.rollback()
                    return False
            elif isinstance(raw_metadata, dict):
                current = dict(raw_metadata)
            else:
                await connection.rollback()
                return False
            if not isinstance(current, dict):
                await connection.rollback()
                return False
            if source_fence is not None:
                recorded_token = current.get("source_fence")
                if recorded_token is None or str(recorded_token) != str(
                    source_fence.opaque_token
                ):
                    await connection.rollback()
                    return False
            if require_pending and not current.get("summary_source_pending"):
                # 已接受/已拒绝的行不再接受本次状态切换。
                await connection.rollback()
                return False
            current["summary_source_orphan"] = bool(orphan)
            current["summary_source_pending"] = False
            updated = await connection.execute(
                "UPDATE documents SET metadata=? WHERE id=?",
                (json.dumps(current, ensure_ascii=False), int(memory_id)),
            )
            if updated.rowcount != 1:
                await connection.rollback()
                return False
            if op_step is not None:
                op_cursor = await connection.execute(
                    """
                    SELECT id FROM memory_write_ops
                    WHERE memory_id = ? AND op_type = 'add'
                      AND status IN ('pending', 'needs_repair')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(memory_id),),
                )
                op_row = await op_cursor.fetchone()
                await op_cursor.close()
                if op_row is not None:
                    await connection.execute(
                        "UPDATE memory_write_ops SET step = ?, updated_at = ? WHERE id = ?",
                        (op_step, time.time(), int(op_row[0])),
                    )
            await connection.commit()
            invalidate = getattr(
                getattr(self, "_retrieval", None), "invalidate_cache", None
            )
            if callable(invalidate):
                invalidate()
            return True
        except asyncio.CancelledError:
            if began:
                try:
                    await connection.rollback()
                except Exception:
                    pass
            raise
        except Exception:
            if began:
                try:
                    await connection.rollback()
                except Exception:
                    pass
            return False

    async def find_memory_id_by_idempotency_key(self, key: str) -> int | None:
        """从 v9 canonical 唯一映射查找幂等键 owner，不返回正文。"""

        if self.db_connection is None:
            return None
        return await find_canonical_memory_id_by_idempotency_key(
            self.db_connection,
            key,
        )

    async def find_memory_id_by_merged_idempotency_key(self, key: str) -> int | None:
        """从 canonical metadata 的 merged 幂等键查找 owner，不返回正文。

        合并只强化既有 canonical，不写 canonical 幂等映射；该窄入口供总结重试
        和启动恢复证明「owner 已更新但进程在 fence 后退出」。
        """

        if self.db_connection is None:
            return None
        return await find_canonical_memory_id_by_merged_idempotency_key(
            self.db_connection,
            key,
        )

    @staticmethod
    def _canonical_idempotency_key(metadata: dict[str, Any] | None) -> str:
        """读取非空幂等键；没有显式键的普通写入保持原行为。"""

        if not isinstance(metadata, dict):
            return ""
        return normalize_canonical_idempotency_key(metadata.get("idempotency_key"))

    def _get_canonical_idempotency_lock(self) -> asyncio.Lock:
        """惰性创建当前引擎的幂等写锁，串行化同进程重试。"""

        lock = getattr(self, "_canonical_idempotency_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._canonical_idempotency_lock = lock
        return lock

    def _staged_index_ports(self) -> _StagedIndexPorts | None:
        """返回真实检索器的 canonical/FAISS/FTS 分段写入端口。

        只有装配了真实 ``VectorRetriever``/``BM25Retriever`` 的混合检索器才暴露
        可分段的主机组件；测试替身与仅实现 ``add_memory`` 的适配器返回 ``None``，
        继续走单段写入，避免按猜想的端口协议调用它们。
        """

        vector_retriever = getattr(self.hybrid_retriever, "vector_retriever", None)
        bm25_retriever = getattr(self.hybrid_retriever, "bm25_retriever", None)
        if not isinstance(vector_retriever, VectorRetriever) or not isinstance(
            bm25_retriever, BM25Retriever
        ):
            return None
        faiss_db = getattr(vector_retriever, "faiss_db", None)
        document_storage = getattr(faiss_db, "document_storage", None)
        embedding_provider = getattr(faiss_db, "embedding_provider", None)
        embedding_storage = getattr(faiss_db, "embedding_storage", None)
        fts_add = getattr(bm25_retriever, "add_document", None)
        if not (
            callable(getattr(document_storage, "insert_document", None))
            and callable(getattr(embedding_provider, "get_embedding", None))
            and callable(getattr(embedding_storage, "insert", None))
            and callable(fts_add)
        ):
            return None
        return _StagedIndexPorts(
            document_storage=document_storage,
            embedding_provider=embedding_provider,
            embedding_storage=embedding_storage,
            fts_add=fts_add,
        )

    async def _insert_canonical_document(
        self,
        ports: _StagedIndexPorts,
        content: str,
        full_metadata: dict[str, Any],
        op_id: int | None,
    ) -> tuple[int, Any]:
        """先预写 ``doc_id`` 意图，再算向量并落 canonical 行，返回整数 ID 与向量。

        ``documents``（宿主文档存储的 SQLAlchemy 引擎）与 ``memory_write_ops``
        （本插件的 aiosqlite 连接）是同一个 ``memora.db`` 文件上的两个连接，无法
        共享事务：INSERT 提交与账本推进之间必然存在崩溃窗口。因此 INSERT 前先把
        该行的 ``doc_id`` 预写进账本（step ``document_intent``），让修复端按同一
        UUID 找回已提交的 canonical 行补索引，既不必复制宿主插入语义，也不会
        产生第二行 canonical。

        其余顺序与宿主 ``FaissVecDB.insert`` 一致（embedding → documents → 向量），
        区别只在把 documents 提交与向量写入拆开，好让整数 canonical ID 在派生
        索引失败前就能进入账本。embedding 输入沿用向量层的字符预算规则。
        """

        import uuid

        import numpy as np

        with measure_memory_write_stage("document_vector"):
            embedding_content = fit_embedding_text(content)
            if embedding_content != content:
                logger.warning(
                    "[MemoryEngine] 记忆内容过长，正文完整入库，仅 embedding "
                    f"输入压缩至 {len(embedding_content)} 字符"
                )
            vector = np.asarray(
                await ports.embedding_provider.get_embedding(embedding_content),
                dtype=np.float32,
            )
            # 预写意图紧贴 INSERT：这之后任何时刻崩溃，账本都能证明「哪一行属于
            # 这次 add」，而不必等整数 ID 回填。
            pending_doc_id = str(uuid.uuid4())
            await self._write_journal.advance_op(
                op_id,
                "document_intent",
                payload_patch={"pending_doc_id": pending_doc_id},
            )
            doc_id = await ports.document_storage.insert_document(
                pending_doc_id,
                content,
                full_metadata,
            )
        return int(doc_id), vector

    async def _write_document_stage(
        self,
        content: str,
        full_metadata: dict[str, Any],
        metadata: dict[str, Any] | None,
        op_id: int | None,
    ) -> DocumentWriteOutcome:
        """提交 canonical 行，并把 canonical 之后的索引失败降级为待修复。

        分段写入时 INSERT 前先在账本登记该行的 ``doc_id``（step
        ``document_intent``）：documents 的提交与账本推进跨连接、无法共享事务，
        预写意图让「已提交但未记账」的崩溃窗口仍可由修复端按 UUID 找回该行。
        canonical 提交后立即用整数 ID 与正文摘要登记 ``documents_committed``
        意图；FAISS/FTS 阶段失败只记 ``needs_repair``（原因码
        ``index_stage_degraded``）并返回该 ID，调用方不会把已提交的 canonical
        当成写失败重试。只有 canonical 提交本身失败才向上报错；缺少分段端口时
        保持既有单段 ``add_memory`` 语义（宿主在一次调用内同时提交 canonical
        与索引，没有可提前登记的 ``doc_id``）。
        """

        ports = self._staged_index_ports()
        vector: Any = None
        try:
            if ports is None:
                with measure_memory_write_stage("document_vector"):
                    doc_id = await self.hybrid_retriever.add_memory(
                        content,
                        full_metadata,
                    )
            else:
                doc_id, vector = await self._insert_canonical_document(
                    ports,
                    content,
                    full_metadata,
                    op_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            owner_id = await self._recover_idempotent_write_owner(op_id, metadata)
            if owner_id is not None:
                return DocumentWriteOutcome(owner_id, True, False)
            await self._write_journal.advance_op(
                op_id,
                "document_failed",
                status="failed",
                error="canonical_write_failed",
            )
            self._record_add_memory_failure("document")
            raise
        await self._write_journal.advance_op(
            op_id,
            "documents_committed",
            memory_id=doc_id,
            payload_patch={
                "memory_id": doc_id,
                "content_digest": ledger_content_digest(content),
                "content_preview": content[:CONTENT_PREVIEW_LIMIT],
                "indexes_pending": ports is not None,
            },
        )
        if ports is None:
            await self._write_journal.advance_op(
                op_id,
                "document_indexed",
                memory_id=doc_id,
            )
            return DocumentWriteOutcome(doc_id, False, False)
        degraded = await self._index_committed_document(
            ports,
            doc_id,
            content,
            full_metadata,
            vector,
        )
        await self._write_journal.advance_op(
            op_id,
            "index_stage_degraded" if degraded else "document_indexed",
            status="needs_repair" if degraded else "pending",
            memory_id=doc_id,
            error="index_stage_degraded" if degraded else None,
            payload_patch={"indexes_pending": degraded},
        )
        return DocumentWriteOutcome(doc_id, False, degraded)

    async def _index_committed_document(
        self,
        ports: _StagedIndexPorts,
        doc_id: int,
        content: str,
        full_metadata: dict[str, Any],
        vector: Any,
    ) -> bool:
        """为已提交的 canonical 补写 FAISS 与 FTS；失败只降级不报错。"""

        degraded = False
        try:
            with measure_memory_write_stage("document_vector"):
                await ports.embedding_storage.insert(vector, doc_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            degraded = True
            self._record_add_memory_failure("vector")
            logger.error(
                "[MemoryEngine] FAISS 写入降级 reason_code=index_stage_degraded"
            )
        try:
            with measure_memory_write_stage("fts"):
                await ports.fts_add(doc_id, content, full_metadata)
        except asyncio.CancelledError:
            raise
        except Exception:
            degraded = True
            self._record_add_memory_failure("fts")
            logger.error("[MemoryEngine] FTS 写入降级 reason_code=index_stage_degraded")
        return degraded

    async def _repair_document_indexes(self, memory_id: int) -> bool:
        """只按当前 canonical 重建指定 ID；删除旧向量/FTS 后补写，重放不重复。"""

        ports = self._staged_index_ports()
        if ports is None:
            return False
        source = await self.get_memory(memory_id)
        if source is None:
            return False
        content = str(source.get("text") or "")
        import numpy as np

        try:
            vector = np.asarray(
                await ports.embedding_provider.get_embedding(
                    fit_embedding_text(content)
                ),
                dtype=np.float32,
            )
            # 与正文 CAS/删除共用锁；embedding 等待期间来源变化时不覆盖新索引。
            async with self.hybrid_retriever.vector_retriever._vector_write_lock:
                current = await self.get_memory(memory_id)
                if current is None or current.get("text") != content:
                    return False
                await ports.embedding_storage.delete([memory_id])
                await ports.embedding_storage.insert(vector, memory_id)
                return await self.hybrid_retriever.bm25_retriever.update_document(
                    memory_id, content, current.get("metadata") or {}
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "[MemoryEngine] 索引修复未完成 reason_code=index_stage_degraded"
            )
            return False

    async def _recover_idempotent_write_owner(
        self,
        op_id: int | None,
        metadata: dict[str, Any] | None,
    ) -> int | None:
        """失败后至多查询一次 owner，并原子收口成功的 loser 日志。"""

        idempotency_key = self._canonical_idempotency_key(metadata)
        if not idempotency_key:
            return None
        try:
            owner_id = await self.find_memory_id_by_idempotency_key(idempotency_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if owner_id is None:
            return None
        await self._write_journal.advance_op(
            op_id,
            "completed",
            status="completed",
            memory_id=owner_id,
            payload_patch={"memory_id": owner_id},
        )
        return owner_id
