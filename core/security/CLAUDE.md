[根目录](../../CLAUDE.md) > [core](../CLAUDE.md) > **security**

## 模块职责

`core/security/` 是 Memora 的安全防线，提供两层安全防护：
1. **Prompt 保护层** (`prompt_sanitizer.py`): 防止注入的记忆上下文被 LLM 泄露给用户
2. **输出护栏层** (`guardrails.py`): Pydantic 强类型验证 LLM 的结构化输出，防止格式错误和恶意注入

共 3 个文件 (含 `__init__.py`)。

## 安全架构概述

```
用户消息 -> LLM 提取/生成
              |
    +---------+---------+
    |                   |
    v                   v
[Prompt 注入层]    [输出护栏层]
    |                   |
    v                   v
MetaInstructionWrapper  MemoryAtomSchema
  .wrap_instruction()     (Pydantic 强类型验证)
    |                   |
    v                   v
ResponseSanitizer      MemoryExtractionResult
  .sanitize()            (整体记忆抽取验证)
  (标签/关键词/片段清洗)  |
    |                   v
    v               GraphExtractionResult
DoubleCheckValidator    (图抽取验证)
  .validate_no_leak()    |
  (Jaccard + LCS +      v
   N-gram + SeqRatio)  validate_and_clean_json()
    |                   (JSON 清洗管道)
    v
PromptProtectionService
  .wrap_prompt()      // 输入包装
  .sanitize_response() // 输出清洗+验证
  .process_interaction() // 完整流程
```

## 子模块详解

### 1. Prompt 保护 (`prompt_sanitizer.py`) -- 4 类

#### MetaInstructionWrapper (标签包装层)
```python
class MetaInstructionWrapper:
    def wrap_instruction(instruction, add_suffix=True, custom_template=None) -> str
    def wrap_multiple(instructions, separator="\n\n") -> str
    def get_wrapped_hashes() -> set[str]
```
- 3 种内置模板风格 (系统指令 / 隐藏指令 / 导演指令)
- 每次包装随机选择不输出后缀 (3 种变体)
- 记录已包装指令的 SHA256 哈希

#### ResponseSanitizer (后处理清洗层)
```python
class ResponseSanitizer:
    def register_instructions(instructions: list[str])
    def sanitize(response, remove_tags=True, remove_keywords=True, remove_original=True) -> tuple[str, list[str]]
    def check_for_leaks(response) -> list[str]
```
- **3 趟清洗**:
  1. 正则移除已知标签模式 (5 种模式: `<system_internal>`, `[HIDDEN_INSTRUCTION]`, `<actor_direction>`, `<internal>`, `[MEMORA_INTERNAL]`)
  2. 句子级关键词过滤 (17 个泄露关键词: "系统指令", "内部指令", "隐藏指令", "我收到了指令" 等)
  3. 原始提示词片段匹配 (完整 + 5-gram 片段)
- 支持自定义正则模式
- 输出 `SanitizeReport` dataclass

#### DoubleCheckValidator (四算法验证层)
```python
class DoubleCheckValidator:
    def __init__(jaccard_threshold=0.4, levenshtein_ratio_threshold=0.6, lcs_ratio_threshold=0.5, ngram_threshold=0.3, ngram_size=3)
    def register_instructions(instructions: list[str])
    def validate_no_leak(response, instructions=None) -> tuple[bool, list[dict]]
    def get_similarity_report(response, instruction) -> dict
```
- **4 种算法**:
  1. **Jaccard**: 词集合交并比 (阈值 0.4)
  2. **SequenceMatcher**: difflib 序列相似度 (阈值 0.6, 截断 2000/500 字符)
  3. **窗口化 LCS**: 1.5x 滑动窗口 + 2-row DP O(n) 空间 (阈值 0.5)
  4. **N-gram 重叠**: 3-gram 集合交集/并集 (阈值 0.3)
- 中英文混合分词 (英文按词，中文按单字符)

