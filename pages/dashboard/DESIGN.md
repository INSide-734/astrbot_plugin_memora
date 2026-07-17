# Memora Dashboard 统一页面设计规范

> 状态：当前分支设计基线（含兼容层与已知边界）
>
> 基线分支：codex/adaptive-memory-injection（基于 codex/dashboard-unified-editing-crud 统一页面基线）
>
> 最后核对：2026-07-17
>
> 适用范围：pages/dashboard 内的应用壳、16 个功能页面、共享 UI、统一编辑流程与后续新增页面

## 1. 文档目的

本文档把当前分支已经落地的页面设计反向整理为统一规范。它不是视觉提案，也不是重做 Dashboard 的计划，而是后续新增页面、修改页面和代码审查时共同使用的设计契约。

规范关键词：

- **必须**：新代码和被修改的现有代码都要满足。
- **应当**：默认选择；只有明确的领域理由才能偏离，并需在代码或设计文档中说明。
- **可以**：受上下文约束的可选模式。
- **兼容层**：为旧页面保留，但不得成为新代码的范例。

当本文档与实现发生漂移时，修改页面的同一提交必须同步更新本文档。页面级代码不得绕过共享布局或共享 UI 建立第二套设计语言。

## 2. 设计上下文

### 2.1 用户

主要用户是 AstrBot 管理员和 Memora 插件维护者。他们会反复执行以下工作：

- 检查记忆、知识、画像、关系、情绪和黑话数据。
- 调整插件配置并观察同步、重载和冲突状态。
- 搜索、筛选、分页、批量处理和修复异常数据。
- 在网络失败、后台并发更新或维护模式下安全恢复工作。
- 在桌面和窄屏设备上处理较长、较密集的管理任务。

### 2.2 产品气质

界面必须表现为**冷静、精确、可信赖的运维控制台**：

- 信息密度高，但层级清楚。
- 视觉克制，不与数据争夺注意力。
- 状态、风险和结果明确，不依赖猜测。
- 日常操作快速，破坏性操作审慎。
- 明暗主题和中、英、俄三语言保持同一信息结构。

### 2.3 设计方向

- 使用现有中性 OKLCH 亮暗主题、Geist 字体和 Lucide 图标。
- 使用紧凑控件、明确分隔、稳定页头和可预测的滚动区域。
- 优先使用无额外容器的密集布局；只有独立对象、指标或明确分组才使用 Card。
- 不使用营销页构图、超大标题、装饰性渐变、霓虹、玻璃卡片、背景光斑或无信息价值的动画。
- 功能状态色只用于成功、警告、错误、离线、选中和危险动作。

## 3. 目标与非目标

### 3.1 目标

1. 所有页面使用同一应用壳、导航结构和页面布局原语。
2. 页面密度、滚动所有权和操作位置可以通过三种模板预测。
3. 同类控件、状态和编辑流程在不同领域保持一致。
4. 页面在 390px 移动端、1366px 桌面端和 2048px 宽屏下均可使用。
5. 所有用户可见枚举、选项和错误均可本地化且可访问。
6. 视觉调整不改变 Hash 路由、AstrBot bridge、SSE、revision 或 Page API 契约。

### 3.2 非目标

- 不建设通用低代码页面生成器或 Schema 表单引擎。
- 不为每个领域创建独立的页面壳、按钮体系或弹层体系。
- 不把所有内容都卡片化，也不嵌套卡片制造层级。
- 不以隐藏移动端功能换取“整洁”。
- 不通过任意原始颜色、超大圆角或高 z-index 修补局部问题。
- 不让设计层直接决定后端字段白名单、权限或事务语义。

## 4. 设计系统架构

~~~mermaid
flowchart TB
    Host["AstrBot 插件页面宿主"] --> App["App 应用壳"]
    App --> Sidebar["五组 Sidebar 导航"]
    App --> Topbar["顶栏、状态、全局搜索"]
    App --> Route["Hash 路由与脏状态保护"]
    Route --> Frame["PageFrame"]
    Frame --> Standard["standard 标准页"]
    Frame --> Dense["dense 高密度页"]
    Frame --> Workspace["workspace 工具工作区"]
    Standard --> Layout["PageHeader / PageToolbar / PageContent"]
    Dense --> Layout
    Workspace --> Layout
    Layout --> Domain["领域页面与 Intelligence 子工作区"]
    Domain --> Editing["统一 Sheet / Dialog / Form / Confirm"]
    Domain --> UI["shadcn + Base UI 共享组件"]
    Tokens["index.css 语义 token、主题、动效"] --> App
    Tokens --> Layout
    Tokens --> Editing
    Tokens --> UI
    Domain --> Bridge["AstrBot bridge 与页面状态"]
~~~

设计权威来源按以下顺序确定：

1. src/index.css：主题、语义颜色、字体、兼容别名和全局动效。
2. src/components/layout/PageLayout.tsx：页面模板和内容尺寸。
3. src/components/ui：共享组件的视觉、状态和可访问性 recipe。
4. src/components/editing：实体查看、编辑、创建、冲突和确认生命周期。
5. src/lib/navigation.ts 与 src/App.tsx：导航、路由和应用壳。
6. 页面代码：只组合上述能力并承载领域内容。

## 5. 应用壳与导航

### 5.1 固定结构

应用壳必须保持：

