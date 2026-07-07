"""
增强版 LLM JSON 解析工具 —— 五阶段清理与三轮修复。

借鉴 self_learning 的 `safe_parse_llm_json()` 设计，替换当前较脆弱的
`extract_json_from_response()`。提供思考标签移除、Markdown 清理、控制字符
过滤、JSON 边界提取、常见错误修复的完整解析流程，以及三轮递增式修复重试。
"""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger


# ---------------------------------------------------------------------------
# 阶段辅助函数 —— 每个阶段返回清理后的文本
# ---------------------------------------------------------------------------

# 8 种思考标签模式（同 self_learning 的 ThinkingTagPattern）
_THINKING_PATTERNS: list[tuple[str, int]] = [
    (r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    (r"<思考>.*?</思考>", re.DOTALL),
    (r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE),
    (r"<reasoning>.*?</reasoning>", re.DOTALL | re.IGNORECASE),
    (r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),
    (r"\[thinking\].*?\[/thinking\]", re.DOTALL | re.IGNORECASE),
    (r"\[thought\].*?\[/thought\]", re.DOTALL | re.IGNORECASE),
    (r"【思考过程】.*?【/思考过程】", re.DOTALL),
]


def remove_thinking_content(text: str) -> str:
    """阶段 1：移除 8 种思考/推理标签及其内容。

    参数:
        text: 可能包含思考标签的 LLM 响应文本。

    返回:
        移除思考标签后的文本。
    """
    if not text:
        return text

    cleaned = text
    for pattern, flags in _THINKING_PATTERNS:
        try:
            cleaned = re.sub(pattern, "", cleaned, flags=flags)
        except re.error:
            continue

    # 压缩多余空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_markdown_blocks(text: str) -> str:
    """阶段 2：移除 Markdown 代码块标记（如 ```json / ```）。

    参数:
        text: 可能包含 Markdown fence 的文本。

    返回:
        清理后的文本。
    """
    if not text:
        return text

    cleaned = text.strip()

    # 开头 ```json / ```  → 切掉第一行
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            first_line = cleaned[:first_nl].strip()
            if first_line.startswith("```"):
                cleaned = cleaned[first_nl + 1 :]
        else:
            cleaned = re.sub(r"^```\w*\s*", "", cleaned)

    # 结尾 ```
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    # 行内残留的 ```lang
    cleaned = re.sub(r"^\s*```\w*\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)

    return cleaned.strip()


def clean_control_characters(text: str) -> str:
    """阶段 3：移除无效控制字符，保留 \\t \\n \\r。

    参数:
        text: 可能包含控制字符的文本。

    返回:
        清理后的文本。
    """
    if not text:
        return text
    # 移除 \x00-\x08 \x0b \x0c \x0e-\x1f \x7f
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def extract_json_content(text: str) -> str:
    """阶段 4：提取 JSON 边界，优先对象 `{...}`，其次数组 `[...]`。

    参数:
        text: 清理后的文本。

    返回:
        仅包含最外层 JSON 的字符串。
    """
    if not text:
        return text

    s = text.strip()

    # 对象优先
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]

    # 数组
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]

    return s


def fix_common_json_errors(text: str) -> str:
    """阶段 5：修复 6 类常见 JSON 格式错误。

    - 尾随逗号 (,}  ,])
    - Python True/False/None → true/false/null
    - NaN / Infinity 特殊值 → null
    - 缺失闭合引号 (启发式)
    - 缺失闭合括号 (计数补齐)

    参数:
        text: JSON 文本。

    返回:
        修复后的文本。
    """
    if not text:
        return text

    fixed = text

    # 尾随逗号
    fixed = re.sub(r",\s*}", "}", fixed)
    fixed = re.sub(r",\s*]", "]", fixed)

    # Python 布尔值 / None -> JSON
    fixed = re.sub(r":\s*True\b", ": true", fixed)
    fixed = re.sub(r":\s*False\b", ": false", fixed)
    fixed = re.sub(r":\s*None\b", ": null", fixed)
    fixed = re.sub(r":\s*NULL\b", ": null", fixed)

    # NaN / Infinity 特殊值
    fixed = re.sub(r":\s*nan\b", ": null", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r":\s*infinity\b", ": null", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r":\s*-infinity\b", ": null", fixed, flags=re.IGNORECASE)

    # 缺失闭合引号 → 补齐 (启发式: 奇数个 ")
    if fixed.count('"') % 2 != 0:
        fixed += '"'

    # 缺失闭合括号
    open_braces = fixed.count("{") - fixed.count("}")
    open_brackets = fixed.count("[") - fixed.count("]")
    fixed += "}" * max(0, open_braces)
    fixed += "]" * max(0, open_brackets)

    return fixed