#### PromptProtectionService (整合服务)
```python
class PromptProtectionService:
    def __init__(wrapper_template_index=0, enable_double_check=True)
    def wrap_prompt(content, label="memory_context", register_for_filter=True) -> str
    def sanitize_response(response, enable_validation=None) -> tuple[str, dict]
    def process_interaction(injected_content: list[str], llm_response: str) -> tuple[str, str, dict]
    def get_stats() -> dict[str, int]
```
- **完整交互流程**: 注入内容包装 -> LLM 回复清洗 -> 双重验证
- 统计计数器: wrapped / sanitized / leaks_detected / validation_failed

---

### 2. 输出护栏 (`guardrails.py`) -- 3 模型 + 5 函数

#### Pydantic 模型

**MemoryAtomSchema** -- 单条记忆原子的强类型 schema
```python
class MemoryAtomSchema(BaseModel):
    content: str       # min_length=5, max_length=2000
    atom_type: str     # fact|event|preference|knowledge|reflection (default: fact)
    importance: float  # [0.0, 1.0] (default: 0.5)
    entities: list[str]
    emotion_tags: list[str]
    confidence: float | None  # [0.0, 1.0]
```
- `content` 不可为空 (field_validator)
- `atom_type` 只能取 5 个允许值

**MemoryExtractionResult** -- LLM 记忆抽取的结构化输出验证
```python
class MemoryExtractionResult(BaseModel):
    memories: list[MemoryAtomSchema]
    confidence: float       # [0.0, 1.0] (default: 0.5)
    extraction_quality: str # low|medium|high (default: medium)
```

**GraphExtractionResult** -- 图抽取的结构化输出验证
```python
class GraphExtractionResult(BaseModel):
    entities: list[dict]   # 每项至少含 name + type
    relations: list[dict]  # 每项至少含 source + target + relation
```

#### JSON 清洗管道

**`validate_and_clean_json(text, fallback_return_none=False) -> dict | None`**
- 4 步清洗:
  1. 移除 Markdown 围栏 (```json ... ```)
  2. 提取 JSON 边界 (嵌套大括号/方括号匹配)
  3. `json.loads` 解析
  4. 修复重试 (单引号->双引号、尾逗号、Python bool->JSON bool)

**内部函数**:
- `_strip_markdown_fences(text) -> str` -- 移除 markdown 代码围栏
- `_extract_json_boundary(text) -> str | None` -- 提取最外层 JSON 边界 (支持对象和数组)
- `_repair_json(text) -> str` -- 修复常见 JSON 格式问题
- `_repair_single_quotes(text) -> str` -- 单引号转双引号 (正确处理转义)

#### 泛型验证器

**`validate_llm_response(response, model, add_json_instruction=False, fallback_return_none=False) -> T | None`**
- 解析 LLM 输出为指定 Pydantic 模型
- 失败时可追加 JSON 格式指令后重试

**`safe_validate(model, data, fallback_return_none=True) -> T | None`**
- 安全 Pydantic 验证，失败返回 None (不抛异常)
- 适用于所有 Pydantic 模型的泛型验证

### 3. 模块导出 (`__init__.py`)

公开导出:
- `PromptProtectionService`, `MetaInstructionWrapper`, `ResponseSanitizer`, `DoubleCheckValidator`
- `MemoryExtractionResult`, `MemoryAtomSchema`, `GraphExtractionResult`
- `validate_and_clean_json`, `validate_llm_response`, `safe_validate`

## 数据流

### Prompt 注入保护流程
```
LLM 回忆记忆 -> 记忆文本
                  |
                  v
PromptProtectionService.wrap_prompt(content, register_for_filter=True)
  -> MetaInstructionWrapper.wrap_instruction(content)
     -> 选择模板 (系统指令/隐藏指令/导演指令)
     -> 追加随机的"不输出"后缀
  -> ResponseSanitizer.register_instructions([content])
  -> DoubleCheckValidator.register_instructions([content])
                  |
                  v
包装后的 prompt -> 注入到 LLM system prompt
                  |
                  v
LLM 回复
                  |
                  v
PromptProtectionService.sanitize_response(response)
  -> ResponseSanitizer.sanitize(response)
     -> 第1轮: 正则移除标签模式 (<system_internal>, [HIDDEN_INSTRUCTION]...)
     -> 第2轮: 句子级关键词过滤 ("系统指令", "隐藏指令"...)
     -> 第3轮: 原始提示词片段匹配 (完整 + 5-gram)
  -> DoubleCheckValidator.validate_no_leak(sanitized)
     -> Jaccard 相似度检查
     -> SequenceMatcher 序列相似度检查
     -> 窗口化 LCS 检查
     -> N-gram 重叠检查
                  |
                  v
清洗后回复 -> 返回给用户
```