- 左侧 Sidebar。
- 顶部 56px 应用栏。
- 主内容区占满剩余宽高，并由当前页面决定内部滚动。
- Ctrl+K 全局搜索。
- Hash 路由。
- 页面离开前的未保存修改保护。
- SSE 在线/离线和未读状态。
- 亮暗主题切换与中、英、俄语言切换。

### 5.2 五组导航

| 导航组 | 页面 |
|---|---|
| Overview | Preview |
| Memory | Graph、Memory、Timeline、Recall、Injection、Knowledge、Notes |
| Insights | Intelligence、Learning、Jargon |
| Relationships | Profiles、Affection、Social |
| System | System、Config |

导航项必须从 src/lib/navigation.ts 的单一模型生成。页面不得在 Sidebar 中手写第二份标签、图标或顺序。

### 5.3 Sidebar 尺寸与行为

| 状态 | 规格 |
|---|---|
| 桌面展开 | 248px |
| 桌面收起 | 72px，仅保留图标并用 title/aria-label 说明 |
| 移动抽屉 | min(86vw, 320px) |
| 导航项 | 高 36px，Lucide 16px，选中态来自 selection-state |
| 分组 | 五组均可折叠，aria-expanded 与 aria-controls 必须完整 |
| 移动关闭 | 关闭按钮、遮罩点击和向左滑动均可关闭 |

移动 Sidebar 是抽屉，不是独立移动版导航。不得复制页面路由或删减关键页面。

## 6. 页面布局原语

所有页面必须组合 PageFrame、PageHeader、可选 PageToolbar 和 PageContent。禁止在页面内重新实现同等页头或根滚动容器。

### 6.1 PageFrame

| 属性 | 行为 |
|---|---|
| 通用 | 高度、宽度占满；min-height 为 0；纵向 flex |
| standard | 页面内容自然滚动 |
| dense | 根容器 overflow-hidden，内容区或表格拥有滚动 |
| workspace | 根容器 overflow-hidden，工具画布拥有稳定尺寸 |

### 6.2 PageHeader

- 最小高度 64px。
- 横向 padding：移动 16px、sm 20px、lg 24px。
- 标题使用单一 h1：20px、28px 行高、600 字重。
- 图标容器为 36px、rounded-lg、边框和 muted 表面。
- 描述最多 3xl 宽度，使用 text-sm 和 muted-foreground。
- actions 在窄屏占满一行并允许换行；sm 起恢复自适应宽度。
- 页面主创建动作、刷新、群组选择等全局操作放在 actions，不散落到内容首屏。

### 6.3 PageToolbar

- 最小高度 48px。
- 使用 muted/30 背景和底部分隔线。
- padding：横向 16/20/24px，纵向 8px。
- 筛选、搜索、视图切换和批量上下文放在 Toolbar。
- Toolbar 可以换行，但控件不得被压缩为不可读宽度。
- 条件批量 Toolbar 出现时，必须说明已选数量并提供清除选择。

### 6.4 PageContent

| 模式 | 规格 |
|---|---|
| constrained | 默认，最大宽度 1440px，水平居中 |
| full | 图、表格、系统工作区等需要完整宽度的内容 |
| padding | 移动 16px、sm 20px、lg 24px |
| scrolling | min-height 0、flex 1、overflow auto |

需要边到边表格、时间线或画布时，可以在 PageContent 上移除 padding，但不能绕过 PageContent。

### 6.5 MetricGrid

MetricGrid 使用自适应网格：

    repeat(auto-fit, minmax(min(100%, minItemWidth), 1fr))

默认最小项宽 14rem，gap 16px。页面只传递内容驱动的 minItemWidth，不手写重复断点网格。

## 7. 三种页面模板

### 7.1 选择规则

~~~mermaid
flowchart TD
    Start["新增或重构页面"] --> Canvas{"主要任务是画布、图谱或工具工作区？"}
    Canvas -- 是 --> Workspace["workspace"]
    Canvas -- 否 --> DenseQ{"是否以高密度表格、列表、配置表单和独立滚动为主？"}
    DenseQ -- 是 --> Dense["dense"]
    DenseQ -- 否 --> Standard["standard"]
~~~

### 7.2 standard

用于概览、分析、时间序列、关系和系统操作页面。

~~~text
PageFrame standard
├── PageHeader
├── 可选 PageToolbar
└── PageContent constrained 或 full
    ├── MetricGrid / 独立对象 Card
    ├── 语义分区
    └── 列表、图表或操作区域
~~~

内容自然滚动；标题和操作不需要固定在视口内。卡片只表达独立对象或可比较指标。

### 7.3 dense

用于长列表、表格、配置和频繁筛选页面。

~~~text
PageFrame dense
├── PageHeader（固定）
├── PageToolbar（固定）
├── 条件批量 Toolbar（固定）
└── PageContent full（独立滚动）
    ├── 表头 / 列表
    ├── 数据行
    └── 分页
~~~

根容器不得滚动。筛选变化、页码变化或查询范围变化时，必须清除已不可见的选择。

### 7.4 workspace

用于图谱等需要占满剩余空间的工具。

~~~text
PageFrame workspace
├── PageHeader / 工具条
└── PageContent full
    └── 稳定 grid/minmax
        ├── 主画布
        └── 搜索、详情或检查器
~~~

画布必须拥有明确最小尺寸和 overflow 边界；浮层不得被画布祖先裁切。

## 8. 当前页面模板清单

