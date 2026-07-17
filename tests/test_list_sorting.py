"""Tests for the shared allowlisted list-sort contract."""

import pytest

from core.base.list_sorting import SortQuery, order_by_clause, parse_sort_query


ALLOWED = {"title": "title COLLATE NOCASE", "updated_at": "updated_at"}
SQL_COLUMNS = {**ALLOWED, "id": "id"}


def test_parse_sort_query_uses_explicit_defaults() -> None:
    assert parse_sort_query(
        {},
        allowed=ALLOWED,
        default_by="updated_at",
        default_order="desc",
    ) == SortQuery("updated_at", "desc")


@pytest.mark.parametrize(
    "args",
    [
        {"sort_by": "title; DROP TABLE knowledge_entries"},
        {"sort_by": "missing"},
        {"sort_order": "DESC"},
        {"sort_order": "sideways"},
    ],
)
def test_parse_sort_query_rejects_unapproved_values(args: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        parse_sort_query(
            args,
            allowed=ALLOWED,
            default_by="updated_at",
            default_order="desc",
        )


def test_order_clause_uses_only_allowlisted_columns_and_stable_tie_breaker() -> None:
    sort = SortQuery("title", "asc")

    assert (
        order_by_clause(sort, columns=SQL_COLUMNS, tie_breaker="id")
        == "title COLLATE NOCASE ASC, id ASC"
    )


def test_order_clause_rejects_unapproved_tie_breaker() -> None:
    with pytest.raises(ValueError, match="tie_breaker"):
        order_by_clause(
            SortQuery("title", "asc"),
            columns=SQL_COLUMNS,
            tie_breaker="id; DROP TABLE knowledge_entries",
        )


def test_order_clause_uses_descending_direction() -> None:
    sort = SortQuery("updated_at", "desc")

    assert (
        order_by_clause(sort, columns=SQL_COLUMNS, tie_breaker="id")
        == "updated_at DESC, id ASC"
    )


def test_order_clause_rejects_key_missing_from_column_mapping() -> None:
    with pytest.raises(ValueError, match="sort_by"):
        order_by_clause(
            SortQuery("missing", "asc"),
            columns=SQL_COLUMNS,
            tie_breaker="id",
        )


def test_order_clause_rejects_a_sort_query_with_an_invalid_direction() -> None:
    sort = SortQuery("title", "sideways")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="sort_order"):
        order_by_clause(sort, columns=SQL_COLUMNS, tie_breaker="id")
