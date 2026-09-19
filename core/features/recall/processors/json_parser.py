"""JSON 解析与修复"""

import json
import re
from typing import Any

from astrbot.api import logger
from pydantic import ValidationError

from ....platform.security.guardrails import MemoryExtractionResult
from .quality_validator import QualityValidator

_PARSE_REASONS: frozenset[str] = frozenset(
    {
        "fence_invalid",
        "json_invalid",
        "schema_invalid",
        "facts_missing",
        "grounding_fact_evidence_mismatch",
    }
)
_FIELD_PATH_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _safe_field_path_part(part: object) -> str:
    """只保留字段名、列表下标与错误类型，其余一律脱敏为 '*'。"""

    if isinstance(part, str) and _FIELD_PATH_PART.match(part):
        return part
    if isinstance(part, int) and not isinstance(part, bool):
        return str(part)
    return "*"


def _summarize_validation_error(error: ValidationError) -> str:
    """只提取首个错误的字段路径与错误类型，绝不携带输入值。"""

    errors = error.errors()
    if not errors:
        return "unknown"
    first = errors[0]
    parts = [*(first.get("loc") or ()), first.get("type")]
    return ".".join(_safe_field_path_part(part) for part in parts)


class SummaryParseError(ValueError):
    """表示总结模型输出不满足严格结构契约。

    ``str(error)`` 固定为 ``summary_invalid`` 以兼容既有 job 级 reason 映射；
    ``reason`` 只区分失败阶段，``detail`` 只允许字段路径、错误类型或字符偏移。
    """

    def __init__(self, reason: str = "summary_invalid", *, detail: str = "") -> None:
        super().__init__("summary_invalid")
        self.reason = reason if reason in _PARSE_REASONS else "summary_invalid"
        self.detail = detail


