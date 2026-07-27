"""生产评测数据集的校验与原子文件仓库。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .retrieval_quality import EvaluationCase

MAX_DATASET_BYTES = 1024 * 1024
MAX_DATASET_CASES = 500
MAX_REFERENCES_PER_CASE = 50
MAX_TOTAL_REFERENCES = 500
_DATASET_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.jsonl")


class EvaluationDatasetValidationError(ValueError):
    """表示不可信评测数据集未通过稳定边界校验。"""

    def __init__(self, code: str, message: str) -> None:
        """保存稳定错误码和可安全返回的中文消息。"""

        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedEvaluationDataset:
    """表示已完成校验、尚未提交的评测数据集。"""

    name: str
    filename: str
    content: str
    cases: tuple[EvaluationCase, ...]


class EvaluationDatasetRepository:
    """在插件隔离数据目录中管理人工标注的 JSONL 数据集。"""

    def __init__(self, directory: str | Path) -> None:
        """绑定生产数据集目录，但不在只读操作中创建目录。"""

        self.directory = Path(directory)

    def prepare(self, filename: object, content: object) -> PreparedEvaluationDataset:
        """校验文件名、大小、JSONL 结构和用例边界，不执行持久化。"""

        safe_filename = self._normalize_filename(filename)
        if not isinstance(content, str) or not content.strip():
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_empty",
                "评测数据集不能为空",
            )
        try:
            encoded = content.encode("utf-8")
        except UnicodeError as exc:
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_invalid_encoding",
                "评测数据集必须使用 UTF-8 编码",
            ) from exc
        if len(encoded) > MAX_DATASET_BYTES:
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_too_large",
                "评测数据集超过大小限制",
            )

        name = safe_filename.removesuffix(".jsonl")
        cases = self._parse_cases(content, dataset_name=name)
        normalized_content = content if content.endswith("\n") else f"{content}\n"
        return PreparedEvaluationDataset(
            name=name,
            filename=safe_filename,
            content=normalized_content,
            cases=tuple(cases),
        )

    def save(self, prepared: PreparedEvaluationDataset) -> dict[str, Any]:
        """将已校验数据集原子写入目标文件，并返回无路径的安全摘要。"""

        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / prepared.filename
        replaced = target.is_file()
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.directory,
                prefix=f".{prepared.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(prepared.content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return {
            "name": prepared.name,
            "filename": prepared.filename,
            "case_count": len(prepared.cases),
            "replaced": replaced,
        }

    @staticmethod
    def _normalize_filename(filename: object) -> str:
        """只接受不含目录片段的有限 ASCII JSONL 文件名。"""

        if not isinstance(filename, str):
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_invalid_name",
                "评测数据集文件名无效",
            )
        normalized = filename.strip()
        if not _DATASET_FILENAME.fullmatch(normalized):
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_invalid_name",
                "评测数据集文件名无效",
            )
        return normalized

    @staticmethod
    def _parse_cases(content: str, *, dataset_name: str) -> list[EvaluationCase]:
        """逐行解析用例，并限制数量、标识、查询和相关集。"""

        cases: list[EvaluationCase] = []
        seen_case_ids: set[str] = set()
        total_references = 0
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise EvaluationDatasetValidationError(
                    "evaluation_dataset_invalid_jsonl",
                    "评测数据集包含无效 JSONL",
                ) from exc
            case = EvaluationDatasetRepository._parse_case(
                payload,
                dataset_name=dataset_name,
                line_number=line_number,
            )
            if case.case_id in seen_case_ids:
                raise EvaluationDatasetValidationError(
                    "evaluation_dataset_duplicate_case",
                    "评测数据集包含重复用例标识",
                )
            seen_case_ids.add(case.case_id)
            total_references += len(case.relevant_doc_ids)
            if total_references > MAX_TOTAL_REFERENCES:
                raise EvaluationDatasetValidationError(
                    "evaluation_dataset_too_many_references",
                    "评测数据集引用的记忆过多",
                )
            cases.append(case)
            if len(cases) > MAX_DATASET_CASES:
                raise EvaluationDatasetValidationError(
                    "evaluation_dataset_too_many_cases",
                    "评测数据集用例数量超过限制",
                )
        if not cases:
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_empty",
                "评测数据集不能为空",
            )
        return cases

    @staticmethod
    def _parse_case(
        payload: object,
        *,
        dataset_name: str,
        line_number: int,
    ) -> EvaluationCase:
        """将一行不可信 JSON 规范化为有界 EvaluationCase。"""

        if not isinstance(payload, dict):
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_invalid_case",
                "评测数据集包含无效用例",
            )
        case_id = str(payload.get("case_id") or "").strip()
        query = str(payload.get("query") or "").strip()
        relevant = payload.get("relevant_doc_ids")
        metadata = payload.get("metadata", {})
        if (
            not case_id
            or len(case_id) > 128
            or not query
            or len(query) > 4000
            or not isinstance(relevant, list)
            or not relevant
            or len(relevant) > MAX_REFERENCES_PER_CASE
            or not isinstance(metadata, dict)
        ):
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_invalid_case",
                "评测数据集包含无效用例",
            )
        relevant_ids = {str(item or "").strip() for item in relevant}
        relevant_ids.discard("")
        if not relevant_ids or any(len(item) > 128 for item in relevant_ids):
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_invalid_case",
                "评测数据集包含无效用例",
            )
        declared_dataset = str(metadata.get("dataset") or "").strip()
        if declared_dataset and declared_dataset != dataset_name:
            raise EvaluationDatasetValidationError(
                "evaluation_dataset_name_mismatch",
                "评测数据集名称与文件名不一致",
            )
        normalized_metadata = dict(metadata)
        normalized_metadata["dataset"] = dataset_name
        normalized_metadata["source_line"] = line_number
        return EvaluationCase(
            case_id=case_id,
            query=query,
            relevant_doc_ids=relevant_ids,
            metadata=normalized_metadata,
        )


__all__ = [
    "EvaluationDatasetRepository",
    "EvaluationDatasetValidationError",
    "PreparedEvaluationDataset",
]
