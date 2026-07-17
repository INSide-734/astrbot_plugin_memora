from __future__ import annotations

import json
import re
import ast
import sys
import configparser
from types import SimpleNamespace
from pathlib import Path

import yaml

from core.utils.version import PLUGIN_VERSION
from core.version_check import _MIN_ASTRBOT_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _load_metadata() -> dict[str, str]:
    return yaml.safe_load((REPO_ROOT / "metadata.yaml").read_text(encoding="utf-8"))


def test_metadata_repo_is_populated() -> None:
    metadata = _load_metadata()
    assert metadata["repo"]
    assert metadata["repo"].startswith("https://")


def test_plugin_version_sources_are_aligned() -> None:
    metadata = _load_metadata()
    assert metadata["version"] == PLUGIN_VERSION

    package_json = _read_text("pages/dashboard/package.json")
    assert f'"version": "{PLUGIN_VERSION}"' in package_json


def test_main_register_uses_metadata_author_and_repo() -> None:
    metadata = _load_metadata()
    main_py = _read_text("main.py")

    assert f'"{metadata["author"]}"' in main_py
    assert f'"{metadata["repo"]}"' in main_py
    assert "PLUGIN_VERSION" in main_py


def test_readme_documents_current_command_set() -> None:
    readme = _read_text("README.md")
    command_names = [
        "status",
        "search",
        "forget",
        "rebuild-index",
        "rebuild-graph",
        "webui",
        "summarize",
        "reset",
        "cleanup",
        "help",
    ]

    for command in command_names:
        assert f"/memora {command}" in readme

    stale_commands = [
        "`/memora recall`",
        "`/memora stats`",
        "`/memora summary`",
        "`/memora clean`",
    ]
    for command in stale_commands:
        assert command not in readme


def test_readme_uses_current_astrbot_minimum_version() -> None:
    readme = _read_text("README.md")
    assert _MIN_ASTRBOT_VERSION in readme


def test_localized_readmes_follow_current_command_set_and_version_floor() -> None:
    localized_paths = ["README_EN.md", "README_RU.md"]
    required_commands = [
        "/memora status",
        "/memora search",
        "/memora forget",
        "/memora rebuild-index",
        "/memora rebuild-graph",
        "/memora webui",
        "/memora summarize",
        "/memora reset",
        "/memora cleanup",
        "/memora help",
    ]
    stale_commands = [
        "`/memora recall`",
        "`/memora stats`",
        "`/memora summary`",
        "`/memora clean`",
    ]

    for path in localized_paths:
        text = _read_text(path)
        assert _MIN_ASTRBOT_VERSION in text
        assert "npm run test" in text
        assert "AGENTS.md" in text
        assert "DESIGN.md" in text
        for command in required_commands:
            assert command in text, f"{path} is missing command: {command}"
        for command in stale_commands:
            assert command not in text, f"{path} still documents stale command: {command}"


def test_readme_documents_fast_context_fallback() -> None:
    readme = _read_text("README.md")
    assert "WINDSURF_API_KEY" in readme
    assert "Select-String" in readme
    assert "fallback" in readme.lower()


def test_root_agents_links_point_to_existing_module_docs() -> None:
    agents_text = _read_text("AGENTS.md")
    click_targets = re.findall(r'click\s+\w+\s+"\.\/([^"]+)"', agents_text)
    markdown_targets = re.findall(r'\]\(\.\/([^\s)#]+)(?:#[^)]+)?\)', agents_text)
    targets = sorted(set(click_targets + markdown_targets))

    assert targets, "No module doc links found in AGENTS.md"
    missing = [target for target in targets if not (REPO_ROOT / target).exists()]
    assert not missing, f"AGENTS.md has broken module doc links: {missing}"


def test_root_design_document_exists() -> None:
    design = _read_text("DESIGN.md")
    for marker in [
        "MemoryAtom",
        "RecallHandler",
        "InjectionStrategyRouter",
        "InjectionExecutor",
        "injection_decisions",
        "python scripts/check_all.py",
    ]:
        assert marker in design, f"DESIGN.md is missing architecture marker: {marker}"


