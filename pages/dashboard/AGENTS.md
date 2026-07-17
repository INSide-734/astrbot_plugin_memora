# Memora Dashboard

## 模块职责

`pages/dashboard` 是 Memora 的 React 18 管理面板。它运行在 AstrBot 插件页面桥接环境中，通过 `src/lib/bridge.ts` 调用后端，并必须保持 Vite 生成的 classic-script 单 bundle 产物格式。

## 应用结构

- `src/App.tsx`: 全局应用壳、Hash 路由、移动端菜单、实时状态与全局搜索。
- `src/components/layout/Sidebar.tsx`: 五组可折叠导航，桌面端支持图标收起模式。
- `src/components/layout/PageLayout.tsx`: 所有页面共享的布局原语。
- `src/pages/`: 16 个功能页面；每个页面必须使用 `PageFrame`。
- `src/components/ui/`: shadcn/ui 本地组件，基础 primitive 为 Base UI，图标库为 Lucide。
- `src/index.css`: shadcn 语义 token、亮暗主题和旧 token 兼容别名。

## 统一布局

页面必须从以下三种模板中选择，不应重新实现独立的页面壳：

| 模板 | 适用页面 | 行为 |
|------|----------|------|
| `standard` | 概览、时间线、召回、笔记、洞察、关系、系统页 | 内容区自然滚动，最大宽度 1440px |
| `dense` | 记忆、知识库、画像、黑话、注入策略等高密度数据与配置页 | 固定页头/顶层切换，活跃内容或表格区域独立滚动 |
| `workspace` | 图谱等工具型页面 | 占满可用空间，使用稳定网格与最小尺寸约束 |

标准组合顺序为 `PageFrame`、`PageHeader`、可选 `PageToolbar`、`PageContent`。指标集合使用 `MetricGrid`；加载、空数据和错误状态使用 `Skeleton` 或 `StatePanel`。

## 视觉规范

- 使用 shadcn 语义类：`bg-background`、`bg-card`、`text-foreground`、`text-muted-foreground`、`border-border`、`bg-primary`。
- 旧的 `--color-*` 与 `--text-*` 只作为复杂旧面板的兼容别名，不得在新代码中增加消费者。
- 主题为中性黑白体系；成功、警告、错误等功能状态色可以保留。
- 最大常规圆角为 8px，即 Tailwind `rounded-lg`；不要新增 `rounded-xl`、`rounded-2xl` 或 `rounded-3xl`。
- 卡片只用于独立对象、指标和明确分组，不把普通页面段落包装成浮动卡片，也不嵌套卡片。
- 使用 Geist 字体；页面标题、面板标题和正文必须保持层级，不按视口宽度缩放字号。

## 组件规范

- 优先复用 `src/components/ui` 中已有的 shadcn 组件，不手写等价的按钮、复选框、文本域、表格、Dialog 或 Sheet。
- 图标命令使用 `Button` 的 icon size 与 Lucide 图标，并提供 `aria-label`/`title`。
- Dialog 和 Sheet 必须包含可访问名称；表格选择框、进度条和分页导航必须有明确的可访问名称。
- 2 到 7 个互斥选项使用 Tabs、ToggleGroup 或等价分段控件；数值范围可以使用滑杆。
- 详情使用受控 Sheet，创建/确认流程使用 Dialog；不得恢复已删除的自定义 `Modal`。
- 所有页面必须在桌面端和移动端保持可滚动、无重叠、无横向内容挤压。固定格式区域应设置 `minmax`、最小宽高或横向滚动边界。

## 导航与行为契约

导航固定为五组：Overview、Memory、Insights、Relationships、System。Hash 路由、三语言 key、SSE 状态和 AstrBot bridge 请求形状属于兼容契约，布局重构不得改变。

知识库列表和画像使用后端 `limit`/`offset` 真分页。知识库搜索和黑话候选接口没有 offset，不得伪造分页。查询范围、筛选条件或页码变化时必须清除隐藏选择，避免对不可见数据执行批量操作。

InjectionStrategyPage 位于 Memory 组并使用 `dense` 模板。固定 PageHeader 与 Overview、Strategy Configuration、Decision History 三个顶层 Tab；各活跃 Tab 的 PageContent 是唯一纵向滚动者。Overview/Config 使用 constrained 宽度，Decision History 使用 full 宽度和受控横向表格滚动。概览、列表与详情读取 SQLite 全量持久化结果的 Page API；配置保存继续保留 revision、草稿和冲突处理。

## 测试与验证

行为变更使用 Vitest + React Testing Library 先写失败测试。直接运行 Vitest 时必须指定 jsdom；优先使用项目脚本：

```bash
npm test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

浏览器 smoke 后必须检查截图内容，尤其是 Graph 画布、移动端侧栏和加载遮罩。Injection 页面必须人工检查 `injection-overview.png`、`injection-config-conflict.png`、`injection-decisions.png`、`mobile-injection-detail.png` 和 2048px 的 `wide-injection-overview.png`；重点确认唯一滚动所有权、表格横向边界、配置冲突草稿和详情 Sheet 固定底栏。仓库级最终门禁为：

```bash
python scripts/check_all.py
```

## 变更记录

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-17 | Injection 策略工作台对齐 | 页面总数更新为 16；注入页采用 dense 三 Tab、单滚动所有者、SQLite/Page API 数据源与五张 browser smoke 基线 |
| 2026-07-10 | Dashboard 布局与视觉系统统一 | 引入三类页面模板、五组导航、shadcn 语义主题、统一数据页/详情面板与响应式约束 |
