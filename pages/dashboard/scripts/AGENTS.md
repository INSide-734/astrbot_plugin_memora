# Dashboard 验证脚本模块上下文

面包屑：[`pages/dashboard/AGENTS.md`](../AGENTS.md) → `scripts/`

## 入口

- `runtime_smoke.mjs`：验证生产 bundle 在宿主 bridge/mock 下能启动并完成关键加载。
- `browser_smoke.mjs`：Playwright 桌面、移动和宽屏导航、交互、溢出、控制台错误与 50 张截图基线；Evaluation 变体卡片保留桌面和 390px 移动端证据。
- `config_smoke_fixture.mjs`：读取根 `_conf_schema.json`，为非宿主 browser smoke bridge 提供 schema、revision conflict 和 reload 生命周期；不得复制第二份手写 schema。
- `evaluation_smoke_fixture.mjs`：为 browser smoke 提供评测数据集和动态消融能力 descriptor；不可用变体保留稳定 reason code，默认选择必须与生产契约一致。报告 fixture 只能使用安全逐用例数值，不得恢复 query、ranked/relevant ID 或身份 metadata。
- `evaluation_smoke_helpers.mjs`：负责打开固定匿名报告、等待变体状态/安全 effective settings 可见，并验证变体卡片在桌面双列、移动端单列且无溢出，避免把交互继续堆入超长 browser smoke 主文件。
- `recall_trace_smoke_fixture.mjs`：为 browser smoke 提供与生产 API 一致的安全 Recall Trace DTO；对应测试禁止 query、正文、身份、canonical ID、source/job metadata 和 explanation。
- `*_helpers.mjs`：无副作用的等待、导航、截图和断言辅助；对应 `.test.mjs` 做单元验证。

## 约束

Smoke 必须使用真实路由、语义角色和可观察响应，不能通过固定 sleep 或伪造客户端分页掩盖问题。截图既要检查非空/尺寸/基线，也必须人工打开确认无 blank、loading、遮罩、重叠、裁切、文本溢出和横向滚动异常。不要把 token、API key、prompt、记忆正文或原始身份写入日志。

构建产物契约由仓库脚本检查：最终 IIFE classic bundle 恰好一个 JS 与一个 CSS，移除 `type="module"`/`crossorigin`，不要修改检查器来掩盖错误产物。

## 验证命令

```powershell
Set-Location pages/dashboard
npm test -- --run scripts
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

浏览器 smoke 后必须人工检查截图；最终仓库门禁为 `python scripts/check_all.py`。
