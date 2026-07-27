"""Run the project's local quality gates in a single command."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from shutil import which

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = REPO_ROOT / "pages" / "dashboard"


def _resolve_command(command: str) -> str:
    if Path(command).is_absolute():
        return command

    resolved = which(command)
    if resolved:
        return resolved

    if sys.platform.startswith("win"):
        resolved = which(f"{command}.cmd") or which(f"{command}.exe")
        if resolved:
            return resolved

    raise FileNotFoundError(f"Executable not found: {command}")


def _run_step(title: str, command: list[str], *, cwd: Path | None = None) -> int:
    workdir = cwd or REPO_ROOT
    resolved_command = [_resolve_command(command[0]), *command[1:]]
    print(f"\n==> {title}")
    print(f"cwd: {workdir}")
    print("cmd:", " ".join(resolved_command))
    started_at = time.perf_counter()
    completed = subprocess.run(resolved_command, cwd=workdir)
    elapsed = time.perf_counter() - started_at
    if completed.returncode == 0:
        print(f"PASSED: {title} in {elapsed:.2f}s")
    else:
        print(f"FAILED: {title} in {elapsed:.2f}s (exit code {completed.returncode})")
    return completed.returncode


def main() -> int:
    started_at = time.perf_counter()
    steps: list[tuple[str, list[str], Path | None]] = []

    schema_validator = REPO_ROOT / "scripts" / "validate_conf_schema.py"
    if schema_validator.exists():
        steps.append(
            (
                "Validate config schema",
                [sys.executable, str(schema_validator)],
                REPO_ROOT,
            )
        )

    steps.extend(
        [
            (
                "Backend regression tests",
                [sys.executable, "-m", "pytest", "tests", "-q"],
                REPO_ROOT,
            ),
            (
                "Smoke tests",
                [sys.executable, "scripts/run_smoke.py", "-q"],
                REPO_ROOT,
            ),
            (
                "Dashboard production build",
                ["npm", "run", "build"],
                DASHBOARD_ROOT,
            ),
            (
                "Dashboard artifact check",
                [
                    sys.executable,
                    "scripts/check_dashboard_build_artifacts.py",
                ],
                REPO_ROOT,
            ),
            (
                "Dashboard frontend tests",
                ["npm", "run", "test"],
                DASHBOARD_ROOT,
            ),
            (
                "Dashboard runtime smoke",
                ["npm", "run", "smoke:runtime"],
                DASHBOARD_ROOT,
            ),
            (
                "Dashboard browser smoke",
                ["npm", "run", "smoke:browser"],
                DASHBOARD_ROOT,
            ),
        ]
    )

    for title, command, cwd in steps:
        exit_code = _run_step(title, command, cwd=cwd)
        if exit_code != 0:
            elapsed = time.perf_counter() - started_at
            print(f"\nQuality gates failed after {elapsed:.2f}s.")
            return exit_code

    elapsed = time.perf_counter() - started_at
    print(f"\nAll quality gates passed in {elapsed:.2f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