| 页面 | 路由 ID | 模板 | 主模式 |
|---|---|---|---|
| PreviewPage | preview | standard | KPI、趋势和模块概览 |
| GraphPage | graph | workspace | 图画布、搜索和节点详情 |
| MemoryPage | memory | dense | 筛选、虚拟列表、批量、详情编辑 |
| TimelinePage | timeline | standard | 时间线与连续滚动 |
| RecallPage | recall | standard | 查询、召回结果和诊断 |
| InjectionStrategyPage | injection | dense | 概览、策略配置和决策历史工作台 |
| KnowledgePage | knowledge | dense | 筛选、分页、详情编辑 |
| NotesPage | notes | standard | 列表/搜索、详情编辑和创建 |
| IntelligencePage | intelligence | standard | 四个治理工作区 Tab |
| LearningPage | learning | standard | 学习状态、指标和群组上下文 |
| JargonPage | jargon | dense | 群组、双 Tab、表格和 CRUD |
| ProfilesPage | profiles | dense | 分页、结构化画像和标签编辑 |
| AffectionPage | affection | standard | 情绪、指标、排行榜和用户 CRUD |
| SocialPage | social | standard | 分类 Tab、关系列表和 CRUD |
| SystemPage | system | standard | 系统 Tab、备份、维护和确认 |
| ConfigPage | config | dense | 分组导航、长表单、revision 和应用状态 |

页面模板是滚动与密度契约，不等于视觉主题。相同组件在三种模板中必须使用同一 token 和状态行为。

### 8.1 InjectionStrategyPage 工作台契约

InjectionStrategyPage 使用 dense 模板，并保持固定 PageHeader 与 Overview、Strategy Configuration、Decision History 三个顶层 Tab。活跃 Tab 内容是页面内部唯一滚动所有者；PageFrame、Tabs 和 PageContent 不得形成嵌套或重复滚动。Overview 与 Strategy Configuration 使用 constrained 内容宽度，Decision History 使用 full 宽度并为表格建立受控横向滚动边界。

本节是 InjectionStrategyPage 页面模板、内容宽度与滚动所有权的当前权威；早期功能规格中与 `standard` 模板或整页滚动有关的描述由本节取代。

三个 Tab 遵守以下领域约束：

- **Overview**：保留六项指标、两个真实数据图表和三类最近事件。指标使用 MetricGrid，图表使用完整 Card composition，加载、空数据和错误使用 StatePanel。时间窗口 Select 使用 `items + SelectValue + SelectGroup` 的单一 label 数据源，Popup 自动匹配 Trigger 宽度。
- **Strategy Configuration**：保留纯手动、自动与混合路由切换、预设对比、交付方式、高级策略预设与覆盖，以及可配置保留期。表单继续组合 FieldSet、FieldGroup、Field、Input、Switch、Select 和 Table；所有 Select 遵守第 10.4 节的单一 `items`/label 数据源与 Popup 匹配 Trigger 宽度契约。离线与错误提示使用共享 Alert。未保存修改和 revision 冲突进入共享编辑流程，保存失败或冲突时必须保留草稿。该页面不引入 Schema 表单生成器或新的通用表单抽象。
- **Decision History**：筛选区使用 PageToolbar，保留全部筛选字段和后端真分页。筛选与分页 Select 遵守第 10.4 节；数据表独立横向滚动，窄屏不得隐藏核心查看操作。loading、empty、error、retry 和 stale-response 保护均属于行为契约。

Overview、Decision History 和决策详情必须通过 Page API 读取 SQLite 全量持久化结果；不得以客户端样本、仅内存聚合或 mock 数据作为生产数据源。可配置保留期与最大行数通过 Strategy Configuration 保存，筛选和分页由后端执行。

决策详情使用受控 Sheet，并固定为 `shrink-0` Header、`min-h-0 flex-1 overflow-y-auto` Body、`shrink-0` Footer 三段结构。Footer 使用 SheetFooter，并以 Separator 与正文分隔；不得让整个 SheetContent 连同标题一起滚动。Sheet 必须保留 Title、Description、焦点恢复和 Recall Trace 跳转，在 390px 视口下最后一个字段和底部按钮均不得被遮挡。

## 9. 视觉语言

### 9.1 排版

运行时字体权威为：

    "Geist Variable", "Noto Sans SC", sans-serif

Dashboard 使用固定应用字体尺度，不随视口流体缩放。

| 角色 | 规格 | 用途 |
|---|---|---|
| 页面标题 | text-xl / 20px，line-height 28px，font-semibold | 唯一 h1 |
| 面板标题 | text-base，font-semibold | Card、Sheet、Dialog、分区 |
| 正文/控件 | text-sm / 14px | 表单、表格、按钮、说明 |
| 元数据 | text-xs / 12px | 次要状态、时间、辅助说明 |
| 微型文本 | 10px | 仅限未读数、极短系统元数据 |

规则：

- 页面不得新增 15px、17px 等相近但无语义的字号。
- 技术键和短 ID 可以使用等宽字体；正文不得使用等宽字体。
- 数据列需要对齐数字时使用 tabular numbers。
- 不通过超大标题制造层级；使用字重、位置、颜色和留白共同建立层级。

### 9.2 颜色

当前主题使用中性 OKLCH 色板。页面只能消费语义 token：

