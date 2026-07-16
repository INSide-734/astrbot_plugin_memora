[根目录](../../CLAUDE.md) > [core](../) > **utils**

## 模块职责

`core/utils/` 是 Memora 的共享工具函数集合，提供 JSON 解析、缓存管理、记忆格式化、注入适配、停用词管理、风格分析、回复多样性、任务调度、版本号读取等 13 个模块的公共能力。12 个源文件 + `__init__.py`。

## 入口与启动

- **对外导出**: `core/utils/__init__.py` 导出 25+ 个公开符号（类、函数、常量）
- **全局函数**: `get_persona_id()` 定义在此，被全项目广泛引用

## 模块清单

### JSON 工具 (`json_utils.py`)
5 阶段清理 + 3 轮修复的 LLM JSON 解析器：

| 函数 | 职责 |
|------|------|
| `safe_parse_llm_json()` | 主入口：5 阶段清理 + 3 轮递增修复 |
| `remove_thinking_content()` | 阶段 1：移除 8 种思考标签（`<thinking>`, `<think>`, `【思考过程】` 等） |
| `clean_markdown_blocks()` | 阶段 2：移除 Markdown 代码块标记 |
| `clean_control_characters()` | 阶段 3：过滤不可打印控制字符 |
| `extract_json_content()` | 阶段 4：提取 JSON 边界（`{...}` 或 `[...]`） |
| `fix_common_json_errors()` | 阶段 5：修复常见 JSON 错误（末尾逗号、单引号等） |
| `_convert_single_quotes()` | 辅助：将单引号 JSON 转为双引号 |
| `detect_llm_provider()` | 检测 LLM Provider 类型 |

### 记忆格式化 (`memory_formatter.py`)

| 函数 | 职责 |
|------|------|
| `format_memories_for_injection()` | 将记忆列表格式化为注入文本（含 INJECTION_HEADER/FOOTER 包裹） |
| `format_memories_for_fake_tool_call()` | 生成伪造的 assistant tool_calls + tool 结果消息对 |
| `format_memories_for_fake_tool_call_deepseek_v4()` | DeepSeek V4 兼容：转录为文本格式 |

### 注入适配 (`injection_adapter.py`)

| 类 | 方法 | 职责 |
|----|------|------|
| `InjectionAdapter` | `resolve(provider, configured_mode)` | 将已路由的传输偏好适配为 Provider 支持的临时投递方式 |

**降级规则**：
- `DeliveryMode` 不接受 `system_prompt`；动态记忆不会写入 System Prompt
- `auto` 传输使用临时 `extra_user_content`
- Gemini 系列不支持 `fake_tool_call`，自动降级为 `user_message_before`

### 缓存管理 (`cache_manager.py`)
9 命名空间 TTL/LRU 缓存管理器，提供同步/异步装饰器：

- **实现**: 优先使用 `cachetools`（TTLCache/LRUCache），不可用时降级为纯 `OrderedDict` 实现
- **单例模式**: `get_cache_manager()` 全局单例
- **命名空间**: 9 个独立缓存空间，惰性过期 + LRU 淘汰
- **监控**: 命中率统计（hits / misses）

### 数据辅助 (`data_helpers.py`)

| 函数 | 职责 |
|------|------|
| `safe_parse_metadata()` | 安全解析元数据（支持 str / dict） |
| `safe_serialize_metadata()` | 安全序列化元数据为 JSON |
| `validate_timestamp()` | 时间戳校验（int / float 范围检查） |
| `retry_on_failure()` | 异步重试装饰器（指数退避） |
| `OperationContext` | 异步上下文管理器（操作级日志隔离） |

### 文本工具 (`text_utils.py`)
共享文本分词工具：

- `tokenize_bigrams()` -- 中文二元分词器
- `tokenize_unigrams()` -- 单字分词器
- `tokenize_mixed()` -- 混合分词器（检测中英文自动选择）
- 内置 100+ 中英文停用词（`_KNOWLEDGE_STOPWORDS`）

### 停用词管理 (`stopwords_manager.py`)

| 类/函数 | 职责 |
|---------|------|
| `StopwordsManager` | 停用词表的加载、维护与持久化 |
| `get_stopwords_manager()` | 全局单例获取 |

支持来源：
- 哈工大停用词表 (`source="hit"`)
- 内置默认停用词 (`DEFAULT_STOPWORDS`)
- 用户自定义停用词（文件持久化）
- 分词引擎: jieba

### 风格分析 (`style_analyzer.py`)
基于双路 LLM 调用的七维语言风格画像：

