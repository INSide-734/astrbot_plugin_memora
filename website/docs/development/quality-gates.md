# 质量门禁

按改动范围选择最窄命令。普通文档站变更不需要运行后端完整回归或 Dashboard smoke，但必须实际构建并检查页面。

## Python 变更

本轮修改 Python 文件时按顺序运行：

```powershell
uv run --locked ruff check --fix path/to/file.py
uv run --locked ruff format path/to/file.py
uv run --locked ruff check path/to/file.py
```

行为变更根据受影响模块运行最窄 pytest；完整仓库门禁为：

```powershell
uv run --locked python scripts/check_all.py
```

## Dashboard 变更

```powershell
Set-Location pages/dashboard
npm test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

浏览器 smoke 后必须人工检查截图。

## 文档站变更

```powershell
Set-Location website
npm ci
npm run docs:build
npm run docs:preview
```

检查首页、侧栏、搜索、代码块、表格、Mermaid 和内部链接。桌面与移动视口都必须无重叠和页面级横向溢出。

## 提交前检查

从仓库根目录执行：

```powershell
git diff --check
uv run --locked pre-commit run --files path/to/changed-file
```

hook 改写文件后审阅差异并重复运行。不要使用 `--no-verify`、`SKIP` 或批量忽略规则绕过失败。

## 文件长度

- 新增或修改源码、测试文件不超过 800 个物理行。
- Markdown 设计和计划文件不超过 400 行。
- 不通过超长单行规避限制。

详细脚本职责见 [scripts/AGENTS.md](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/scripts/AGENTS.md)。
