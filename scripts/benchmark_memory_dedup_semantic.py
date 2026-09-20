"""离线语义近重复阈值校准 CLI：只读匿名 fixture，输出脱敏聚合报告。

```bash
uv run --locked python scripts/benchmark_memory_dedup_semantic.py \
  --fixture tests/fixtures/dedup_semantic_fixture.json
uv run --locked python scripts/benchmark_memory_dedup_semantic.py \
  --fixture authorized.json --output report.json
```

安全边界：只读取 `--fixture`（匿名/授权样本），只在显式 `--output` 时写该
路径，从不触碰生产数据库、插件运行时或 Provider；报告先过隐私 canary，
违规时 fail-closed 并以退出码 3 结束，不输出任何报告。退出码：0 证据门通过、
1 拒绝/证据不足、2 用法或输入错误、3 privacy canary 违规。

语义分数由授权离线运行预计算并写入 fixture 的 `semantic_score`；本脚本不做
embedding 调用，因此可以在没有 AstrBot 实例的环境中运行（`--help` 亦然）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.features.evaluation.application.memory_dedup_evidence import (  # noqa: E402
    LOAD_REASONS,
    SEMANTIC_THRESHOLD_GRID,
    build_calibration_report,
    check_report_privacy,
    forbidden_values_from_payload,
    load_calibration_fixture,
)

EXIT_OK = 0
EXIT_GATE_REJECTED = 1
EXIT_USAGE = 2
EXIT_PRIVACY_VIOLATION = 3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析参数；fixture 必填，output 只在显式给出时写入。"""

    parser = argparse.ArgumentParser(
        prog="benchmark_memory_dedup_semantic.py",
        description=(
            "离线语义近重复阈值校准：只读匿名 fixture，输出聚合计数、比率与"
            "预注册阈值网格的证据门结论。"
        ),
        epilog=(
            "退出码：0 证据门通过；1 拒绝或证据不足；2 用法/输入错误；"
            "3 privacy canary 违规（不输出报告）。"
        ),
    )
    parser.add_argument(
        "--fixture",
        required=True,
        help="匿名/授权 fixture JSON 路径（只读，不修改）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="报告输出路径；省略时只打印到 stdout，绝不写其他位置",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="即使给出 --output 也不写文件，只把报告打印到 stdout",
    )
    parser.add_argument(
        "--now-ms",
        type=int,
        default=None,
        help="报告时间戳（Unix 毫秒）；省略时用当前时间，便于可复现证据",
    )
    return parser.parse_args(argv)


def _read_fixture(path: Path) -> dict[str, Any] | None:
    """读取并解析 fixture；坏路径/坏 JSON 返回 ``None``。"""

    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _now_ms(explicit: int | None) -> int:
    """返回报告时间戳；显式值必须为非负整数。"""

    if explicit is not None:
        return explicit
    import time

    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> int:
    """执行一次校准；任何失败都返回稳定退出码而不是伪成功报告。"""

    args = _parse_args(argv)
    if args.now_ms is not None and args.now_ms < 0:
        print("error: --now-ms must be >= 0", file=sys.stderr)
        return EXIT_USAGE
    payload = _read_fixture(Path(args.fixture))
    if payload is None:
        print("error: fixture is missing or not a JSON object", file=sys.stderr)
        return EXIT_USAGE
    samples, reason = load_calibration_fixture(payload)
    if not samples:
        if reason not in LOAD_REASONS:
            reason = "unknown_key"
        print(f"error: invalid fixture ({reason})", file=sys.stderr)
        return EXIT_USAGE
    forbidden = forbidden_values_from_payload(payload)
    report = build_calibration_report(
        samples,
        grid=SEMANTIC_THRESHOLD_GRID,
        now_ms=_now_ms(args.now_ms),
        forbidden_values=forbidden,
    )
    violations = check_report_privacy(report, forbidden)
    if violations or report["privacy_canary"]["violations"]:
        # 只输出违规路径，不输出违规值本身。
        print(
            "error: privacy canary rejected the report "
            f"({len(violations) or report['privacy_canary']['violations']} violation(s))",
            file=sys.stderr,
        )
        return EXIT_PRIVACY_VIOLATION
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.dry_run or not args.output:
        print(serialized)
    else:
        output_path = Path(args.output)
        try:
            output_path.write_text(serialized + "\n", encoding="utf-8")
        except OSError as error:
            print(
                f"error: cannot write report ({error.__class__.__name__})",
                file=sys.stderr,
            )
            return EXIT_USAGE
        print(
            f"report written: {output_path} gate={report['gate']['status']} "
            f"reason={report['gate']['reason']} "
            f"samples={report['samples']['total']} "
            f"recommended_threshold={report['recommended_threshold']}"
        )
    return EXIT_OK if report["gate"]["status"] == "pass" else EXIT_GATE_REJECTED


if __name__ == "__main__":
    raise SystemExit(main())
