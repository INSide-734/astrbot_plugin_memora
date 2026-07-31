# Memora 文档站

`website/` 保存 Memora 的 VitePress 中文文档站。公开内容位于 `docs/`，通过独立 npm 工程构建，并由 GitHub Actions 部署到 GitHub Pages。

## 本地使用

前置条件为 Node.js 22.12 或更高版本和 npm。从仓库根目录执行：

```powershell
Set-Location website
npm ci
npm run docs:dev
```

开发服务器默认监听 `http://localhost:5173/`。端口被 Dashboard 占用时，可通过 `npm run docs:dev -- --port 5174` 改用其他端口。生产构建和本地预览命令为：

```powershell
npm run docs:build
npm run docs:preview
```

构建成功时，静态产物位于 `docs/.vitepress/dist/`；该目录属于生成物，不进入版本控制。

## 内容边界

- `docs/` 是详细中文用户文档和开发指南的权威来源。
- 根级 `README.md` 只保留项目介绍、最短安装路径和站点入口。
- 根级 `DESIGN.md` 继续定义稳定架构契约；站点只提供面向读者的架构导览。
- 根级 `CHANGELOG.md` 继续记录发布事实，站点不维护副本。
- `AGENTS.md`、任务计划、运行时数据和敏感诊断材料不得进入 `docs/`。

## 目录结构

```text
website/
├── AGENTS.md
├── DESIGN.md
├── README.md
├── package.json
├── package-lock.json
└── docs/
    ├── .vitepress/
    ├── public/
    ├── index.md
    ├── guide/
    ├── concepts/
    ├── features/
    ├── operations/
    ├── reference/
    └── development/
```

## 维护要求

- 行为说明必须核对当前代码、Schema 和测试。
- 每个 Markdown 文件不得超过 400 个物理行。
- 图片、命令输出和示例必须脱敏。
- 修改导航或移动页面时必须同步所有相对链接。
- 完成前运行站点构建、pre-commit，并检查桌面和移动截图。

## 相关文档

- [站点设计](DESIGN.md)
- [局部协作约束](AGENTS.md)
- [项目设计契约](../DESIGN.md)
- [根级文档维护边界](../docs/AGENTS.md)
