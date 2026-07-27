"""CI 发布信息契约测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "unified-release.yml"


def test_release_workflow_publishes_artifact_details_and_checksums() -> None:
    """发布页与步骤摘要必须说明产物用途并提供校验信息。"""
    workflow: dict[str, Any] = yaml.load(
        WORKFLOW_PATH.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    build_job = workflow["jobs"]["build-and-package"]
    build_commands = "\n".join(
        step.get("run", "") for step in build_job["steps"] if "run" in step
    )
    assert "sha256sum *.zip > SHA256SUMS.txt" in build_commands
    assert "scripts/generate_release_notes.py" in build_commands
    assert ".github/release-notes-template.md" in build_commands
    assert "--changelog CHANGELOG.md" in build_commands
    assert "dist/release-notes.md" in build_commands
    assert "GITHUB_STEP_SUMMARY" in build_commands

    upload_step = next(
        step
        for step in build_job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
        and step.get("with", {}).get("if-no-files-found") == "error"
    )
    assert "dist/*.zip" in upload_step["with"]["path"]
    assert "dist/SHA256SUMS.txt" in upload_step["with"]["path"]
    assert "dist/release-notes.md" in upload_step["with"]["path"]

    release_job = workflow["jobs"]["create-release"]
    release_action = next(
        step for step in release_job["steps"] if step.get("id") == "release"
    )
    assert release_action["with"]["body_path"] == "dist/release-notes.md"
    assert "body" not in release_action["with"]
    assert "GITHUB_STEP_SUMMARY" in "\n".join(
        step.get("run", "") for step in release_job["steps"] if "run" in step
    )
