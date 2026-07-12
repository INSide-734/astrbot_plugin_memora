"""Configuration persistence and schema contract tests."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
_MISSING = object()


class SavingConfig(dict[str, Any]):
    """Faithful mutable config double with AstrBot's synchronous save boundary."""

    def __init__(self, *args: Any, fail_save: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_save = fail_save
        self.saved_snapshots: list[dict[str, Any]] = []
        self.save_thread_id: int | None = None

    def save_config(self) -> None:
        self.save_thread_id = threading.get_ident()
        self.saved_snapshots.append(copy.deepcopy(dict(self)))
        if self.fail_save:
            raise OSError("simulated atomic save failure")


def _iter_schema_defaults(
    schema: Mapping[str, Any],
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], Any]]:
    for key, field_schema in schema.items():
        path = (*prefix, key)
        if field_schema.get("type") == "object":
            yield from _iter_schema_defaults(field_schema.get("items", {}), path)
        elif "default" in field_schema:
            yield path, field_schema["default"]


def _schema_default_tree(schema: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path, default in _iter_schema_defaults(schema):
        current = result
        for part in path[:-1]:
            current = current.setdefault(part, {})
        current[path[-1]] = copy.deepcopy(default)
    return result


def _get_path(config: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def test_memora_config_preserves_every_schema_leaf_and_default() -> None:
    from core.base.config_validator import MemoraConfig

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    schema_defaults = list(_iter_schema_defaults(schema))
    validated = MemoraConfig(**_schema_default_tree(schema)).model_dump()
    model_defaults = MemoraConfig().model_dump()
    modeled_sections = set(MemoraConfig.model_fields)

    dropped_or_changed: list[str] = []
    default_drift: list[str] = []
    for path, expected in schema_defaults:
        actual = _get_path(validated, path)
        if actual is _MISSING or actual != expected:
            dropped_or_changed.append(".".join(path))

        if path[0] in modeled_sections:
            declared_default = _get_path(model_defaults, path)
            if declared_default is _MISSING or declared_default != expected:
                default_drift.append(".".join(path))

    assert not dropped_or_changed and not default_drift, (
        "schema leaves dropped/changed: "
        + ", ".join(dropped_or_changed)
        + "; Pydantic default drift: "
        + ", ".join(default_drift)
    )


def test_main_does_not_reference_legacy_persisted_config_file() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "config_persisted.json" not in source


def test_config_revision_is_stable_across_dict_insertion_order() -> None:
    from core.base.config_manager import ConfigManager

    first = ConfigManager(
        {
            "bot_language": "zh",
            "recall_engine": {"top_k": 5, "max_k": 10},
        }
    )
    second = ConfigManager(
        {
            "recall_engine": {"max_k": 10, "top_k": 5},
            "bot_language": "zh",
        }
    )

    assert first.get_config_snapshot()[1] == second.get_config_snapshot()[1]


def test_config_snapshots_are_deeply_isolated() -> None:
    from core.base.config_manager import ConfigManager

    manager = ConfigManager(
        {
            "topic_segmentation": {
                "strategy_b": {"similarity_threshold": 0.75}
            }
        }
    )

    snapshot, _ = manager.get_config_snapshot()
    snapshot["topic_segmentation"]["strategy_b"]["similarity_threshold"] = 0.1
    all_config = manager.get_all()
    all_config["topic_segmentation"]["strategy_b"]["similarity_threshold"] = 0.2
    section = manager.get_section("topic_segmentation")
    section["strategy_b"]["similarity_threshold"] = 0.3

    assert manager.get("topic_segmentation.strategy_b.similarity_threshold") == 0.75


@pytest.mark.asyncio
async def test_apply_rejects_a_stale_revision_with_current_revision() -> None:
    from core.base.config_manager import ConfigConflictError, ConfigManager

    manager = ConfigManager({"recall_engine": {"top_k": 5}})
    _, original_revision = manager.get_config_snapshot()
    first = await manager.apply_config_changes(
        {"recall_engine.top_k": 6},
        expected_revision=original_revision,
        persist=False,
    )

    with pytest.raises(ConfigConflictError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.top_k": 7},
            expected_revision=original_revision,
            persist=False,
        )

    assert exc_info.value.current_revision == first.revision
    assert manager.get("recall_engine.top_k") == 6


@pytest.mark.asyncio
async def test_apply_rejects_unknown_leaf_when_schema_is_available() -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    manager = ConfigManager({})
    _, revision = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.not_a_real_setting": True},
            expected_revision=revision,
        )

    assert "recall_engine.not_a_real_setting" in exc_info.value.field_errors


@pytest.mark.asyncio
async def test_apply_reports_pydantic_errors_by_dotted_field_path() -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    manager = ConfigManager({})
    _, revision = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.top_k": 51},
            expected_revision=revision,
        )

    assert "recall_engine.top_k" in exc_info.value.field_errors


