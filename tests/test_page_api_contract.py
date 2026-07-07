from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from core.page_api import PAGE_API_PREFIX, PluginPageApi

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

    assert frontend_endpoints, "No dashboard endpoints were extracted from frontend source"

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
