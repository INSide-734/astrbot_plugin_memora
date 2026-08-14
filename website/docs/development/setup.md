# 开发环境

本页面向 Memora 贡献者说明可复现的本地环境。除非特别说明，命令从仓库根目录执行。

## 项目结构

```text
astrbot_plugin_memora/
├── main.py
├── core/
│   ├── event_handler.py
│   ├── platform/          # 组合根、配置、安全、资源与 transport 宿主适配
│   ├── shared/            # 无状态 DTO、契约与纯工具
│   └── features/          # 按领域组织的业务实现
├── pages/dashboard/
├── website/
├── tests/
├── scripts/
├── _conf_schema.json
├── pyproject.toml
└── uv.lock
```

## 代码组织

| 位置 | 职责 |
|---|---|
| `main.py` | 插件注册、hooks、生命周期与工具注册。 |
| `core/event_handler.py` | 消息捕获、召回、反思与维护任务协调。 |
| `core/platform/composition/` | Provider 等待、组件构建、失败回滚与有序关闭。 |
| `core/platform/config/` | 配置模型、校验、revision 与运行时读取。 |
| `core/platform/transport/` | Page API、`/memora` 命令与 Agent 工具的宿主适配。 |
| `core/platform/security/` | Prompt 防护与输出护栏。 |
| `core/shared/` | DTO、错误、序列化与 SQL 原语、窄端口契约。 |
| `core/features/*/` | 记忆、召回、注入、检索、身份、演化、质量等业务实现。 |

依赖方向固定为 feature 依赖 shared，由 `core/platform/composition/` 集中装配；feature 不得反向依赖组合根。完整运行时边界见 [`core/AGENTS.md`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/core/AGENTS.md)。

## Python 环境

Python 版本和依赖以 `pyproject.toml`、`.python-version` 与 `uv.lock` 为准。新克隆后执行：

```powershell
uv sync --locked --dev
uv run --locked pre-commit install --install-hooks
```

## Dashboard

Dashboard 开发需要 Node.js 20：

```powershell
Set-Location pages/dashboard
npm ci
npm run dev
```

## 文档站

VitePress 2 文档站使用独立 npm 工程，需要 Node.js 22.12 或更高版本：

```powershell
Set-Location website
npm ci
npm run docs:dev
```

默认端口是 `5173`。Dashboard 已占用时运行 `npm run docs:dev -- --port 5174`。

## 插件打包

从仓库根目录执行：

```powershell
uv run --locked python scripts/package_plugin.py
uv run --locked python scripts/package_plugin.py --mode both --from-git
```

runtime 模式会构建 Dashboard，但不会自动执行 `npm ci`。产物写入被忽略的 `dist/`。完整产物校验与发布门禁见[打包与发布](/development/packaging)。

项目级边界见 [AGENTS.md](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/AGENTS.md) 和 [DESIGN.md](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/DESIGN.md)。

环境就绪后，继续阅读[开发指南](/development/guide)了解扩展入口与不变量，提交前按[质量门禁](/development/quality-gates)执行检查。
