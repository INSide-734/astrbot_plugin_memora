# Dashboard Hooks 模块上下文

面包屑：[`pages/dashboard/AGENTS.md`](../../AGENTS.md) → `src/hooks/`

## 职责

Hooks 封装页面可复用的异步状态和副作用：`useRealtimeStream` 管理唯一 SSE 连接及 cleanup，`useConfigSync`/`useInjectionStrategyConfig` 管理 revision 配置读写，`useEntityEditor` 管理草稿与冲突，`useI18n`/`useTheme` 管理显示状态，领域 hooks 负责查询和分页。

## 约束

- 页面不得各自创建重复 SSE；取消、卸载和 `AbortController` 必须清理监听器并传播 `CancelledError` 语义。
- hook 返回的 loading/empty/error/data 状态必须可区分，不能把 API 错误吞成空列表。
- 写回 hook 保留本地草稿直到服务端成功；冲突返回远端快照和可重放信息，不得静默 last-write-wins。
- hook 的用户可见错误通过 i18n key；测试 mock 不得漂移于真实 bridge envelope。

## 验证

```powershell
Set-Location pages/dashboard
npm test -- --run src/hooks
```
