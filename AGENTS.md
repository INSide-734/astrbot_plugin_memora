# Memora — AstrBot 长期记忆插件协作指南

## 项目目标

Memora 为 AstrBot 提供完整的长期记忆生命周期：消息捕获、内容抽取、
MemoryAtom 建模、SQLite/FAISS 存储、BM25 与向量混合检索、记忆衰减、
图记忆、知识库、笔记、画像、社交关系与 Dashboard 管理。

本文件是仓库级协作入口。模块实现细节以源码、模块文档和测试为准；
文档与源码冲突时，以当前源码和可执行测试为权威证据。

## 架构入口

```text
AstrBot
  -> main.py / MemoraPlugin
  -> PluginInitializer / ComponentFactory
  -> EventHandler
       -> ConversationManager
       -> MemoryProcessor
       -> RecallHandler
       -> ReflectionHandler
  -> MemoryEngine
       -> SQLite stores
       -> BM25 + vector retrieval
       -> FAISS / graph memory
  -> PluginPageApi
       -> Dashboard bridge
```

自适应记忆注入由 `RecallHandler` 协调，`InjectionStrategyRouter` 负责确定性
Manual/Auto/Hybrid 路由，`InjectionExecutor` 负责全局硬预算、格式化、保护和
原子请求变更。动态记忆不得进入 System Prompt。

## 模块文档索引

| 模块 | 职责 | 维护文档 |
|---|---|---|
| `core/` | 核心业务与运行时总览 | [core/CLAUDE.md](./core/CLAUDE.md) |
| `core/base/` | 配置、常量、异常 | [core/base/CLAUDE.md](./core/base/CLAUDE.md) |
| `core/managers/` | 生命周期与业务管理器 | [core/managers/CLAUDE.md](./core/managers/CLAUDE.md) |
| `core/processors/` | 会话处理、抽取、格式化 | [core/processors/CLAUDE.md](./core/processors/CLAUDE.md) |
| `core/retrieval/` | 检索、融合、重排序 | [core/retrieval/CLAUDE.md](./core/retrieval/CLAUDE.md) |
| `core/storage/` | SQLite/FAISS 持久化 | [core/storage/CLAUDE.md](./core/storage/CLAUDE.md) |
| `core/api/` | Page API | [core/api/CLAUDE.md](./core/api/CLAUDE.md) |
| `core/handlers/` | 召回与反思编排 | [core/handlers/CLAUDE.md](./core/handlers/CLAUDE.md) |
| `core/security/` | Prompt 保护与输出护栏 | [core/security/CLAUDE.md](./core/security/CLAUDE.md) |
| `core/tools/` | Agent 工具 | [core/tools/CLAUDE.md](./core/tools/CLAUDE.md) |
| `core/models/` | 数据模型 | [core/models/CLAUDE.md](./core/models/CLAUDE.md) |
| `core/monitoring/` | 指标、追踪、质量评分 | [core/monitoring/CLAUDE.md](./core/monitoring/CLAUDE.md) |
| `core/evaluation/` | 离线检索评测 | [core/evaluation/CLAUDE.md](./core/evaluation/CLAUDE.md) |
| `pages/dashboard/` | React 管理面板 | [pages/dashboard/AGENTS.md](./pages/dashboard/AGENTS.md) |
| `tests/` | 后端、集成、压力和契约测试 | [tests/CLAUDE.md](./tests/CLAUDE.md) |
| `scripts/` | 门禁、smoke 与 benchmark | [scripts/CLAUDE.md](./scripts/CLAUDE.md) |
| `docs/` | 开发和设计文档 | [docs/CLAUDE.md](./docs/CLAUDE.md) |

其余领域模块遵循相邻 `CLAUDE.md` 的职责与边界，不在根文档中复制细节。

## 开发环境

## 语言规范