- 7 个维度: 词汇丰富度、句法复杂度、情感表达强度、互动倾向、话题多样性、正式度、创意性
- 3 种分析模式: 定量统计（TextBlob/jieba）、定性 LLM 分析、混合模式
- `StyleProfile` -- 单条消息风格画像
- `StyleEvolution` -- 时间序列风格演变追踪
- 滑窗统计（mean/median/stdev of 7 dims over 100 messages）

### 回复多样性 (`diversity_manager.py`)

| 类/常量 | 职责 |
|---------|------|
| `ResponseDiversityManager` | 管理风格轮换、反重复与动态温度 |
| `LANGUAGE_STYLES` | 8 种语言风格（简洁直接、温和友善、活泼开朗...） |
| `RESPONSE_PATTERNS` | 6 种回复模式（直接回答、引导思考、幽默调侃...） |
| `EXPRESSION_VARIATIONS` | 3 种表达维度（句法、语气、强调） |
| `TEMPERATURE_RANGES` | 4 种场景温度区间（creative 0.8-1.2, precise 0.3-0.6） |
| `HomogeneityReport` | 同质性报告数据类 |
| `VariationComposition` | 变体组合数据类 |

### 任务调度 (`task_scheduler.py`)

| 类 | 职责 |
|----|------|
| `TaskScheduler` | APScheduler 包装器，支持 interval/cron 任务 |
| `_NoOpScheduler` | 降级空实现（APScheduler 不可用时） |
| `get_task_scheduler()` | 全局单例获取 |

### 数值工具 (`number_utils.py`)

| 函数 | 职责 |
|------|------|
| `safe_float()` | 安全浮点数读取（None/空串→默认值） |
| `clamp_float()` | 钳制浮点数到指定范围 |

### 版本号 (`version.py`)

| 常量 | 来源 | 用法 |
|------|------|------|
| `PLUGIN_VERSION` | `metadata.yaml` | 唯一事实来源，避免硬编码版本号 |

## 关键依赖与配置

- **外部库**: `cachetools`, `APScheduler`, `jieba`, `pytz`, `PyYAML`, `json`, `re`
- **内部依赖**: `core.base.constants`（`MEMORY_INJECTION_HEADER/FOOTER`, `FAKE_TOOL_CALL_ID_PREFIX`）, `core.base.config_manager`, `core.processors.text_processor`, `core.models.default_stopwords`, `astrbot.api`

## 数据模型

| 数据类 | 文件 | 字段 |
|--------|------|------|
| `StyleProfile` | `style_analyzer.py` | 7 维 float 评分 + timestamp |
| `StyleEvolution` | `style_analyzer.py` | 维度 → (mean, median, stdev, trend) |
| `HomogeneityReport` | `diversity_manager.py` | 重复率, 风格统计, 建议 |
| `VariationComposition` | `diversity_manager.py` | style, pattern, sentence_style, tone, emphasis |

## 测试与质量

- 对应测试文件: `tests/test_utils.py`, `tests/test_json_utils.py`
- 类型注解全覆盖（`from __future__ import annotations`）
- 降级策略: cachetools → OrderedDict, APScheduler → NoOpScheduler

## 常见问题 (FAQ)

**Q: LLM JSON 解析失败怎么办？**
A: 使用 `safe_parse_llm_json()` 替代直接 `json.loads()`。它内置 5 阶段清理 + 3 轮修复重试，能处理大多数 LLM 输出格式问题。

**Q: 缓存命中率怎么调优？**
A: 通过 `CacheManager` 的 `get_stats()` 查看各命名空间命中率。调整 TTL 和 maxsize 参数。

**Q: 如何添加自定义停用词？**
A: 使用 `StopwordsManager.add_custom_word()`，词表会持久化到 `data_dir/stopwords/custom.txt`。

## 相关文件清单

- `__init__.py` -- 公共导出 + `get_persona_id()` 全局函数（223 行）
- `json_utils.py` -- 增强版 LLM JSON 解析器
- `memory_formatter.py` -- 记忆格式化（注入文本 / 伪工具调用）
- `injection_adapter.py` -- 注入策略适配层（95 行）
- `cache_manager.py` -- 9 命名空间 TTL/LRU 缓存
- `data_helpers.py` -- 元数据解析 / 时间戳校验 / 重试装饰器
- `text_utils.py` -- 3 种分词变体 + 100+ 停用词
- `stopwords_manager.py` -- 停用词管理
- `style_analyzer.py` -- 七维语言风格画像
- `diversity_manager.py` -- 回复多样性管理
- `task_scheduler.py` -- APScheduler 包装器
- `number_utils.py` -- 数值安全读取（28 行）
- `version.py` -- 版本号单一事实来源（28 行）

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 读取 12 个源文件（每个前 50 行 + 核心文件完整），生成模块级 CLAUDE.md |
