from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DASHBOARD_DIR = REPO_ROOT / "pages" / "dashboard"


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []
        self.stylesheets: list[dict[str, str]] = []
        self.crossorigin_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if "crossorigin" in attr_map:
            self.crossorigin_tags.append(tag)
        if tag == "script":
            self.scripts.append(attr_map)
        elif tag == "link" and "stylesheet" in attr_map.get("rel", "").lower():
            self.stylesheets.append(attr_map)


def check_dashboard_build_artifacts(dashboard_dir: str | Path) -> list[str]:
    dashboard_path = Path(dashboard_dir)
    index_path = dashboard_path / "index.html"
    assets_path = dashboard_path / "assets"
    temp_build_path = dashboard_path / ".vite-build"
    errors: list[str] = []

    if not index_path.exists():
        return [f"missing dashboard index: {index_path}"]
    if not assets_path.is_dir():
        return [f"missing dashboard assets directory: {assets_path}"]

    html = index_path.read_text(encoding="utf-8")
    parser = _AssetParser()
    parser.feed(html)

    if temp_build_path.exists():
        errors.append(".vite-build should be removed after production build")
    if "/src/main" in html:
        errors.append("index.html still references /src/main dev entry")

    module_scripts = [
        script
        for script in parser.scripts
        if script.get("type", "").lower() == "module"
    ]
    if module_scripts:
        errors.append('index.html must not contain type="module" scripts')

    if parser.crossorigin_tags:
        errors.append("index.html must not contain crossorigin attributes")

    local_js_refs = [
        script.get("src", "")
        for script in parser.scripts
        if script.get("src", "").startswith("./assets/")
        and script.get("src", "").endswith(".js")
    ]
    if len(local_js_refs) != 1:
        errors.append(
            f"expected exactly one local JS bundle, found {len(local_js_refs)}"
        )

    local_css_refs = [
        link.get("href", "")
        for link in parser.stylesheets
        if link.get("href", "").startswith("./assets/")
        and link.get("href", "").endswith(".css")
    ]
    if len(local_css_refs) != 1:
        errors.append(
            f"expected exactly one local CSS bundle, found {len(local_css_refs)}"
        )

    for asset_ref in local_js_refs + local_css_refs:
        asset_file = dashboard_path / asset_ref.removeprefix("./")
        if not asset_file.exists():
            errors.append(f"referenced asset is missing: {asset_ref}")

    js_files = sorted(assets_path.glob("*.js"))
    css_files = sorted(assets_path.glob("*.css"))
    if len(js_files) != 1:
        errors.append(f"expected exactly one JS file in assets, found {len(js_files)}")
    if len(css_files) != 1:
        errors.append(
            f"expected exactly one CSS file in assets, found {len(css_files)}"
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    dashboard_dir = Path(args[0]) if args else DEFAULT_DASHBOARD_DIR
    errors = check_dashboard_build_artifacts(dashboard_dir)
    if errors:
        print("Dashboard build artifact check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Dashboard build artifacts look compatible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
