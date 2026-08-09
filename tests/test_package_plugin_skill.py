"""插件 runtime 包的 Skill 打包与安装发现契约测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import package_plugin

PLUGIN_SKILL_PATH = Path(package_plugin.PLUGIN_SKILL_PATH)
PLUGIN_SKILL_AGENT_PATH = Path("skills/memora-recall-and-memorize/agents/openai.yaml")


def _write(path: Path, content: str = "内容") -> None:
    """创建测试文件及其父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_runtime_archive(tmp_path: Path) -> Path:
    """构造包含插件 Skill 源文件的最小 runtime 归档。"""
    source_root = tmp_path / "repo"
    staging_root = tmp_path / "staging"
    archive_path = tmp_path / "memora-runtime.zip"

    for relative in package_plugin.RUNTIME_ROOT_FILES:
        _write(source_root / relative)
    _write(
        source_root / "metadata.yaml",
        "\n".join(
            (
                "name: astrbot_plugin_memora",
                "desc: 测试插件",
                "version: 1.1.0",
                "author: Memora",
                "",
            )
        ),
    )
    _write(source_root / "core" / "runtime.py")
    _write(source_root / "static" / "placeholder.txt")
    _write(source_root / "pages" / "dashboard" / "index.html")
    _write(source_root / "pages" / "dashboard" / "assets" / "bundle.js")
    for locale in package_plugin.PAGE_I18N_LOCALES:
        _write(source_root / ".astrbot-plugin" / "i18n" / f"{locale}.json", "{}")
    _write(
        source_root / PLUGIN_SKILL_PATH,
        "---\n"
        "name: memora-recall-and-memorize\n"
        "description: 测试插件内置记忆 Skill。\n"
        "---\n"
        "# Memora\n",
    )
    _write(source_root / PLUGIN_SKILL_AGENT_PATH, "interface: {}\n")

    package_plugin.copy_runtime_files(
        source_root,
        staging_root,
        "astrbot_plugin_memora",
    )
    package_plugin.create_zip(staging_root, archive_path)
    return archive_path


def test_runtime_archive_includes_complete_plugin_skill_tree(tmp_path: Path) -> None:
    """runtime ZIP 应保留 AstrBot 可发现的 Skill 路径及伴随资源。"""
    archive_path = _build_runtime_archive(tmp_path)

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    prefix = "astrbot_plugin_memora/"
    assert f"{prefix}{PLUGIN_SKILL_PATH.as_posix()}" in names
    assert f"{prefix}{PLUGIN_SKILL_AGENT_PATH.as_posix()}" in names


def test_runtime_archive_rejects_missing_plugin_skill(tmp_path: Path) -> None:
    """runtime ZIP 校验应拒绝缺少插件 Skill 的候选包。"""
    archive_path = _build_runtime_archive(tmp_path)
    stripped_archive = tmp_path / "memora-runtime-without-skill.zip"

    with (
        zipfile.ZipFile(archive_path) as source,
        zipfile.ZipFile(
            stripped_archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target,
    ):
        for info in source.infolist():
            if info.filename.endswith(PLUGIN_SKILL_PATH.as_posix()):
                continue
            target.writestr(info, source.read(info.filename))

    with pytest.raises(package_plugin.PackageError, match="插件 Skill"):
        package_plugin.validate_archive(
            stripped_archive,
            "runtime",
            "astrbot_plugin_memora",
        )


def test_runtime_archive_skill_is_discoverable_after_astrbot_install(
    tmp_path: Path,
) -> None:
    """AstrBot 4.27.2 解包安装后应发现只读的插件来源 Skill。"""
    archive_path = _build_runtime_archive(tmp_path)
    plugins_root = tmp_path / "plugins"
    skills_root = tmp_path / "global-skills"
    data_root = tmp_path / "data"
    plugins_root.mkdir()
    data_root.mkdir()

    probe = """
import json
import sys
from pathlib import Path

import astrbot.core.skills.skill_manager as skill_manager_module
from astrbot.core.skills.skill_manager import SkillManager
from astrbot.core.star.updater import _PluginUpdater

archive_path = Path(sys.argv[1])
plugins_root = Path(sys.argv[2])
skills_root = Path(sys.argv[3])
data_root = Path(sys.argv[4])
plugin_root = plugins_root / "astrbot_plugin_memora"

skill_manager_module.get_astrbot_data_path = lambda: str(data_root)
_PluginUpdater.__new__(_PluginUpdater)._extract_plugin_archive(
    str(archive_path),
    str(plugin_root),
)
skills = SkillManager(
    skills_root=str(skills_root),
    plugins_root=str(plugins_root),
).list_skills()
matched = [skill for skill in skills if skill.name == "memora-recall-and-memorize"]
assert len(matched) == 1
skill = matched[0]
print(
    "MEMORA_SKILL_JSON="
    + json.dumps(
        {
            "name": skill.name,
            "path": skill.path,
            "source_type": skill.source_type,
            "source_label": skill.source_label,
            "plugin_name": skill.plugin_name,
            "readonly": skill.readonly,
        },
        ensure_ascii=False,
    )
)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            str(archive_path),
            str(plugins_root),
            str(skills_root),
            str(data_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("MEMORA_SKILL_JSON=")
    )
    discovered = json.loads(result_line.removeprefix("MEMORA_SKILL_JSON="))
    expected_skill_path = (
        plugins_root / "astrbot_plugin_memora" / PLUGIN_SKILL_PATH
    ).as_posix()
    assert discovered == {
        "name": "memora-recall-and-memorize",
        "path": expected_skill_path,
        "source_type": "plugin",
        "source_label": "astrbot_plugin_memora",
        "plugin_name": "astrbot_plugin_memora",
        "readonly": True,
    }