def test_root_agents_documents_real_commands_and_quality_gate() -> None:
    agents = _read_text("AGENTS.md")
    for command in [
        "status",
        "search",
        "forget",
        "rebuild-index",
        "rebuild-graph",
        "webui",
        "summarize",
        "reset",
        "cleanup",
        "help",
    ]:
        assert f"/memora {command}" in agents
    assert "python scripts/check_all.py" in agents


def test_pytest_ini_is_a_real_repository_entrypoint() -> None:
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "pytest.ini", encoding="utf-8")

    assert parser.has_section("pytest")
    testpaths = parser.get("pytest", "testpaths").split()
    assert testpaths == ["tests"]


def test_dashboard_module_guidance_documents_real_entrypoints() -> None:
    dashboard_agents = _read_text("pages/dashboard/AGENTS.md")
    assert (REPO_ROOT / "pages" / "dashboard" / "src" / "main.tsx").exists()
    assert "PageFrame" in dashboard_agents
    assert "python scripts/check_all.py" in dashboard_agents


def test_quality_gate_entrypoints_exist() -> None:
    assert (REPO_ROOT / "scripts" / "check_all.py").exists()
    assert (REPO_ROOT / ".github" / "workflows" / "ci.yml").exists()
    assert (REPO_ROOT / "docs" / "DEV_SETUP.md").exists()


def test_dev_setup_documents_unified_quality_gate() -> None:
    dev_setup = _read_text("docs/DEV_SETUP.md")
    assert "python scripts/check_all.py" in dev_setup
    assert "python -m pytest tests -q" in dev_setup
    assert "python scripts/run_smoke.py -q" in dev_setup
    assert "npm run build" in dev_setup
    assert "npm run check:artifacts" in dev_setup
    assert "npm run test" in dev_setup
    assert "npm run smoke:runtime" in dev_setup
    assert "npm run smoke:browser" in dev_setup


def test_dashboard_real_browser_smoke_is_wired_into_quality_gate() -> None:
    package_json = json.loads(_read_text("pages/dashboard/package.json"))
    check_all = _read_text("scripts/check_all.py")

    assert "smoke:browser" in package_json["scripts"]
    assert package_json["scripts"]["smoke:browser"] == "node scripts/browser_smoke.mjs"
    assert (REPO_ROOT / "pages" / "dashboard" / "scripts" / "browser_smoke.mjs").exists()
    assert "Dashboard browser smoke" in check_all
    assert '"smoke:browser"' in check_all


def test_dashboard_browser_smoke_uses_real_navigation_and_screenshots() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")

    assert "clickSidebarNav" in browser_smoke
    assert "page.getByRole(\"button\"" in browser_smoke
    assert "screenshots" in browser_smoke
    assert "page.screenshot" in browser_smoke
    assert "assertScreenshotLooksNonEmpty" in browser_smoke


def test_dashboard_browser_smoke_compares_screenshot_baselines() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")

    assert "SCREENSHOT_BASELINES" in browser_smoke
    assert "assertScreenshotMatchesBaseline" in browser_smoke
    assert "readPngDimensions" in browser_smoke
    assert "screenshot-baseline-manifest.json" in browser_smoke
    assert "baselineResults" in browser_smoke
    assert "system-confirmation.png" in browser_smoke


def test_dashboard_browser_smoke_covers_mobile_menu_navigation() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")

    assert "clickMobileNav" in browser_smoke
    assert "mobilePage" in browser_smoke
    assert "viewport: { width: 390, height: 844 }" in browser_smoke
    assert 'page.getByRole("button", { name: "Open menu" })' in browser_smoke
    assert "mobile-system.png" in browser_smoke
    assert "mobile-jargon.png" in browser_smoke