| Token/类 | 用途 |
|---|---|
| background / bg-background | 应用和页面基底 |
| foreground / text-foreground | 主文本 |
| card / bg-card | 独立对象或指标表面 |
| popover | Select、Dialog、浮层表面 |
| muted / muted-foreground | 工具栏、次要区域、辅助文本 |
| primary / primary-foreground | 主动作、导航选中、关键强调 |
| secondary | 次级动作和轻量分组 |
| destructive | 错误与不可逆动作 |
| border / input | 分隔与表单边界 |
| ring | 键盘焦点和打开态 |
| --selection-surface / border / indicator / foreground | 选中表面、边框、指示线和前景色 |
| sidebar-* | Sidebar 专用语义层 |

navigation、control、row、surface、current-item 是 selection-state recipe 的使用场景，不是颜色 token。页面应复用 selectionStateVariants，不自行拼接第二套选中态颜色。

要求：

- 新代码不得使用任意 gray、blue、red、emerald 等原始 Tailwind 色值。
- 成功、警告、错误必须同时有文本、图标或结构提示，不能只靠颜色。
- 明暗主题只重定义语义 token；页面不得手写一套 dark 颜色。
- 旧 --color-*、--text-* 仅为兼容别名，不得新增消费者。
- 装饰性透明、渐变和玻璃效果禁止使用；共享 Popover 的轻量模糊只用于浮层可读性。

### 9.3 圆角、边框和阴影

- 常规页面和新组件的最大圆角为 8px，即 rounded-lg。
- Badge、状态点、头像、进度轨道等语义上必须为圆形或胶囊形的元素可以使用 rounded-full；该例外不得扩展到普通容器或按钮。
- 按钮、输入、Select、Card、Sheet、Dialog 均从共享组件获得圆角。
- Tailwind 配置中存在的 xl、2xl、3xl 是兼容能力，不代表页面可以使用。
- 边框使用 border-border 或组件内置 border-input。
- Card 使用轻量 shadow-sm；Select/Popover 使用 shadow-md 和细 ring；Dialog/Sheet 使用共享 elevation。
- 阴影只表达层级，不用于装饰。如果阴影成为视觉主角，应改用边框、表面或留白。

共享组件内部尚存的 registry 兼容圆角属于实现债务；页面调用方不得复制。

### 9.4 间距

使用 4px 基线，主要节奏为 4、8、12、16、20、24、32、48、64px。

- 同组控件：gap 8px。
- 标签与控件、紧密元数据：gap 4–8px。
- 表单字段：gap 16px。
- Card/MetricGrid：gap 16px。
- 页面分区：24px 起。
- 新的 flex/grid 布局优先使用 gap；已有垂直内容节奏可以保留 space-y-*，但同一布局层不得混用两种间距机制。
- 禁止无理由的任意像素间距。

### 9.5 图标

- 图标库固定为 Lucide。
- 普通控件图标默认 16px；页面标题图标通常 20px。
- Button 内图标使用 data-icon=inline-start 或 inline-end，由 Button 控制尺寸。
- 纯图标按钮必须有 aria-label，必要时同时提供 title。
- 图标不能替代不可推断的文本含义。

## 10. 共享组件规范

### 10.1 总原则

优先使用 src/components/ui 已有 shadcn/Base UI 组件。不得手写等价 Button、Input、Textarea、Checkbox、Select、Tabs、Dialog、Sheet、Table、Toast、Skeleton 或 Separator。

组件样式由 variant 和语义 token决定；页面 className 主要用于布局、宽度和响应式组合，不覆盖组件颜色和字重。

### 10.2 Button

| 场景 | Variant |
|---|---|
| 当前流程的主要提交/创建 | default |
| 次要上下文动作 | secondary 或 outline |
| 低强调、行内、关闭和清除 | ghost |
| 删除和不可逆操作 | destructive |

现有尺寸：xs 24px、sm 28px、default 32px、lg 36px。xs/sm 只用于桌面密集区域；移动端主要动作应使用 default/lg 或确保至少 44px 的实际触控范围。

提交中必须禁用重复点击并显示明确动词，如“正在保存”；不得使用“确定”“提交”等无法说明结果的标签。

### 10.3 Card

Card 只用于：

- 单个可操作对象。
- 指标或同维度对比。
- 有明确边界的系统分组。

必须使用 CardHeader、CardTitle、可选 CardDescription、CardContent 和需要时的 CardFooter。普通段落不包 Card，Card 内不再嵌套 Card。

### 10.4 Select

Select 是 Base UI 组件，必须满足以下契约：

1. SelectItem 必须位于 SelectGroup 内。
2. 菜单文案与关闭状态文案必须来自同一份 items 数据。
3. 当 value 与用户可见 label 不同、需要本地化或 label 含统计信息时，Select Root 必须接收 items。
4. Trigger 使用 SelectValue，不手写另一套映射。
5. 不得把内部枚举值直接作为最终用户文案；未知后端值可以显式回退到原值。
6. Popup 使用 w-[var(--anchor-width)] 和 min-w-[var(--anchor-width)]，自动匹配 Trigger 实际宽度。
7. 页面只决定 Trigger 的布局宽度，不为 Popup 设置独立固定宽度。
8. 长 label 必须截断或换行而不扩大页面；中、英、俄需使用同一选项结构。
9. 空值/全部筛选必须使用与 Base UI 语义兼容的值，并有明确 placeholder。

推荐数据形状：

~~~tsx
const items = source.map((item) => ({
  value: item.id,
  label: localize(item),
}));

