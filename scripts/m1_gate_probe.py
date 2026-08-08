"""M1 removed-edge 运行探针：隔离域执行 + 受信观察者证据。

候选代码只在隔离容器域中执行（``docker run``：``--network none``、
``--read-only``、``--tmpfs``、非 root、no-new-privileges，输入只读挂载，
无任何 token/secrets 环境、无 docker socket 暴露、无共享可写路径）；
最终证据文件由隔离域**外**的受信观察者（本模块）从容器 stdout 生成，
并完整绑定 base/head/tree/contract/version/精确 edges/exit/evidence
hash。受信 gate job（m1_gate_analysis.run_gate）复核该绑定。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from m1_gate_analysis import git_materialize_tree
    from m1_gate_core import (
        M1_PROBE_VERSION,
        PROBE_IMAGE,
        SCHEMA_VERSION,
        M1GateError,
        _decode_utf8,
        _load_contract,
        _validate_provenance,
        evidence_sha256,
    )
    from m1_gate_report import _atomic_write, _ensure_empty_report_dir
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.m1_gate_analysis import (  # type: ignore[no-redef]
        git_materialize_tree,
    )
    from scripts.m1_gate_core import (  # type: ignore[no-redef]
        M1_PROBE_VERSION,
        PROBE_IMAGE,
        SCHEMA_VERSION,
        M1GateError,
        _decode_utf8,
        _load_contract,
        _validate_provenance,
        evidence_sha256,
    )
    from scripts.m1_gate_report import (  # type: ignore[no-redef]
        _atomic_write,
        _ensure_empty_report_dir,
    )


_PROBE_TIMEOUT = 60.0
# 观察者进程（含 docker CLI 与探针子进程）环境：完全清除 token/secrets
# （无 GITHUB_*/RUNNER_*/CI 变量、无工作区/运行元数据）。
_SCRUBBED_ENV = {"PATH": "/usr/bin:/bin"}

# 隔离域 harness（stdlib）：导入 source 包子树后报告 target 模块族是否
# 被加载。候选导入期间 stdout 被吞掉，容器 stdout 只有 harness 自己的
# JSON 报告（候选无法污染证据通道）。结果 {"ok": bool, ...} 单行 JSON。
_PROBE_HARNESS = """\
import io, importlib, json, os, pkgutil, sys
root, source, target = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
real_stdout = sys.stdout
sys.stdout = io.StringIO()
try:
    pkg = importlib.import_module(source)
    if hasattr(pkg, "__path__"):
        for info in pkgutil.walk_packages(pkg.__path__, prefix=source + "."):
            importlib.import_module(info.name)
    loaded = sorted(
        m for m in sys.modules if m == target or m.startswith(target + ".")
    )
    outcome = {"ok": True, "loaded_targets": loaded, "uid": os.geteuid()}
except BaseException as exc:
    outcome = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
finally:
    sys.stdout = real_stdout
