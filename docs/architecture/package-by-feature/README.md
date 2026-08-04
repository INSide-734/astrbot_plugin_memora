# Package-by-Feature Migration Baseline

> 状态：阶段 1 基线，待 AST-6 架构评审
>
> 最后更新：2026-08-04
>
> 事实提交：`f1ad662cd56766f090b6699ea81ee164783b78b6`

本目录为 Memora 全仓 Package-by-Feature 重构提供可执行输入。它描述当前
Python 后端、目标领域结构、逐文件迁移路径、公共导入契约、依赖规则、迁移
步骤、测试门禁、数据安全和阶段屏障。本目录不表示目标目录已经落地；除本
文档集外，阶段 1 不移动生产代码、不建立兼容层，也不修改 API、配置、SQL
或持久化 schema。

## 事实基线

本轮从 Multica 检出的实际仓库重新盘点，而不是复用早期估算：

| 范围 | 文件 | 物理行 |
|---|---:|---:|
| `main.py` + `core/**/*.py` | 329 | 81,033 |
| `tests/**/*.py` | 287 | 88,521 |
| `scripts/**/*.py` | 9 | 2,347 |
| `docs/**/*.md` + `website/docs/**/*.md` | 27 | 以各文档为准 |

当前有 10 个生产文件超过 800 行。早期评审中的热点方向正确，但具体行数已
变化：`core/managers/memory_engine_crud.py` 当前为 790 行，不是 818 行；
`core/managers/backup_manager.py` 当前为 889 行，不是 954 行。完整证据见
[CURRENT_BASELINE.md](CURRENT_BASELINE.md)。

## 文档导航

| 文档 | 用途 | 主要消费者 |
|---|---|---|
| [CURRENT_BASELINE.md](CURRENT_BASELINE.md) | 当前规模、入口、热点和动态引用证据 | AST-6、各领域执行者 |
| [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) | 目标目录、领域词汇和分层职责 | AST-6、AST-7 至 AST-15 |
| [DEPENDENCY_GRAPH.md](DEPENDENCY_GRAPH.md) | 当前耦合热点与目标依赖图 | AST-6、依赖门禁实现者 |
| [MODULE_MIGRATION_MATRIX.md](MODULE_MIGRATION_MATRIX.md) | 329 个生产文件的唯一目标路径 | 所有后端迁移任务 |
| [PUBLIC_API_AND_COMPATIBILITY.md](PUBLIC_API_AND_COMPATIBILITY.md) | 公共门面、动态引用和兼容窗口 | AST-6、AST-9 至 AST-17 |
| [DEPENDENCY_RULES.md](DEPENDENCY_RULES.md) | 允许/禁止依赖和自动门禁要求 | AST-7 及所有后续任务 |
| [MIGRATION_RUNBOOK.md](MIGRATION_RUNBOOK.md) | 单领域迁移、提交、PR 和回滚步骤 | 领域执行者与 Lead |
| [TEST_AND_ACCEPTANCE.md](TEST_AND_ACCEPTANCE.md) | 精确命令、基线结果和阶段验收 | QA、Reviewer、Lead |
| [SECURITY_AND_DATA_SAFETY.md](SECURITY_AND_DATA_SAFETY.md) | 数据权威、事务、隐私和四类高风险领域 | Graph/Memory/Backup/Conversation 执行者 |
| [WAVES.md](WAVES.md) | 阶段 1 至 9 的输入、所有权和屏障 | AST-5 协调者 |

## 目标与成功标准

- 每个生产业务文件只有一个目标领域和目标路径；横向门面若应删除，矩阵明确
  写出删除条件而不是虚构目标文件。
- 领域包按实际职责使用 `domain / application / infrastructure / interfaces`，
  不机械创建空目录。
- 包外只导入目标领域 `__init__.py` 或显式 `contracts`；具体实现、mixin、
  repository 和 Page API helper 默认内部。
- `platform/bootstrap` 是唯一可以装配多个领域具体实现的 composition root。
- `shared` 只接收无业务语义且至少被两个领域稳定复用的技术能力，不成为新的
  `utils/base` 杂项目录。
- 后端、Dashboard、测试和文档采用相同领域名；前端文件级映射由 AST-22 负责，
  本基线只固定双方要共享的词汇。
- 每个领域结构迁移可以独立 revert，且无需数据库回滚。

## 术语

- **领域门面**：领域顶层 `__init__.py` 或显式 `contracts`，只导出稳定类型、
  协议、异常和用例入口。
- **内部实现**：`application` 内部协作对象、`infrastructure` 适配器、私有模块、
  mixin、repository helper 和页面实现。
- **composition root**：`platform/bootstrap` 中集中构造 Store、Provider、领域服务
  和平台适配器的唯一位置。
- **canonical memory**：SQLite 中具有稳定整数 ID 的权威记忆；FTS、FAISS、Graph
  与 relation/projection 均为可重建派生面。
- **结构迁移**：只改变包和导入位置，不改变算法、SQL、schema、配置、返回值、
  异常、事务、日志或隐私语义。

## 权威顺序

运行时行为以生产代码、schema 和可执行测试为准。当前稳定架构约束以
[项目级设计](../../../DESIGN.md) 和各模块 `AGENTS.md` 为准。本目录中的目标路径
在 AST-6 评审通过前属于提案；评审修改后应原地更新，不能另建第二份矩阵。

## 已知评审点

1. 仓库没有第三方插件消费者清单，因此无法仅凭当前代码证明
   `core.managers.*`、`core.storage.*` 等具体模块路径是外部公共 API。AST-6 必须
   决定哪些候选入口需要一个版本的纯 re-export。
2. 实际代码还包含 `TraitEvolutionTracker` 与 `StyleAnalyzer`，目标词汇因此增加
   `cognition/personality`；这比早期只列 affection/expression/jargon/social 更完整，
   需与 AST-22 的前端词汇表复核。
3. 现有命名空间导入测试只覆盖 recall trace、injection recorder 和 perf tracker。
   AST-7 必须扩大到所有新门面以及命令 `__module__` 绑定。
4. `npm ci` 在 2026-08-04 报告 1 个 moderate 和 2 个 high 依赖审计项。本任务不
   修改前端依赖或锁文件；风险由后续依赖治理单独处理，不能通过自动修复混入
   结构迁移。

## 更新纪律

矩阵只在源文件增删、目标架构评审结论或调用方证据变化时更新。每次更新同时
记录新 commit、重新运行静态 import 与动态字符串扫描，并确认 329 行覆盖断言
已调整到新的真实文件数。一次运行结果不是永久门禁；新的结果应写入
[TEST_AND_ACCEPTANCE.md](TEST_AND_ACCEPTANCE.md) 的带日期基线节。
