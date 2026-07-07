[根目录](../../CLAUDE.md) > [pages](../) > **dashboard**

## 模块职责

Memora Dashboard 是一个基于 React 18 + TypeScript + Vite 构建的 Web 管理面板，使用 shadcn/ui 组件库和 Tailwind CSS，为 Memora 记忆插件提供可视化管理界面。包含 12 个功能页面，通过后端 REST API 进行数据交互。

## 入口与启动

- **入口**: `pages/dashboard/src/main.tsx` -- React 应用入口
- **根组件**: `pages/dashboard/src/App.tsx` -- 路由和应用布局
- **构建系统**: Vite 6.0, TypeScript 5.6
- **包管理**: npm (`package.json`)

### 启动命令

```bash
cd pages/dashboard
npm ci              # 安装依赖
npm run dev         # 开发模式 (Vite HMR)
npm run build       # 生产构建 (tsc + vite build)
npm run test        # Vitest 单元测试
npm run preview     # 构建预览
npm run smoke:runtime  # Node.js 运行时 smoke
npm run smoke:browser  # Playwright 浏览器 smoke
```

## 对外接口

### 页面路由

| 路由 | 页面组件 | 职责 |
|------|---------|------|
| `/` | `DashboardPage` (MemoryPage) | 记忆管理主页 |
| `/memory` | `MemoryPage` | 记忆浏览/搜索/编辑 |
| `/recall` | `RecallPage` | 记忆召回测试 |
| `/graph` | `GraphPage` | 知识图谱可视化 |
| `/knowledge` | `KnowledgePage` | 知识库管理 |
| `/notes` | `NotesPage` | 笔记管理 |
| `/profiles` | `ProfilesPage` | 用户画像 |
| `/affection` | `AffectionPage` | 好感度追踪 |
| `/jargon` | `JargonPage` | 黑话管理 |
| `/learning` | `LearningPage` | 自主学习状态 |
| `/social` | `SocialPage` | 社交关系 |
| `/intelligence` | `IntelligencePage` | 智能特性 (评测/诊断/审核队列/召回追踪) |
| `/system` | `SystemPage` | 系统设置 (功能委托/质量监控) |
| `/timeline` | `TimelinePage` | 时间线视图 |
| `/preview` | `PreviewPage` | 预览页 |

### 后端通信

通过 `src/lib/bridge.ts` 封装与 AstrBot 后端 API 的通信：

```typescript
// API 调用模式
import { apiGet, apiPost } from '@/lib/bridge';
const memories = await apiGet('/api/plugin/memora/memories');
```

### 组件库 (`src/components/`)

| 目录 | 职责 |
|------|------|
| `ui/` | 通用 UI 组件 (Button, Card, Dialog, Input, Toast, ErrorBoundary, SearchBar 等 17 个) |
| `layout/` | 布局组件 (Sidebar 导航) |
| `intelligence/` | 智能特性组件 (DiagnosticCenter, EvaluationWorkbench, RecallTracePanel, ReviewQueue) |
| `system/` | 系统管理组件 (DelegationTab, QualityMonitorTab) |

### Hooks

| Hook | 文件 | 职责 |
|------|------|------|
| `useTheme` | `useTheme.ts` | 暗色/亮色主题切换 |
| `useI18n` | `useI18n.ts` | 多语言切换 |
| `useRealtimeStream` | `useRealtimeStream.ts` | 实时数据流 (WebSocket) |
| `useToast` | `useToast.ts` | Toast 通知 |
| `useGroups` | `useGroups.ts` | 群组数据管理 |

## 关键依赖与配置

### 运行时依赖
- **React 18** + **React DOM**: 核心框架
- **@antv/g6**: 图可视化引擎 (知识图谱渲染)
- **@antv/layout**: 图布局算法
- **framer-motion**: 动画库
- **lucide-react**: 图标库
- **@fontsource-variable/geist**: 字体
- **next-themes**: 主题管理
- **sonner**: Toast 通知
- **shadcn/ui** 相关: class-variance-authority, clsx, tailwind-merge, tw-animate-css

### 开发依赖
- **TypeScript 5.6**, **Vite 6.0**
- **Vitest 3.x**: 单元测试
- **@testing-library/react 16.x**: 组件测试
- **Playwright 1.61**: 浏览器 E2E smoke
- **Tailwind CSS 3.4**: 样式框架
- **PostCSS + autoprefixer**: CSS 处理

### UI 系统
- **组件系统**: shadcn/ui (基于 Radix primitives)
- **样式**: Tailwind CSS + CSS 自定义属性
- **布局**: 响应式设计 (Sidebar + 主内容区)
- **主题**: 暗色/亮色双主题 via `next-themes`

## 测试与质量

### 测试策略
- **单元测试**: 每个组件 (`*.test.tsx`) 使用 Vitest + React Testing Library
- **Hooks 测试**: `useI18n.test.tsx`, `useTheme.test.tsx`, `useRealtimeStream.test.tsx`
- **工具测试**: `bridge.test.ts`, `bootstrap.test.ts`, `viteConfig.test.ts`, `SearchBar.test.tsx`
- **页面测试**: 所有 12 个页面组件均有测试文件
- **Runtime smoke**: `scripts/runtime_smoke.mjs` -- Node.js 环境下验证 API 响应结构
- **Browser smoke**: `scripts/browser_smoke.mjs` -- Playwright 真实浏览器验证
- **Smoke helpers 测试**: `scripts/browser_smoke_helpers.test.mjs`

### 构建质量检查
- `scripts/check_dashboard_build_artifacts.py` -- 验证生产构建产物
  - 检查 `index.html` 是否为 classic script 单 bundle
  - 确保无 `type="module"` / `crossorigin`
  - 检查无陈旧 JS/CSS hash 堆积

## 常见问题 (FAQ)

**Q: Dashboard 构建失败？**
A: 确认 Node.js >= 20 且执行了 `npm ci`。检查 `package-lock.json` 是否与 `package.json` 一致。

**Q: Dashboard 页面空白？**
A: 检查 AstrBot 后端是否正常运行，确认 API 端点可访问。查看浏览器控制台是否有网络错误。

**Q: 如何添加新页面？**
A: 在 `src/pages/` 创建页面组件和测试文件，在 `src/App.tsx` 注册路由，在 `src/components/layout/Sidebar.tsx` 添加导航项。

## 相关文件清单

- `package.json` -- 依赖与脚本
- `tsconfig.json` -- TypeScript 配置
- `vite.config.ts` 或等效配置
- `postcss.config.js` -- PostCSS 配置
- `components.json` -- shadcn/ui 配置
- `src/App.tsx` -- 应用根组件与路由
- `src/main.tsx` -- 入口文件
- `src/lib/bridge.ts` -- 后端 API 封装
- `src/lib/constants.ts` -- 常量定义
- `src/types/index.ts` -- 类型定义
- `src/types/intelligence.ts` -- 智能特性类型定义
- `src/hooks/` -- 自定义 hooks
- `src/components/ui/` -- 通用 UI 组件
- `src/pages/` -- 页面组件
- `scripts/` -- smoke 测试脚本

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 生成 dashboard 模块级 CLAUDE.md |