@pytest.mark.asyncio
async def test_apply_updates_source_and_saves_before_publishing() -> None:
    from core.base.config_manager import ConfigManager

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()
    event_loop_thread = threading.get_ident()

    result = await manager.apply_config_changes(
        {"recall_engine.top_k": 8},
        expected_revision=revision,
    )

    assert result.changed_paths == ("recall_engine.top_k",)
    assert source["recall_engine"]["top_k"] == 8
    assert source.saved_snapshots[-1]["recall_engine"]["top_k"] == 8
    assert source.save_thread_id != event_loop_thread
    assert manager.get_config_snapshot()[1] == result.revision


@pytest.mark.asyncio
async def test_apply_rolls_back_source_and_snapshot_when_save_fails() -> None:
    from core.base.config_manager import ConfigManager, ConfigPersistenceError

    source = SavingConfig({"recall_engine": {"top_k": 5}}, fail_save=True)
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    snapshot_before = manager.get_config_snapshot()

    with pytest.raises(ConfigPersistenceError):
        await manager.apply_config_changes(
            {"recall_engine.top_k": 9},
            expected_revision=snapshot_before[1],
        )

    assert dict(source) == source_before
    assert manager.get_config_snapshot() == snapshot_before


@pytest.mark.asyncio
async def test_concurrent_writes_with_same_revision_are_serialized() -> None:
    from core.base.config_manager import (
        ConfigApplyResult,
        ConfigConflictError,
        ConfigManager,
    )

    source: dict[str, Any] = {"recall_engine": {"top_k": 5}}
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()

    async def write(value: int) -> ConfigApplyResult | ConfigConflictError:
        try:
            return await manager.apply_config_changes(
                {"recall_engine.top_k": value},
                expected_revision=revision,
            )
        except ConfigConflictError as exc:
            return exc

    outcomes = await asyncio.gather(write(6), write(7))

    assert sum(isinstance(item, ConfigApplyResult) for item in outcomes) == 1
    assert sum(isinstance(item, ConfigConflictError) for item in outcomes) == 1
    assert source["recall_engine"]["top_k"] in {6, 7}


@pytest.mark.asyncio
async def test_persist_false_changes_only_runtime_snapshot() -> None:
    from core.base.config_manager import ConfigManager

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    _, revision = manager.get_config_snapshot()

    result = await manager.apply_config_changes(
        {"recall_engine.top_k": 9},
        expected_revision=revision,
        persist=False,
    )

    assert dict(source) == source_before
    assert source.saved_snapshots == []
    assert manager.get("recall_engine.top_k") == 9
    assert manager.get_config_snapshot()[1] == result.revision


@pytest.mark.asyncio
async def test_update_runtime_config_wraps_apply_contract() -> None:
    from core.base.config_manager import ConfigManager

    source: dict[str, Any] = {"recall_engine": {"top_k": 5}}
    manager = ConfigManager(source)

    assert await manager.update_runtime_config(
        {"recall_engine.top_k": 10}, persist=True
    )
    assert source["recall_engine"]["top_k"] == 10
    assert not await manager.update_runtime_config(
        {"recall_engine.top_k": 51}, persist=True
    )
    assert manager.get("recall_engine.top_k") == 10
