"""AstrBot Page 生产 i18n 资源契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE_I18N_DIR = ROOT / ".astrbot-plugin" / "i18n"
RUNTIME_NOTICE_KEYS = (
    "waiting",
    "failed",
    "offline",
    "unknown",
    "waitingDescription",
    "failedDescription",
    "offlineDescription",
    "unknownDescription",
    "openConfig",
    "retry",
    "provider.embedding",
    "provider.llm",
    "provider.unknown",
)


def _get_nested(data: dict[str, object], path: str) -> object | None:
    value: object = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


@pytest.mark.parametrize("locale", ("zh-CN", "en-US", "ru-RU"))
def test_page_runtime_notice_keys_exist_in_production_i18n(locale: str) -> None:
    """运行状态提示不能只存在于 Dashboard 的测试兜底字典。"""
    data = json.loads((PAGE_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))

    missing = [
        key
        for key in RUNTIME_NOTICE_KEYS
        if not isinstance(_get_nested(data, f"dashboard.runtime.notice.{key}"), str)
    ]

    assert missing == [], f"{locale} 缺少生产 Page i18n 键: {missing}"
    waiting_description = _get_nested(
        data, "dashboard.runtime.notice.waitingDescription"
    )
    assert isinstance(waiting_description, str)
    assert "{0}" in waiting_description
