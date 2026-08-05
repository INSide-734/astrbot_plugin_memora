# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

主要用户是 AstrBot 管理员和 Memora 插件维护者。他们在日常运行、排障和维护 AstrBot 时使用 Dashboard，检查记忆、知识、用户画像、关系、情绪和群聊黑话数据，调整插件配置，搜索、筛选、分页和批量处理数据，并在网络失败、后台并发更新、配置冲突或维护状态下安全恢复工作。

## Product Purpose

Memora 是 AstrBot 的长期记忆插件，为对话提供从记忆提取、长期存储、混合检索到安全召回和可视化管理的完整生命周期。Dashboard 让管理员能够观察记忆系统、验证召回行为、管理派生数据和领域实体、修改配置并处理冲突与运维操作。产品成功意味着管理员可以理解当前状态、做出可追溯的更改，并在异常情况下恢复而不破坏聊天主链路或权威记忆。

## Positioning

Memora 以 SQLite canonical memory 作为唯一权威数据；全文、向量、图和 Projection 都是可重建的派生数据。它将 BM25、FAISS、图检索、关系扩展和重排序组合为混合检索，并在请求级应用会话、稳定身份、隐私和安全注入边界。这一 canonical-first、可重建且带安全边界的记忆生命周期，是 Dashboard 所管理系统的核心机制。

## Operating Context

Dashboard 运行在 AstrBot 插件页面桥接环境中，通过相对 `page/` 端点访问 Page API，并使用 Hash URL 在多个功能页之间导航。用户会在桌面和窄屏设备上处理较长、密集的管理任务；界面需要展示加载、空数据、错误、离线、未保存、提交中、成功和 revision 冲突等状态。实时状态通过 SSE 提供，配置和实体写回必须保留服务端 revision 与冲突处理流程。

## Capabilities and Constraints

- Dashboard 使用 React 18、TypeScript、Vite、Tailwind CSS、Base UI-backed shadcn 组件、Lucide、Recharts、Framer Motion 和 Geist variable font。
- 应用壳、Hash 路由、导航模型、AstrBot bridge、统一 API envelope、SSE、三语言 key（中文、英文、俄文）和经典脚本单 bundle 是兼容契约。
- 页面覆盖概览、图谱、记忆、时间线、召回、注入策略、知识库、笔记、智能控制台、学习、黑话、画像、好感度、社交、系统和配置等管理工作区。
- 生产页面必须消费真实 Page API 数据；前端 mock 仅用于 runtime/browser smoke，不得冒充生产数据或伪造分页、排序和身份来源。
- 数据写回必须携带 `base_revision` 或 `expected_revision`，冲突时保留本地草稿并提供明确的解决路径；失败不得伪装成成功或清空用户输入。
- 设计与布局须保持 standard、dense、workspace 三种页面模板，支持 390×844、1366×900 和 2048×1152 视口，关键操作不能因移动端适配而隐藏。
- 动态记忆不得进入 System Prompt；来源、隐私、身份和派生数据的模型可见范围受后端安全契约限制。

## Brand Commitments

- 产品名称为 Memora，插件标识为 `astrbot_plugin_memora`，品牌图形使用仓库根目录的 `logo.png` 及 Dashboard 的 `MemoraLogo` 组件。
- Dashboard 的产品声音是冷静、精确、可信赖的运维控制台：信息密度高但层级清楚，状态、风险和结果明确，破坏性操作审慎。
- 现有中性亮暗主题、Geist 字体、Lucide 图标、紧凑控件和清晰分隔是已确认的品牌与界面约束；不得在没有明确重设计请求时替换为营销式视觉世界。

## Evidence on Hand

- 产品说明、安装与运维入口：[README.md](../../README.md)、[metadata.yaml](../../metadata.yaml) 和仓库文档站链接。
- 真实品牌资产：[logo.png](../../logo.png)；Dashboard 入口和路由位于 `src/main.tsx`、`src/App.tsx`。
- 现有界面设计契约：[DESIGN.md](DESIGN.md)、`src/index.css`、共享布局和 UI 组件。
- 产品提供 `/memora status`、`/memora webui`、Page API、诊断、评测、备份恢复、索引重建和在线更新能力。
- 当前记录没有已确认的外部客户、评价、性能基准或营销证明；后续界面和文档不得编造这些材料。

## Product Principles

1. canonical 数据优先，所有派生索引都必须可解释、可重建且不能取代权威记忆。
2. 先让管理员理解状态和风险，再提供可执行、可恢复的操作。
3. 准确召回、安全边界和身份/隐私隔离不能为了界面或操作便利而牺牲。
4. 真实后端结果、revision 和错误契约必须在界面中保持诚实可见。
5. 高密度运维工作应在桌面和窄屏上都保持可扫描、可访问和不丢失关键操作。

## Accessibility & Inclusion

Dashboard 需要支持键盘完成导航、搜索、分页、编辑、保存、取消、删除确认和冲突处理；所有 Dialog/Sheet、表单字段、图标按钮、选择框、表格选择和分页都必须有稳定的可访问名称、焦点反馈及错误关联。亮暗主题下焦点指示必须保持可见，状态不能只依赖颜色，长英文和俄文文本需要有足够的扩展空间。
