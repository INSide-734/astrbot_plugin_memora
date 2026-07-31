[`根级 AGENTS.md`](../AGENTS.md) > **website**

# VitePress 文档站维护边界

**最后更新：** 2026-07-31  
**适用范围：** `website/` 中的站点配置、主题、公开文档与本地验证

## 职责

- `docs/` 保存公开中文用户文档和开发指南。
- `docs/.vitepress/` 维护导航、搜索、Pages 基础路径和轻量主题。
- `package.json` 与锁文件定义独立文档工具链。
- `README.md` 和 `DESIGN.md` 说明模块维护方式与稳定设计边界，不参与公开构建。

## 内容权威

- 安装、配置、命令、Dashboard、运维和开发指南以 `docs/` 为详细权威来源。
- 项目级架构不变量仍以根级 `DESIGN.md` 为准。
- 配置字段和默认值以 `_conf_schema.json`、Pydantic 模型、运行时读取和契约测试为准。
- `docs/reference/configuration/` 由维护者逐项解释配置行为，不使用 Schema 自动生成正文；字段变化时人工核对并同步。
- 发布历史以根级 `CHANGELOG.md` 为准。
- 同一事实只保留一份详细说明，其他页面使用链接和必要摘要。

## 实施约束

- 公开内容只使用脱敏示例，不记录用户数据、内部 ID、Provider 信息或原始堆栈。
- 不把 `AGENTS.md`、计划、生成报告、缓存、构建产物或截图放入 `docs/`。
- 主题覆盖保持克制，不重写 VitePress 原生搜索、导航和可访问性交互。
- 使用 OKLCH token、4px 间距体系和不超过 8px 的卡片圆角。
- 文字间距固定为 0；正文不得小于 1rem，按钮和移动导航保留可用触控面积。
- 每个 Markdown 文件不得超过 400 个物理行；源码不得超过 800 行。
- 修改页面路径时同步侧栏、页内链接、README 和编辑链接。
- 修改根级 `logo.png` 时同步 `docs/public/logo.png`。

## 验证

从 `website/` 执行：

```powershell
npm ci
npm run docs:build
npm run docs:preview
```

完成前还必须：

- 运行本轮文件的 pre-commit；只有公开的可执行契约发生变化时才运行对应 Python 检查。
- 检查所有相对 Markdown 链接。
- 在桌面和移动视口验证首页、搜索、侧栏、表格、代码块和 Mermaid。
- 人工查看浏览器截图，确认无空白、重叠和页面级横向溢出。

## 相关上下文

- [模块说明](README.md)
- [模块设计](DESIGN.md)
- [项目设计契约](../DESIGN.md)
- [普通文档维护边界](../docs/AGENTS.md)
