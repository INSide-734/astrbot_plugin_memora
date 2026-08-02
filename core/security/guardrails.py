"""Pydantic 强类型 LLM 输出验证 — 记忆抽取与图抽取的结构化护栏。

提供:
- **MemoryExtractionResult** — LLM 记忆抽取的结构化输出验证
- **MemoryAtomSchema** — 单条记忆原子的 schema 约束
- **GraphExtractionResult** — 图抽取的结构化输出验证
- **validate_and_clean_json** — 多步 JSON 清洗管道
- **validate_llm_response** — 泛型 LLM 响应 → Pydantic 模型验证器
- **safe_validate** — 安全验证，失败返回 None
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Pydantic 模型定义
# ---------------------------------------------------------------------------


class SourceReferenceSchema(BaseModel):
    """限制单条抽取结果只能引用当前匿名消息窗口。"""

    message_index: int = Field(ge=0, le=4095, description="当前窗口内的消息序号")
    start: int = Field(ge=0, le=100_000, description="正文引用起点，左闭")
    end: int = Field(ge=1, le=100_000, description="正文引用终点，右开")

    @model_validator(mode="after")
    def _validate_range(self) -> "SourceReferenceSchema":
        """拒绝空区间和反向区间。"""

        if self.end <= self.start:
            raise ValueError("source_refs 的 end 必须大于 start")
        return self


class MemoryAtomSchema(BaseModel):
    """单条记忆原子的强类型 schema。

    字段覆盖 content/text 类型、重要性、关联实体和情感标签。
    """

    content: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="记忆的核心文本内容",
    )
    atom_type: str = Field(
        default="fact",
        description=(
            "可选记忆类型提示，兼容领域类型和历史 fact/event/knowledge/reflection"
        ),
    )
    importance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="重要性评分 [0.0, 1.0]",
    )
    entities: list[str] = Field(
        default_factory=list,
        description="关联的命名实体列表",
    )
    emotion_tags: list[str] = Field(
        default_factory=list,
        description="情感标签列表",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="抽取置信度（可选，LLM 可能不提供）",
    )
    topics: list[str] = Field(default_factory=list, description="记忆主题列表")
    key_facts: list[str] = Field(default_factory=list, description="长期事实列表")
    participants: list[str] = Field(default_factory=list, description="参与者列表")
    sentiment: str = Field(default="neutral", description="整体情感基调")
    causal_relations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="明确因果关系列表",
    )
    source_refs: list[SourceReferenceSchema] = Field(
        default_factory=list,
        max_length=10,
        description="当前匿名消息窗口中的受控来源引用",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_summary_prompt_contract(cls, value: Any) -> Any:
        """把现有 Prompt 的 summary/topics 字段映射到护栏基础字段。"""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if not normalized.get("content") and normalized.get("summary"):
            normalized["content"] = normalized["summary"]
        if not normalized.get("entities") and normalized.get("topics"):
            normalized["entities"] = normalized["topics"]
        return normalized

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content 不能为空字符串")
        return stripped

    @field_validator("atom_type")
    @classmethod
    def _valid_atom_type(cls, v: str) -> str:
        """规范可选类型提示，并兼容历史与领域枚举词表。"""

        normalized = v.strip().lower()
        allowed = {
            "fact",
            "event",
            "knowledge",
            "reflection",
            "factual",
            "episodic",
            "relational",
            "preference",
            "planned",
            "unknown",
        }
        if normalized not in allowed:
            raise ValueError(f"atom_type 必须是 {allowed} 之一，收到: {v!r}")
        return normalized

    @field_validator("sentiment")
    @classmethod
    def _valid_sentiment(cls, value: str) -> str:
        """限制总结情感字段为 Prompt 公开的三个固定值。"""

        allowed = {"positive", "neutral", "negative"}
        if value not in allowed:
            raise ValueError(f"sentiment 必须是 {allowed} 之一，收到: {value!r}")
        return value


class MemoryExtractionResult(BaseModel):
    """LLM 记忆抽取的结构化输出验证。

    验证 LLM 返回的整个抽取结果，包括 memories 数组和质量元数据。
    """

    memories: list[MemoryAtomSchema] = Field(
        default_factory=list,
        description="抽取的记忆原子数组",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="整体抽取置信度 [0.0, 1.0]",
    )
    extraction_quality: str = Field(
        default="medium",
        description="抽取质量等级: low, medium, high",
    )

    @field_validator("extraction_quality")
    @classmethod
    def _valid_quality(cls, v: str) -> str:
        allowed = {"low", "medium", "high"}
        if v not in allowed:
            raise ValueError(f"extraction_quality 必须是 {allowed} 之一，收到: {v!r}")
        return v


class GraphExtractionResult(BaseModel):
    """图抽取的结构化输出验证。

    验证 LLM 抽取的实体和关系列表。
    """

    entities: list[dict[str, Any]] = Field(
        default_factory=list,
        description="实体列表，每项至少含 name 和 type 字段",
    )
    relations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="关系列表，每项至少含 source, target, relation 字段",
    )

    @field_validator("entities")
    @classmethod
    def _validate_entities(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, entity in enumerate(v):
            if "name" not in entity or "type" not in entity:
                raise ValueError(
                    f"entities[{i}] 必须包含 'name' 和 'type' 字段: {entity}"
                )
        return v

    @field_validator("relations")
    @classmethod
    def _validate_relations(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        required = {"source", "target", "relation"}
        for i, rel in enumerate(v):
            missing = required - set(rel.keys())
            if missing:
                raise ValueError(f"relations[{i}] 缺少必需字段 {missing}: {rel}")
        return v


# ---------------------------------------------------------------------------
# JSON 清洗管线
# ---------------------------------------------------------------------------


def validate_and_clean_json(
    text: str,
    *,
    fallback_return_none: bool = False,
) -> dict[str, Any] | None:
    """多步 JSON 清洗管道 — 从 LLM 输出中提取并解析 JSON。

    步骤:
    1. 移除 Markdown 围栏（```json ... ```）
    2. 用正则提取 JSON 边界（嵌套大括号匹配）
    3. 执行 `json.loads` 解析
    4. 若失败，则修复单引号、尾逗号、Python 布尔值后重试

    参数:
        text: LLM 返回的原始文本
        fallback_return_none: 若为 True，全部失败后返回 None；否则 raise

    返回:
        解析后的 dict，或 None（当 fallback_return_none=True 且全部失败时）
    """
    if not text or not text.strip():
        if fallback_return_none:
            return None
        raise ValueError("输入文本为空")

    working = text.strip()

    # 步骤 1：移除 Markdown 围栏
    working = _strip_markdown_fences(working)

    # 步骤 2：提取 JSON 边界
    extracted = _extract_json_boundary(working)
    if extracted is not None:
        working = extracted

    # 步骤 3：执行 json.loads 解析
    try:
        return json.loads(working)
    except json.JSONDecodeError:
        pass

    # 步骤 4：修复并重试
    repaired = _repair_json(working)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        if fallback_return_none:
            return None
        raise ValueError(f"JSON 解析失败: {exc}") from exc


def _strip_markdown_fences(text: str) -> str:
    """移除 markdown 代码围栏。"""
    # 带语言标识的: ```json ... ```
    m = re.match(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 只有开头围栏: ```json\n...
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_json_boundary(text: str) -> str | None:
    """从文本中找出最外层 JSON 边界 — 支持对象 { } 和数组 [ ]。

    选择先出现的边界字符开始匹配，确保数组 [{...}] 也被正确提取。
    """
    brace_start = text.find("{")
    bracket_start = text.find("[")
    if brace_start == -1 and bracket_start == -1:
        return None

    # 选择先出现的作为起始
    if bracket_start != -1 and (brace_start == -1 or bracket_start < brace_start):
        # 提取数组
        depth = 0
        for i in range(bracket_start, len(text)):
            ch = text[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[bracket_start : i + 1]
        return None

    # 提取对象
    depth = 0
    for i in range(brace_start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start : i + 1]
    return None


def _repair_json(text: str) -> str:
    """修复常见 JSON 格式问题：单引号、尾逗号、Python bool。"""
    # 1. 单引号 → 双引号（仅键和字符串值）
    # 简单策略：全局替换单引号为双引号（在 control 字符安全的文本中可行）
    working = _repair_single_quotes(text)

    # 2. 移除尾逗号
    working = re.sub(r",(\s*[}\]])", r"\1", working)

    # 3. Python bool → JSON bool
    working = re.sub(r"\bTrue\b", "true", working)
    working = re.sub(r"\bFalse\b", "false", working)
    working = re.sub(r"\bNone\b", "null", working)

    return working


def _repair_single_quotes(text: str) -> str:
    """将单引号字符串替换为双引号。

    策略：识别被单引号包裹的字符串片段并替换。
    """
    result: list[str] = []
    i = 0
    in_double = False
    in_single = False
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            result.append(ch)
            if i + 1 < len(text):
                result.append(text[i + 1])
                i += 1
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch == "'" and not in_double:
            result.append('"')
        else:
            result.append(ch)
        i += 1
    return "".join(result)


# ---------------------------------------------------------------------------
# 泛型 LLM 响应验证器
# ---------------------------------------------------------------------------

_JSON_INSTRUCTION_SUFFIX = (
    "\n\n请严格以 JSON 格式返回结果，不要包含任何额外的解释或 markdown 格式。"
)


def validate_llm_response(
    response: str,
    model: type[T],
    *,
    add_json_instruction: bool = False,
    fallback_return_none: bool = False,
) -> T | None:
    """泛型 LLM 响应验证器。

    解析 LLM 输出为指定 Pydantic 模型，验证字段约束。

    参数:
        response: LLM 返回的原始文本
        model: Pydantic 模型类
        add_json_instruction: 若为 True，在 response 后追加 JSON 格式指令后重新解析
        fallback_return_none: 失败时返回 None 而非抛出异常

    返回:
        验证通过的模型实例，或 None
    """
    data = validate_and_clean_json(response, fallback_return_none=True)

    if data is None and add_json_instruction:
        augmented = response + _JSON_INSTRUCTION_SUFFIX
        data = validate_and_clean_json(augmented, fallback_return_none=True)

    if data is None:
        if fallback_return_none:
            return None
        raise ValueError("无法从 LLM 响应中解析 JSON")

    return safe_validate(model, data, fallback_return_none=fallback_return_none)


def safe_validate(
    model: type[T],
    data: dict[str, Any],
    *,
    fallback_return_none: bool = True,
) -> T | None:
    """安全 Pydantic 验证 — 失败返回 None（不抛异常）。

    参数:
        model: Pydantic 模型类
        data: 要验证的 dict
        fallback_return_none: 若为 False，验证失败时 raise

    返回:
        验证通过的模型实例，或 None
    """
    try:
        return model(**data)
    except Exception:
        if fallback_return_none:
            return None
        raise