<Select items={items} value={value} onValueChange={change}>
  <SelectTrigger aria-label={fieldLabel}>
    <SelectValue />
  </SelectTrigger>
  <SelectContent>
    <SelectGroup>
      {items.map((item) => (
        <SelectItem key={item.value} value={item.value}>
          {item.label}
        </SelectItem>
      ))}
    </SelectGroup>
  </SelectContent>
</Select>
~~~

### 10.5 Field 与表单控件

- 表单使用 FieldGroup + Field + FieldLabel。
- Placeholder 不能代替 Label。
- data-invalid 放在 Field，aria-invalid 放在控件。
- 错误使用 aria-describedby 与唯一错误 ID 连接。
- 布尔值使用 Switch/Checkbox；2–7 个短互斥选项优先 ToggleGroup/Tabs；枚举使用 Select；长文本使用 Textarea；数值使用有边界的数字输入或 Slider。
- 不让管理员编辑派生指标、revision、时间戳或不可变标识；它们应显示为只读信息，而不是伪装为 disabled 输入。

### 10.6 Table、列表和选择

- 表头使用语义 Table 组件；数值、状态和操作列保持稳定对齐。
- 行选择 Checkbox 必须有可读名称。
- 点击 Checkbox 只改变选择，不打开详情。
- 整行可点击时仍保留明确的“查看”或行尾操作。
- 查询、群组、筛选和页码变化时清除不可见选择。
- 批量栏说明选中数量，并只暴露可安全验证的动作。
- 窄屏优先使用受控横向滚动；不得压缩列到无法识别，也不得隐藏核心操作。

### 10.7 Tabs 与分段控件

- TabsTrigger 必须位于 TabsList。
- 同一层级只保留一个明确的 Tab 组。
- Tab 使用 roving tabindex 和方向键导航。
- 2–7 个互斥、短标签选项可以使用 Tabs/ToggleGroup；复杂筛选仍使用 Select。

## 11. 编辑、创建与危险操作

### 11.1 容器选择

| 任务 | 容器 |
|---|---|
| 查看现有实体详情 | 受控 EntityEditorSheet 的 view 模式 |
| 编辑现有实体 | 同一 Sheet 切换到 edit 模式 |
| 创建实体 | EntityCreateDialog |
| 未保存修改 | UnsavedChangesDialog |
| revision 冲突 | EditConflictDialog |
| 单条/批量删除 | DeleteConfirmDialog |
| 非实体高影响运维动作 | ActionConfirmDialog |

不得恢复自定义 Modal，也不得用普通 Card 模拟模态流程。

### 11.2 编辑状态机

~~~mermaid
stateDiagram-v2
    [*] --> Closed
    Closed --> View: 打开记录
    View --> Edit: 编辑
    Edit --> View: 取消且恢复基线
    Edit --> Saving: 有修改、有效、空闲
    Saving --> View: 保存成功
    Saving --> Edit: 校验/网络失败，保留草稿
    Saving --> Conflict: revision 不一致
    Conflict --> Edit: 加载远端或重放本地差异
    Edit --> Unsaved: 关闭/切换记录/离开页面
    Unsaved --> Edit: 继续编辑
    Unsaved --> Closed: 明确放弃
    View --> Closed: 关闭
~~~

### 11.3 固定结构

Sheet 和 Dialog 都使用：

- shrink-to-content 标题区。
- min-h-0、flex-1、overflow-y-auto 内容区。
- shrink-to-content、可换行的底部操作区。

移动端最后一个字段不得被底栏遮挡。Sheet 宽度不超过 min(100vw, 32rem)，创建 Dialog 使用视口相对最大高和宽。

### 11.4 校验

- EditFormLayout 汇总 path-indexed fieldErrors 与表单级错误。
- 错误摘要使用 role=alert，可聚焦，并链接到真实字段。
- 首个错误字段在校验激活时获得焦点；等价 rerender 不抢走用户当前焦点。
- 网络失败、未知错误和无法映射的字段错误进入同一表单级摘要。
- 失败不关闭容器，不丢失草稿。

### 11.5 并发冲突

冲突界面只提供明确选择：

1. 加载服务端最新值。
2. 将用户实际修改的字段重新应用到最新值。
3. 继续检查当前草稿。

不提供绕过 revision 的“强制覆盖全部”。冲突处理中禁止静默关闭。

### 11.6 删除与确认

- 普通单条删除显示对象名称、群组和不可撤销后果。
- 大数量或跨群组批量删除要求输入确认词。
- 确认按钮必须使用“删除对象/删除 N 项”等具体动作。
- 删除进行中禁用重复提交和关闭。
- 部分失败后清除成功项选择，保留失败项及其错误。
- 不将一次确认沿用到下一次打开。

## 12. 页面状态与反馈

每个数据页面必须设计以下状态：

| 状态 | 行为 |
|---|---|
| initial | 显示页面结构，不闪现错误或旧选择 |
| loading | 优先 Skeleton/StatePanel；长流程说明正在加载什么 |
| refreshing | 保留已有数据，局部显示刷新状态 |
| empty | 说明为什么为空，并在可操作时给出创建/调整筛选动作 |
| error | 说明发生了什么、可重试方式；不暴露内部堆栈 |
| submitting | 冻结提交快照，禁用重复提交和危险关闭 |
| success | 简短 Toast，并用真实响应更新或重新验证数据 |
| conflict | 保留草稿，打开冲突流程 |
| offline | 应用壳明确显示离线；页面错误不冒充成功 |
| partial failure | 显示成功/失败数量和失败项 |

