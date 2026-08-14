"""跨 feature 共享的白名单列表排序与 SQL 片段原语。"""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any, Literal

SortOrder = Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class SortQuery:
    """保存一个已批准的公开排序键与方向。"""

    by: str
    order: SortOrder


def parse_sort_query(
    args: Mapping[str, Any],
    *,
    allowed: Mapping[str, str] | Set[str],
    default_by: str,
    default_order: SortOrder,
) -> SortQuery:
    """解析单个排序请求，并拒绝白名单之外的 SQL 标识符。"""

    by = str(args.get("sort_by", default_by))
    order = str(args.get("sort_order", default_order))

    if by not in allowed:
        raise ValueError("sort_by is not supported")
    if order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")

    return SortQuery(by=by, order="asc" if order == "asc" else "desc")


def order_by_clause(
    sort: SortQuery,
    *,
    columns: Mapping[str, str],
    tie_breaker: str,
) -> str:
    """仅使用固定 SQL 表达式映射构造确定性的 ORDER BY 片段。"""

    column = columns.get(sort.by)
    if column is None:
        raise ValueError("sort_by is not supported")
    if sort.order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    tie_breaker_column = columns.get(tie_breaker)
    if tie_breaker_column is None:
        raise ValueError("tie_breaker is not supported")

    direction = "ASC" if sort.order == "asc" else "DESC"
    return f"{column} {direction}, {tie_breaker_column} ASC"


__all__ = ["SortOrder", "SortQuery", "order_by_clause", "parse_sort_query"]