# ---------------------------------------------------------------------------
# 单引号 → 双引号状态机
# ---------------------------------------------------------------------------


def _convert_single_quotes(text: str) -> str:
    """状态机：安全地将 JSON 中的单引号转为双引号。

    跟踪 in_string / escape_next / string_char 状态，避免误改字符串内部的引号。

    参数:
        text: 可能含单引号的 JSON 文本。

    返回:
        双引号 JSON。
    """
    if not text:
        return text

    result: list[str] = []
    in_string = False
    escape_next = False
    string_char: str | None = None

    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            continue

        if not in_string:
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                result.append('"')
            else:
                result.append(ch)
        else:
            if ch == string_char:
                in_string = False
                string_char = None
                result.append('"')
            elif ch == '"' and string_char == "'":
                result.append('\\"')
            else:
                result.append(ch)

    return "".join(result)


# ---------------------------------------------------------------------------
# 主解析入口
# ---------------------------------------------------------------------------


def safe_parse_llm_json(text: str) -> dict | list | None:
    """五阶段解析与三轮修复。

    - 阶段 1：移除思考标签（8 种模式）
    - 阶段 2：移除 Markdown 代码围栏
    - 阶段 3：清理控制字符（保留 \\t \\n \\r）
    - 阶段 4：提取 JSON 边界（`{...}` 或 `[...]`）
    - 阶段 5：修复常见错误（6 类）

    - 第 1 轮：直接解析
    - 第 2 轮：修复错误后重试
    - 第 3 轮：单引号转双引号后重试

    参数:
        text: LLM 响应原始文本。

    返回:
        解析后的 dict 或 list；完全失败返回 None。
    """
    if not text or not text.strip():
        return None

    # 阶段 1-4：清理管线
    cleaned = remove_thinking_content(text)  # 阶段 1
    cleaned = clean_markdown_blocks(cleaned)  # 阶段 2
    cleaned = clean_control_characters(cleaned)  # 阶段 3
    cleaned = extract_json_content(cleaned)  # 阶段 4

    if not cleaned:
        return None

    # 第 1 轮：直接解析
    try:
        result = json.loads(cleaned)
        if isinstance(result, (dict, list)):
            return result
    except json.JSONDecodeError:
        pass

    # 第 2 轮：修复常见错误后重试
    try:
        fixed = fix_common_json_errors(cleaned)  # 阶段 5
        result = json.loads(fixed)
        if isinstance(result, (dict, list)):
            return result
    except json.JSONDecodeError:
        pass

    # 第 3 轮：单引号转换后修复并重试
    try:
        sq = _convert_single_quotes(cleaned)
        sq = fix_common_json_errors(sq)
        result = json.loads(sq)
        if isinstance(result, (dict, list)):
            return result
    except json.JSONDecodeError:
        pass

    logger.debug(f"safe_parse_llm_json 解析失败（前 200 字符）: {text[:200]}...")
    return None


def detect_llm_provider(model_name: str) -> str:
    """检测 LLM 提供器类型。

    参数:
        model_name: 模型名称。

    返回:
        "deepseek" | "claude" | "openai" | "generic"
    """
    if not model_name:
        return "generic"

    lower = model_name.lower()
    if "deepseek" in lower:
        return "deepseek"
    if "claude" in lower:
        return "claude"
    if any(kw in lower for kw in ("gpt-", "text-", "davinci", "openai")):
        return "openai"
    return "generic"


__all__ = [
    "safe_parse_llm_json",
    "remove_thinking_content",
    "clean_markdown_blocks",
    "clean_control_characters",
    "extract_json_content",
    "fix_common_json_errors",
    "_convert_single_quotes",
    "detect_llm_provider",
]
