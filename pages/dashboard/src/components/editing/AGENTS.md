# Dashboard 编辑组件模块上下文

面包屑：[`src/components/AGENTS.md`](../AGENTS.md) → `editing/`

## 职责

此目录承载跨实体编辑器、表单字段、错误汇总、冲突 Dialog/Sheet 与批量操作工具栏。它接收页面传入的实体快照、revision、字段 schema 和回调；数据读取与写回仍由 `src/hooks/` 和 `src/lib/bridge.ts` 负责。

## 写回与冲突

- 配置保存提交最小 `changes` + `base_revision`，不得覆盖整个远端配置。
- 实体保存提交 `expected_revision`；冲突时保留草稿，展示远端快照，允许接受远端或基于新 revision 重放本地修改。
- 成功后以服务端返回实体/revision 更新调用方状态；失败保留输入并展示字段级错误。
- 批量操作只作用于当前可见且服务端确认的选中 ID，破坏性动作必须显式确认。

## 交互与可访问性

表单用受控输入和稳定的 field id；错误摘要链接到字段；Dialog/Sheet 有名称、焦点管理和关闭路径；移动端底栏与背景滚动不能重叠。所有文案通过 i18n key，不能在 JSX 中写硬编码语言。

## 验证

```powershell
Set-Location pages/dashboard
npm test -- --run src/pages/SocialPage.test.tsx src/pages/InjectionStrategyPage.test.tsx
npm run smoke:browser
```

重点人工检查 `editing-social-sheet.png`、`editing-social-conflict.png`、`editing-error-summary.png`、`editing-batch-toolbar.png` 及移动端编辑截图。