class JsonParser:
    """多级备选 JSON 解析：直接解析 → 修复后解析 → 正则提取 → 默认值"""

    def __init__(self, quality_validator: QualityValidator | None = None):
        self.quality = quality_validator or QualityValidator()

    @staticmethod
    def try_fix_json(text: str) -> str:
        fixed = text.strip()

        if fixed.startswith("```json"):
            fixed = fixed[7:]
        elif fixed.startswith("```"):
            fixed = fixed[3:]
        if fixed.endswith("```"):
            fixed = fixed[:-3]
        fixed = fixed.strip()

        open_quotes = fixed.count('"') - fixed.count('\\"')
        if open_quotes % 2 != 0:
            fixed += '"'

        open_brackets = fixed.count("[") - fixed.count("]")
        if open_brackets > 0:
            fixed += "]" * open_brackets

        open_braces = fixed.count("{") - fixed.count("}")
        if open_braces > 0:
            fixed += "}" * open_braces

        fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)
        fixed = fixed.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

        return fixed

    @staticmethod
    def _strip_summary_code_fence(text: str) -> str:
        """仅移除完整 JSON/普通代码围栏，不修复围栏或正文。"""

        cleaned = text.strip()
        if not cleaned.startswith("```"):
            return cleaned
        lines = cleaned.splitlines()
        if (
            len(lines) < 3
            or lines[0].strip().lower() not in {"```", "```json"}
            or lines[-1].strip() != "```"
        ):
            raise SummaryParseError("fence_invalid")
        return "\n".join(lines[1:-1]).strip()

    @staticmethod
    def _normalize_strict_numeric_fields(data: dict[str, Any]) -> None:
        """仅把 JSON 整数转换为浮点字段，不修复或改变事实内容。"""

        for field in ("confidence",):
            value = data.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                data[field] = float(value)
        for memory in data["memories"]:
            if not isinstance(memory, dict):
                continue
            for field in ("importance", "confidence"):
                value = memory.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    memory[field] = float(value)

    @staticmethod
    def _decode_summary_object(text: str) -> Any:
        """解析总结对象；整段非法时退化为解析首个 ``{`` 起的 JSON 对象。

        前后允许混有解释文本，但仍必须是合法 JSON；无法解析时抛出
        ``json_invalid`` 并只附带整段解析的字符偏移。
        """

        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            start = text.find("{")
            if start >= 0:
                try:
                    data, _ = json.JSONDecoder().raw_decode(text, start)
                    return data
                except ValueError:
                    pass
            raise SummaryParseError(
                "json_invalid", detail=f"char_offset={error.pos}"
            ) from None

    def parse_summary_response(self, response_text: str) -> MemoryExtractionResult:
        """严格解析并验证总结对象，允许空 memories 列表。"""

        cleaned = self._strip_summary_code_fence(response_text)
        data = self._decode_summary_object(cleaned)
        if (
            not isinstance(data, dict)
            or "memories" not in data
            or not isinstance(data["memories"], list)
        ):
            raise SummaryParseError("schema_invalid", detail="memories")
        self._normalize_strict_numeric_fields(data)
        try:
            result = MemoryExtractionResult.model_validate(data, strict=True)
        except ValidationError as error:
            if any(
                item.get("ctx", {}).get("error") is not None
                and str(item["ctx"]["error"]) == "grounding_fact_evidence_mismatch"
                for item in error.errors()
            ):
                raise SummaryParseError("grounding_fact_evidence_mismatch") from None
            raise SummaryParseError(
                "schema_invalid", detail=_summarize_validation_error(error)
            ) from None
        if any(
            not memory.key_facts or any(not fact.strip() for fact in memory.key_facts)
            for memory in result.memories
        ):
            raise SummaryParseError("facts_missing")
        return result

    def parse_llm_response(
        self, response_text: str, is_group_chat: bool
    ) -> dict[str, Any]:
        logger.debug(f"[MemoryProcessor] 开始解析 LLM 响应，长度={len(response_text)}")

        try:
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            cleaned_text = cleaned_text.strip()

            data = json.loads(cleaned_text)
            if not isinstance(data, dict):
                raise ValueError(f"期望 dict 类型，实际为 {type(data).__name__}")

            logger.info("[MemoryProcessor] JSON 解析成功")
            return self._normalize_parsed_data(data, is_group_chat)

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[MemoryProcessor] JSON 解析失败: {e}")
            logger.info("[MemoryProcessor] 尝试修复 JSON 后重新解析")
            try:
                fixed_text = self.try_fix_json(response_text)
                data = json.loads(fixed_text)
                if isinstance(data, dict):
                    logger.info("[MemoryProcessor] JSON 修复后解析成功")
                    return self._normalize_parsed_data(data, is_group_chat)
            except (json.JSONDecodeError, ValueError):
                logger.debug("[MemoryProcessor] JSON 修复后仍无法解析")

            logger.info("[MemoryProcessor] 尝试使用正则表达式提取 JSON")
            return self._extract_by_regex(response_text, is_group_chat)
        except Exception as e:
            logger.error(
                f"[MemoryProcessor] 解析 LLM 响应时发生异常: {e}", exc_info=True
            )
            return self.quality.get_default_structured_data(is_group_chat)

    def _normalize_parsed_data(
        self, data: dict[str, Any], is_group_chat: bool
    ) -> dict[str, Any]:
        """统一规范直接解析和修复解析得到的兼容结构。"""

        raw_memories = data.get("memories")
        if isinstance(raw_memories, list):
            first_memory = next(
                (item for item in raw_memories if isinstance(item, dict)), None
            )
            if first_memory is not None:
                for field in (
                    "summary",
                    "topics",
                    "key_facts",
                    "sentiment",
                    "importance",
                ):
                    if field not in data and field in first_memory:
                        data[field] = first_memory[field]
                if is_group_chat and "participants" not in data:
                    data["participants"] = first_memory.get("participants", [])

        return self.quality.normalize_parsed_data(data, is_group_chat)

    def _extract_by_regex(self, text: str, is_group_chat: bool) -> dict[str, Any]:
        logger.debug("[MemoryProcessor] 开始使用正则表达式提取结构化数据")
        data = self.quality.get_default_structured_data(is_group_chat)

        try:
            json_matches = re.findall(
                r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL
            )
            logger.debug(
                f"[MemoryProcessor] 正则匹配到 {len(json_matches)} 个可能的 JSON 块"
            )

            for i, match in enumerate(json_matches):
                try:
                    parsed = json.loads(match)
                    if "summary" in parsed:
                        logger.info(
                            f"[MemoryProcessor] 成功从第 {i + 1} 个 JSON 块中解析数据"
                        )
                        data = parsed
                        break
                except json.JSONDecodeError:
                    continue

            if data == self.quality.get_default_structured_data(is_group_chat):
                logger.debug("[MemoryProcessor] 未找到完整 JSON，尝试提取单独字段")

                summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
                if summary_match:
                    data["summary"] = summary_match.group(1)

                importance_match = re.search(r'"importance"\s*:\s*([0-9.]+)', text)
                if importance_match:
                    data["importance"] = float(importance_match.group(1))

                sentiment_match = re.search(r'"sentiment"\s*:\s*"(\w+)"', text)
                if sentiment_match:
                    data["sentiment"] = sentiment_match.group(1)

                topics_match = re.search(r'"topics"\s*:\s*\[(.*?)\]', text, re.DOTALL)
                if topics_match:
                    topics_str = topics_match.group(1)
                    data["topics"] = re.findall(r'"([^"]+)"', topics_str)[:5]

                facts_match = re.search(r'"key_facts"\s*:\s*\[(.*?)\]', text, re.DOTALL)
                if facts_match:
                    facts_str = facts_match.group(1)
                    data["key_facts"] = re.findall(r'"([^"]+)"', facts_str)[:5]

        except Exception as e:
            logger.error(f"[MemoryProcessor] 正则提取失败: {e}", exc_info=True)

        return data
