"""评测数据集加载与按请求筛选。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ..application.retrieval_quality import EvaluationCase, load_fixture_dir


def load_datasets(
    fixture_dir: str | Path,
    *,
    include_experimental: bool = False,
) -> dict[str, list[EvaluationCase]]:
    """从夹具目录加载全部 JSONL 数据集。"""

    return load_fixture_dir(
        fixture_dir,
        include_experimental=include_experimental,
    )


def select_datasets(
    requested: Sequence[str] | None,
    *,
    fixture_dir: str | Path,
    include_experimental: bool = False,
    runtime_datasets: Mapping[str, Sequence[EvaluationCase]] | None = None,
) -> dict[str, list[EvaluationCase]]:
    """合并受信运行时用例并按请求名称筛选；空请求表示全部。"""

    available = load_datasets(
        fixture_dir,
        include_experimental=include_experimental,
    )
    for name, cases in (runtime_datasets or {}).items():
        available[str(name)] = list(cases)
    if not requested:
        return available
    return {name: available[name] for name in requested if name in available}


__all__ = ["load_datasets", "select_datasets"]
