# 开发环境

本页面向 Memora 贡献者说明可复现的本地环境。除非特别说明，命令从仓库根目录执行。

## 项目结构

```text
astrbot_plugin_memora/
├── main.py
├── core/
├── pages/dashboard/
├── website/
├── tests/
├── runtime_tests/
├── scripts/
├── _conf_schema.json
├── pyproject.toml
└── uv.lock
```

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

runtime 模式会构建 Dashboard，但不会自动执行 `npm ci`。产物写入被忽略的 `dist/`。

项目级边界见 [AGENTS.md](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/AGENTS.md) 和 [DESIGN.md](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/DESIGN.md)。
