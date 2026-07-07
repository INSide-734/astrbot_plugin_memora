"""Retrieval evaluation workbench API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import logger
from quart import request

from ..evaluation.evaluation_service import EvaluationService
from .response_utils import error_response, ok_response


class EvaluationApiMixin:
    """Expose retrieval evaluation datasets, runs, reports, and comparisons."""

    async def get_evaluation_datasets(self):
        return await self.get_evaluation_datasets_payload({})

    async def run_evaluation(self):
        try:
            payload = await request.get_json()
        except Exception as exc:
            logger.debug("[评测接口] JSON 请求体无效: %s", exc, exc_info=True)
            return error_response("JSON 请求体无效")
        if not isinstance(payload, dict):
            return error_response("请求体必须为 JSON 对象")
        return await self.run_evaluation_payload(payload)

    async def list_evaluation_reports(self):
        return await self.list_evaluation_reports_payload(dict(request.args))

    async def get_evaluation_report(self):
        return await self.get_evaluation_report_payload(dict(request.args))

    async def compare_evaluation_reports(self):
        args = dict(request.args)
        if not args:
            try:
                payload = await request.get_json()
            except Exception:
                payload = {}
            args = payload if isinstance(payload, dict) else {}
        return await self.compare_evaluation_reports_payload(args)

    async def get_evaluation_datasets_payload(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._build_evaluation_service(require_engine=False)
        try:
            return ok_response(service.list_datasets())
        except Exception as exc:
            logger.error("[评测接口] 获取数据集失败: %s", exc, exc_info=True)
            return error_response(f"获取评测数据集失败: {exc}")

    async def run_evaluation_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        engine = self._get_evaluation_engine()
        if engine is None:
            return error_response("MemoryEngine unavailable")

        service = self._build_evaluation_service(engine=engine)
        try:
            await service.initialize()
            known_datasets = {
                item["name"]
                for item in service.list_datasets().get("datasets", [])
            }
            requested_dataset_items = self._payload_list(payload.get("datasets"))
            selected_datasets = [
                str(item)
                for item in requested_dataset_items
                if str(item) in known_datasets
            ]
            if "datasets" not in payload or not requested_dataset_items:
                selected_datasets = sorted(known_datasets)
            elif not selected_datasets:
                return error_response("No known evaluation datasets selected")
            result = await service.run_evaluation(
                datasets=selected_datasets,
                k=self._clamp_evaluation_k(payload.get("k", 5)),
                variants=self._payload_list(payload.get("variants")) or ["baseline"],
                baseline=str(payload.get("baseline") or "baseline"),
                save_report=bool(payload.get("save_report", False)),
            )
            if result.get("status") == "error":
                return error_response(result.get("message") or "执行评测失败")
            return ok_response(result)
        except Exception as exc:
            logger.error("[评测接口] 执行评测失败: %s", exc, exc_info=True)
            return error_response(f"执行评测失败: {exc}")

    async def list_evaluation_reports_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._build_evaluation_service(require_engine=False)
        try:
            await service.initialize()
            limit = self._parse_positive_int(payload.get("limit"), default=20, maximum=100)
            reports = await service.list_reports(limit=limit)
            return ok_response({"reports": reports, "total": len(reports)})
        except Exception as exc:
            logger.error("[评测接口] 获取报告列表失败: %s", exc, exc_info=True)
            return error_response(f"获取评测报告列表失败: {exc}")

    async def get_evaluation_report_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        report_id = str(payload.get("report_id") or "").strip()
        if not report_id:
            return error_response("缺少必填参数 report_id")
        service = self._build_evaluation_service(require_engine=False)
        try:
            await service.initialize()
            report = await service.get_report(report_id)
            if report is None:
                return error_response("评测报告不存在")
            return ok_response({"report": report})
        except Exception as exc:
            logger.error("[评测接口] 获取报告详情失败: %s", exc, exc_info=True)
            return error_response(f"获取评测报告详情失败: {exc}")

    async def compare_evaluation_reports_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        report_id_a = str(payload.get("report_id_a") or "").strip()
        report_id_b = str(payload.get("report_id_b") or "").strip()
        if not report_id_a or not report_id_b:
            return error_response("缺少必填参数 report_id_a 或 report_id_b")
        service = self._build_evaluation_service(require_engine=False)
        try:
            await service.initialize()
            comparison = await service.compare_reports(report_id_a, report_id_b)
            if comparison is None:
                return error_response("评测报告不存在")
            return ok_response(comparison)
        except Exception as exc:
            logger.error("[评测接口] 对比报告失败: %s", exc, exc_info=True)
            return error_response(f"对比评测报告失败: {exc}")

    def _build_evaluation_service(
        self,
        *,
        engine: Any | None = None,
        require_engine: bool = True,
    ) -> EvaluationService:
        if engine is None and require_engine:
            engine = self._get_evaluation_engine()
        return EvaluationService(
            engine=engine,
            fixture_dir="tests/fixtures/retrieval",
            db_path=self._evaluation_report_db_path(),
        )

    def _get_evaluation_engine(self) -> Any | None:
        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        return getattr(initializer, "memory_engine", None) if initializer else None

    def _evaluation_report_db_path(self) -> Path:
        plugin = getattr(self, "plugin", None)
        initializer = getattr(plugin, "initializer", None)
        for owner in (plugin, initializer):
            if owner is None:
                continue
            data_dir = getattr(owner, "data_dir", None)
            if data_dir:
                return Path(data_dir) / "evaluation_reports.db"
        return Path("data") / "evaluation_reports.db"

    @staticmethod
    def _payload_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            return [value.decode() if isinstance(value, bytes) else value]
        try:
            return list(value)
        except TypeError:
            return [value]

    @staticmethod
    def _clamp_evaluation_k(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 5
        return max(1, min(20, parsed))

    @staticmethod
    def _parse_positive_int(value: Any, *, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if parsed <= 0:
            parsed = default
        return min(parsed, maximum)


__all__ = ["EvaluationApiMixin"]
