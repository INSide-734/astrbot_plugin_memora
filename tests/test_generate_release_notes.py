"""发布说明生成脚本测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.generate_release_notes import ReleaseNotesError, render_release_notes


def _write_fixture(repo_root: Path, *, valid_checksums: bool = True) -> Path:
    """创建最小的元数据、模板和两个 ZIP 测试输入。"""
    (repo_root / "metadata.yaml").write_text(
        "name: astrbot_plugin_memora\nversion: 2.3.4\n",
        encoding="utf-8",
    )
    (repo_root / "template.md").write_text(
        "${package_name} ${version} ${release_tag} ${release_kind}\n"
        "${runtime_filename} ${runtime_size} ${runtime_sha256}\n"
        "${source_filename} ${source_size} ${source_sha256}\n"
        "${changelog}\n"
        "${commit_sha}\n",
        encoding="utf-8",
    )
    (repo_root / "CHANGELOG.md").write_text(
        "## [2.3.4] — 2026-07-27\n\n### 新增\n\n- 新增发布说明生成。\n\n"
        "## [2.3.3] — 2026-07-26\n\n- 旧版本。\n",
        encoding="utf-8",
    )
    dist = repo_root / "dist"
    dist.mkdir()
    artifacts = {
        "astrbot_plugin_memora-2.3.4-runtime.zip": b"runtime",
        "astrbot_plugin_memora-2.3.4-source.zip": b"source",
    }
    checksums: list[str] = []
    for filename, content in artifacts.items():
        path = dist / filename
        path.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        if not valid_checksums:
            checksum = "0" * 64
        checksums.append(f"{checksum} *{filename}")
    (dist / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return dist / "SHA256SUMS.txt"


def test_render_release_notes_uses_template_and_verified_artifacts(
    tmp_path: Path,
) -> None:
    """脚本必须把已校验产物信息注入模板。"""
    checksum_path = _write_fixture(tmp_path)

    output = render_release_notes(
        repo_root=tmp_path,
        template_path=Path("template.md"),
        checksum_path=checksum_path.relative_to(tmp_path),
        changelog_path=Path("CHANGELOG.md"),
        output_path=Path("dist/release-notes.md"),
        commit_sha="a" * 40,
        release_type="pre-release",
    )

    content = output.read_text(encoding="utf-8")
    assert "astrbot_plugin_memora 2.3.4 v2.3.4 预发布" in content
    assert "astrbot_plugin_memora-2.3.4-runtime.zip" in content
    assert hashlib.sha256(b"runtime").hexdigest() in content
    assert "新增发布说明生成" in content
    assert "a" * 40 in content


def test_render_release_notes_rejects_checksum_mismatch(tmp_path: Path) -> None:
    """脚本必须拒绝清单与实际 ZIP 内容不一致的发布输入。"""
    checksum_path = _write_fixture(tmp_path, valid_checksums=False)

    with pytest.raises(ReleaseNotesError, match="哈希不匹配"):
        render_release_notes(
            repo_root=tmp_path,
            template_path=Path("template.md"),
            checksum_path=checksum_path.relative_to(tmp_path),
            changelog_path=Path("CHANGELOG.md"),
            output_path=Path("dist/release-notes.md"),
            commit_sha="b" * 40,
            release_type="release",
        )


def test_render_release_notes_rejects_missing_current_changelog_section(
    tmp_path: Path,
) -> None:
    """脚本必须拒绝缺少当前版本变更段落的 CHANGELOG。"""
    checksum_path = _write_fixture(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "## [2.3.3]\n\n- 只有旧版本记录。\n", encoding="utf-8"
    )

    with pytest.raises(ReleaseNotesError, match="缺少版本 2.3.4"):
        render_release_notes(
            repo_root=tmp_path,
            template_path=Path("template.md"),
            checksum_path=checksum_path.relative_to(tmp_path),
            changelog_path=Path("CHANGELOG.md"),
            output_path=Path("dist/release-notes.md"),
            commit_sha="c" * 40,
            release_type="release",
        )