def test_dashboard_browser_smoke_covers_injection_strategy_workbench() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")
    required_markers = [
        "runInjectionStrategySmoke",
        "runMobileInjectionStrategySmoke",
        "#/injection",
        "injection-overview.png",
        "injection-config-conflict.png",
        "injection-decisions.png",
        "mobile-injection-detail.png",
        "assertNoHorizontalOverflow",
        "assertScreenshotLooksNonEmpty",
        "assertScreenshotMatchesBaseline",
        'page.getByRole("tab"',
        'page.getByRole("dialog"',
        "ROUTE_LOADING_TEXT",
        "注入决策详情",
        "下一页",
        "ConfigConflictDialog",
        "trace_id",
        "page/recall/trace/detail",
        "sanitizeBridgeCallParams",
        "sanitizeBridgeCallValue",
        "body: sanitizeBridgeCallValue(body ?? {})",
        "BRIDGE_CALL_SENSITIVE_FIELDS",
        "sensitiveFieldSet.forEach((field) => delete sanitized[field])",
    ]
    for marker in required_markers:
        assert marker in browser_smoke

    for marker in (
        "runWideInjectionStrategySmoke",
        "wide-injection-overview.png",
        '[data-slot="injection-decision-body"]',
        '[data-slot="sheet-header"]',
        '[data-slot="sheet-footer"]',
    ):
        assert marker in browser_smoke, marker


def test_dashboard_browser_smoke_trace_diagnostics_are_metadata_only() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")
    start = browser_smoke.index("const traceState = await page.evaluate")
    end = browser_smoke.index("await waitForRootText", start)
    trace_diagnostics = browser_smoke[start:end]

    assert "traceCallCount" in trace_diagnostics
    assert "rootText" not in trace_diagnostics
    assert "calls:" not in trace_diagnostics


def test_injection_overview_charts_use_valid_tokens_and_deterministic_motion() -> None:
    overview = _read_text(
        "pages/dashboard/src/components/injection/InjectionOverviewTab.tsx"
    )
    assert 'color: "hsl(var(--primary))"' not in overview
    assert 'color: "hsl(var(--destructive))"' not in overview
    assert 'color: "var(--primary)"' in overview
    assert 'color: "var(--destructive)"' in overview
    assert overview.count("isAnimationActive={false}") == 3


def test_dashboard_browser_smoke_covers_high_impact_confirmation_flow() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")

    assert "assertHighImpactConfirmation" in browser_smoke
    assert "Install Dependencies" in browser_smoke
    assert "安装依赖" in browser_smoke
    assert "Install Dashboard dependencies now?" in browser_smoke
    assert "现在安装 Dashboard 依赖吗？" in browser_smoke
    assert "dashboard/install" in browser_smoke
    assert "Build Page" in browser_smoke
    assert "构建页面" in browser_smoke
    assert "Build Dashboard production assets now?" in browser_smoke
    assert "现在构建 Dashboard 生产产物吗？" in browser_smoke
    assert "dashboard/build" in browser_smoke
    assert "postCalls" in browser_smoke
    assert "assertNoPostCall" in browser_smoke


def test_dashboard_browser_smoke_covers_backup_destructive_confirmation_flow() -> None:
    browser_smoke = _read_text("pages/dashboard/scripts/browser_smoke.mjs")

    assert "assertBackupDestructiveConfirmations" in browser_smoke
    assert "backup-smoke-a" in browser_smoke
    assert "backup/restore" in browser_smoke
    assert "backup/delete" in browser_smoke
    assert "backup/batch-delete" in browser_smoke
    assert "Restore data from backup-smoke-a? This will overwrite current data." in browser_smoke
    assert "Delete backup backup-smoke-a? This cannot be undone." in browser_smoke
    assert "Delete 2 backups? This cannot be undone." in browser_smoke
    assert "assertNoPostCall" in browser_smoke


def test_run_smoke_reports_each_target_status_and_total_duration(
    monkeypatch, tmp_path, capsys
) -> None:
    from scripts import run_smoke

    target_a = "tests/integration/test_fake_a.py"
    target_b = "tests/integration/test_fake_b.py"
    for target in (target_a, target_b):
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_run(command, cwd):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_smoke, "SMOKE_TARGETS", [target_a, target_b])
    monkeypatch.setattr(run_smoke.Path, "resolve", lambda self: tmp_path / "scripts" / "run_smoke.py")
    monkeypatch.setattr(run_smoke, "which", lambda command: None)
    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        run_smoke.time,
        "perf_counter",
        iter([10.0, 10.0, 10.2, 10.2, 10.5, 10.5]).__next__,
    )

    assert run_smoke.main(["-q"]) == 0

    output = capsys.readouterr().out
    assert f"PASS {target_a}" in output
    assert f"PASS {target_b}" in output
    assert "Smoke summary: 2 passed, 0 failed" in output
    assert "Total smoke time: 0.50s" in output
    assert calls == [
        [sys.executable, "-m", "pytest", target_a, "-q"],
        [sys.executable, "-m", "pytest", target_b, "-q"],
    ]