Toast 用于短暂结果，不承担必须阅读的校验或冲突信息。长错误、字段错误和未保存状态必须留在相关页面或弹层内。

## 13. 响应式设计

### 13.1 验收视口

| 场景 | 视口 |
|---|---|
| 移动 | 390 × 844 |
| 桌面 | 1366 × 900 |
| 宽屏 | 2048 × 1152 |

断点由内容决定，当前主要使用 sm、md、lg。不得以设备类型分叉两套页面代码。

### 13.2 移动适配

- Sidebar 变为抽屉，顶栏保留菜单与全局搜索入口。
- PageHeader actions 占满一行并换行。
- 创建 Dialog 接近全屏；编辑 Sheet 不超过视口宽。
- 表单内容独立滚动，底栏保持可见。
- 表格使用横向滚动边界或领域专用重排，不隐藏主操作。
- 搜索、筛选、创建、保存、取消、删除和冲突处理不能在移动端消失。
- 长俄文允许换行；按钮不依赖固定宽度截断。
- 页面和浮层不得产生根级横向溢出。

### 13.3 触控与输入方式

- 不依赖 hover 才能发现功能。
- 关键移动操作目标至少 44px；紧凑 24–32px 控件只用于细指针桌面环境。
- 可滑动手势必须有可见按钮替代。
- 软键盘出现时，当前字段和底部动作仍应可滚动到视口内。

### 13.4 InjectionStrategyPage 三档验收

| 视口 | 验收重点 |
|---|---|
| 390 × 844 | 顶层 Tab 可横向访问且不挤压页面；配置字段和操作可完整滚动；决策表在自身边界内横向滚动且查看操作可见；详情 Sheet 的 Header/Footer 保持可见，最后字段不被遮挡 |
| 1366 × 900 | PageHeader 与三 Tab 稳定，活跃内容承担唯一纵向滚动；Overview 和 Strategy Configuration 保持 constrained，Decision History 使用 full 宽度且分页可见 |
| 2048 × 1152 | constrained 内容不被无意义拉宽；Overview 图表保持可比较尺度；Decision History 利用可用宽度但仍保留表格滚动边界，并生成独立宽屏截图基线 |

## 14. 动效

动效只表达状态变化，不作为装饰。

| 场景 | 时长 |
|---|---|
| 按压、颜色、选中反馈 | 100–150ms |
| Select、Popover、Tooltip | 100–200ms |
| 页面切换 | 200ms，opacity + translateY |
| Sheet、Dialog、Sidebar | 200–300ms |
| 功能性 loading pulse | 可以更长，但不能阻断阅读 |

规则：

- 默认只动画 transform 和 opacity；功能性进度条可以动画 width/inline-size，但不得推动周围布局。避免动画 height、padding 和 margin。
- 桌面 Sidebar 当前使用 width + transform 完成展开/收起，属于应用壳兼容实现；普通页面和新组件不得复制该模式。
- 进入使用 ease-out，离开更快。
- 禁止 bounce、elastic 和长时间连续装饰动画。
- prefers-reduced-motion 下关闭空间位移与非必要过渡；焦点、进度和功能性反馈仍需可见。
- App 的 200ms 页面过渡是当前权威；旧 .page-enter 300ms 视为兼容实现，不应形成第二套页面动效。

## 15. 可访问性

### 15.1 页面与导航

- PageFrame 使用可命名 region。
- 每页只有一个 h1。
- Sidebar nav 有可访问名称，当前项使用 aria-current=page。
- 分组折叠状态使用 aria-expanded 和 aria-controls。
- 全局搜索、分页和批量 Toolbar 必须有明确名称。

### 15.2 交互组件

- 所有控件必须拥有稳定用途名称，而不是只依赖当前值。
- focus-visible ring 在亮暗主题下均至少 3:1 对比，不得取消且无替代。
- hover 和 focus 都要有反馈；功能不能只存在于 hover。
- Dialog、Sheet、Drawer 必须有 Title 和 Description。
- 图标按钮必须有 aria-label；装饰图标使用 aria-hidden。
- 进度条、Checkbox、Select、Tabs 和表格选择必须使用正确角色和键盘模式。

### 15.3 表单与错误

- Label 与控件通过 htmlFor/id 或组件语义连接。
- aria-invalid、aria-describedby 和错误 ID 完整。
- 错误摘要可聚焦并跳转到具体字段。
- 错误、警告、冲突和成功不只依赖颜色。
- 键盘可以完成打开详情、编辑、保存、取消、创建、删除确认和冲突处理。

## 16. 国际化与 UX 文案

### 16.1 三语言契约

- 所有用户可见文案同步提供中文、英文和俄文。
- key 集合必须保持一致。
- 内部枚举不得直接作为最终文案。
- 未知后端枚举可显示原值，但不能伪装为已本地化。
- Select 关闭状态和菜单项必须使用同一 label。
- 为俄文和英文预留至少 30–40% 文本扩展空间。

### 16.2 术语

同一动作只使用一个术语：

- 创建，不混用新增/添加，除非领域含义不同。
- 编辑，不混用修改。
- 删除用于不可恢复操作。
- 归档用于可恢复状态变化。
- 保存更改、创建对象、删除 N 项等按钮使用“动词 + 对象/结果”。

禁止使用“确定”“OK”“Submit”“Yes/No”等模糊标签。

### 16.3 错误文案

