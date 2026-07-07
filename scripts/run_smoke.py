"""Run the Memora smoke test suite."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from shutil import which

SMOKE_TARGETS = [
    "tests/integration/test_pipeline_ingest.py",
    "tests/integration/test_pipeline_event.py",
    "tests/integration/test_pipeline_retrieval.py",
    "tests/integration/test_pipeline_graph.py",
    "tests/integration/test_pipeline_lifecycle.py",
]


def _pytest_command(target: str, args: list[str]) -> list[str]:
    if which("uv"):
        return ["uv", "run", "pytest", target, *args]
    return [sys.executable, "-m", "pytest", target, *args]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    plugin_root = Path(__file__).resolve().parents[1]
    missing = [
        target for target in SMOKE_TARGETS
        if not (plugin_root / target.split("::", 1)[0]).exists()
    ]
    if missing:
        print("Missing smoke targets:")
        for target in missing:
            print(f"- {target}")
        return 2

    print("Running smoke suite:")
    started_at = time.perf_counter()
    passed = 0
    failed = 0
    first_failure = 0

    for target in SMOKE_TARGETS:
        target_started_at = time.perf_counter()
        cmd = _pytest_command(target, args)
        print(f"- {target}")
        completed = subprocess.run(cmd, cwd=plugin_root)
        elapsed = time.perf_counter() - target_started_at
        if completed.returncode == 0:
            passed += 1
            print(f"PASS {target} ({elapsed:.2f}s)")
        else:
            failed += 1
            if first_failure == 0:
                first_failure = completed.returncode
            print(f"FAIL {target} ({elapsed:.2f}s, exit code {completed.returncode})")

    total_elapsed = time.perf_counter() - started_at
    print(f"Smoke summary: {passed} passed, {failed} failed")
    print(f"Total smoke time: {total_elapsed:.2f}s")
    return first_failure


if __name__ == "__main__":
    raise SystemExit(main())
