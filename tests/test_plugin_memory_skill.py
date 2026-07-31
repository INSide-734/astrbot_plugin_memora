"""验证插件内置长期记忆 Skill 的发现与安全契约。"""

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT_ROOT / "skills" / "memora-recall-and-memorize"
SKILL_PATH = SKILL_DIR / "SKILL.md"


def _load_skill() -> tuple[dict[str, object], str]:
    """读取 Skill frontmatter 与正文，并校验基础文件格式。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    match = re.fullmatch(
        r"---\r?\n(?P<frontmatter>.*?)\r?\n---\r?\n(?P<body>.*)",
        content,
        flags=re.DOTALL,
    )
    assert match is not None, "SKILL.md 必须包含 YAML frontmatter 与非空正文"

    metadata = yaml.safe_load(match.group("frontmatter"))
    assert isinstance(metadata, dict)
    return metadata, match.group("body").strip()


def test_plugin_memory_skill_is_discoverable_by_astrbot() -> None:
    """插件应按 AstrBot 约定提供名称一致且可发现的 Skill。"""
    assert SKILL_PATH.is_file()
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", SKILL_DIR.name)

    metadata, body = _load_skill()

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == SKILL_DIR.name
    assert isinstance(metadata["description"], str)
    assert metadata["description"].strip()
    assert body


def test_plugin_memory_skill_uses_existing_tools_and_safe_write_boundary() -> None:
    """Skill 应只编排现有记忆工具，并明确限制主动持久化。"""
    _, body = _load_skill()

    assert "recall_long_term_memory" in body
    assert "memorize_long_term_memory" in body
    assert "仅在用户明确要求长期保存时" in body
    assert "如果工具不可用" in body
    assert "不得声称写入成功" in body
