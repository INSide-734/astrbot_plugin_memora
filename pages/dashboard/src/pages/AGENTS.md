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

## Injection 与 Social 特殊契约

- Injection 的 Decision History 使用 DataTable 的服务端排序与固定操作列；操作菜单打开受控 Sheet，关闭后把焦点还给行操作触发按钮。表格横向滚动只属于外层局部容器。Overview 成本趋势保留数值型 `bucket_ms`，tick/tooltip 再按当前语言格式化。
- Social 只请求当前 `group_id` 与可选 `category` 的关系集，不虚构分页或服务端排序。加载使用 generation 防止旧请求覆盖新筛选；组、类别、刷新或写回变化时按可见复合身份收敛选择。
- Social 关系身份由 `from_user`、`to_user`、`group_id`、`relation_type` 共同组成。更新、单项删除和 batch items 必须携带 `expected_revision`；批处理部分失败时仅保留失败身份，成功创建/更新到当前筛选外的数据不得继续显示在当前表格。

## SystemPage 备份、恢复与热重载

- `SystemPage` 只消费 `/backup/list` 返回的脱敏摘要；不得读取 `directory`、服务器绝对路径或文件正文。列表显示类型、时间、完整性、大小、文件数及 `can_restore` 能力，`invalid`、`incompatible` 或 `can_restore=false` 必须禁用恢复。
- 恢复确认根据 `/backup/list` 的 `capabilities.hot_reload` 与单项 `can_hot_restore` 决定 `apply_mode=reload|restart`；`legacy_unverified` 必须在确认 Dialog 中显示未校验风险警告。
- 热恢复提交后使用返回的 operation ID 轮询 `/backup/status?operation_id=...`，最长 60 秒且有界；热重载窗口内短暂请求失败不能立即判定恢复失败，组件卸载必须清理 timer。终态按 `succeeded`、`failed_before_apply`、`rolled_back`、`cancelled` 收敛 UI。
- 热重载不可用时显示手动重启提示、写保护状态和取消按钮；仅可通过 `/backup/restore/cancel` 取消尚未应用的事务，不能在客户端伪造恢复完成。
- 批量删除按服务端 `deleted_names` 只移除成功项，保留 `failed_items` 对应选择供用户重试；部分失败不能清空整个选择集。

## 精确验证

```powershell
Set-Location pages/dashboard
npm test -- --run src/pages/<PageName>.test.tsx
npm run smoke:browser
```

页面行为变更先补失败测试，再运行对应页面测试；browser smoke 通过后人工检查相关截图。
