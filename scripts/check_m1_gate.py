"""M1 阶段受信双树 gate 的 CLI 入口。

参数解析、SHA 校验、顶层退出码收口（任何 repository/report-dir 路径
解析、report-dir/envelope 失败都稳定 exit 2，不外泄 traceback）。
attestation 以仓库内 pinned 公钥验签（签发私钥只存在于外部受保护
attestor）。gate 裁决见 `m1_gate_analysis.run_gate`；报告发布/consumer
见 `m1_gate_report`。

退出码语义：
  0 = 全部不变量成立（或 governance-only PR，无生产 cut）
  1 = 已完成可信分析，但候选被 policy 阻断
  2 = 无法形成可信裁决（工具/对象/契约/超时/IO/报告目录/路径解析错误）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from m1_gate_analysis import run_gate
    from m1_gate_core import M1GateError, _full_sha
    from m1_gate_report import _write_error_envelope
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.m1_gate_analysis import (  # type: ignore[no-redef]
        run_gate,
    )
    from scripts.m1_gate_core import (  # type: ignore[no-redef]
        M1GateError,
        _full_sha,
    )
    from scripts.m1_gate_report import (  # type: ignore[no-redef]
        _write_error_envelope,
    )


def build_parser() -> argparse.ArgumentParser:
    """构造 M1 gate 命令行解析器（不接受 --files/--write-baseline/--policy-update）。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=None, help="仓库根目录")
    parser.add_argument("--base-commit", required=True, help="PR base 提交 SHA")
    parser.add_argument("--pr-head-commit", required=True, help="PR head 提交 SHA")
    parser.add_argument(
        "--candidate-commit", required=True, help="merge candidate 提交 SHA"
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/m1"),
        help="报告输出目录（受信 run_id/run_attempt 唯一空目录）",
    )
    parser.add_argument(
        "--probe-result",
        type=Path,
        default=None,
        help="外部受信 attestation 文件路径（verifier-only bootstrap：无外部 "
        "attestation 时 production 必须 exit 2）",
    )
    parser.add_argument(
        "--attestation-nonce",
        default="",
        help="本裁决的 run nonce（受保护 producer 经受保护传输获取；attestation "
        "必须回显该 nonce）",
    )
    parser.add_argument("--run-id", default=None, help="GitHub run_id（受信来源）")
    parser.add_argument(
        "--run-attempt", default=None, help="GitHub run_attempt（受信来源）"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行 M1 gate 主流程（最外层收口路径解析与一切异常为 exit 2）。"""

    args = build_parser().parse_args(argv)
    report_dir: Path | None = None
    try:
        root = (args.repository or Path(__file__).resolve().parents[1]).resolve()
        report_dir = args.report_dir.resolve()
        base_commit = _full_sha(args.base_commit, label="base")
        pr_head_commit = _full_sha(args.pr_head_commit, label="PR head")
        candidate_commit = _full_sha(args.candidate_commit, label="candidate")
        return run_gate(
            root,
            base_commit,
            pr_head_commit,
            candidate_commit,
            report_dir,
            probe_result=args.probe_result,
            attestation_nonce=args.attestation_nonce,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        )
    except M1GateError as exc:
        return _fail(report_dir, str(exc), args=args)
    except Exception as exc:  # 顶层兜底：路径解析/任何未预期异常都稳定 exit 2
        return _fail(report_dir, f"未预期错误: {type(exc).__name__}: {exc}", args=args)


def _fail(
    report_dir: Path | None,
    error: str,
    *,
    args: argparse.Namespace,
) -> int:
    """顶层收口：写 error envelope（尽力而为，绝不外泄异常）并返回 exit 2。"""

    if report_dir is None:
        report_dir = Path(args.report_dir)
    _write_error_envelope(
        report_dir, error, 2, run_id=args.run_id, run_attempt=args.run_attempt
    )
    print(f"M1 gate error: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
