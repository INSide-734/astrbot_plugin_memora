"""检索评测工作台 Page API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import logger
from quart import request

from ..evaluation.evaluation_service import EvaluationService
from .response_utils import error_response, ok_response


class EvaluationApiMixin:
    """暴露检索评测的数据集、运行、报告和对比接口。"""

    async def get_evaluation_datasets(self):
        """返回评测数据集和当前引擎的变体能力描述。"""

        return await self.get_evaluation_datasets_payload({})

    async def run_evaluation(self):
        """解析请求并执行一次离线检索评测。"""

        try:
            payload = await request.get_json()
        except Exception as exc:
            logger.debug(
                "[评测接口] JSON 请求体无效，异常类型=%s",
                exc.__class__.__name__,
            )
            return error_response("JSON 请求体无效")
        if not isinstance(payload, dict):
            return error_response("请求体必须为 JSON 对象")
        return await self.run_evaluation_payload(payload)

    async def list_evaluation_reports(self):
        """按查询参数返回评测报告列表。"""

        return await self.list_evaluation_reports_payload(dict(request.args))

    async def get_evaluation_report(self):
        """按查询参数返回单份评测报告。"""

        return await self.get_evaluation_report_payload(dict(request.args))

    async def compare_evaluation_reports(self):
        """从查询参数或请求体读取两份报告并进行对比。"""

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
        """构造数据集响应，并使用 live engine 只读探测变体能力。"""

        service = self._build_evaluation_service(
            engine=self._get_evaluation_engine(),
            require_engine=False,
        )
        try:
            return ok_response(service.list_datasets())
        except Exception as exc:
            logger.error("[评测接口] 获取数据集失败，异常类型=%s", exc.__class__.__name__)
            return error_response(
                "获取评测数据集失败",
                code="evaluation_datasets_failed",
            )

    async def run_evaluation_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验结构化参数并运行选定数据集和变体。"""

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
            logger.error("[评测接口] 执行评测失败，异常类型=%s", exc.__class__.__name__)
            return error_response("执行评测失败", code="evaluation_run_failed")

    async def list_evaluation_reports_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """返回有界数量的持久化评测报告摘要。"""

        service = self._build_evaluation_service(require_engine=False)
        try:
            await service.initialize()
            limit = self._parse_positive_int(payload.get("limit"), default=20, maximum=100)
            reports = await service.list_reports(limit=limit)
            return ok_response({"reports": reports, "total": len(reports)})
        except Exception as exc:
            logger.error("[评测接口] 获取报告列表失败，异常类型=%s", exc.__class__.__name__)
            return error_response(
                "获取评测报告列表失败",
                code="evaluation_reports_list_failed",
            )

    async def get_evaluation_report_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """按 report_id 返回一份持久化评测报告。"""

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
            logger.error("[评测接口] 获取报告详情失败，异常类型=%s", exc.__class__.__name__)
            return error_response(
                "获取评测报告详情失败",
                code="evaluation_report_get_failed",
            )

    async def compare_evaluation_reports_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """对比两份已持久化报告的安全指标摘要。"""

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
            logger.error("[评测接口] 对比报告失败，异常类型=%s", exc.__class__.__name__)
            return error_response(
                "对比评测报告失败",
                code="evaluation_reports_compare_failed",
            )

    def _build_evaluation_service(
        self,
        *,
        engine: Any | None = None,
        require_engine: bool = True,
    ) -> EvaluationService:
        """使用明确引擎和插件数据目录创建评测服务。"""

        if engine is None and require_engine:
            engine = self._get_evaluation_engine()
        return EvaluationService(
            engine=engine,
            fixture_dir="tests/fixtures/retrieval",
            db_path=self._evaluation_report_db_path(),
        )

    def _get_evaluation_engine(self) -> Any | None:
        """读取当前初始化器装配的 MemoryEngine。"""

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        return getattr(initializer, "memory_engine", None) if initializer else None

    def _evaluation_report_db_path(self) -> Path:
        """解析插件隔离的评测报告数据库路径。"""

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
        """将标量或可迭代请求值规范化为列表。"""

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
        """将 K 规范化到评测服务支持的 1..20。"""

        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 5
        return max(1, min(20, parsed))

    @staticmethod
    def _parse_positive_int(value: Any, *, default: int, maximum: int) -> int:
        """解析正整数，并应用默认值和上限。"""

        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if parsed <= 0:
            parsed = default
        return min(parsed, maximum)


__all__ = ["EvaluationApiMixin"]
