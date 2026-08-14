# 测试指南

本页说明 Memora Python 测试体系的组织、fixture 契约与变更定位。质量门禁命令见[质量门禁](/development/quality-gates)，测试模块的完整上下文见 [`tests/AGENTS.md`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/tests/AGENTS.md)。

## 目录职责

| 路径 | 职责 |
|---|---|
| 根目录 `test_*.py` | 按被测领域组织的单元、API、Store/Manager、处理器、配置与包导出契约。 |
| `integration/` | 以真实 SQLite、真实 FAISS 索引和 Mock Provider 组装跨模块管线；是五条集成 smoke 的固定目标。 |
| `evaluation/` | JSONL 数据集加载、Recall@K/MRR/nDCG/延迟指标、variant 对比与报告持久化。 |
| `stress/` | 并发写入等竞争条件；不放入机器抖动敏感的绝对耗时阈值。 |
| `fixtures/retrieval/` | 六组标准离线检索样本。 |

依赖只能从测试指向运行代码；生产代码不得反向导入 `tests`。

## 装配顺序与关键 fixture

`tests/conftest.py` 的加载顺序是硬约束：`_install_astrbot_mocks()` 必须在任何依赖 AstrBot 的 `core.*` 导入之前执行。

| Fixture | 作用 |
|---|---|
| `mock_llm_caller` / `mock_llm_provider` | 固定 JSON 摘要与稳定模型名的 Provider 替身；不访问真实模型。 |
| `test_config_dict` / `test_config` | 最小配置字典与点号路径读取对象；修改默认值时同步配置契约测试。 |
| `tmp_db_path` | 每测试独立的文件型 SQLite 路径，fixture 负责删除。 |
| `sample_atoms` | 五种 MemoryAtom 类型的确定性样本。 |
| `mock_event` / `mock_context` | AstrBot 事件与上下文替身。 |

`tests/integration/conftest.py` 复用根 fixtures，再增加真实存储与完整 `MemoryEngine` 装配；`integration_faiss` 使用 128 维 `faiss.IndexFlatIP`，embedding fixture 必须同维度。

## 离线评测样本

| 数据集 | 覆盖重点 |
|---|---|
| `private_basic.jsonl` | 私聊事实、偏好、计划与边界。 |
| `group_topic_shift.jsonl` | 群聊话题切换、决策和归属。 |
| `emotion_context.jsonl` | 情绪、语气和支持偏好。 |
| `graph_relation.jsonl` | 关系、来源、依赖和多跳图召回。 |
| `noise_negative.jsonl` | 无相关结果、错人/错群/虚假关系。 |

fixture schema 变化必须同步评测服务与测试；评测必须对结果 ID、指标或持久化输出作可观察验证，不用测试名称代替真实断言。

## 变更定位

| 变更类型 | 首选测试位置 | 常见相邻门禁 |
|---|---|---|
| Page API 请求/响应 | `test_api_<domain>.py` | `test_page_api.py`、`test_page_api_contract.py` |
| Store/SQL/事务 | `test_<store>.py` | 相关 Manager/API 测试、并发冲突测试 |
| Manager 业务规则 | `test_managers_<domain>.py` | 对应 Store 与 API 测试 |
| 召回、注入、格式化 | `test_handlers.py`、`test_injection_*.py`、`test_memory_formatter.py` | `integration/test_pipeline_event.py` |
| 配置/schema | `test_config_contract.py`、`test_api_config.py` | `test_plugin_init.py`、`test_project_metadata.py` |
| 插件初始化/导入 | `test_plugin_init.py`、`test_plugin_package_imports.py` | `test_event_handler.py` |
| 检索质量 | `evaluation/` 与 `fixtures/retrieval/` | 相关 retriever 单测 |
| 跨模块主路径 | `integration/test_pipeline_*.py` | `python scripts/run_smoke.py -q` |

## 精确验证命令

均从仓库根目录执行，先跑最小相关文件再扩大范围：

```powershell
# 单文件行为回归
uv run --locked python -m pytest tests/test_<domain>.py -q

# 单个测试节点
uv run --locked python -m pytest tests/test_<domain>.py::TestClass::test_behavior -q

# Page API 契约变更
uv run --locked python -m pytest tests/test_api_<domain>.py tests/test_page_api_contract.py -q

# 单条真实管线
uv run --locked python -m pytest tests/integration/test_pipeline_ingest.py -q

# 五条集成 smoke
uv run --locked python scripts/run_smoke.py -q
```

## 禁止事项

- 禁止在 AstrBot Mock 安装前导入依赖 AstrBot 的生产模块。
- 禁止访问真实 LLM、Embedding Provider、用户数据目录或生产 SQLite。
- 禁止硬编码开发机临时目录、绝对路径、凭据或用户标识。
- 禁止用 `sleep` 和宽松超时证明并发正确性；使用事件、锁、屏障或可控 mock。
- 禁止无理由 `skip`/`xfail`；环境能力缺失必须写出明确条件。
- 禁止修改测试迎合错误实现；先确认公开契约与调用方，再修根因。

继续阅读[数据流与关键链路](/development/data-flow)与[Dashboard 前端开发](/development/frontend)。
