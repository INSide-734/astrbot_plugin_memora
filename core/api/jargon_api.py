"""控制台页面的黑话发现、管理与统计接口。"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import asyncio
from typing import Any

from astrbot.api import logger
from quart import request

from ..base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from ..base.list_sorting import parse_sort_query
from ..base.feature_config import is_jargon_discovery_enabled
from ..jargon.jargon_store import JARGON_MEANING_SORT_COLUMNS
from ..jargon.statistical_filter import JARGON_CANDIDATE_SORT_FIELDS
from .editing_utils import (
    conflict_error,
    entity_ok,
    reject_unknown_fields,
    require_object,
    required_text,
)
from .response_utils import error_response, ok_response


_CREATE_FIELDS = frozenset(
    {
        "term",
        "group_id",
        "meaning",
        "confidence",
        "is_jargon",
        "is_confirmed",
        "is_global",
    }
)
_UPDATE_FIELDS = frozenset({"identity", "changes", "expected_revision"})
_DELETE_FIELDS = frozenset({"identity", "expected_revision"})
_IDENTITY_FIELDS = frozenset({"term", "group_id"})
_EDITABLE_FIELDS = frozenset(
    {"meaning", "confidence", "is_jargon", "is_confirmed", "is_global"}
)
_BATCH_FIELDS = frozenset({"action", "items"})


_AUDIT_EMITTED: ContextVar[bool] = ContextVar(
    "jargon_mutation_audit_emitted", default=False
)


def _audit_event(
    action: str,
    identity: Any,
    *,
    result: str,
    error_code: str = "none",
    error_class: str = "none",
    count: int = 1,
    succeeded_count: int | None = None,
    failed_count: int | None = None,
) -> None:
    _AUDIT_EMITTED.set(True)
    if succeeded_count is not None or failed_count is not None:
        logger.info(
            "[黑话 AUDIT] action=%s entity=jargon identity=%s result=%s error_code=%s error_class=%s succeeded_count=%d failed_count=%d",
            action,
            identity,
            result,
            error_code,
            error_class,
            0 if succeeded_count is None else succeeded_count,
            0 if failed_count is None else failed_count,
        )
        return
    logger.info(
        "[黑话 AUDIT] action=%s entity=jargon identity=%s result=%s error_code=%s error_class=%s count=%d",
        action,
        identity,
        result,
        error_code,
        error_class,
        count,
    )


def _audit_boundary(action: str):
    def decorate(handler):
        @wraps(handler)
        async def wrapped(*args, **kwargs):
            token = _AUDIT_EMITTED.set(False)
            try:
                try:
                    response = await handler(*args, **kwargs)
                except Exception as exc:
                    response = _exception_response(exc, operation=action)
                if (
                    isinstance(response, dict)
                    and response.get("status") == "error"
                    and not _AUDIT_EMITTED.get()
                ):
                    code = response.get("code")
                    _audit_event(
                        action,
                        "unavailable",
                        result="failure",
                        error_code=(
                            code
                            if isinstance(code, str) and code
                            else "request_error"
                        ),
                    )
                return response
            finally:
                _AUDIT_EMITTED.reset(token)

        return wrapped

    return decorate


def _parse_limit(raw_value: Any, *, default: int, maximum: int) -> int:
    """解析正整数 limit 参数，非法时使用合理的默认值回退。"""
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, maximum)


def _meaning_to_dict(meaning: Any) -> dict[str, Any]:
    """将 ``JargonMeaning`` 转换为 JSON 响应字典。"""
    return {
        "term": meaning.term,
        "group_id": meaning.group_id,
        "meaning": meaning.meaning,
        "confidence": meaning.confidence,
        "is_jargon": meaning.is_jargon,
        "is_confirmed": meaning.is_confirmed,
        "is_global": meaning.is_global,
        "is_complete": meaning.is_complete,
        "count": meaning.count,
        "last_inference_count": meaning.last_inference_count,
        "context_examples": list(meaning.context_examples or []),
        "created_at": meaning.created_at,
        "updated_at": meaning.updated_at,
    }


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    """将 ``JargonCandidate`` 转换为 JSON 响应字典。"""
    return {
        "term": candidate.term,
        "group_id": candidate.group_id,
        "score": candidate.score,
        "frequency": candidate.frequency,
        "unique_users": candidate.unique_users,
        "idf_score": candidate.idf_score,
        "burst_score": candidate.burst_score,
        "concentration_score": candidate.concentration_score,
        "first_seen": candidate.first_seen,
        "context_examples": candidate.context_examples,
    }


def _safe_meaning_to_dict(meaning: Any) -> dict[str, Any] | None:
    try:
        return _meaning_to_dict(meaning)
    except Exception:
        return None


def _safe_candidate_to_dict(candidate: Any) -> dict[str, Any] | None:
    try:
        return _candidate_to_dict(candidate)
    except Exception:
        return None


def _safe_list_items(items: Any) -> list[Any]:
    try:
        return list(items or [])
    except Exception:
        return []


def _validation_error(exc: EntityValidationError) -> dict[str, Any]:
    return error_response(
        "黑话数据校验失败",
        code="validation_error",
        field_errors=exc.field_errors,
    )


def _sort_query_error(exc: ValueError) -> dict[str, Any]:
    message = str(exc)
    field = "sort_order" if message == "sort_order must be asc or desc" else "sort_by"
    return error_response(
        message,
        code="invalid_query",
        field_errors={field: message},
    )


def _component_unavailable() -> dict[str, Any]:
    return error_response("黑话管理服务不可用", code="component_unavailable")


def _exception_response(
    exc: Exception, *, operation: str, audit: bool = True
) -> dict[str, Any]:
    error_code = (
        "validation_error" if isinstance(exc, EntityValidationError)
        else "already_exists" if isinstance(exc, EntityAlreadyExistsError)
        else "not_found" if isinstance(exc, EntityNotFoundError)
        else "edit_conflict" if isinstance(exc, EditConflictError)
        else "internal_error"
    )
    if audit:
        _audit_event(
            operation,
            "unavailable",
            result="failure",
            error_code=error_code,
            error_class=type(exc).__name__,
        )
    """映射领域异常，并对未知异常隐藏请求和异常文本。"""

    if isinstance(exc, EntityValidationError):
        return _validation_error(exc)
    if isinstance(exc, EntityAlreadyExistsError):
        return error_response("黑话词条已存在", code="already_exists")
    if isinstance(exc, EntityNotFoundError):
        return error_response("黑话词条不存在", code="not_found")
    if isinstance(exc, EditConflictError):
        return conflict_error(
            exc.current_entity,
            current_revision=exc.current_revision,
        )
    logger.error(
        "[黑话接口] operation=%s error_class=%s",
        operation,
        type(exc).__name__,
    )
    return error_response("黑话操作失败", code="internal_error")


def _invalid_json_response(exc: Exception, *, operation: str) -> dict[str, Any]:
    """记录安全的 JSON 解析失败摘要，并保留 legacy 客户端消息。"""

    logger.warning(
        "[黑话接口] operation=%s error_class=%s",
        operation,
        type(exc).__name__,
    )
    return error_response("JSON 请求体无效")


def _parse_identity(value: Any) -> tuple[dict[str, str] | None, dict | None]:
    identity, error = require_object(value)
    if error:
        return None, error
    unknown = reject_unknown_fields(identity, _IDENTITY_FIELDS)
    if unknown:
        return None, unknown
    try:
        normalized = {
            "term": required_text(
                identity.get("term"), field="identity.term", maximum=128
            ),
            "group_id": required_text(
                identity.get("group_id"), field="identity.group_id", maximum=128
            ),
        }
    except EntityValidationError as exc:
        return None, _validation_error(exc)
    return normalized, None


def _parse_revision(value: Any) -> tuple[str | None, dict | None]:
    try:
        return (
            required_text(value, field="expected_revision", maximum=256),
            None,
        )
    except EntityValidationError as exc:
        return None, _validation_error(exc)


def _parse_changes(value: Any) -> tuple[dict[str, Any] | None, dict | None]:
    changes, error = require_object(value)
    if error:
        return None, error
    unknown = reject_unknown_fields(changes, _EDITABLE_FIELDS)
    if unknown:
        return None, unknown
    if not changes:
        return None, _validation_error(
            EntityValidationError({"changes": "不能为空"})
        )
    return changes, None


class JargonApiMixin:
    """为 Memora 控制台页面提供黑话相关 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_feature_delegation(self) -> Any | None:
        """从插件属性中解析 ``FeatureDelegation`` 实例。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        fd = getattr(plugin, "feature_delegation", None)
        if fd is not None:
            return fd
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            return getattr(initializer, "feature_delegation", None)
        return None

    def _get_jargon_filter(self) -> Any | None:
        """从插件属性中惰性解析 ``JargonStatisticalFilter``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_jargon_filter", "jargon_filter"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            obj = getattr(initializer, "jargon_filter", None)
            if obj is not None:
                return obj
        # 惰性创建并缓存
        from ..jargon.statistical_filter import JargonStatisticalFilter

        jf = JargonStatisticalFilter()
        plugin._jargon_filter = jf
        logger.info("[黑话接口] 已惰性创建黑话统计过滤器实例")
        return jf

    def _get_jargon_resolution_lock(self) -> asyncio.Lock:
        """同步创建 plugin-scoped 解析锁；检查和赋值之间没有 suspension。"""

        plugin = self.plugin
        lock = getattr(plugin, "_jargon_resolution_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            plugin._jargon_resolution_lock = lock
        return lock

    @staticmethod
    def _is_closed_jargon_store(store: Any) -> bool:
        """仅识别已初始化后关闭的真实 ``JargonStore`` 实例。"""

        from ..jargon.jargon_store import JargonStore

        return (
            isinstance(store, JargonStore)
            and getattr(store, "_initialized", False) is True
            and store.connection is None
        )

    def _find_open_jargon_store(self, plugin: Any) -> tuple[Any | None, str | None]:
        """查找可用 store，并保留已关闭真实 store 的数据库路径。"""

        closed_db_path = None
        initializer = getattr(plugin, "initializer", None)
        candidates = (
            getattr(plugin, "_jargon_store", None),
            getattr(plugin, "jargon_store", None),
            getattr(initializer, "jargon_store", None)
            if initializer is not None
            else None,
        )
        for store in candidates:
            if store is None:
                continue
            if self._is_closed_jargon_store(store):
                closed_db_path = getattr(store, "db_path", closed_db_path)
                continue
            return store, closed_db_path
        return None, closed_db_path

    @staticmethod
    async def _close_unpublished_jargon_store(store: Any) -> None:
        """将 cleanup 跑到确定结束，并隐藏 cleanup 自身的失败。"""

        try:
            close_task = asyncio.ensure_future(store.close())
        except BaseException:
            return
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                continue
        try:
            close_task.result()
        except BaseException:
            pass

    async def _get_jargon_store_locked(self, plugin: Any) -> Any | None:
        """在调用方已经持有解析锁时解析或初始化 store。"""

        store, closed_db_path = self._find_open_jargon_store(plugin)
        if store is not None:
            return store

        from pathlib import Path

        from ..jargon.jargon_store import JargonStore

        data_dir = getattr(plugin, "data_dir", None)
        initializer = getattr(plugin, "initializer", None)
        if data_dir is None and initializer is not None:
            data_dir = getattr(initializer, "data_dir", None)
        if data_dir is not None:
            db_path = str(Path(data_dir) / "jargon.db")
        elif closed_db_path is not None:
            db_path = closed_db_path
        else:
            logger.warning("[黑话接口] 无法惰性创建黑话存储：未找到数据目录")
            return None

        store = JargonStore(db_path)
        try:
            await store.initialize()
        except BaseException:
            await self._close_unpublished_jargon_store(store)
            raise
        plugin._jargon_store = store
        logger.info("[黑话接口] 已惰性创建黑话存储实例")
        return store

    async def _get_jargon_store(self) -> Any | None:
        """并发安全地解析或创建唯一的 plugin-scoped ``JargonStore``。"""

        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        store, _ = self._find_open_jargon_store(plugin)
        if store is not None:
            return store
        lock = self._get_jargon_resolution_lock()
        async with lock:
            return await self._get_jargon_store_locked(plugin)

    def _get_current_jargon_query_service(self) -> Any | None:
        """同步读取当前 query service，不缓存可被替换的 bound method。"""

        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        query_service = (
            getattr(plugin, "_jargon_query", None)
            or getattr(plugin, "jargon_query", None)
            or getattr(plugin, "jargon_query_service", None)
        )
        initializer = getattr(plugin, "initializer", None)
        if query_service is None and initializer is not None:
            query_service = (
                getattr(initializer, "_jargon_query", None)
                or getattr(initializer, "jargon_query", None)
                or getattr(initializer, "jargon_query_service", None)
            )
        return query_service

    def _invalidate_current_jargon_query(self, group_id: str) -> None:
        """在每次提交后发现并失效当前 query service。"""

        query_service = self._get_current_jargon_query_service()
        invalidator = getattr(query_service, "invalidate_group", None)
        if callable(invalidator):
            invalidator(group_id)

    async def _get_jargon_admin_service(self) -> Any | None:
        """解析并在插件上缓存唯一的 ``JargonAdminService``。"""

        plugin = getattr(self, "plugin", None)
        if plugin is None:
            logger.warning(
                "[黑话接口] operation=resolve_service unavailable=plugin"
            )
            return None
        cached = getattr(plugin, "_jargon_admin_service", None)
        if cached is not None and not self._is_closed_jargon_store(
            getattr(cached, "_store", None)
        ):
            return cached

        lock = self._get_jargon_resolution_lock()
        try:
            async with lock:
                cached = getattr(plugin, "_jargon_admin_service", None)
                if cached is not None and not self._is_closed_jargon_store(
                    getattr(cached, "_store", None)
                ):
                    return cached
                store = await self._get_jargon_store_locked(plugin)
                if store is None:
                    logger.warning(
                        "[黑话接口] operation=resolve_service unavailable=store"
                    )
                    return None

                from ..jargon.jargon_admin_service import JargonAdminService

                service = JargonAdminService(
                    store,
                    self._invalidate_current_jargon_query,
                )
                plugin._jargon_admin_service = service
                return service
        except Exception as exc:
            logger.error(
                "[黑话接口] operation=resolve_service error_class=%s",
                type(exc).__name__,
            )
            return None

    async def _get_jargon_miner(self) -> Any | None:
        """惰性解析或创建 ``JargonMiner``。

        优先从插件或初始化器上查找现有 miner。
        若不存在，则惰性创建一个新实例（依赖 LLM provider、filter 与 store），
        并缓存到 ``plugin._jargon_miner``。

        ``jargon.enabled`` 关闭时直接返回 ``None``，避免页面 API 绕过
        初始化器重新创建自动发现组件。
        """
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        if not is_jargon_discovery_enabled(
            getattr(plugin, "config_manager", None)
        ):
            logger.info("[黑话接口] 黑话自动发现功能已禁用")
            return None
        for attr_name in ("_jargon_miner", "jargon_miner"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            obj = getattr(initializer, "jargon_miner", None)
            if obj is not None:
                return obj

        # ── 惰性创建并缓存 ──
        from ..jargon.jargon_miner import JargonMiner

        # 解析依赖项
        jf = self._get_jargon_filter()
        if jf is None:
            logger.warning("[黑话接口] 无法惰性创建黑话挖掘器：过滤器不可用")
            return None

        store = await self._get_jargon_store()
        if store is None:
            logger.warning("[黑话接口] 无法惰性创建黑话挖掘器：存储不可用")
            return None

        # 从初始化器解析 LLM 客户端
        llm_client = None
        if initializer is not None:
            llm_client = getattr(initializer, "llm_provider", None)
        if llm_client is None:
            # 回退：尝试从插件上下文获取
            ctx = getattr(plugin, "context", None)
            if ctx is not None and hasattr(ctx, "get_using_provider"):
                try:
                    llm_client = ctx.get_using_provider()
                except Exception as exc:
                    logger.debug(
                        "[黑话接口] operation=%s error_class=%s",
                        "resolve_miner_provider",
                        type(exc).__name__,
                    )
        if llm_client is None:
            logger.warning("[黑话接口] 无法惰性创建黑话挖掘器：LLM 提供器不可用")
            return None

        miner = JargonMiner(llm_client, jf, store)
        plugin._jargon_miner = miner
        logger.info("[黑话接口] 已惰性创建黑话挖掘器实例")
        return miner

    @staticmethod
    def _require_group_id(args: Any) -> tuple[str | None, dict | None]:
        """提取并校验 ``group_id`` 查询参数。

        返回:
            成功时返回 ``(group_id, None)``，失败时返回
            ``(None, error_response)``。
        """
        group_id = (args.get("group_id", "") or "").strip()
        if not group_id:
            return None, error_response("缺少必填参数 group_id")
        return group_id, None

    # ------------------------------------------------------------------
    # GET /candidates
    # ------------------------------------------------------------------

    async def get_jargon_candidates(self):
        """返回指定群组经统计预过滤后的黑话候选词。

        查询参数:
            group_id (str, 必填): 群组标识。
            limit (int, 可选): 返回上限，默认 20，最大 100。
        """
        try:
            jf = self._get_jargon_filter()
            if jf is None:
                return error_response("黑话统计过滤器不可用")
            args = request.args
            group_id, err = self._require_group_id(args)
            if err:
                return err
            limit = _parse_limit(args.get("limit", 20), default=20, maximum=100)
            try:
                sort = parse_sort_query(
                    args,
                    allowed=JARGON_CANDIDATE_SORT_FIELDS,
                    default_by="score",
                    default_order="desc",
                )
            except ValueError as exc:
                return _sort_query_error(exc)
            candidates = jf.get_candidates(group_id, limit=limit, sort=sort)
            candidates = _safe_list_items(candidates)
            serialized_candidates = [
                item
                for item in (_safe_candidate_to_dict(c) for c in candidates)
                if item is not None
            ]
            return ok_response({
                "candidates": serialized_candidates,
                "total": len(serialized_candidates),
                "group_id": group_id,
            })
        except Exception as exc:
            return _exception_response(exc, operation="list_candidates")

    # ------------------------------------------------------------------
    # GET /meanings
    # ------------------------------------------------------------------

    async def get_jargon_meanings(self):
        """返回指定群组已确认的黑话释义。

        查询参数:
            group_id (str, 必填): 群组标识。
            confirmed_only (bool, 可选): 是否仅返回已确认条目，
                默认 true。
        """
        service = await self._get_jargon_admin_service()
        if service is None:
            return _component_unavailable()
        store = await self._get_jargon_store()
        if store is None:
            return _component_unavailable()

        args = request.args
        group_id, err = self._require_group_id(args)
        if err:
            return err

        try:
            confirmed_only = args.get("confirmed_only", "true").lower() != "false"
            try:
                sort = parse_sort_query(
                    args,
                    allowed=JARGON_MEANING_SORT_COLUMNS,
                    default_by="updated_at",
                    default_order="desc",
                )
            except ValueError as exc:
                return _sort_query_error(exc)
            meanings = await store.list_by_group(
                group_id,
                confirmed_only=confirmed_only,
                sort=sort,
            )
            meanings = _safe_list_items(meanings)
            serialized_meanings = []
            for meaning in meanings:
                item = _safe_meaning_to_dict(meaning)
                if item is None:
                    continue
                try:
                    item["revision"] = service.revision_for(meaning)
                except Exception:
                    continue
                serialized_meanings.append(item)
            return ok_response({
                "meanings": serialized_meanings,
                "total": len(serialized_meanings),
                "group_id": group_id,
            })
        except Exception as exc:
            return _exception_response(exc, operation="list_meanings")

    # ------------------------------------------------------------------
    # POST /create, /update, /delete, /batch
    # ------------------------------------------------------------------

    @_audit_boundary("create")
    async def create_jargon(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _CREATE_FIELDS)
            if unknown:
                return unknown
            service = await self._get_jargon_admin_service()
            if service is None:
                return _component_unavailable()
            meaning = await service.create(**payload)
            response = entity_ok(
                _meaning_to_dict(meaning),
                revision=service.revision_for(meaning),
            )
            _audit_event(
                "create",
                {"term": meaning.term, "group_id": meaning.group_id},
                result="success",
            )
            return response
        except Exception as exc:
            return _exception_response(exc, operation="create")

    @_audit_boundary("update")
    async def update_jargon(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _UPDATE_FIELDS)
            if unknown:
                return unknown
            identity, error = _parse_identity(payload.get("identity"))
            if error:
                return error
            changes, error = _parse_changes(payload.get("changes"))
            if error:
                return error
            revision, error = _parse_revision(payload.get("expected_revision"))
            if error:
                return error
            service = await self._get_jargon_admin_service()
            if service is None:
                return _component_unavailable()
            meaning = await service.update(
                term=identity["term"],
                group_id=identity["group_id"],
                changes=changes,
                expected_revision=revision,
            )
            response = entity_ok(
                _meaning_to_dict(meaning),
                revision=service.revision_for(meaning),
            )
            _audit_event("update", identity, result="success")
            return response
        except Exception as exc:
            return _exception_response(exc, operation="update")

    @_audit_boundary("delete")
    async def delete_jargon(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _DELETE_FIELDS)
            if unknown:
                return unknown
            identity, error = _parse_identity(payload.get("identity"))
            if error:
                return error
            revision, error = _parse_revision(payload.get("expected_revision"))
            if error:
                return error
            service = await self._get_jargon_admin_service()
            if service is None:
                return _component_unavailable()
            deleted = await service.delete(
                term=identity["term"],
                group_id=identity["group_id"],
                expected_revision=revision,
            )
            if not deleted:
                raise EntityNotFoundError("黑话词条不存在")
            response = ok_response({"deleted": True, "identity": identity})
            _audit_event("delete", identity, result="success")
            return response
        except Exception as exc:
            return _exception_response(exc, operation="delete")

    @_audit_boundary("batch")
    async def batch_jargon(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _BATCH_FIELDS)
            if unknown:
                return unknown
            service = await self._get_jargon_admin_service()
            if service is None:
                return _component_unavailable()
            result = await service.batch(
                action=payload.get("action"),
                items=payload.get("items"),
            )
            succeeded_count = int(result.get("succeeded_count", 0))
            failed_count = int(result.get("failed_count", 0))
            batch_result = (
                "success"
                if not failed_count
                else ("failure" if not succeeded_count else "partial")
            )
            _audit_event(
                "batch_" + str(payload.get("action")),
                "batch",
                result=batch_result,
                error_code="none" if not failed_count else "item_failure",
                succeeded_count=succeeded_count,
                failed_count=failed_count,
            )
            return ok_response(result)
        except Exception as exc:
            return _exception_response(exc, operation="batch")

    # ------------------------------------------------------------------
    # GET /stats
    # ------------------------------------------------------------------

    async def get_jargon_stats(self):
        """返回指定群组的黑话统计摘要。

        查询参数:
            group_id (str, 必填): 群组标识。
        """
        try:
            jf = self._get_jargon_filter()
            if jf is None:
                return error_response("黑话统计过滤器不可用")
            args = request.args
            group_id, err = self._require_group_id(args)
            if err:
                return err
            stats = jf.get_stats(group_id)
            top_candidates = _safe_list_items(getattr(stats, "top_candidates", []))
            serialized_candidates = [
                item
                for item in (
                    _safe_candidate_to_dict(c) for c in top_candidates
                )
                if item is not None
            ]
            result = {
                "group_id": stats.group_id,
                "total_terms": stats.total_terms,
                "candidate_count": stats.candidate_count,
                "top_candidates": serialized_candidates,
            }

            # 若存储层可用，则补充 store 统计
            store = await self._get_jargon_store()
            if store is not None:
                try:
                    store_total = await store.count_by_group(group_id)
                    store_confirmed = await store.count_confirmed(group_id)
                    result.update(
                        store_total=store_total,
                        store_confirmed=store_confirmed,
                    )
                except Exception as exc:
                    logger.debug(
                        "[黑话接口] operation=%s error_class=%s",
                        "stats_store_supplement",
                        type(exc).__name__,
                    )

            return ok_response(result)
        except Exception as exc:
            return _exception_response(exc, operation="read_stats")

    # ------------------------------------------------------------------
    # POST /confirm
    # ------------------------------------------------------------------

    @_audit_boundary("confirm")
    async def confirm_jargon(self):
        """手动确认或拒绝某个黑话词条。

        请求体（JSON）:
            term (str, 必填): 黑话词条。
            group_id (str, 必填): 群组标识。
            confirmed (bool, 可选): ``True`` 表示确认，``False`` 表示拒绝，
                默认 true。
        """
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard

        try:
            store = await self._get_jargon_store()
        except Exception as exc:
            return _exception_response(exc, operation="confirm_resolve_store")
        if store is None:
            return error_response("黑话存储不可用")

        try:
            body = await request.get_json()
        except Exception as exc:
            return _invalid_json_response(exc, operation="confirm_parse_json")

        if not body or not isinstance(body, dict):
            return error_response("请求体必须为 JSON 对象")

        term = (body.get("term", "") or "").strip()
        group_id = (body.get("group_id", "") or "").strip()
        confirmed = body.get("confirmed", True)
        if not isinstance(confirmed, bool):
            return error_response(
                "confirmed 必须为布尔值", code="validation_error"
            )

        if not term:
            return error_response("缺少必填参数 term")
        if not group_id:
            return error_response("缺少必填参数 group_id")

        try:
            if not callable(getattr(store, "get_by_term", None)):
                await store.confirm(term, group_id, confirmed=confirmed)
                _audit_event(
                    "confirm",
                    {"term": term, "group_id": group_id},
                    result="success",
                )
                action = "confirmed" if confirmed else "rejected"
                action_text = "已确认" if confirmed else "已驳回"
                return ok_response({
                    "term": term,
                    "group_id": group_id,
                    "action": action,
                    "message": f"词条“{term}”{action_text}",
                })
            service = await self._get_jargon_admin_service()
            if service is None:
                return _component_unavailable()
            current = await store.get_by_term(term, group_id)
            if current is None:
                raise EntityNotFoundError("jargon meaning not found")
            await service.update(
                term=term,
                group_id=group_id,
                changes={"is_confirmed": confirmed},
                expected_revision=service.revision_for(current),
            )
            _audit_event(
                "confirm",
                {"term": term, "group_id": group_id},
                result="success",
            )
            action = "confirmed" if confirmed else "rejected"
            action_text = "已确认" if confirmed else "已驳回"
            return ok_response({
                "term": term,
                "group_id": group_id,
                "action": action,
                "message": f"词条“{term}”{action_text}",
            })
        except Exception as exc:
            return _exception_response(exc, operation="confirm")

    # ------------------------------------------------------------------
    # POST /mine
    # ------------------------------------------------------------------

    @_audit_boundary("mine")
    async def mine_jargon(self):
        """手动触发指定群组的一轮黑话挖掘。

        请求体（JSON）:
            group_id (str, 必填): 群组标识。
            limit (int, 可选): 最多推断词条数，默认 5，最大 20。
        """
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard

        try:
            # 若 self_learning 接管黑话能力，则拒绝执行挖掘。
            fd = self._get_feature_delegation()
            if fd is not None and fd.should_delegate_jargon():
                return error_response(
                    "黑话挖掘能力已委托给 self_learning 插件；"
                    "伴侣插件启用期间，Memora 本地黑话处理会保持关闭。"
                )
        except Exception as exc:
            return _exception_response(exc, operation="mine_delegation")

        try:
            miner = await self._get_jargon_miner()
        except Exception as exc:
            return _exception_response(exc, operation="mine_resolve_miner")
        if miner is None:
            return error_response("黑话挖掘器不可用")

        try:
            body = await request.get_json()
        except Exception as exc:
            return _invalid_json_response(exc, operation="mine_parse_json")

        if not body or not isinstance(body, dict):
            return error_response("请求体必须为 JSON 对象")

        group_id = (body.get("group_id", "") or "").strip()
        if not group_id:
            return error_response("缺少必填参数 group_id")

        try:
            limit = _parse_limit(body.get("limit", 5), default=5, maximum=20)
            results = await miner.run_once(group_id, limit=limit)
            results = _safe_list_items(results)
            serialized_results = [
                item
                for item in (_safe_meaning_to_dict(r) for r in results)
                if item is not None
            ]
            response = ok_response({
                "group_id": group_id,
                "inferred_count": len(results),
                "results": serialized_results,
                "message": f"黑话挖掘完成，共推断出 {len(results)} 个词条",
            })
            _audit_event(
                "mine",
                {"group_id": group_id},
                result="success",
                count=len(results),
            )
            return response
        except Exception as exc:
            return _exception_response(exc, operation="mine")


__all__ = ["JargonApiMixin"]