def test_check_all_reports_step_durations_and_total(monkeypatch, capsys) -> None:
    from scripts import check_all

    calls: list[tuple[list[str], Path]] = []

    def _fake_run(command, cwd):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(check_all, "_resolve_command", lambda command: command)
    monkeypatch.setattr(check_all.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        check_all.time,
        "perf_counter",
        iter(
            [
                100.0,
                100.0,
                100.2,
                100.2,
                101.0,
                101.0,
                101.3,
                101.3,
                101.6,
                101.6,
                102.0,
                102.0,
                102.5,
                102.5,
                102.9,
                102.9,
            ]
        ).__next__,
    )

    assert check_all.main() == 0

    output = capsys.readouterr().out
    assert "PASSED: Backend regression tests in 0.20s" in output
    assert "PASSED: Smoke tests in 0.80s" in output
    assert "PASSED: Dashboard artifact check in 0.30s" in output
    assert "PASSED: Dashboard runtime smoke in 0.50s" in output
    assert "PASSED: Dashboard browser smoke in 0.40s" in output
    assert "All quality gates passed in 2.90s." in output
    assert calls
    assert any(
        "check_dashboard_build_artifacts.py" in " ".join(command)
        for command, _cwd in calls
    )


def test_dashboard_build_artifact_checker_accepts_compatible_bundle(
    tmp_path, capsys
) -> None:
    from scripts import check_dashboard_build_artifacts

    dashboard = tmp_path / "dashboard"
    assets = dashboard / "assets"
    assets.mkdir(parents=True)
    (assets / "index-abc123.js").write_text("window.App = {};", encoding="utf-8")
    (assets / "style-def456.css").write_text("body{}", encoding="utf-8")
    (dashboard / "index.html").write_text(
        """
        <html><head>
          <script defer src="./assets/index-abc123.js"></script>
          <link rel="stylesheet" href="./assets/style-def456.css">
        </head><body><div id="root"></div></body></html>
        """,
        encoding="utf-8",
    )

    assert check_dashboard_build_artifacts.main([str(dashboard)]) == 0

    output = capsys.readouterr().out
    assert "Dashboard build artifacts look compatible" in output


def test_dashboard_build_artifact_checker_rejects_module_graph(
    tmp_path, capsys
) -> None:
    from scripts import check_dashboard_build_artifacts

    dashboard = tmp_path / "dashboard"
    assets = dashboard / "assets"
    assets.mkdir(parents=True)
    (assets / "index-abc123.js").write_text("import './chunk.js';", encoding="utf-8")
    (assets / "chunk-def456.js").write_text("export {};", encoding="utf-8")
    (dashboard / "index.html").write_text(
        """
        <html><head>
          <script type="module" crossorigin src="/src/main.tsx"></script>
          <script type="module" src="./assets/index-abc123.js"></script>
          <link rel="stylesheet" crossorigin href="./assets/style-def456.css">
        </head></html>
        """,
        encoding="utf-8",
    )

    assert check_dashboard_build_artifacts.main([str(dashboard)]) == 1

    output = capsys.readouterr().out
    assert "type=\"module\"" in output
    assert "crossorigin" in output
    assert "/src/main" in output
    assert "expected exactly one JS file in assets" in output


def test_changelog_documents_current_command_set() -> None:
    changelog = _read_text("CHANGELOG.md")
    required_commands = [
        "/memora status",
        "/memora search",
        "/memora forget",
        "/memora rebuild-index",
        "/memora rebuild-graph",
        "/memora webui",
        "/memora summarize",
        "/memora reset",
        "/memora cleanup",
        "/memora help",
    ]
    stale_commands = [
        "`/memora recall`",
        "`/memora stats`",
        "`/memora summary`",
        "`/memora clean`",
    ]

    for command in required_commands:
        assert command in changelog
    for command in stale_commands:
        assert command not in changelog


