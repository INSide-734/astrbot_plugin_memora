# Memora 开发说明

本文面向 Memora 的开发者和贡献者，说明项目结构、开发环境、验证入口、Dashboard 开发与插件打包流程。除非特别说明，所有命令都从仓库根目录执行；插件安装、配置和使用方法见 [README](../README.md)。

## 项目结构

```text
astrbot_plugin_memora/
├── main.py              # AstrBot 插件入口
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 Schema
├── core/                # Python 运行时、存储、检索、处理和 API
├── pages/dashboard/     # React 管理面板
├── tests/               # pytest 测试
├── scripts/             # 校验、smoke、benchmark 和打包脚本
└── docs/                # 开发、设计与计划文档
```

项目级设计见 [DESIGN.md](../DESIGN.md)。协作与模块入口见 [AGENTS.md](../AGENTS.md)、[core/AGENTS.md](../core/AGENTS.md) 和 [pages/dashboard/AGENTS.md](../pages/dashboard/AGENTS.md)。

## 环境准备

- Python 版本和依赖以 `pyproject.toml`、`.python-version` 与 `uv.lock` 为准。
- Dashboard 开发和重新构建前端产物需要 Node.js `20`。
- 新克隆仓库后同步锁定环境并安装本地 Git hook：

```powershell
uv sync --locked --dev
uv run --locked pre-commit install --install-hooks
```

Dashboard 依赖需要在前端目录单独安装：

```powershell
Set-Location pages/dashboard
npm ci
Set-Location ../..
```

## 开发与验证

仓库级环境约束和完整质量门禁见 [AGENTS.md](../AGENTS.md)，脚本职责与精确入口见 [scripts/AGENTS.md](../scripts/AGENTS.md)。根据改动范围选择最窄命令。

后端测试与集成 smoke：

```powershell
uv run --locked python -m pytest tests -q
uv run --locked python scripts/run_smoke.py -q
```

Dashboard 测试与产物检查需要在 `pages/dashboard/` 中执行：

```powershell
Set-Location pages/dashboard
npm test
npm run build
npm run check:artifacts
Set-Location ../..
```

对本轮修改文件运行提交前门禁：

```powershell
uv run --locked pre-commit run --files path/to/changed-file
```

需要验证整个仓库时运行统一门禁：

```powershell
uv run --locked python scripts/check_all.py
```

Dashboard 的 `npm run smoke:runtime` 与 `npm run smoke:browser` 属于完整门禁；浏览器 smoke 完成后还需要人工检查截图。文档变更通常只需检查差异、相对链接和对应文件的 pre-commit，无需运行后端、Dashboard 或完整仓库门禁。

## Dashboard 开发

在前端目录启动开发服务器：

```powershell
Set-Location pages/dashboard
npm ci
npm run dev
```

`npm run dev` 会保持前台运行；结束开发服务器后再返回仓库根目录。

## 插件打包

默认生成可直接安装的精简 runtime 包：

```powershell
python scripts/package_plugin.py
```

生成 source 包或同时生成两种包：

```powershell
python scripts/package_plugin.py --mode source
python scripts/package_plugin.py --mode both --from-git
```

`runtime` 模式会在 `pages/dashboard/` 执行 `npm run build`，但不会自动执行 `npm ci`。默认产物写入 `dist/`，文件名中的版本来自 `metadata.yaml`：

```text
dist/astrbot_plugin_memora-1.0.0-runtime.zip
dist/astrbot_plugin_memora-1.0.0-source.zip
```

使用 `--output-dir releases` 可以将产物写入仓库根目录下的 `releases/`。`dist/` 和 `releases/` 中的安装包是生成产物，不应作为源码提交。