- 生产代码中的注释、docstring、日志消息和可观测性 reason 文本统一使用中文。
- 测试、脚本和文档中的解释性注释也使用中文；Python/SQLite/API 的固定标识符、枚举值、协议字段和第三方原始错误信息可保留英文。
- 新增或修改代码时，先把已有英文注释、docstring 和日志改为中文，再继续扩展行为；不要只为新增代码遵守而留下同一文件中可避免的英文解释文本。
- 该规范回溯适用于已经完成的功能、当前正在实施的改动以及执行计划中的后续任务；继续触及旧文件时，先完成本次范围内的中文迁移再提交行为变更。

- Python 3.12+
- AstrBot 4.24.2+
- Node.js 20+
- SQLite 与 FAISS

安装：

```powershell
python -m pip install -r requirements.txt
Set-Location pages/dashboard
npm ci
```

## 质量门禁

统一门禁：

```powershell
python scripts/check_all.py
```

分层验证：

```powershell
python -m pytest tests -q
python scripts/run_smoke.py -q
python scripts/benchmark_recall_cost.py --all
python scripts/benchmark_injection_decisions.py

Set-Location pages/dashboard
npm test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

浏览器 smoke 完成后必须人工打开截图，确认无残留 loading 遮罩、空白、裁切、
重叠、页面级横向溢出或移动端滚动问题。日志不能替代截图检查。

## 管理命令

- `/memora status`：查看记忆系统状态。
- `/memora search <query>`：搜索长期记忆。
- `/memora forget <id>`：删除指定记忆。
- `/memora rebuild-index`：重建向量索引。
- `/memora rebuild-graph`：重建图记忆索引。
- `/memora webui`：显示 Dashboard 指引。
- `/memora summarize`：手动触发记忆总结。
- `/memora reset`：清除当前会话上下文。
- `/memora cleanup`：清理历史注入片段。
- `/memora help`：显示当前命令帮助。

## 实施约束

### 先理解，再修改

- 不猜测运行时 API；优先核对当前 AstrBot SDK 和仓库源码。
- 有多种解释时先说明差异和取舍。
- 设计已由规格或实施计划锁定时，按既有设计执行，不创建平行体系。

### 最小且可追溯

- 每个改动必须能追溯到需求、失败测试或门禁发现。
- 不顺手重构无关代码，不清理用户的本地修改。
- 新行为和 bug 修复遵循 RED → GREEN → REFACTOR。
- 不使用 `git add .`；按关注点精确暂存并检查 staged diff。

### 配置与兼容性

- 配置合并顺序是 AstrBot config → persisted config → 默认值。
- `_conf_schema.json`、Pydantic 模型、Dashboard 默认值和测试必须同步。
- 破坏性配置变更应明确记录；不得暗中加入未批准的迁移路径。

### 数据与安全

- 动态 SQL 标识符必须经过固定 allowlist，值始终使用参数绑定。
- 决策记录不得持久化 query、prompt、记忆正文或 ID 列表、原始身份、
  Provider 密钥/请求头/API 地址或堆栈。
- Dashboard URL、浏览器存储、调用日志和 mock 同样遵守上述边界。
- `asyncio.CancelledError` 必须继续传播；普通失败不得破坏聊天主链路。

### Dashboard

- 保持现有主题、PageFrame 布局、Base UI-backed shadcn 组件与三语言契约。
- 需要写回的页面变更必须同时具备后端/API 路径与冲突处理。
- 数据筛选、分页和选择状态必须使用服务端真实契约，不能伪造客户端分页。

## 代码探索与降级

需要理解代码上下文或自然语言定位时，优先使用
`mcp__fast-context__fast_context_search`。如果服务不可用或返回资源错误，按
[开发环境说明](./docs/DEV_SETUP.md) 使用 `rg`、PowerShell `Select-String` 和
定向文件读取降级，并记录失败原因。

大于 30 行或设计级变更必须运行 change/quality 校验；安全边界、持久化、API
或 Prompt 变更必须运行 security 校验。最终完成声明必须以本轮新鲜命令输出为证据。