def test_readmes_and_changelog_document_adaptive_injection_breaking_change() -> None:
    required = {
        "README.md": [
            "Manual",
            "Auto",
            "Hybrid",
            "manual + balanced",
            "30 天",
            "100,000",
        ],
        "README_EN.md": [
            "Manual",
            "Auto",
            "Hybrid",
            "manual + balanced",
            "30 days",
            "100,000",
        ],
        "README_RU.md": [
            "Manual",
            "Auto",
            "Hybrid",
            "manual + balanced",
            "30 дней",
            "100 000",
        ],
        "CHANGELOG.md": [
            "injection_method",
            "breaking",
            "manual + balanced",
            "injection_decisions",
        ],
    }
    for path, phrases in required.items():
        text = _read_text(path)
        for phrase in phrases:
            assert phrase.lower() in text.lower(), f"{path} missing {phrase}"


def test_injection_decision_benchmark_is_file_backed_and_checks_thresholds() -> None:
    source = _read_text("scripts/benchmark_injection_decisions.py")
    for marker in [
        "100_000",
        "TemporaryDirectory",
        "memora.db",
        "median",
        "warmup",
        "SUMMARY_LIMIT_MS",
        "PAGE_LIMIT_MS",
        "CLEANUP_LIMIT_MS",
        "ENQUEUE_LIMIT_MS",
    ]:
        assert marker in source
    assert '":memory:"' not in source


def test_recall_cost_benchmark_covers_each_routing_mode_and_p95() -> None:
    source = _read_text("scripts/benchmark_recall_cost.py") + _read_text(
        "scripts/recall_total_path_benchmark.py"
    )
    for marker in [
        "ManualRoutingAccuracy",
        "AutoRoutingAccuracy",
        "HybridRoutingAccuracy",
        "StrategyDecisionLatency",
        "percentile_95",
        "OrdinaryMemoryCharsP95",
        "LowCostPayloadReduction",
        "validate_cross_profile_metrics",
        "RecallHandler.handle_memory_recall",
        "TOTAL_RECALL_REGRESSION_LIMIT",
        "TotalRecallPathP95",
        "RecordedBaselineP95",
        "TotalRecallPathRegression",
        "--handler-worker",
        "--source-root",
        "subprocess.run",
        "scripts/baselines/recall_total_path.json",
    ]:
        assert marker in source

    baseline = json.loads(
        _read_text("scripts/baselines/recall_total_path.json")
    )
    assert baseline["schema_version"] == 1
    assert baseline["metric"] == "RecallHandler.handle_memory_recall total-path p95"
    assert baseline["scenario"] == "balanced_full_path_with_fixed_retrieval"
    assert baseline["p95_ms"] > 0
    assert baseline["measured_runs"] >= 100
    assert baseline["warmup_runs"] >= 10
    assert baseline["retrieval_delay_ms"] > 0
    assert re.fullmatch(r"[0-9a-f]{40}", baseline["source_commit"])


