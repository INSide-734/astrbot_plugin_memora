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


class BlockingSavingConfig(SavingConfig):
    """Persistence double that exposes deterministic thread boundaries."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.save_entered = threading.Event()
        self.release_save = threading.Event()
        self.save_finished = threading.Event()
        self.clear_calls = 0

    def clear(self) -> None:
        self.clear_calls += 1
        super().clear()

    def save_config(self) -> None:
        self.save_thread_id = threading.get_ident()
        self.save_entered.set()
        try:
            if not self.release_save.wait(timeout=5):
                raise TimeoutError("test did not release save_config")
            if self.fail_save:
                raise OSError("simulated atomic save failure")
            self.saved_snapshots.append(copy.deepcopy(dict(self)))
        finally:
            self.save_finished.set()


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


def _iter_schema_options(
    schema: Mapping[str, Any],
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[str, Any, tuple[Any, ...]]]:
    for key, field_schema in schema.items():
        path = (*prefix, key)
        if field_schema.get("type") == "object":
            yield from _iter_schema_options(field_schema.get("items", {}), path)
        elif "options" in field_schema:
            yield (
                ".".join(path),
                field_schema.get("default", _MISSING),
                tuple(field_schema["options"]),
            )


def _schema_option_cases() -> list[Any]:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    return [
        pytest.param(path, default, options, id=path)
        for path, default, options in _iter_schema_options(schema)
    ]


def _value_outside_options(options: tuple[Any, ...]) -> Any:
    def is_declared(candidate: Any) -> bool:
        return any(
            type(candidate) is type(option) and candidate == option
            for option in options
        )

    for option in options:
        if isinstance(option, str):
            candidate = f"{option}__memora_invalid_schema_option__"
        elif type(option) is bool:
            candidate = not option
        elif type(option) is int:
            candidate = option + 1
        elif type(option) is float:
            candidate = option + 0.5
        else:
            continue
        if not is_declared(candidate):
            return candidate
    return {"not": "a JSON scalar option"}


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


def _model_numeric_bounds() -> dict[str, dict[str, int | float]]:
    from core.base.config_validator import MemoraConfig

    model_schema = MemoraConfig.model_json_schema()
    definitions = model_schema.get("$defs", {})
    bounds_by_path: dict[str, dict[str, int | float]] = {}

    def resolve(node: Mapping[str, Any]) -> Mapping[str, Any]:
        while "$ref" in node:
            node = definitions[str(node["$ref"]).rsplit("/", 1)[-1]]
        return node

    def visit(node: Mapping[str, Any], path: tuple[str, ...] = ()) -> None:
        node = resolve(node)
        if node.get("type") == "object":
            for key, child in node.get("properties", {}).items():
                visit(child, (*path, key))
            return

        unsupported = {
            key: node[key]
            for key in ("exclusiveMinimum", "exclusiveMaximum", "multipleOf")
            if key in node
        }
        assert not unsupported, (
            f"unsupported numeric constraints at {'.'.join(path)}: {unsupported}"
        )
        bounds = {
            name: node[key]
            for key, name in (("minimum", "min"), ("maximum", "max"))
            if key in node
        }
        if bounds:
            bounds_by_path[".".join(path)] = bounds

    visit(model_schema)
    return bounds_by_path


def _schema_numeric_bounds(
    schema: Mapping[str, Any],
    prefix: tuple[str, ...] = (),
) -> dict[str, dict[str, int | float]]:
    bounds_by_path: dict[str, dict[str, int | float]] = {}
    for key, field_schema in schema.items():
        path = (*prefix, key)
        if field_schema.get("type") == "object":
            bounds_by_path.update(
                _schema_numeric_bounds(field_schema.get("items", {}), path)
            )
            continue
        bounds = {
            name: field_schema[name]
            for name in ("min", "max")
            if name in field_schema
        }
        if bounds:
            bounds_by_path[".".join(path)] = bounds
    return bounds_by_path


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


def test_schema_numeric_bounds_match_every_pydantic_constraint() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    model_bounds = _model_numeric_bounds()
    schema_bounds = _schema_numeric_bounds(schema)

    assert len(model_bounds) == 71
    assert schema_bounds == model_bounds


def test_main_does_not_reference_legacy_persisted_config_file() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "config_persisted.json" not in source


def test_config_revision_is_stable_across_dict_insertion_order() -> None:
    from core.base.config_manager import ConfigManager

    first = ConfigManager(
        {
            "扩展乙": {"内部乙": "值乙", "内部甲": "值甲"},
            "扩展甲": {"列表": [{"右": 2, "左": 1}]},
            "recall_engine": {"top_k": 5, "max_k": 10},
        }
    )
    second = ConfigManager(
        {
            "recall_engine": {"max_k": 10, "top_k": 5},
            "扩展甲": {"列表": [{"左": 1, "右": 2}]},
            "扩展乙": {"内部甲": "值甲", "内部乙": "值乙"},
        }
    )

    first_snapshot, first_revision = first.get_config_snapshot()
    second_snapshot, second_revision = second.get_config_snapshot()

    assert first_snapshot == second_snapshot
    assert first_revision == second_revision


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


def test_get_mutable_value_cannot_bypass_config_revision() -> None:
    from core.base.config_manager import ConfigManager

    manager = ConfigManager(
        {
            "topic_segmentation": {
                "strategy_b": {"similarity_threshold": 0.75}
            }
        }
    )
    snapshot_before, revision_before = manager.get_config_snapshot()

    exposed_section = manager.get("topic_segmentation")
    exposed_section["strategy_b"]["similarity_threshold"] = 0.1

    snapshot_after, revision_after = manager.get_config_snapshot()
    assert snapshot_after == snapshot_before
    assert revision_after == revision_before


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
async def test_apply_rejects_external_source_change_without_overwriting_it() -> None:
    from core.base.config_manager import ConfigConflictError, ConfigManager

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, original_revision = manager.get_config_snapshot()

    source["recall_engine"]["top_k"] = 9

    with pytest.raises(ConfigConflictError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.max_k": 12},
            expected_revision=original_revision,
        )

    snapshot, reconciled_revision = manager.get_config_snapshot()
    assert exc_info.value.current_revision == reconciled_revision
    assert reconciled_revision != original_revision
    assert snapshot["recall_engine"]["top_k"] == 9
    assert source["recall_engine"]["top_k"] == 9
    assert source.saved_snapshots == []


@pytest.mark.asyncio
async def test_apply_detects_external_source_change_during_persistence() -> None:
    from core.base.config_manager import ConfigConflictError, ConfigManager

    source = BlockingSavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, original_revision = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 6},
            expected_revision=original_revision,
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    source["recall_engine"]["top_k"] = 9
    source.release_save.set()

    with pytest.raises(ConfigConflictError) as exc_info:
        await apply_task

    snapshot, reconciled_revision = manager.get_config_snapshot()
    assert exc_info.value.current_revision == reconciled_revision
    assert snapshot["recall_engine"]["top_k"] == 9
    assert source["recall_engine"]["top_k"] == 9
    assert source.saved_snapshots[-1]["recall_engine"]["top_k"] == 9


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
async def test_apply_prefers_valid_injected_schema_when_repo_schema_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    source.schema = {
        "recall_engine": {
            "type": "object",
            "items": {"top_k": {"type": "int", "default": 5}},
        }
    }

    def missing_schema(*args: Any, **kwargs: Any) -> str:
        raise FileNotFoundError("repo schema unavailable")

    monkeypatch.setattr(Path, "read_text", missing_schema)
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.max_k": 12},
            expected_revision=revision,
        )

    assert "recall_engine.max_k" in exc_info.value.field_errors
    assert source.saved_snapshots == []


@pytest.mark.asyncio
async def test_persistent_source_fails_closed_when_schema_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    source = SavingConfig({"recall_engine": {"top_k": 5}})

    def missing_schema(*args: Any, **kwargs: Any) -> str:
        raise FileNotFoundError("repo schema unavailable")

    monkeypatch.setattr(Path, "read_text", missing_schema)
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    snapshot_before = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.top_k": 8},
            expected_revision=snapshot_before[1],
        )

    assert "*" in exc_info.value.field_errors
    assert dict(source) == source_before
    assert source.saved_snapshots == []
    assert manager.get_config_snapshot() == snapshot_before


@pytest.mark.asyncio
async def test_persistent_source_fails_closed_when_schemas_are_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    source = SavingConfig({"recall_engine": {"top_k": 5}})
    source.schema = {
        "recall_engine": {"type": "object", "items": "not-an-object"}
    }
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: "{malformed-json",
    )
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    snapshot_before = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.top_k": 8},
            expected_revision=snapshot_before[1],
        )

    assert "*" in exc_info.value.field_errors
    assert dict(source) == source_before
    assert source.saved_snapshots == []
    assert manager.get_config_snapshot() == snapshot_before


@pytest.mark.asyncio
async def test_plain_dict_remains_usable_without_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.base.config_manager import ConfigManager

    def missing_schema(*args: Any, **kwargs: Any) -> str:
        raise FileNotFoundError("repo schema unavailable")

    monkeypatch.setattr(Path, "read_text", missing_schema)
    source: dict[str, Any] = {"recall_engine": {"top_k": 5}}
    manager = ConfigManager(source)

    assert await manager.update_runtime_config(
        {"recall_engine.top_k": 8},
        persist=True,
    )
    assert source["recall_engine"]["top_k"] == 8


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
@pytest.mark.parametrize("path,default,options", _schema_option_cases())
async def test_apply_enforces_every_schema_options_list(
    path: str,
    default: Any,
    options: tuple[Any, ...],
) -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    assert default is not _MISSING
    assert any(
        type(default) is type(option) and default == option
        for option in options
    )
    assert options
    invalid_value = _value_outside_options(options)

    source = SavingConfig({})
    manager = ConfigManager(source)
    snapshot_before = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {path: invalid_value},
            expected_revision=snapshot_before[1],
        )

    assert path in exc_info.value.field_errors
    assert manager.get_config_snapshot() == snapshot_before
    assert dict(source) == {}
    assert source.saved_snapshots == []

    result = await manager.apply_config_changes(
        {path: default},
        expected_revision=snapshot_before[1],
        persist=False,
    )
    assert result.changed_paths == (path,)


@pytest.mark.asyncio
async def test_schema_options_use_exact_json_scalar_equality() -> None:
    from core.base.config_manager import ConfigManager, ConfigValidationError

    source = SavingConfig({"recall_engine": {"top_k": 1}})
    source.schema = {
        "recall_engine": {
            "type": "object",
            "items": {
                "top_k": {
                    "type": "int",
                    "default": 1,
                    "options": [1],
                }
            },
        }
    }
    manager = ConfigManager(source)
    snapshot_before = manager.get_config_snapshot()

    with pytest.raises(ConfigValidationError) as exc_info:
        await manager.apply_config_changes(
            {"recall_engine.top_k": True},
            expected_revision=snapshot_before[1],
        )

    assert "recall_engine.top_k" in exc_info.value.field_errors
    assert manager.get_config_snapshot() == snapshot_before
    assert source.saved_snapshots == []


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
async def test_failed_save_preserves_concurrent_external_source_change() -> None:
    from core.base.config_manager import ConfigManager, ConfigPersistenceError

    source = BlockingSavingConfig(
        {"recall_engine": {"top_k": 5}},
        fail_save=True,
    )
    manager = ConfigManager(source)
    _, original_revision = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 6},
            expected_revision=original_revision,
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    source["recall_engine"]["top_k"] = 9
    source.release_save.set()

    with pytest.raises(ConfigPersistenceError):
        await apply_task

    snapshot, reconciled_revision = await manager.get_config_snapshot_async()
    assert reconciled_revision != original_revision
    assert snapshot["recall_engine"]["top_k"] == 9
    assert source["recall_engine"]["top_k"] == 9


@pytest.mark.asyncio
async def test_cancelled_apply_publishes_successful_save_before_propagating() -> None:
    from core.base.config_manager import ConfigManager

    source = BlockingSavingConfig({"recall_engine": {"top_k": 5}})
    manager = ConfigManager(source)
    _, original_revision = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 9},
            expected_revision=original_revision,
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    apply_task.cancel()
    await asyncio.sleep(0)
    assert not apply_task.done()
    apply_task.cancel()
    await asyncio.sleep(0)
    assert not apply_task.done()
    source.release_save.set()
    assert await asyncio.to_thread(source.save_finished.wait, 2)

    with pytest.raises(asyncio.CancelledError):
        await apply_task

    snapshot, revision = manager.get_config_snapshot()
    assert source.saved_snapshots == [snapshot]
    assert dict(source) == snapshot
    assert snapshot["recall_engine"]["top_k"] == 9
    assert revision != original_revision


@pytest.mark.asyncio
async def test_cancelled_apply_rolls_back_after_failed_save() -> None:
    from core.base.config_manager import ConfigManager

    source = BlockingSavingConfig(
        {"recall_engine": {"top_k": 5}},
        fail_save=True,
    )
    manager = ConfigManager(source)
    source_before = copy.deepcopy(dict(source))
    snapshot_before = manager.get_config_snapshot()
    apply_task = asyncio.create_task(
        manager.apply_config_changes(
            {"recall_engine.top_k": 9},
            expected_revision=snapshot_before[1],
        )
    )

    assert await asyncio.to_thread(source.save_entered.wait, 2)
    apply_task.cancel()
    await asyncio.sleep(0)
    source.release_save.set()
    assert await asyncio.to_thread(source.save_finished.wait, 2)

    with pytest.raises(asyncio.CancelledError):
        await apply_task

    assert source.saved_snapshots == []
    assert dict(source) == source_before
    assert manager.get_config_snapshot() == snapshot_before


@pytest.mark.asyncio
async def test_concurrent_writes_with_same_revision_are_serialized() -> None:
    from core.base.config_manager import (
        ConfigApplyResult,
        ConfigConflictError,
        ConfigManager,
    )

    source = BlockingSavingConfig({"recall_engine": {"top_k": 5}})
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

    first_write = asyncio.create_task(write(6))
    assert await asyncio.to_thread(source.save_entered.wait, 2)
    second_write = asyncio.create_task(write(7))
    await asyncio.sleep(0)
    replacements_before_release = source.clear_calls
    source.release_save.set()
    outcomes = await asyncio.gather(first_write, second_write)

    assert replacements_before_release == 1
    assert sum(isinstance(item, ConfigApplyResult) for item in outcomes) == 1
    assert sum(isinstance(item, ConfigConflictError) for item in outcomes) == 1
    assert len(source.saved_snapshots) == 1
    assert source["recall_engine"]["top_k"] == 6
    assert manager.get("recall_engine.top_k") == 6


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
