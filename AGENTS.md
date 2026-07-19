# Memora — AI 协作入口

**最后更新：** 2026-07-18 19:13:27 +08:00

## 项目定位

Memora 是 AstrBot 的长期记忆插件。`main.py` 注册 `MemoraPlugin`；后端以 Python 3.12、SQLite/FTS5、FAISS、Quart 与 Pydantic 为主，`pages/dashboard/` 是 React 18 + TypeScript + Vite 管理面板。源码、配置模型与可执行测试高于文档；冲突时核对当前实现。

## 架构与入口

```mermaid
flowchart LR
    AstrBot["AstrBot 事件与 Provider"] --> Plugin["main.py / MemoraPlugin"]
    Plugin --> Init["PluginInitializer / ComponentFactory"]
    Plugin --> Events["EventHandler"]
    Plugin --> API["PluginPageApi"]
    Init --> Engine["MemoryEngine"]
    Events --> Recall["RecallHandler"]
    Events --> Reflect["ReflectionHandler"]
    Recall --> Retrieval["BM25 + FAISS + Graph"]
    Recall --> Injection["Router + Executor"]
    Reflect --> Processor["MemoryProcessor"]
    Processor --> Engine
    Engine --> SQLite["SQLite 权威持久化"]
    API --> Dashboard["AstrBot bridge / Dashboard"]
```

- `main.py`：插件注册、hooks、生命周期与工具注册。
- `core/plugin_initializer.py`：Provider 等待、组件构建、失败回滚与关停。
- `core/event_handler.py`：消息捕获、召回、反思与维护任务协调。
- `core/page_api.py`：Page API mixin 组合与 `/astrbot_plugin_memora/page/*` 路由。
- `pages/dashboard/src/main.tsx`、`App.tsx`：前端入口与 Hash 导航。

## 模块导航

| 模块 | 职责 | 上下文 |
|---|---|---|
| `core/` | 后端总览 | [AGENTS.md](./core/AGENTS.md) |
| `core/base/` | 配置、异常、基础约束 | [AGENTS.md](./core/base/AGENTS.md) |
| `core/initializer/` | Provider、数据库与组件构造 | [AGENTS.md](./core/initializer/AGENTS.md) |
| `core/handlers/` | 召回与反思编排 | [AGENTS.md](./core/handlers/AGENTS.md) |
| `core/injection/` | 注入路由、选择、执行与记录 | [AGENTS.md](./core/injection/AGENTS.md) |
| `core/managers/` | 生命周期与领域管理器 | [AGENTS.md](./core/managers/AGENTS.md) |
| `core/processors/` | 抽取、分类、格式化与话题处理 | [AGENTS.md](./core/processors/AGENTS.md) |
| `core/retrieval/` | 多路检索、融合与重排 | [AGENTS.md](./core/retrieval/AGENTS.md) |
| `core/storage/` | SQLite、FTS、图与决策持久化 | [AGENTS.md](./core/storage/AGENTS.md) |
| `core/api/` | Page API 与响应契约 | [AGENTS.md](./core/api/AGENTS.md) |
| `core/security/` | Prompt 保护与输出护栏 | [AGENTS.md](./core/security/AGENTS.md) |
| `core/review/` | 复核检测、队列与动作历史 | [AGENTS.md](./core/review/AGENTS.md) |
| `core/models/` | 共享领域模型 | [AGENTS.md](./core/models/AGENTS.md) |
| `core/tools/` | AstrBot Agent 工具 | [AGENTS.md](./core/tools/AGENTS.md) |
| `core/commands/` | `/lmem` 查询与维护命令 | [AGENTS.md](./core/commands/AGENTS.md) |
| `core/cleaners/` | 历史注入清理 | [AGENTS.md](./core/cleaners/AGENTS.md) |
| `core/extractors/` | 消息内容提取 | [AGENTS.md](./core/extractors/AGENTS.md) |
| `core/dedup/` | 消息去重 | [AGENTS.md](./core/dedup/AGENTS.md) |
| `core/validators/` | 索引/持久化校验与重建 | [AGENTS.md](./core/validators/AGENTS.md) |
| `core/schedulers/` | 衰减与回填调度 | [AGENTS.md](./core/schedulers/AGENTS.md) |
| `core/monitoring/` | 指标、追踪与质量评分 | [AGENTS.md](./core/monitoring/AGENTS.md) |
| `core/diagnostics/` | 诊断事件与健康评分 | [AGENTS.md](./core/diagnostics/AGENTS.md) |
| `core/evaluation/` | 离线检索评测 | [AGENTS.md](./core/evaluation/AGENTS.md) |
| `core/affection/` | 好感度与 Bot 情绪 | [AGENTS.md](./core/affection/AGENTS.md) |
| `core/expression/` | 表达模式学习 | [AGENTS.md](./core/expression/AGENTS.md) |
| `core/jargon/` | 黑话挖掘、存储与查询 | [AGENTS.md](./core/jargon/AGENTS.md) |
| `core/social/` | 社交关系 | [AGENTS.md](./core/social/AGENTS.md) |
| `core/utils/` | 通用工具与降级实现 | [AGENTS.md](./core/utils/AGENTS.md) |
| `pages/dashboard/` | React 管理面板 | [AGENTS.md](./pages/dashboard/AGENTS.md) |
| `tests/` | pytest 测试体系 | [AGENTS.md](./tests/AGENTS.md) |
| `scripts/` | 门禁、smoke 与 benchmark | [AGENTS.md](./scripts/AGENTS.md) |
| `docs/` | 开发、设计与计划维护边界 | [AGENTS.md](./docs/AGENTS.md) |