### 输出护栏流程
```
LLM 原始输出 (含 JSON)
                  |
                  v
validate_llm_response(response, MemoryExtractionResult)
  -> validate_and_clean_json(response)
     -> 移除 markdown 围栏
     -> 提取 JSON 边界
     -> json.loads 解析
     -> 修复重试 (单引号/尾逗号/Python bool)
  -> safe_validate(MemoryExtractionResult, data)
     -> Pydantic 字段校验 (content非空, atom_type合法, 数值范围...)
                  |
                  v
MemoryExtractionResult (强类型验证通过) 或 None (失败)
```

## 关键函数和类签名汇总

```python
# Prompt 保护
class PromptProtectionService:
    def wrap_prompt(content: str, label: str = "memory_context", register_for_filter: bool = True) -> str
    def sanitize_response(response: str, enable_validation: bool | None = None) -> tuple[str, dict]
    def process_interaction(injected_content: list[str], llm_response: str) -> tuple[str, str, dict]

class MetaInstructionWrapper:
    def wrap_instruction(instruction: str, add_suffix: bool = True, custom_template: str | None = None) -> str
    def wrap_multiple(instructions: list[str], separator: str = "\n\n") -> str

class ResponseSanitizer:
    def register_instructions(instructions: list[str])
    def sanitize(response: str, remove_tags: bool = True, remove_keywords: bool = True, remove_original: bool = True) -> tuple[str, list[str]]

class DoubleCheckValidator:
    def validate_no_leak(response: str, instructions: list[str] | None = None) -> tuple[bool, list[dict]]
    def get_similarity_report(response: str, instruction: str) -> dict

# 输出护栏
def validate_and_clean_json(text: str, fallback_return_none: bool = False) -> dict | None
def validate_llm_response(response: str, model: type[T], add_json_instruction: bool = False, fallback_return_none: bool = False) -> T | None
def safe_validate(model: type[T], data: dict, fallback_return_none: bool = True) -> T | None
```

## 测试与质量

- `tests/test_guardrails.py` -- 输出护栏测试 (JSON 清洗/验证/Pydantic 模型)
- `tests/test_security.py` -- Prompt 保护测试
- 集成测试: `tests/integration/test_security_pipeline.py` (完整安全管道)

## 常见问题 (FAQ)

**Q: 为什么需要三层 Prompt 防护？**
A: 单层防护存在盲区 -- LLM 可能不遵守标签指令（标签包裹层可能失效），清洗器可能漏掉新的泄露模式，验证器可能误判。三层互补，每层覆盖不同的攻击面。

**Q: 双重验证的性能开销？**
A: 4 种算法均为纯文本操作，无 I/O。LCS 使用 O(n) 空间的 2-row DP，窗口化限制扫描范围。单次验证通常在 ms 级别。

**Q: JSON 清洗管道能处理哪些格式问题？**
A: Markdown 代码围栏包裹、多余的文本前缀后缀、单引号 key/value、尾逗号、Python 的 True/False/None。

**Q: 如何添加新的泄露检测模式？**
A: 通过 `ResponseSanitizer` 的 `custom_patterns` 参数传入自定义正则，或直接扩展 `LEAK_KEYWORDS` 和 `TAG_PATTERNS`。

## 相关文件清单

| 文件 | 主要类 | 行数 |
|------|--------|------|
| `__init__.py` | 模块导出 | ~40 |
| `prompt_sanitizer.py` | PromptProtectionService, MetaInstructionWrapper, ResponseSanitizer, DoubleCheckValidator | ~670 |
| `guardrails.py` | MemoryExtractionResult, MemoryAtomSchema, GraphExtractionResult, validate_and_clean_json, validate_llm_response, safe_validate | ~370 |

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 深度扫描 | 完整读取 security 模块 3 文件，生成模块级 CLAUDE.md |
