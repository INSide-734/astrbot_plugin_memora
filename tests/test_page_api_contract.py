from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from core.platform.transport.page_api.page_api import (
    PAGE_API_ALIAS_PREFIXES,
    PAGE_API_PREFIX,
    PluginPageApi,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_SRC = REPO_ROOT / "pages" / "dashboard" / "src"

BRIDGE_CALL_PATTERN = re.compile(
    r"\b(?:apiRequest|apiGet|apiPost|(?:[A-Za-z_][A-Za-z0-9_]*\.)?subscribeSSE)\(\s*"
    r"(?P<quote>['\"`])(?P<path>.+?)(?P=quote)"
)
GENERIC_ENDPOINT_PATTERN = re.compile(
    r"(?P<quote>['\"`])(?P<path>[a-z][a-z0-9-]*(?:/[a-z0-9?=&${}_.-]+)+)(?P=quote)"
)
KNOWN_ENDPOINT_ROOTS = {
    "affection",
    "backfill",
    "backup",
    "dashboard",
    "delegation",
    "export",
    "expression",
    "graph",
    "groups",
    "injection-strategy",
    "jargon",
    "knowledge",
    "learning",
    "memories",
    "memory",
    "notes",
    "profiles",
    "quality",
    "recall",
    "social",
    "stats",
    "system",
}
SINGLETON_ENDPOINTS = {"groups", "knowledge", "memories", "notes", "profiles", "stats"}


def _iter_dashboard_files() -> list[Path]:
    return sorted(
        path
        for path in DASHBOARD_SRC.rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and "mock" not in path.parts
        and not path.name.endswith(".test.ts")
        and not path.name.endswith(".test.tsx")
    )


def _normalize_frontend_endpoint(raw: str) -> str | None:
    path = raw.strip()
    if not path or path.startswith(("@/", "./", "../")):
        return None

    path = path.split("?", 1)[0]
    path = path.split("${", 1)[0]
    path = path.strip().strip("/")
    if not path:
        return None

    first_segment = path.split("/", 1)[0]
    if "/" in path:
        if first_segment not in KNOWN_ENDPOINT_ROOTS:
            return None
    elif path not in SINGLETON_ENDPOINTS:
        return None

    return f"page/{path}"


def _extract_frontend_endpoints() -> set[str]:
    endpoints: set[str] = set()

    for path in _iter_dashboard_files():
        text = path.read_text(encoding="utf-8")

        for match in BRIDGE_CALL_PATTERN.finditer(text):
            normalized = _normalize_frontend_endpoint(match.group("path"))
            if normalized:
                endpoints.add(normalized)

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import "):
                continue

            for match in GENERIC_ENDPOINT_PATTERN.finditer(line):
                normalized = _normalize_frontend_endpoint(match.group("path"))
                if normalized:
                    endpoints.add(normalized)

    return endpoints


def _collect_registered_routes() -> set[str]:
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    routes: set[str] = set()
    for call in plugin.context.register_web_api.call_args_list:
        route_path = call.args[0]
        if not route_path.startswith(PAGE_API_PREFIX):
            continue
        suffix = route_path[len(PAGE_API_PREFIX) :].lstrip("/")
        routes.add(f"page/{suffix}")
    return routes


def test_dashboard_frontend_endpoints_are_registered() -> None:
    frontend_endpoints = _extract_frontend_endpoints()
    backend_routes = _collect_registered_routes()

    assert frontend_endpoints, (
        "No dashboard endpoints were extracted from frontend source"
    )

    missing = sorted(frontend_endpoints - backend_routes)
    assert not missing, (
        "Dashboard frontend endpoints are missing backend registrations:\n"
        + "\n".join(missing)
    )


def test_dashboard_critical_system_endpoints_are_covered() -> None:
    frontend_endpoints = _extract_frontend_endpoints()

    expected = {
        "page/backup/list",
        "page/backup/create",
        "page/backup/restore",
        "page/backup/delete",
        "page/backup/batch-delete",
        "page/dashboard/install",
        "page/dashboard/build",
        "page/export/memories",
        "page/system/rebuild",
        "page/system/purge",
        "page/system/compact",
    }

    missing = sorted(expected - frontend_endpoints)
    assert not missing, (
        "Critical dashboard endpoints were not detected in frontend source:\n"
        + "\n".join(missing)
    )


def test_backup_restore_status_and_cancel_routes_are_registered_under_both_prefixes() -> (
    None
):
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        assert (f"{prefix}/backup/status", ("GET",)) in registered
        assert (f"{prefix}/backup/restore/cancel", ("POST",)) in registered
        assert (f"{prefix}/backup/status", ("POST",)) not in registered
        assert (f"{prefix}/backup/restore/cancel", ("GET",)) not in registered

    metadata = {item["path"]: item for item in api.get_route_metadata()}
    assert metadata[f"{PAGE_API_PREFIX}/backup/status"]["risk"] == "read"
    assert metadata[f"{PAGE_API_PREFIX}/backup/restore/cancel"]["risk"] == "destructive"


def test_runtime_update_apply_and_status_routes_are_registered_under_both_prefixes() -> (
    None
):
    """一键更新写操作与状态读取必须在主前缀和别名前缀保持同一契约。"""
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        assert (f"{prefix}/update/apply", ("POST",)) in registered
        assert (f"{prefix}/update/status", ("GET",)) in registered
        assert (f"{prefix}/update/apply", ("GET",)) not in registered
        assert (f"{prefix}/update/status", ("POST",)) not in registered

    metadata = {item["path"]: item for item in api.get_route_metadata()}
    assert metadata[f"{PAGE_API_PREFIX}/update/apply"]["risk"] == "maintenance"
    assert metadata[f"{PAGE_API_PREFIX}/update/status"]["risk"] == "read"
    assert metadata[f"{PAGE_API_PREFIX}/update/status"]["requires_ready"] is False


def test_social_write_contract_is_post_only_under_every_page_prefix() -> None:
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        for suffix in (
            "/social/create",
            "/social/update",
            "/social/delete",
            "/social/batch",
        ):
            assert (f"{prefix}{suffix}", ("POST",)) in registered
            assert (f"{prefix}{suffix}", ("GET",)) not in registered


def test_profile_create_contract_is_post_only_under_every_page_prefix() -> None:
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        path = f"{prefix}/profiles/create"
        assert (path, ("POST",)) in registered
        assert (path, ("GET",)) not in registered


def test_jargon_write_contract_is_post_only_under_every_page_prefix() -> None:
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        for suffix in (
            "/jargon/create",
            "/jargon/update",
            "/jargon/delete",
            "/jargon/batch",
        ):
            assert (f"{prefix}{suffix}", ("POST",)) in registered
            assert (f"{prefix}{suffix}", ("GET",)) not in registered

    for method_name in (
        "create_jargon",
        "update_jargon",
        "delete_jargon",
        "batch_jargon",
    ):
        assert callable(getattr(api, method_name))


def test_affection_editing_contract_uses_read_only_history_and_post_only_mutations() -> (
    None
):
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    get_routes = ("/affection/users", "/affection/moods/history")
    post_routes = (
        "/affection/users/create",
        "/affection/users/update",
        "/affection/users/delete",
        "/affection/users/batch",
        "/affection/mood/set",
        "/affection/mood/reset",
    )
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        for suffix in get_routes:
            assert (f"{prefix}{suffix}", ("GET",)) in registered
            assert (f"{prefix}{suffix}", ("POST",)) not in registered
        for suffix in post_routes:
            assert (f"{prefix}{suffix}", ("POST",)) in registered
            assert (f"{prefix}{suffix}", ("GET",)) not in registered

    for method_name in (
        "list_affection_users",
        "create_affection_user",
        "update_affection_user",
        "delete_affection_user",
        "batch_affection_users",
        "set_affection_mood",
        "reset_affection_mood",
        "get_affection_mood_history",
    ):
        assert callable(getattr(api, method_name))


def test_learning_action_is_one_post_handler_under_every_page_prefix() -> None:
    """生产发布与回滚必须复用同一管理员写入口和别名 handler。"""

    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registrations = {
        (call.args[0], tuple(call.args[2])): call.args[1]
        for call in plugin.context.register_web_api.call_args_list
    }
    canonical_handler = registrations[(f"{PAGE_API_PREFIX}/learning/action", ("POST",))]
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        path = f"{prefix}/learning/action"
        assert (path, ("POST",)) in registrations
        assert (path, ("GET",)) not in registrations
        assert registrations[(path, ("POST",))] == canonical_handler

    metadata = {item["path"]: item for item in api.get_route_metadata()}
    action_metadata = metadata[f"{PAGE_API_PREFIX}/learning/action"]
    assert action_metadata["handler_name"] == "learning_action"
    assert action_metadata["auth"] == "admin"
    assert action_metadata["risk"] == "write"
    assert action_metadata["write_guard"] is True


def test_gate_dry_run_contract_is_post_only_under_every_page_prefix() -> None:
    """门禁 dry-run 为只读预览，仍仅注册 POST；响应契约不含身份回显。"""
    plugin = MagicMock()
    api = PluginPageApi(plugin)
    api.register_routes()

    registered = {
        (call.args[0], tuple(call.args[2]))
        for call in plugin.context.register_web_api.call_args_list
    }
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        path = f"{prefix}/gate/dry-run"
        assert (path, ("POST",)) in registered
        assert (path, ("GET",)) not in registered

    metadata = {item["path"]: item for item in api.get_route_metadata()}
    dry_run_metadata = metadata[f"{PAGE_API_PREFIX}/gate/dry-run"]
    assert dry_run_metadata["handler_name"] == "dry_run_gate"
    assert dry_run_metadata["auth"] == "admin"
    assert dry_run_metadata["risk"] == "write"