print(json.dumps(outcome))
"""


def docker_probe_command(
    candidate_root: Path,
    harness_path: Path,
    source: str,
    target: str,
) -> list[str]:
    """构造隔离域 docker 命令（资源边界 + network none/只读/非 root）。

    env 完全不继承宿主（docker run 默认只透传 --env/镜像 ENV）；容器无
    docker socket、无网络接口；cgroup/pids/CPU/memory/tmpfs/FD 均有上限，
    全部 capabilities 剥离。此实现是受保护基础设施的参考 producer，
    bootstrap 的 gate 不执行它，也不信任其解释器内证据。
    """

    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "65534:65534",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        "128",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1",
        "--ulimit",
        "nofile=64:64",
        "--read-only",
        "--tmpfs",
        "/tmp:size=16m",
        "--volume",
        f"{candidate_root}:/candidate:ro",
        "--volume",
        f"{harness_path.parent}:/harness:ro",
        PROBE_IMAGE,
        "python3",
        "-I",
        "-B",
        "/harness/" + harness_path.name,
        "/candidate",
        source,
        target,
    ]


_PROBE_OUTPUT_CAP = 64 * 1024


def _run_docker(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """以清除环境执行 docker（timeout/OSError/输出上限统一 M1GateError）。"""

    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            env=_SCRUBBED_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        raise M1GateError(f"探针容器超时: {command[:3]}") from exc
    except OSError as exc:
        raise M1GateError(f"无法执行 docker: {exc}") from exc
    if (
        len(completed.stdout) > _PROBE_OUTPUT_CAP
        or len(completed.stderr) > _PROBE_OUTPUT_CAP
    ):
        raise M1GateError("探针容器输出超上限（fail-closed）")
    return completed


def _run_edge_probe(
    candidate_root: Path,
    source: str,
    target: str,
    harness_path: Path,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]] | None = None,
) -> dict[str, Any]:
    """对单条 removed edge 运行隔离探针并解析观察者捕获的 stdout。"""

    result: dict[str, Any] = {
        "edge": [source, target],
        "verdict": "inconclusive",
        "loaded_targets": [],
        "error": None,
    }
    command = docker_probe_command(candidate_root, harness_path, source, target)
    runner = runner or _run_docker
    completed = runner(command)
    if completed.returncode != 0:
        stderr = _decode_utf8(completed.stderr, label="探针容器 stderr")[:400]
        result["error"] = f"探针容器退出码 {completed.returncode}: {stderr}"
        return result
    try:
        payload = json.loads(_decode_utf8(completed.stdout, label="探针输出"))
    except (json.JSONDecodeError, M1GateError):
        result["error"] = "探针输出不可解析"
        return result
    if not payload.get("ok"):
        result["error"] = payload.get("error") or "候选导入失败"
        return result
    loaded = [str(item) for item in payload.get("loaded_targets", [])]
    if loaded:
        result["verdict"] = "edge_present"
        result["loaded_targets"] = loaded
    else:
        result["verdict"] = "confirmed_removed"
    return result


def run_probe(
    root: Path,
    base_commit: str,
    pr_head_commit: str,
    candidate_commit: str,
    report_dir: Path,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]] | None = None,
    attestation_nonce: str = "",
) -> int:
    """参考 producer：完整对象历史上运行隔离域探针并生成候选证据。

    退出码：0=全部确认移除；1=发现仍在运行时加载（policy）；
    2=无法形成可信裁决（inconclusive/工具错误）。本实现是受保护
    attestor 的参考（隔离域/资源边界），**不含签发私钥，其输出永远
    无法通过 verifier 的 pinned 公钥验签**；生产 attestation 必须由
    外部受保护 attestor 用仓库外私钥签名。
    """

    root = root.resolve()
    report_dir = report_dir.resolve()
    try:
        _ensure_empty_report_dir(report_dir)
    except M1GateError as exc:
        print(f"M1 probe error: {exc}", file=sys.stderr)
        return 2
    try:
        provenance = _validate_provenance(
            root, base_commit, pr_head_commit, candidate_commit
        )
        base_tree = provenance["base_tree"]
        candidate_tree = provenance["candidate_tree"]
        contract = _load_contract(root, base_tree)
        edges: list[list[str]] = []
        contract_path = None
        contract_oid = None
        if contract is not None:
            contract_path = contract.get("_contract_path")
            contract_oid = contract.get("_contract_oid")
            for edge in contract.get("expected_removed_edges", []):
                if not isinstance(edge, list) or len(edge) != 2:
                    raise M1GateError(f"contract 边格式无效: {edge!r}")
                edges.append([str(edge[0]), str(edge[1])])
        with tempfile.TemporaryDirectory(prefix=".m1-probe-") as probe_text:
            candidate_root = Path(probe_text)
            # TemporaryDirectory 默认 0700，容器 nobody(65534) 无法读取挂载；
            # 显式放开读/执行位（隔离域内仍只读挂载，无写路径）。
            os.chmod(candidate_root, 0o755)
            git_materialize_tree(root, candidate_tree, candidate_root)
            harness = report_dir / "_probe_harness.py"
            harness.write_text(_PROBE_HARNESS, encoding="utf-8")
            os.chmod(report_dir, 0o755)
            edge_results = [
                _run_edge_probe(candidate_root, source, target, harness, runner=runner)
                for source, target in edges
            ]
        verdicts = {item["verdict"] for item in edge_results}
        if "edge_present" in verdicts:
            probe_exit = 1
        elif "inconclusive" in verdicts:
            probe_exit = 2
        else:
            probe_exit = 0
        evidence_hash = evidence_sha256(edges, edge_results, probe_exit)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "probe_version": M1_PROBE_VERSION,
            "attestation": "reference_producer",
            "nonce": attestation_nonce,
            "image": PROBE_IMAGE,
            "base_commit": base_commit,
            "pr_head_commit": pr_head_commit,
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "contract_path": contract_path,
            "contract_oid": contract_oid,
            "expected_edges": edges,
            "edges_checked": edge_results,
            "all_edges_confirmed": probe_exit == 0,
            "exit_code": probe_exit,
            "environment": {
                "isolated_domain": "docker",
                "network": "none",
                "read_only_inputs": True,
                "non_root": True,
                "no_new_privileges": True,
                "secrets": False,
            },
            "evidence_sha256": evidence_hash,
        }
        _atomic_write(report_dir / "probe-result.json", payload)
        return probe_exit
    except M1GateError as exc:
        try:
            _atomic_write(
                report_dir / "probe-result.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "probe_version": M1_PROBE_VERSION,
                    "attestation": "reference_producer",
                    "nonce": attestation_nonce,
                    "image": PROBE_IMAGE,
                    "base_commit": base_commit,
                    "pr_head_commit": pr_head_commit,
                    "candidate_commit": candidate_commit,
                    "error": str(exc),
                    "exit_code": 2,
                },
            )
        except OSError:
            pass
        print(f"M1 probe error: {exc}", file=sys.stderr)
        return 2
