"""从 metadata.yaml 读取插件版本号 — 唯一事实来源。

所有模块应从此处导入 PLUGIN_VERSION，而非硬编码版本号。
"""

from __future__ import annotations

from pathlib import Path

import yaml

# metadata.yaml 位于插件根目录（从 core/platform/resources/version.py 向上 4 层）
_METADATA_PATH = Path(__file__).resolve().parent.parent.parent.parent / "metadata.yaml"


def _load_metadata() -> dict:
    """加载并返回 metadata.yaml 的完整内容。"""
    with open(_METADATA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


_METADATA = _load_metadata()

PLUGIN_VERSION: str = _METADATA["version"]
"""插件版本号字符串，例如 "1.0.0"。"""

__all__ = ["PLUGIN_VERSION"]
