"""JSON 解析与修复"""

import json
import re
from typing import Any

from astrbot.api import logger

from .quality_validator import QualityValidator


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
