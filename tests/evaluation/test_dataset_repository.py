"""生产评测数据集仓库测试。"""

from __future__ import annotations

import pytest

from core.evaluation.dataset_repository import (
    EvaluationDatasetRepository,
    EvaluationDatasetValidationError,
)

VALID_DATASET = """{"case_id":"coffee","query":"用户喜欢什么咖啡","relevant_doc_ids":["17"],"metadata":{"session_id":"private:user-1"}}\n"""


def test_repository_prepares_and_atomically_saves_a_dataset(tmp_path) -> None:
    """合法标注集应保存到生产目录，并使用文件名作为稳定数据集名称。"""

    repository = EvaluationDatasetRepository(tmp_path)

    prepared = repository.prepare("daily-recall.jsonl", VALID_DATASET)
    descriptor = repository.save(prepared)

    assert descriptor == {
        "name": "daily-recall",
        "filename": "daily-recall.jsonl",
        "case_count": 1,
        "replaced": False,
    }
    assert (tmp_path / "daily-recall.jsonl").read_text(
        encoding="utf-8"
    ) == VALID_DATASET


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("../escape.jsonl", VALID_DATASET, "evaluation_dataset_invalid_name"),
        ("daily.txt", VALID_DATASET, "evaluation_dataset_invalid_name"),
        ("daily.jsonl", "", "evaluation_dataset_empty"),
        (
            "daily.jsonl",
            '{"case_id":"x","query":"q","relevant_doc_ids":[]}\n',
            "evaluation_dataset_invalid_case",
        ),
        (
            "daily.jsonl",
            VALID_DATASET + VALID_DATASET,
            "evaluation_dataset_duplicate_case",
        ),
    ],
)
def test_repository_rejects_untrusted_dataset_inputs(
    tmp_path,
    filename: str,
    content: str,
    code: str,
) -> None:
    """文件名、空数据、无效相关集和重复 case 必须在写入前拒绝。"""

    repository = EvaluationDatasetRepository(tmp_path)

    with pytest.raises(EvaluationDatasetValidationError) as exc_info:
        repository.prepare(filename, content)

    assert exc_info.value.code == code
    assert list(tmp_path.glob("*")) == []


def test_repository_rejects_dataset_name_mismatch(tmp_path) -> None:
    """metadata.dataset 不得把文件映射为另一套逻辑名称。"""

    repository = EvaluationDatasetRepository(tmp_path)
    content = VALID_DATASET.replace(
        '"session_id":"private:user-1"',
        '"dataset":"other","session_id":"private:user-1"',
    )

    with pytest.raises(EvaluationDatasetValidationError) as exc_info:
        repository.prepare("daily-recall.jsonl", content)

    assert exc_info.value.code == "evaluation_dataset_name_mismatch"
