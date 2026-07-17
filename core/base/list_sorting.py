"""Validated, allowlisted query sorting helpers for list APIs."""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any, Literal


SortOrder = Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class SortQuery:
    """A single approved public sort key and direction."""

    by: str
    order: SortOrder


def parse_sort_query(
    args: Mapping[str, Any],
    *,
    allowed: Mapping[str, str] | Set[str],
    default_by: str,
    default_order: SortOrder,
) -> SortQuery:
    """Parse a single sort query without accepting arbitrary SQL identifiers."""
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
    """Build an ORDER BY fragment from fixed SQL expressions only."""
    column = columns.get(sort.by)
    if column is None:
        raise ValueError("sort_by is not supported")

    direction = "ASC" if sort.order == "asc" else "DESC"
    return f"{column} {direction}, {tie_breaker} ASC"
