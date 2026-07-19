# Dashboard 页面模块上下文

面包屑：[`pages/dashboard/AGENTS.md`](../../AGENTS.md) → `src/pages/`

## 职责

这里承载 16 个 Hash 路由页面及其 Vitest/React Testing Library 测试。页面负责组合数据 hooks、`PageFrame`、共享表格/编辑组件和 i18n 文案；API 访问必须经过 `src/lib/bridge.ts`，不得在页面中直接拼接宿主前缀或复制 fetch 封装。

## 页面分组与入口

- workspace：`GraphPage.tsx`。
- standard：`PreviewPage`、`TimelinePage`、`RecallPage`、`NotesPage`、`IntelligencePage`、`LearningPage`、`AffectionPage`、`SystemPage`。
- dense：`MemoryPage`、`InjectionStrategyPage`、`KnowledgePage`、`ProfilesPage`、`JargonPage`、`ConfigPage`、`SocialPage`。
- `App.tsx` 集中维护懒加载映射、未知 hash 回退到 graph、history index、脏表单导航保护和唯一 realtime stream。

新增页面时同步 `App.tsx` 导航分组、`src/lib/navigation.ts`、三语言 key、页面测试和 browser smoke 路径；不要为同一页面创建第二套路由或备用布局。

## 页面契约

- 所有页面使用 `PageFrame` 并明确唯一滚动所有权；宽表使用局部滚动，不允许页面级横向溢出。
- 读取、分页、筛选、选择和写回遵守服务端 envelope；冲突保留本地草稿并展示可操作的解决路径。
- 用户可见文字、错误、空态和按钮全部来自 `src/lib/i18n.ts` 的 zh/en/ru key。
- 破坏性或高影响动作必须使用可访问 Dialog；详情使用受控 Sheet。

## 精确验证

```powershell
Set-Location pages/dashboard
npm test -- --run src/pages/<PageName>.test.tsx
npm run smoke:browser
```

页面行为变更先补失败测试，再运行对应页面测试；browser smoke 通过后人工检查相关截图。
