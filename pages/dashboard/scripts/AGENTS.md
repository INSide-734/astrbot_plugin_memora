# Dashboard 验证脚本模块上下文

面包屑：[`pages/dashboard/AGENTS.md`](../AGENTS.md) → `scripts/`

## 入口

- `runtime_smoke.mjs`：验证生产 bundle 在宿主 bridge/mock 下能启动并完成关键加载。
- `browser_smoke.mjs`：Playwright 桌面、移动和宽屏导航、交互、溢出、控制台错误与 40 张截图基线。
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