def test_requirements_cover_mandatory_runtime_dependencies() -> None:
    requirements = {
        line.strip()
        for line in _read_text("requirements.txt").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    required_prefixes = [
        "numpy",
        "pydantic",
        "PyYAML",
        "quart",
        "sqlalchemy",
    ]

    for prefix in required_prefixes:
        assert any(req.startswith(prefix) for req in requirements), (
            f"requirements.txt is missing mandatory runtime dependency: {prefix}"
        )


def test_agent_tool_switches_are_aligned_across_schema_model_and_main() -> None:
    from core.base.config_validator import AgentToolsConfig

    schema = json.loads(_read_text("_conf_schema.json"))
    main_py = _read_text("main.py")

    agent_tools_props = schema["agent_tools"]["items"]
    model_fields = AgentToolsConfig.model_fields
    expected_switches = [
        "enable_recall_tool",
        "enable_memorize_tool",
        "enable_note_read_tools",
        "enable_note_write_tool",
        "enable_knowledge_tools",
        "enable_profile_tools",
        "enable_jargon_tools",
        "enable_affection_tools",
        "enable_social_tools",
        "enable_expression_tools",
    ]

    for switch in expected_switches:
        assert switch in agent_tools_props, f"_conf_schema.json is missing {switch}"
        assert switch in model_fields, f"AgentToolsConfig is missing {switch}"
        assert f"agent_tools.{switch}" in main_py, f"main.py does not read {switch}"

    assert model_fields["enable_note_read_tools"].default is True
    assert model_fields["enable_note_write_tool"].default is False
    assert agent_tools_props["enable_note_read_tools"]["default"] is True
    assert agent_tools_props["enable_note_write_tool"]["default"] is False


def test_security_switches_are_aligned_across_schema_and_model() -> None:
    from core.base.config_validator import SecurityConfig, get_default_config

    schema = json.loads(_read_text("_conf_schema.json"))
    security_props = schema["security"]["items"]
    model_fields = SecurityConfig.model_fields
    defaults = get_default_config()["security"]
    expected = {
        "prompt_protection_enabled": True,
        "sanitize_llm_response": True,
        "guardrails_enabled": True,
        "double_check_enabled": True,
        "wrapper_template_index": 0,
        "strict_mode": False,
    }

    for switch, default in expected.items():
        assert switch in security_props, f"_conf_schema.json is missing security.{switch}"
        assert switch in model_fields, f"SecurityConfig is missing {switch}"
        assert defaults[switch] == default


def test_documented_commands_match_command_endpoints() -> None:
    command_source = _read_text("core/command_endpoints.py")
    commands = re.findall(r'@memora\.command\("([^"]+)"', command_source)
    assert commands, "No /memora commands found in core/command_endpoints.py"

    documented_files = [
        "README.md",
        "README_EN.md",
        "README_RU.md",
        "CHANGELOG.md",
    ]
    stale_patterns = [
        r"/memora\s+recall(?:\s|`|<|$)",
        r"/memora\s+stats(?:\s|`|<|$)",
        r"/memora\s+summary(?:\s|`|<|$)",
        r"/memora\s+clean(?:\s|`|<|$)",
    ]

    for relative_path in documented_files:
        text = _read_text(relative_path)
        for command in commands:
            assert f"/memora {command}" in text, (
                f"{relative_path} is missing documented command: /memora {command}"
            )
        for pattern in stale_patterns:
            assert re.search(pattern, text) is None, (
                f"{relative_path} still documents stale command pattern: {pattern}"
            )


def test_runtime_dependency_imports_are_declared_or_allowlisted() -> None:
    requirements = {
        line.strip().split(">=", 1)[0].split("==", 1)[0].split("[", 1)[0]
        for line in _read_text("requirements.txt").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    stdlib_modules = {
        "__future__", "abc", "argparse", "array", "ast", "asyncio", "base64", "bisect",
        "collections", "concurrent", "contextlib", "copy", "csv", "dataclasses",
        "contextvars", "datetime", "difflib", "enum", "fnmatch", "functools", "gc", "hashlib", "heapq",
        "html", "importlib", "inspect", "io", "itertools", "json", "logging",
        "math", "numbers", "operator", "os", "pathlib", "pickle", "platform",
        "queue", "random", "re", "secrets", "shlex", "shutil", "sqlite3",
        "statistics", "string", "subprocess", "sys", "tempfile", "textwrap",
        "threading", "time", "traceback", "types", "typing", "unicodedata",
        "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
    }
    local_or_optional = {
        "astrbot",
        "core",
        "main",
        "prometheus_client",
        "apscheduler",
        "cachetools",
    }
    package_name_map = {
        "yaml": "PyYAML",
        "PIL": "Pillow",
        "faiss": "faiss-cpu",
    }

    local_modules = {path.stem for path in (REPO_ROOT / "core").rglob("*.py")}
    local_modules.update(
        directory.name
        for directory in (REPO_ROOT / "core").iterdir()
        if directory.is_dir()
    )
    local_modules.update({"main"})

    imported_roots: set[str] = set()
    scan_roots = [REPO_ROOT / "core", REPO_ROOT / "main.py"]
    for root in scan_roots:
        files = [root] if root.is_file() else list(root.rglob("*.py"))
        for file_path in files:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_roots.add(alias.name.split(".", 1)[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])

    missing: set[str] = set()
    for root_name in imported_roots:
        if (
            root_name in stdlib_modules
            or root_name in local_or_optional
            or root_name in local_modules
        ):
            continue
        requirement_name = package_name_map.get(root_name, root_name)
        if requirement_name not in requirements:
            missing.add(requirement_name)

    assert not missing, f"requirements.txt is missing imported runtime dependencies: {sorted(missing)}"
