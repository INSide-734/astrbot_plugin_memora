"""Topic catalog stages used by the derived rebuild coordinator."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, cast

from astrbot.api import logger


class DerivedRebuildCatalogMixin:
    """Own catalog generation verification and compensation for rebuilds."""

    async def _safe_active_catalog_state(
        self,
        state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """仅保留经当前聚合复核通过的旧 active generation 状态。"""

        if not isinstance(state, dict) or state.get("status") != "ready":
            return None
        generation = state.get("active_generation")
        verify = getattr(self.catalog_store, "verify_published_generation", None)
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation <= 0
            or not callable(verify)
        ):
            return None
        try:
            verified = verify(generation)
            if inspect.isawaitable(verified):
                verified = await verified
            return state if verified is True else None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _rebuild_catalog(self) -> dict[str, Any]:
        """从 canonical documents 回填 topic catalog staging generation。"""

        if self.catalog_store is None:
            return {
                "status": "skipped",
                "success": True,
                "reason_code": "catalog_unavailable",
            }
        previous_state = await self._safe_active_catalog_state(
            await self._catalog_state_snapshot()
        )
        rebuild = getattr(self.catalog_store, "rebuild_from_canonical", None)
        if not callable(rebuild):
            return {
                "status": "failed",
                "success": False,
                "reason_code": "catalog_rebuild_unavailable",
            }
        operation = cast(Callable[[], Awaitable[dict[str, Any]]], rebuild)
        result = await operation()
        if not isinstance(result, dict):
            return {"success": False, "reason_code": "catalog_rebuild_failed"}
        if not result.get("success"):
            return result
        generation = result.get("generation")
        if isinstance(generation, bool):
            generation = None
        try:
            generation_value = 0 if generation is None else int(generation)
        except (TypeError, ValueError):
            generation_value = 0
        if generation_value <= 0:
            await self._mark_catalog_degraded("catalog_generation_missing")
            return {"success": False, "reason_code": "catalog_generation_missing"}
        verify = getattr(self.catalog_store, "verify_published_generation", None)
        if not callable(verify):
            await self._restore_catalog_after_verify_failure(
                generation_value,
                previous_state,
                "catalog_post_publish_verify_unavailable",
            )
            return {
                "success": False,
                "reason_code": "catalog_post_publish_verify_unavailable",
            }
        verified = False
        try:
            verification = verify(generation_value)
            if inspect.isawaitable(verification):
                verification = await verification
            verified = verification is True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("话题目录发布后复核失败")
        if not verified:
            await self._restore_catalog_after_verify_failure(
                generation_value,
                previous_state,
                "catalog_post_publish_verify_failed",
            )
            return {
                "success": False,
                "reason_code": "catalog_post_publish_verify_failed",
            }
        previous_generation = (
            previous_state.get("active_generation")
            if isinstance(previous_state, dict)
            else None
        )
        retire = getattr(self.catalog_store, "retire_generation", None)
        if (
            isinstance(previous_generation, int)
            and not isinstance(previous_generation, bool)
            and previous_generation > 0
            and previous_generation != generation_value
            and callable(retire)
        ):
            try:
                retired = retire(previous_generation)
                if inspect.isawaitable(retired):
                    retired = await retired
                if retired is not True:
                    logger.warning("旧话题目录 generation 清理未完成")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("旧话题目录 generation 清理失败")
        cleanup = getattr(self.catalog_store, "cleanup_orphan_generations", None)
        if callable(cleanup):
            try:
                cleaned = cleanup()
                if inspect.isawaitable(cleaned):
                    cleaned = await cleaned
                if cleaned is not True:
                    logger.warning("孤儿话题目录 generation 清理未完成")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("孤儿话题目录 generation 清理失败")
        return result

    async def _restore_catalog_after_verify_failure(
        self,
        failed_generation: int,
        previous_state: dict[str, Any] | None,
        reason_code: str,
    ) -> None:
        """撤回未通过复核的 generation，无法撤回时转为原子降级。"""

        previous_generation = None
        previous_watermark = 0
        previous_revision = None
        if isinstance(previous_state, dict):
            candidate = previous_state.get("active_generation")
            if (
                previous_state.get("status") == "ready"
                and isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and candidate > 0
                and candidate != failed_generation
            ):
                previous_generation = candidate
                previous_watermark = max(
                    0,
                    int(previous_state.get("published_dirty_watermark") or 0),
                )
                revision = previous_state.get("canonical_snapshot_revision")
                previous_revision = revision if isinstance(revision, str) else None
        restore = getattr(
            self.catalog_store, "restore_generation_after_verify_failure", None
        )
        if callable(restore):
            try:
                restored = restore(
                    failed_generation,
                    previous_generation=previous_generation,
                    previous_published_dirty_watermark=previous_watermark,
                    previous_canonical_snapshot_revision=previous_revision,
                    reason_code=reason_code,
                )
                if inspect.isawaitable(restored) and await restored:
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("话题目录发布后撤回失败")
        await self._mark_catalog_degraded(reason_code)


__all__ = ["DerivedRebuildCatalogMixin"]