错误应回答：

1. 发生了什么。
2. 为什么。
3. 用户如何恢复。

不责备用户，不使用幽默，不暴露堆栈、SQL 或敏感载荷。

## 17. 数据可视化与图工作区

- 图表必须表达可比较的真实数据，不使用装饰性 sparkline。
- 图表、进度和状态必须有可访问名称或等价文本。
- 数字使用一致格式、单位和小数位；跨语言使用 locale。
- 颜色不能是图区分系列或状态的唯一手段。
- GraphPage 的主画布保持 workspace 模板，不用普通 Card 包裹整张画布。
- 图节点详情、筛选和搜索不得覆盖或挤压画布到不可用尺寸。

## 18. 测试与视觉验收

### 18.1 行为测试

页面或组件行为变更必须先写失败的 Vitest + React Testing Library 测试，再做最小实现。

重点覆盖：

- PageFrame 模板和滚动所有权。
- 可访问名称、焦点和键盘操作。
- loading、empty、error、submitting、conflict。
- Select value/label、菜单宽度和选中态。
- 脏状态、取消、保存、创建和重复提交保护。
- 查询变化后的选择清理。
- 移动端弹层结构。
- Injection 顶层 Tab、唯一滚动所有权和 constrained/full 内容模式。
- Injection 配置保存失败、未保存修改和 revision 冲突时的草稿保留。
- Injection 决策真分页、筛选、retry、stale-response 保护和详情 Sheet 三段结构。

### 18.2 Browser smoke

当前真实浏览器基线覆盖：

- 390 × 844 移动端。
- 1366 × 900 桌面端。
- 2048 × 1152 宽屏。
- 亮色与暗色。
- 中文、英文和俄文。
- Graph、Memory、Jargon、System、Intelligence 等核心页面。
- Config loading/conflict。
- Injection Overview、Strategy Configuration、Decision History 和决策详情 Sheet；现有四张截图为 `injection-overview.png`、`injection-config-conflict.png`、`injection-decisions.png` 和 `mobile-injection-detail.png`。
- 全局搜索滚动与精确导航。
- 编辑 Sheet、冲突、错误摘要、批量 Toolbar、移动 Affection/Mood。

Injection 页面对齐完成后，必须在现有四张截图之外增加 2048 × 1152 独立宽屏基线。

截图验收必须人工检查：

- 页面内容进入视口。
- 没有残留 Loading 遮罩或加载文案。
- 没有根级横向溢出。
- 固定底栏没有遮挡最后字段。
- 长翻译没有破坏关键动作。
- Graph 画布、移动 Sidebar 和弹层布局可用。

### 18.3 质量门禁

~~~bash
cd pages/dashboard
npm test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser

cd ../..
python scripts/check_all.py
~~~

生产产物必须保持 AstrBot 页面桥接所需的 classic-script 单 bundle 兼容格式；不得提交 type=module、crossorigin 或陈旧 hash 资产。

## 19. 新页面实施清单

新增或重构页面前：

- [ ] 明确目标用户任务和主要数据状态。
- [ ] 从 standard、dense、workspace 中选择模板。
- [ ] 将路由加入 src/App.tsx，并将导航加入 src/lib/navigation.ts 的正确组。
- [ ] 读取 PageLayout 和现有同类页面，不复制页面壳。

布局：

- [ ] 使用 PageFrame、PageHeader、可选 PageToolbar、PageContent。
- [ ] 明确唯一滚动所有者。
- [ ] Header actions 在移动端可换行。
- [ ] 需要指标时使用 MetricGrid。
- [ ] Card 只用于独立对象或指标，不嵌套。

组件：

- [ ] 优先使用现有 shadcn/Base UI 组件。
- [ ] 使用语义颜色和 rounded-lg 上限。
- [ ] Button variant 与动作层级一致。
- [ ] Select 使用 items、SelectValue、SelectGroup，并保证内外 label 同源。
- [ ] Dialog/Sheet 包含 Title 与 Description。

状态与编辑：

- [ ] initial/loading/refreshing/empty/error/submitting/success 均有设计。
- [ ] 写操作保留草稿并防止重复提交。
- [ ] revision 冲突使用共享流程。
- [ ] 删除和批量动作使用正确确认等级。
- [ ] 查询上下文变化清理隐藏选择。

响应式、i18n、a11y：

- [ ] 390、1366、2048 三档可用。
- [ ] 关键功能未在移动端隐藏。
- [ ] 中、英、俄 label 和错误完整。
- [ ] 无 raw enum 作为已知用户文案。
- [ ] 控件、图标按钮、表格选择和分页具有稳定可访问名称。
- [ ] 键盘与 reduced-motion 可用。

验证：

- [ ] 行为测试先红后绿。
- [ ] npm test、build、artifact、runtime smoke 通过。
- [ ] browser smoke 通过且截图已人工检查。
- [ ] 设计级变更同步更新本文档和 pages/dashboard/AGENTS.md。

## 20. 禁止模式

