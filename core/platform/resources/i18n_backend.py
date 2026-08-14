"""Bot 命令响应使用的后端国际化（i18n）模块。"""

import json
from pathlib import Path

from astrbot.api import logger

_fallback: dict = {}
_translations: dict = {}
_current_lang: str = "zh"


def init(language: str = "zh"):
    """初始化翻译资源，始终加载中文回退包。"""
    global _fallback, _translations, _current_lang
    if not language or language not in ("zh", "en", "ru"):
        language = "zh"
    _current_lang = language
    base = Path(__file__).parent.parent.parent / "i18n"

    # 加载中文回退包
    fallback_path = base / "zh.json"
    try:
        with open(fallback_path, encoding="utf-8") as f:
            _fallback = json.load(f)
    except Exception as exc:
        logger.error(f"加载回退语言包 zh.json 失败: {exc}")
        _fallback = {}

    # 加载目标语言包
    target_path = base / f"{language}.json"
    if target_path.exists():
        try:
            with open(target_path, encoding="utf-8") as f:
                _translations = json.load(f)
        except Exception as exc:
            logger.error(f"加载 i18n 文件 {language}.json 失败: {exc}")
            _translations = _fallback
    else:
        logger.warning(f"未找到 {language} 的 i18n 文件，回退到 zh")
        _translations = _fallback


def _get(data: dict, key: str):
    parts = key.split(".")
    for part in parts:
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data


def t(key: str, **kwargs) -> str:
    """通过点号路径键获取翻译字符串。"""
    value = _get(_translations, key)
    if value is None:
        value = _get(_fallback, key)
    if value is None:
        logger.warning(f"缺少 i18n 键: {key}")
        return key
    if not isinstance(value, str):
        return str(value)
    try:
        return value.format(**kwargs)
    except Exception as exc:
        logger.warning(f"i18n 键 '{key}' 的格式化失败: {exc}")
        return value


def t_list(key: str) -> list[str]:
    """通过点号路径键获取翻译字符串列表。"""
    value = _get(_translations, key)
    if value is None:
        value = _get(_fallback, key)
    if value is None:
        logger.warning(f"缺少 i18n 列表键: {key}")
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []
