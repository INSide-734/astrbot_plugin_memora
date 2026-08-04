# Dashboard Package-by-Feature 迁移基线与执行清单

> 状态：草案，等待阶段 2 架构评审；最后更新：2026-08-04；范围：`pages/dashboard/` 的 `app / features / shared` 结构迁移；关联：Multica `AST-22` 与上层执行计划 `AST-4-PACKAGE-BY-FEATURE-EXECUTION-PLAN.md`

> **供后续执行 Agent 使用：**按阶段 3 至 8 的子议题逐项执行本清单；每个领域一个可独立回滚的提交或 PR。不得在阶段 1 实施迁移。

**目标：**在不改变页面行为、视觉、Hash URL、Page API、bridge envelope、配置、SSE、三语言和构建产物契约的前提下，为每个 Dashboard 业务文件确定唯一领域所有者。

**架构：**`app` 只拥有启动、路由、全局壳和跨 feature 组合；`features/<domain>` 拥有页面、业务组件、数据流、契约、测试和 fixture；`shared` 只接纳无领域含义且被至少两个 feature 稳定复用的 UI、bridge、i18n 和编辑基础。跨 feature 只能导入目标 feature 的 `index.ts` 公共入口。

**实际技术栈：**React 18.3.1、React DOM 18.3.1、TypeScript 7.0.2、Vite 8.1.5、Tailwind CSS 3.4.19、PostCSS 8.5.24、npm/package-lock。

---

## 1. 事实基线与非目标

- 盘点基于当前分支 `agent/agent/9fe30497`，进入时工作树干净；跳过 `node_modules`、构建产物、缓存和运行时数据。
- `src` 中有 208 个非声明 TS/TSX 文件、680 条本地 import 边、21 个动态 import；esbuild metafile 的强连通分量为 0，即当前没有循环依赖。
- 16 个页面都由 `src/App.tsx` 的 `lazy()` 动态导入。`HASH_TO_PAGE` 与 `src/lib/navigation.ts` 重复维护路由事实，未知 hash 回退 `graph`。
- 当前没有 Redux、Zustand 或全局 React Provider。`main.tsx` 只挂载 `React.StrictMode`；全局状态由 `App.tsx` 与模块级 hooks 管理。
- `src/lib/bridge.ts` 是唯一 HTTP bridge 边界，统一补 `page/` 前缀并解析 `status/data/error/code/field_errors` envelope；SSE 目前由 `useRealtimeStream` 直接访问宿主 bridge。
- 本计划不移动生产文件，不修改 API、配置、schema、路由、视觉或算法，也不创建空 feature。没有独立 UI 文件的领域只记录契约提取点。

### 版本漂移

仓库上下文仍描述 TypeScript 5.6 / Vite 6，但 `package.json` 与 `package-lock.json` 实际锁定 TypeScript 7.0.2 / Vite 8.1.5；React 18.3 与 Tailwind 3.4 一致。TypeScript 7 的稳定包入口只导出版本信息，编译器 AST 已迁到 `unstable/*`；依赖门禁不应假设 TypeScript 5/6 Compiler API。阶段 2 必须裁决“保留实际版本并更新文档”或“单独建立降级迁移”，不得混入目录搬迁。

## 2. 目标目录与依赖规则

```text
pages/dashboard/src/
  app/                         # bootstrap、route registry、shell、全局搜索和组合页
  features/<domain>/
    index.ts                   # 唯一公共入口；只显式导出稳定契约
    api/ model/ ui/ pages/     # 按实际需要创建，不机械补空目录
    testing/                   # 本领域 fixture、mock handler 与测试 helper
  shared/
    bridge/                    # 宿主 HTTP/SSE 基础、envelope 与安全错误
    editing/                   # 无领域的 revision 编辑状态机和表单壳
    i18n/                      # locale 基础、格式化与资源注册协议
    ui/                        # Base UI-backed primitives、布局、表格、品牌和主题
```

允许依赖为 `app -> feature public entry -> shared`。允许 feature 通过另一个 feature 的公共入口消费稳定契约；禁止 `shared -> feature`、feature 私有目录互引、feature 反向导入 `app`、从根 barrel 暴露全部领域类型。测试遵守同一边界，仅 `app/testing` 可组合多个 feature fixture。

### shared 准入与独占入口

| 入口 | 独占内容 | 明确禁止 |
|---|---|---|
| `shared/ui/index.ts` | Button、Dialog、Sheet、Table、PageFrame、DataTable、品牌、主题、toast | SearchBar、Mood/Relation 常量、领域表单、页面状态 |
| `shared/bridge/index.ts` | `apiGet/apiPost/apiRequest/unwrapApiData`、SSE adapter、envelope/error contracts | endpoint 名、memory importance、领域 DTO、mock 业务数据 |
| `shared/i18n/index.ts` | `Translate`、locale/数字/日期格式化、`useI18n` 基础与资源注册协议 | 集中存放所有领域文案 |
| `shared/editing/index.ts` | revision editor、通用冲突/确认/详情壳、字段错误映射 | Knowledge/Memory/Profile 等领域字段与校验 |
| `features/<domain>/index.ts` | 本领域页面入口、公开 DTO、跨域所需 query/command contract | 私有组件、store 实现、testing fixture、无边界 `export *` |

阶段 3 建立自动门禁：解析静态 import、`import()` 与测试 import；拒绝上述非法方向，并报告 source、target 与导入符号。不要使用 TypeScript 7 的不稳定 AST；优先复用锁定的 esbuild metafile。

## 3. 逐文件迁移矩阵

下表中的 `*` 包含同名前缀的生产文件、`.test.ts(x)`、`.test-support.ts(x)` 和局部 helper；未列出的 catch-all 不允许自行进入 shared。测试与被测实现同属一个 feature。

### 阶段 4：identity / cognition / review / evaluation

| 领域 | 当前 source | 唯一 target / 动作 |
|---|---|---|
| identity | `components/graph/GraphNodeDetail*` 中 `identity_namespace/stable_user_id` 展示片段；`types/index.ts::GraphNode` 对应字段 | 没有独立文件，不创建空包；阶段 4 提取公开 `IdentityDisplay` contract 后，由 graph 通过 `features/identity` 入口消费 |
| cognition | `pages/{Learning,Jargon,Affection,Social}Page*` | `features/cognition/pages/*`；保留 affection/expression/jargon/social 子域名 |
| cognition | `components/TopicSegmentationConfig*`、`components/editing/forms/{Affection,Jargon,Mood,SocialRelation}Form.tsx` | `features/cognition/ui/*` |
| cognition | `lib/constants.ts` | `features/cognition/model/displayConstants.ts`；MOOD_TYPES 与 RELATION_CATEGORIES 有领域语义，不准进 shared |
| review | `components/intelligence/{ReviewQueue,ReviewItemDetail}*` | `features/review/ui/*` |
| evaluation | `components/intelligence/{EvaluationWorkbench,EvaluationCaseTable}*` | `features/evaluation/ui/*` |
| evaluation | `mock/evaluationI18n.ts`、`mock/evaluationServer*` | `features/evaluation/{i18n,testing}/*` |

`pages/IntelligencePage*` 是 evaluation/retrieval/operations/review 四域组合页，归 `app/routes/IntelligenceRoute*`，只导入四个 feature 公共入口。

### 阶段 5：conversation / profile / knowledge / notes / operations

| 领域 | 当前 source | 唯一 target / 动作 |
|---|---|---|
| conversation | `hooks/useGroups.ts` | `features/conversation/api/useGroups.ts`；cognition 只经 conversation 公共入口使用群组契约 |
| profile | `pages/ProfilesPage*`、`components/editing/forms/ProfileForm.tsx` | `features/profile/{pages,ui}/*` |
| knowledge | `pages/KnowledgePage*`、`components/editing/forms/KnowledgeForm.tsx` | `features/knowledge/{pages,ui}/*` |
| notes | `pages/NotesPage*`、`components/editing/forms/NoteForm.tsx` | `features/notes/{pages,ui}/*` |
| operations | `pages/{Preview,Config,System}Page*`、`components/preview/**` | `features/operations/{pages,ui}/**`；Preview 是跨域指标视图，由 operations 组合公开查询契约 |
| operations | `components/config/**`、`components/system/**`、`components/layout/RuntimeStatusBanner.tsx` | `features/operations/ui/**` |
| operations | `hooks/{useConfigSync,useRuntimeStatus}*`、`lib/{config,configSections}*`、`types/config.ts` | `features/operations/{api,model}/*` |
| operations | `mock/{configServer,updateServer}*` | `features/operations/testing/*` |

Injection 对 revision 配置与 Config dialogs 的复用是 `injection -> operations public entry`，不是 shared 候选。`SystemPage` 对 TopicSegmentationConfig 的复用是 `operations -> cognition public entry`。

### 阶段 6：graph / retrieval / evolution / injection

| 领域 | 当前 source | 唯一 target / 动作 |
|---|---|---|
| graph | `pages/GraphPage*`、`components/graph/**` | `features/graph/{pages,ui,model}/*` |
| retrieval | `pages/RecallPage*`、`components/intelligence/{RecallTracePanel,TraceContributionList}*` | `features/retrieval/{pages,ui}/*` |
| retrieval | `mock/{recallTrace,recallTraceI18n}*` | `features/retrieval/testing/*` 与 `features/retrieval/i18n/*` |
| evolution | 当前无独立页面、组件、hook 或 DTO | 不创建空包；仅在后端暴露独立 Dashboard contract 时新增，否则保持无前端实现 |
| injection | `pages/InjectionStrategyPage*`、`components/injection/**` | `features/injection/{pages,ui}/*` |
| injection | `hooks/useInjection*`、`types/injection.ts` | `features/injection/{api,model}/*` |

### 阶段 7：canonical memory 与 app 聚合入口

| 所有者 | 当前 source | 唯一 target / 动作 |
|---|---|---|
| memory | `pages/{Memory,Timeline}Page*`、`components/editing/forms/MemoryForm.tsx` | `features/memory/{pages,ui}/*` |
| memory | `types/index.ts::MemoryItem`、`bridge.ts::normalizeImportance` | `features/memory/model/*`；retrieval 如需归一化只经 memory 公共 contract |
| app | `main.tsx`、`App*`、`bootstrap.test.ts` | `app/bootstrap.tsx`、`app/App.tsx`、同目录测试 |
| app | `lib/navigation*`、`types/navigation*`、`types/index.ts::PageId` | `app/routes/{registry,contracts}.ts`；registry 独占 hash、lazy import、nav metadata 与 graph fallback |
| app | `components/layout/Sidebar*` | `app/shell/Sidebar*` |
| app | `components/ui/SearchBar*`、`lib/globalSearch*` | `app/search/*`；通过 memory/knowledge/notes/operations 公共 search adapter 组合，不直接承载各域 endpoint |
| app | `mock/{i18n,production-i18n}.test.ts` | `app/i18n/catalog*.test.ts`，验证 feature 资源注册后的三语言完整性 |

### shared 与构建文件

| 当前 source | 唯一 target / 保留位置 |
|---|---|
| `components/ui/**`，但排除 SearchBar | `shared/ui/primitives/**` |
| `components/brand/**`、`components/data-table/**`、`components/layout/PageLayout*` | `shared/ui/{brand,data-table,layout}/**` |
| `components/editing/**`，但排除 `forms/**` | `shared/editing/**` |
| `hooks/useEntityEditor*` | `shared/editing/useEntityEditor*` |
| `hooks/{useTheme,useToast}*`、`lib/utils.ts` | `shared/ui/{theme,toast,cn}.ts(x)` |
| `lib/i18n.ts`、`hooks/useI18n*` | `shared/i18n/*`；领域资源不随之进入 shared |
| `lib/bridge*`、`hooks/useRealtimeStream*` | `shared/bridge/*`；先移出 `normalizeImportance` |
| `index.css` | `app/index.css`，继续作为全局 token/字体入口，不经 shared barrel 导出 |
| `vite-env.d.ts`、`vitest-compat.d.ts` | 保留 `src/` 声明入口或迁至 `app/types/`，由 tsconfig 明确 include |
| `package*.json`、`tsconfig.json`、`vite.config.ts`、`buildUtils.ts`、`tailwind.config.js`、`postcss.config.js`、`components.json`、`index.html`、`index.src.bak` | 保留 Dashboard 根；属于构建/宿主契约，不进入 app/shared |
| `viteConfig.test.ts`、`tailwindConfig.test.ts`、`themeMotion.test.ts` | `app/testing/build/` 或保持根测试入口；不归业务 feature |

### 必须拆分的 8 个混合热点

| source | 拆分结果 |
|---|---|
| `types/index.ts` | PageId -> app；MemoryItem -> memory；GraphNode -> graph/identity contract；RecallResult -> retrieval；Jargon/Affection/Social -> cognition；ProfileDraft -> profile；Quality/Delegation -> operations；删除经调用方扫描确认无用的 `GraphEdge/PageActions/JargonStats` |
| `types/intelligence.ts` | evaluation、retrieval、operations diagnostics、review 四域 DTO；删除确认无用的三个旧 `Intelligence*Summary/Step/QueueItem` 类型 |
| `types/editing.ts` | envelope/ApiRequestError/FieldErrors -> shared/bridge contracts；editingErrorDetails/BULK_CONFIRMATION_THRESHOLD -> shared/editing |
| `components/editing/forms/domain-forms.test.tsx` | 按七个领域表单拆为 cognition/profile/knowledge/notes/memory 测试，不保留跨域巨型 fixture |
| `mock/index.ts` | feature 三语言资源 + `app/i18n/catalog` + `shared/i18n/runtime` + `shared/bridge/mock/registerMockBridge` |
| `mock/data.ts` | 按 memory/profile/cognition/evaluation/injection/operations 等领域拆 fixture；禁止 shared 业务数据 |
| `mock/server.ts` | 各 feature `testing/handlers.ts` + 无业务的 `app/testing/mockDispatcher.ts`；dispatcher 只匹配 path 并委托 |
| `mock/server.test.ts` | handler 断言回到各 feature；只保留 dispatcher 路由、未知端点和统一 envelope 集成测试 |

## 4. 路由、状态与依赖风险

### 动态路由

`App.tsx` 当前有 16 个页面动态 import，另有 5 个测试动态 import；Vite IIFE 构建通过 `inlineDynamicImports`/Rolldown 收敛为单 classic-script bundle。阶段 7 迁移到 route registry 时必须保持 `#/preview|graph|memory|timeline|recall|injection|knowledge|notes|intelligence|learning|jargon|profiles|affection|social|system|config`、未知回退 graph、history index、脏表单前进/后退保护和 `Suspense` fallback。

### 全局状态

| 当前所有者 | 状态/副作用 | 目标所有者 |
|---|---|---|
| `App.tsx` | 当前页、dirty/pending intent、history refs、移动菜单、toast、SSE 未读、runtime status | `app`；领域状态不得提升到 app |
| `useI18n.ts` + `mock/index.ts` | module listeners/cache/override、localStorage、宿主 context、`window.t` | `shared/i18n` runtime + app catalog + feature resources |
| `useTheme.ts` | DOM class/data-theme、localStorage、宿主 context、动效 timer | `shared/ui/theme` |
| `useRealtimeStream.ts` | 唯一 SSE subscription、50 事件、重连 timer、cleanup | `shared/bridge/sse`，仍只由 app 实例化一次 |
| 各 Page/hook | loading/empty/error/data、selection、revision draft、Abort/generation | 各 feature；迁移时保持竞态、取消和失败状态 |
| mock 模块 | 跨域可变数组、revision、reset、i18n map | 各 feature testing；app dispatcher 只统一 reset 注册顺序 |

当前图无循环，但候选跨 feature 边包括 `cognition -> conversation(groups)`、`injection -> operations(config contract)`、`operations -> cognition(topic config)`、`operations overview -> memory/profile/knowledge/notes`、`graph -> identity(display contract)`、`retrieval -> memory(importance contract)`、`app search -> memory/knowledge/notes/operations`。阶段 3 门禁必须只允许这些边指向公共入口；不得通过 shared 隐藏领域依赖。

## 5. Page API / bridge / type 契约

| 领域 | 前端相对端点 | 后端事实来源 | 精确契约测试 |
|---|---|---|---|
| memory | `memories*`、`memory/detail|update`、`stats` | `memory_{read,write,batch,stats_recall}_api.py` | `tests/test_api_memory.py`、`test_page_api_contract.py` |
| conversation | `groups` | `core/page_api.py::get_groups` | `tests/test_page_api.py`、`test_page_api_contract.py` |
| profile | `profiles*` | `profile_api.py` | `tests/test_api_profile.py` |
| cognition | `learning/*`、`expression/patterns`、`jargon/*`、`affection/*`、`social/*`、`backfill/*` | 对应 `learning/expression/jargon/affection/social/topic_segmentation_api.py` | 对应 `tests/test_api_*.py` |
| knowledge | `knowledge*` | `knowledge_api.py` | `tests/test_api_knowledge.py`、`test_knowledge_api.py` |
| notes | `notes*` | `note_api.py` | `tests/test_api_note.py`、`test_note_api.py` |
| graph | `graph/overview|query|search`、`stats` | `graph_api.py`、memory stats | `tests/test_api_graph.py`、`test_api_memory.py` |
| retrieval | `recall/test`、`recall/trace*` | `memory_stats_recall_api.py`、`recall_trace_api.py` | `tests/test_api_memory.py`、`test_api_recall_trace.py` |
| injection | `injection-strategy/catalog|summary|decisions*`、`config/*` | `injection_strategy_api.py`、`config_api.py` | `tests/test_api_injection_strategy.py`、`test_api_config.py` |
| review | `review/items*|refresh|action` | `review_api.py` | `tests/test_api_review.py` |
| evaluation | `evaluation/datasets*|run|reports*` | `evaluation_api.py` | `tests/test_api_evaluation.py` |
| operations | `config/*`、`metrics/summary`、`diagnostics/*`、`quality/*`、`delegation/*`、`backup/*`、`update/*`、`export/memories` | 对应 `core/api/*_api.py` | 对应 API 测试 + `test_page_api_contract.py` |
| identity/evolution | 当前无独立 Dashboard endpoint | graph identity DTO 与现有后端领域 contract | 随消费者测试验证；不得虚构新 API |

所有 feature API adapter 依赖 `shared/bridge`，但 endpoint、请求 DTO、响应校验与 revision 语义留在 feature。`ApiResponse` 全局声明与 `AstrBotPluginPage` window contract 必须保持；不得把错误 envelope 吞成空数据，不得伪造 offset/total，不得记录 query、正文、身份、canonical ID、Provider 凭据或内部路径。

## 6. 分阶段执行清单

### 阶段 3：骨架与门禁

- [ ] 创建 `app/features/shared` 最小目录与公共入口，不移动领域实现，不修改根路由、Provider、main、全局 bridge 注册或公共测试配置。
- [ ] 添加基于 esbuild metafile 的依赖门禁，覆盖 production/test、静态/动态 import；先以当前 0 cycle 为基线。
- [ ] 固化 shared 准入评审模板和 feature public-entry allowlist；门禁失败信息包含 source/target/symbol。
- [ ] 补充 TypeScript/Vite 版本裁决；若不降级，更新过期上下文，结构 PR 不改核心版本。

### 阶段 4 至 6：领域波次

- [ ] 按第 3 节逐领域移动实现、同目录测试、DTO、i18n 和 fixture；每域单独提交/PR。
- [ ] 先更新内部相对 import，再只从公共入口接入允许的跨域依赖；不得提前修改阶段 7 独占入口。
- [ ] 每域运行精确 Vitest、对应后端 Page API 契约测试、类型检查、依赖门禁和 `git diff --check`。
- [ ] 对 800 行以上历史文件只做职责拆分，不复制逻辑或创建 `part1/part2`；`mock/index.ts/server.ts` 必须按 dispatcher 模式拆分。

### 阶段 7：app 与 memory 收敛

- [ ] 迁移 memory 后，统一 route registry、PageId/navigation intent、Sidebar、IntelligenceRoute、SearchBar 和 bootstrap。
- [ ] app 只组合 feature public entry；删除旧 pages/components/hooks/types barrel 的业务导出和临时 re-export。
- [ ] 保持 16 个 Hash URL、graph fallback、dirty history、唯一 SSE、宿主主题、三语言、classic single bundle。

### 阶段 8：终验与清理

- [ ] 证明旧 `pages/components/hooks/types/mock` 不再承载业务规则；删除确认无消费者的旧类型和限时兼容层。
- [ ] 运行第 7 节全量命令、依赖门禁和关键 browser smoke，人工核对全部截图。
- [ ] 更新 `pages/dashboard/AGENTS.md`、`DESIGN.md` 和长期架构导航；本计划标为完成或被后继文档取代。

## 7. 工具链、命令与 2026-08-04 基线结果

环境：Windows、Node `v22.22.0`、npm `11.7.0`。Node 满足当前 Vite 8；包管理器以 `pages/dashboard/package-lock.json` 为准。

| 目的 | 从 `pages/dashboard` 执行 | 基线结果 |
|---|---|---|
| 安装 | `npm ci` | 通过；安装 580 个锁定包 |
| 类型检查 | `npx --no-install tsc -b` | 通过；TypeScript 7.0.2 |
| lint | `npm run lint` | 不可用；`Missing script: "lint"`。阶段 3 必须新增或明确以何种门禁替代 |
| 单元/组件测试 | `npm test` | 通过；79 files / 919 tests；jsdom 有两条已知 navigation 提示 |
| 生产构建 | `npm run build` | 通过；Vite 8.1.5，4257 modules；JS 3377.04 kB / gzip 973.70 kB |
| artifact gate | `npm run check:artifacts` | 通过；单 classic JS/CSS 宿主契约兼容 |
| runtime smoke | `npm run smoke:runtime` | 通过；9 GET / 18 POST 编辑路由 |
| browser smoke | `npm run smoke:browser` | 在刚完成且入口哈希匹配的 build 后通过；Google Chrome，50 张基线 |

Browser smoke 必须紧跟 build：构建会清理旧 hash 资源并重写 `index.html`。本 run 的自动截图尺寸、最低字节、横向溢出和交互断言通过，但 `view_image` 因 Windows 沙箱 helper 1223 无法启动，未完成肉眼截图复核；阶段 2 不得把本次结果表述为完整视觉验收。

文档自身从仓库根验证：

```powershell
git diff --check
(Get-Content docs/plans/2026-08-04-dashboard-package-by-feature-migration.md).Count
```

## 8. 风险、待决策与回滚

| 项目 | 结论 / 阶段 2 决策 |
|---|---|
| TypeScript 7 / Vite 8 与上下文不一致 | 阻塞目录迁移前的工具链裁决；不得在结构 PR 顺带升降级 |
| lint 缺失 | 当前只有 TypeScript/build/test；阶段 3 需定义可执行 lint 与依赖门禁脚本 |
| identity/evolution 无独立前端文件 | 接受 contract-only，不创建空 feature；新增文件须由真实消费者驱动 |
| Preview、Intelligence、SearchBar 跨域 | 分别由 operations、app route、app search 组合公共契约，不得放 shared |
| 17 个 800 行以上历史 TS/TSX 文件 | 按领域/职责拆分；禁止继续扩大或用转发壳规避上限 |
| mock 与 i18n 高耦合 | 先拆 feature fixture/resources，再收敛 app registry/dispatcher，避免全局替换造成三语言或 smoke 漂移 |
| 视觉基线未肉眼复核 | 阶段 2/后续迁移需在可用图像查看环境补做 50 图检查 |

每个领域 PR 仅移动该领域及其测试/fixture/i18n，并保留 API 与数据格式，因此回滚为 `git revert <domain-commit>`；不需要数据库或生产数据回滚。阶段 7 app 集成单独提交，可在保留已迁 feature 公共入口的前提下独立 revert。任何测试、Page API、Hash 路由、SSE、revision、备份恢复、隐私或构建产物契约回归都停止阶段推进并回滚最近领域 PR。
