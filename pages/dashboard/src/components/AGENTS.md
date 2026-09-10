# Dashboard 组件模块上下文

面包屑：[`pages/dashboard/AGENTS.md`](../../AGENTS.md) → `src/components/`

## 职责与分层

- `layout/`：`PageFrame`、侧栏、标题区和响应式骨架；只负责布局，不拥有页面数据。
- `brand/`：`MemoraLogo` 可访问 SVG 品牌图形；Sidebar 等品牌入口统一复用。
- `ui/`：Base UI-backed shadcn 原语（Button、Dialog、Sheet、Table、Tabs、Input 等）；保持可访问属性和语义 token。
- `data-table/`：表格列、分页、筛选、选择和批量工具栏；分页必须反映服务端 total/边界。
- `editing/`：跨页面实体编辑、字段错误和冲突处理，详见 [`editing/AGENTS.md`](./editing/AGENTS.md)。
- `config/`、`injection/`、`intelligence/`、`preview/`、`system/`：领域组件，调用 hooks/lib，不复制 bridge/API 逻辑。

## 约束

优先组合现有原语与 `PageFrame`，不得恢复旧 Modal 或引入平行主题系统。纯图标按钮必须有 `aria-label`/`title`；Dialog/Sheet 有可访问名称；组件不可把 query、prompt、正文或敏感字段写入日志。

`MemoraLogo` 使用固定 `0 0 24 24` viewBox、`currentColor` 和可选 `size`，保留 `role="img"`/`aria-label="Memora"`；消费方通过语义文本颜色控制主题，不复制其路径。Injection Overview 的成本趋势以原始数值 `bucket_ms` 驱动横轴，日期格式化仅放在 tick/tooltip；不要预先把时间转换为字符串，否则 Recharts hover 可能命中错误数据点。

组件测试放在同目录或消费它的页面测试中。视觉或布局变化须补相应 browser smoke 截图，并在桌面、移动、暗色和三语言下检查无重叠/溢出。

## 验证

```powershell
Set-Location pages/dashboard
npm test -- --run
npm run build
npm run check:artifacts
```