- 页面内自建 Header、Toolbar 或根滚动壳。
- 原始 button/input/select 替代已有共享组件。
- 菜单内外分别维护两套 label。
- SelectItem 直接放在 SelectContent 下。
- 固定 Popup 宽度与 Trigger 宽度不一致。
- 把 raw enum、技术 key 或未翻译错误直接显示给已知场景用户。
- 普通内容全部卡片化、卡片嵌套或重复标题。
- 新增 rounded-xl、rounded-2xl、rounded-3xl 页面容器。
- 新增 raw Tailwind 颜色或页面级 dark 颜色分支。
- 以 z-9999、绝对定位或 overflow hidden 修补浮层。
- 仅有 hover 的操作、不可见焦点或 placeholder 充当 label。
- 移动端隐藏创建、保存、删除、筛选或冲突处理。
- 保存失败后关闭编辑器或清空草稿。
- 未人工检查截图就宣称 browser smoke 完成。

## 21. 兼容层与已知边界

以下内容存在于当前代码，但不得作为新规范复制：

| 项目 | 当前边界 | 后续原则 |
|---|---|---|
| 旧颜色/文本变量 | --color-*、--text-* 仍服务复杂旧组件 | 只迁移消费者，不新增 |
| 原始状态色 | 少量在线/错误位置仍使用 raw 色 | 修改相关组件时迁移到语义 token |
| 超大圆角 token | Tailwind 仍声明 xl/2xl/3xl | 页面与新组件仍以 rounded-lg 为上限 |
| Dialog footer 圆角 | 共享 registry 代码有大于 8px 的实现细节 | 页面不得复制，更新组件时再统一 |
| 字体配置 | index.css 使用 Geist；Tailwind fontFamily.sans 仍含旧 fallback | index.css 为运行时权威 |
| 页面进入动效 | App 200ms 与旧 page-enter 300ms 并存 | App 200ms 为新页面权威 |
| reduced-motion 覆盖 | 主题切换和部分选中态已覆盖，Framer 页面切换、旧 page-enter、卡片位移和部分 loading 尚未完全覆盖 | 修改相关动效时补齐 motion-reduce 或媒体查询 |
| 旧响应式辅助类 | index.css 保留少量兼容类 | 新组件优先使用布局原语和局部 Tailwind |
| 紧凑控件高度 | xs/sm 低于 44px | 仅用于桌面密集区，移动关键操作扩大目标 |
| 页面加载/空状态 | 少量页面仍使用裸文本 loading/empty | 新代码使用 Skeleton/StatePanel，触碰页面时迁移 |
| 手写 Select 外显 | 少量筛选仍手写与菜单相同的 label | 输出目前一致，但修改时迁移到 items + SelectValue |
| Graph 详情区 | GraphPage 使用固定 380px 自定义检查器 | 保持 workspace 特例；不得复制到普通实体详情 |
| 页面 region 命名 | Graph、Recall、Timeline 的 PageFrame 尚无本地化 aria-label | 修改这些页面时补齐，不复制缺口 |
| 跳过导航 | 当前应用壳尚无 skip-to-main-content | 新增壳级可访问性工作时统一实现 |

兼容层的治理遵循“触碰时迁移”：只有任务实际修改相关组件时才做窄范围统一，不在无关功能中批量重写。

## 22. 决策记录

| 日期 | 决策 | 理由 | 影响 |
|---|---|---|---|
| 2026-07-10 | 使用 standard、dense、workspace 三种页面模板 | 统一滚动、密度和工具工作区边界 | 所有页面必须选择模板 |
| 2026-07-10 | 固定五组导航与语义主题 | 保持信息架构和主题一致 | 页面不得自建导航与主题 |
| 2026-07-14 | 使用共享编辑生命周期 + 领域表单 | 统一草稿、校验、冲突和移动端交互，同时保留领域语义 | CRUD 页面复用 editing 组件 |
| 2026-07-17 | Select Popup 自动匹配 Trigger，label 使用单一 items 数据源 | 消除打开/关闭宽度和文案漂移 | 所有 Select 遵守第 10.4 节 |
| 2026-07-17 | 将当前分支设计整理为统一页面规范 | 为新增页面、审查和后续迁移提供单一设计契约 | 设计级变更同步维护本文档 |
| 2026-07-17 | InjectionStrategyPage 使用 dense 三 Tab 工作台 | 固定滚动所有权、内容宽度、配置草稿和决策详情弹层契约 | 注入工作台按第 8.1、13.4 和 18 节验收 |

## 23. 相关文件

- .impeccable.md：用户、品牌气质和美学方向。
- pages/dashboard/AGENTS.md：模块结构、强制规则和质量门禁。
- pages/dashboard/src/index.css：主题、token、字体和全局动效。
- pages/dashboard/src/components/layout/PageLayout.tsx：页面布局原语。
- pages/dashboard/src/components/layout/Sidebar.tsx：导航壳和响应式行为。
- pages/dashboard/src/lib/navigation.ts：五组导航模型。
- pages/dashboard/src/components/ui：共享 UI recipe。
- pages/dashboard/src/components/editing/DESIGN.md：统一编辑组件的详细状态契约。
- pages/dashboard/scripts/browser_smoke.mjs：视口、主题、语言和编辑流程视觉基线。
- docs/superpowers/specs/2026-07-14-dashboard-unified-editing-crud-design.md：统一 CRUD 的产品与技术决策来源。

## 24. 变更历史

| 日期 | 变更 |
|---|---|
| 2026-07-17 | 将 InjectionStrategyPage 纳入 16 页统一基线，补充 dense 三 Tab、唯一滚动、配置与决策 Sheet、三档视口和 browser smoke 契约。 |
| 2026-07-17 | 基于当前 Dashboard 分支、统一 CRUD、Select 修复和 browser smoke 基线，建立第一版统一页面设计规范。 |