## 跨模块契约

- 写入链：AstrBot 消息 → `EventHandler` → `ConversationManager`/`MemoryProcessor` → `MemoryEngine` → SQLite。FTS、FAISS 与图索引是可重建派生数据。
- 召回链：请求 → 改写/隔离过滤 → 双路检索与重排 → `InjectionStrategyRouter` → `InjectionExecutor`。动态记忆不得进入 System Prompt；请求变更须先完整构建再原子应用。
- 注入观测只持久化 allowlist 标量；不得记录 query、prompt、记忆正文或 ID 列表、原始身份、Provider 密钥/请求头/API 地址或堆栈。
- `PluginPageApi` 与 `src/lib/bridge.ts` 是后端/前端边界。写回保留 revision、字段校验、冲突处理和显式错误 envelope；不得伪造客户端分页。
- 配置叶变更同步 `_conf_schema.json`、Pydantic 模型、运行时读取、Dashboard 类型/默认值与契约测试。

## 实施约束

- 先阅读目标目录 `AGENTS.md`；复用既有路径，不创建兼容双轨或重复抽象。
- 新行为与 bug 修复遵循 RED → GREEN → REFACTOR；不顺手修改无关代码或用户本地改动。
- `asyncio.CancelledError` 必须传播；普通可恢复失败不得破坏聊天主链路。
- SQL 值参数绑定；动态标识符只允许固定 allowlist。
- Dashboard 复用 Base UI-backed shadcn、`PageFrame`、语义 token、Lucide 与三语言 key；桌面/移动端均需可访问、可滚动、无重叠和页面级横向溢出。

## 语言规范

- 生产代码中的注释、docstring、日志消息和可观测性 reason 文本统一使用中文。
- 测试、脚本和文档中的解释性注释也使用中文；Python/SQLite/API 的固定标识符、枚举值、协议字段和第三方原始错误信息可保留英文。
- 新增或修改代码时，先把已有英文注释、docstring 和日志改为中文，再继续扩展行为；不要只为新增代码遵守而留下同一文件中可避免的英文解释文本。

## 管理命令

以下命令由 `core/command_endpoints.py` 注册；修改命令名或行为时，必须同步 README、CHANGELOG、测试与本页：

`/lmem status`、`/lmem search <query>`、`/lmem forget <id>`、`/lmem rebuild-index`、`/lmem rebuild-graph`、`/lmem webui`、`/lmem summarize`、`/lmem reset`、`/lmem cleanup`、`/lmem help`。

## 验证入口

按范围选择最窄命令；完整门禁由 `scripts/check_all.py` 编排，schema validator 仅在对应脚本存在时执行：

```powershell
python -m pytest tests -q
python scripts/run_smoke.py -q
python scripts/check_all.py
python scripts/benchmark_recall_cost.py --all
python scripts/benchmark_injection_decisions.py

Set-Location pages/dashboard
npm test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

浏览器 smoke 后必须人工检查截图；日志不能替代视觉确认。

## 代码探索与降级

需要理解代码上下文或进行自然语言定位时，优先使用
`mcp__fast-context__fast_context_search`。如果服务不可用或返回资源错误，使用
`rg`、PowerShell `Select-String` 和定向文件读取降级，并记录失败原因；不要把
`node_modules/`、`dist/`、`build/`、运行时数据或工作树当作架构事实来源。

## 扫描边界

跳过 `node_modules/`、`dist/`、`build/`、覆盖率输出、缓存、二进制、运行时数据、工作树和 Dashboard 生成物；这些路径不是架构事实来源。
