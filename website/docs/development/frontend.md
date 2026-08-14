# Dashboard 前端开发

本页说明 `pages/dashboard/` 的技术栈、页面规范与验证流程。前端完整上下文见 [`pages/dashboard/AGENTS.md`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/pages/dashboard/AGENTS.md)。

## 技术栈与入口

- React 18.3、TypeScript 5.6、Vite 6、Tailwind CSS 3.4。
- shadcn 本地组件以 `@base-ui/react` primitives 为基础（不是 Radix），样式组合使用 CVA、`clsx`、`tailwind-merge`。
- 图谱 `@antv/g6`、图标 Lucide、动画 Framer Motion、图表 Recharts、长列表 TanStack Virtual、Toast Sonner。
- `src/main.tsx` 是入口；`src/App.tsx` 拥有全局壳、懒加载路由、移动菜单、SSE 状态与全局搜索。
- `src/lib/bridge.ts` 把相对请求转换为 AstrBot 的 `page/` endpoint，统一解析响应 envelope。

## 目录地图

| 路径 | 责任 |
|---|---|
| `src/pages/` | 16 个功能页面；必须使用共享 `PageFrame`。 |
| `src/components/layout/` | Sidebar、PageFrame、PageHeader、PageToolbar、PageContent 等布局原语。 |
| `src/components/ui/` | Base UI-backed shadcn 本地组件；优先复用，不手写等价控件。 |
| `src/hooks/` | 主题、i18n、SSE、编辑、配置同步与数据流。 |
| `src/lib/` | bridge、导航、i18n、常量与纯工具。 |
| `src/mock/` | browser/runtime smoke 的确定性 bridge 与三语言模拟数据。 |
| `src/types/` | 页面、API、编辑与领域类型。 |

## Hash 路由与导航

`src/App.tsx` 的 `HASH_TO_PAGE` 与 `src/lib/navigation.ts` 必须一起维护，不引入第二套路由器。五个导航组：

| 导航组 | 主要页面 |
|---|---|
| Overview | Preview |
| Memory | Graph、Memory、Timeline、Recall、Injection、Knowledge、Notes |
| Insights | Intelligence、Learning、Jargon |
| Relationships | Profiles、Affection、Social |
| System | System、Config |

新增或移动页面时同步 `PageId`、懒加载映射、hash 映射、导航、三语言文案、全局搜索、mock、单测和 browser smoke。

## 页面模板与滚动

页面从三种 `PageFrame` 模板中选用，不重新实现页面壳：

| 模板 | 典型页面 | 契约 |
|---|---|---|
| `standard` | Preview、Timeline、Recall、Notes 等 | 内容自然滚动，常规最大宽度 1440px。 |
| `dense` | Memory、Injection、Knowledge、Config 等 | 活跃内容/表格区是唯一纵向滚动者。 |
| `workspace` | Graph | 占满可用空间，以稳定网格约束画布。 |

桌面与 390px 移动端都必须可滚动、无遮挡、无页面级横向溢出；宽表在局部使用受控横向滚动。

## 主题、组件与可访问性

- 使用 `bg-background`、`text-foreground`、`border-border` 等语义 token；旧 `--color-*` 只是兼容别名，不新增消费者。
- 常规最大圆角 `rounded-lg`（8px）；卡片只表示独立对象或明确分组，不嵌套卡片。
- Dialog/Sheet 必须有可访问名称；纯图标按钮有 `aria-label`/`title`。
- 品牌入口复用 `MemoraLogo`，不在 Sidebar 中重新拼 SVG。

## 三语言与实时状态

- 语言为 `zh`、`en`、`ru`；所有用户可见文案通过 i18n key，禁止散落硬编码。
- 更新 key 时同步 `src/lib/i18n.ts`、相关 hook/mock、页面测试和 browser smoke。
- 实时连接由 `useRealtimeStream` 使用 SSE；页面不得各自创建重复连接。

## 前后端契约

- 后端 Page API 位于 `core/platform/transport/page_api/`；前端沿用 bridge 相对路径，不手工拼接宿主前缀。
- 读取、编辑和批量动作尊重统一 envelope；不得把 `error`、`code` 或 `field_errors` 吞成成功空数据。
- 配置保存发送 `base_revision` + 最小 `changes`；实体编辑发送 `expected_revision`。冲突必须展示远端快照与用户可控的解决路径，不静默 last-write-wins。
- 服务端分页契约由后端决定，客户端不得先取全集再伪造分页；过期响应必须被抑制。

## 验证流程

行为变更先写能失败的 Vitest + React Testing Library 测试，再执行：

```powershell
Set-Location pages/dashboard
npm test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

直接调用 Vitest 时必须指定 jsdom，例如 `npx vitest run --environment jsdom src/pages/MemoryPage.test.tsx`。

browser smoke 在桌面、移动与宽屏视口生成 50 张基线截图并覆盖暗色、三语言、冲突流程与横向溢出检查；通过后仍须人工打开截图复核，日志不能替代视觉确认。

继续阅读[打包与发布](/development/packaging)与[质量门禁](/development/quality-gates)。
