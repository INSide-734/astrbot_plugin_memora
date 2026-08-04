"""运行真实 AstrBot 黑盒测试的稳定命令入口。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_TARGETS = {"pr": ["runtime_tests/test_bootstrap.py"]}


def main(argv: Sequence[str] | None = None) -> int:
    """解析首期测试档位，并把其余参数原样传递给 pytest。"""
    parser = argparse.ArgumentParser(description="运行 AstrBot 黑盒测试")
    parser.add_argument(
        "--profile",
        choices=PROFILE_TARGETS,
        default="pr",
        help="选择黑盒测试档位（默认：pr）",
    )
    arguments, pytest_arguments = parser.parse_known_args(argv)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *PROFILE_TARGETS[arguments.profile],
        *pytest_arguments,
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, shell=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
