# Migration Waves and Stage Barriers

> 状态：与 AST-5 当前 9 个阶段、17 个子任务对齐
>
> 阶段推进所有者：AST-5

本文件是导航，不替代 Multica issue 的实时状态。只有 AST-5 在当前阶段全部终态并
验收后才能提升下一阶段；执行者不得因阅读本文件自行启动 `backlog` 任务。

## 阶段总览

| 阶段 | 后端任务 | 前端/评审任务 | 输入 | 完成屏障 |
|---:|---|---|---|---|
| 1 | AST-21 基线/矩阵/契约 | AST-22 Dashboard 映射 | 实际仓库、AST-4 计划 | 两份 PR、统一词汇、基线和风险完整 |
| 2 | AST-6 只读架构评审 | 同一评审覆盖两份输入 | 阶段 1 PR | 明确通过/修改后通过；阻塞项关闭 |
| 3 | AST-7 platform/shared/门禁 | AST-8 app/features/shared/门禁 | 评审通过的架构 | 正反例门禁、namespace import、骨架零行为变化 |
| 4 | AST-9 identity/cognition/review/evaluation | AST-10 对应 Dashboard | 阶段 3 门禁 | 每领域独立 PR、接口清单、精确测试 |
| 5 | AST-11 conversation/profile/knowledge/notes/operations | AST-12 对应 Dashboard | 阶段 4 完成 | 数据/备份专项证据、门面和回滚完整 |
| 6 | AST-13 graph/retrieval/evolution/injection | AST-14 对应 Dashboard | 阶段 5 完成 | ID/排序/事务/隐私语义不变 |
| 7 | AST-15 memory + 后端聚合入口 | AST-16 memory + Dashboard app | 阶段 6 接入清单 | composition root、真实 namespace、端到端入口通过 |
| 8 | AST-17 后端全量/清理 | AST-18 Dashboard 全量/清理 | 阶段 7 集成基线 | 全量门禁、过期 shim/横向桶清理 |
| 9 | AST-19 后端独立终审 | AST-20 前端独立终审 | 阶段 8 候选 PR | 无阻塞 finding，残余风险透明 |

## 阶段 1：事实基线

AST-21 交付本目录和后端 PR；AST-22 交付 Dashboard 文件级映射。双方必须使用
memory、conversation、identity、retrieval、graph、injection、evolution、knowledge、
notes、profile、review、evaluation、cognition、operations、platform/shared 等一致词汇。

当前仓库额外出现的 `cognition/personality` 由 AST-6 与 AST-22 共同确认。任一任务
checkout 失败都必须报告错误并阻塞，不允许以估算补矩阵。

## 阶段 2：架构评审

AST-6 检查 329 行后端矩阵、Dashboard 映射、公共/动态契约、目标路径、shared 准入、
阶段顺序、数据安全和回滚。结论为“修改后通过”时，修改项必须进入阶段 1 文档并由
AST-6 复核；不能把阻塞项推到领域实现阶段。

## 阶段 3：承载面与门禁

AST-7 创建最小 platform/shared 包和后端 AST/import 门禁，不批量迁移业务代码。
AST-8 创建前端 app/features/shared 骨架和跨 feature 门禁。双方补充真实 namespace、
bridge/type 和正反例测试。

## 阶段 4：自治领域

AST-9 按领域分别迁移 identity、cognition/affection/expression/jargon/social/personality、
review、evaluation。每个领域独立提交或 PR。AST-10 同步相同 Dashboard features。
共享入口仅提交接入清单。

## 阶段 5：业务支撑与运维

AST-11 迁移 conversation、profile、knowledge、notes、operations/backup/maintenance/
diagnostics/update。Backup 和 Conversation 使用
[SECURITY_AND_DATA_SAFETY.md](SECURITY_AND_DATA_SAFETY.md) 专项门禁。AST-12 同步
前端确认、失败、取消、进度和错误状态。

## 阶段 6：派生与召回链

AST-13 迁移 graph、retrieval、evolution、injection，每个领域独立 PR，不调整算法、
SQL 或阈值。AST-14 同步图、检索、演化和注入 Dashboard features。Graph/canonical
identity、route/ranking 和隐私测试是屏障证据。

## 阶段 7：中心 Memory 与聚合入口

AST-15 独占后端共享热点，迁移 canonical memory 并消费阶段 4 至 6 接入清单；
AST-16 独占 Dashboard 根路由、Provider、启动和 bridge。两项完成后，平台只装配，
不承载领域规则。

## 阶段 8：全量与清理

AST-17/AST-18 在所有领域 PR 合入后运行全量门禁，删除已无消费者的横向目录和
到期 shim。仍保留的兼容层必须有批准范围、版本和删除条件，不能用“可能有人用”
作为无限期理由。

## 阶段 9：独立终审

AST-19 检查后端 API/config/schema/ID/transaction/cancel/restore/privacy、依赖门禁、
动态引用和测试证据。AST-20 检查 Dashboard 架构、交互、可访问性、性能和测试。
Reviewer 只读；阻塞问题返回原领域负责人修复后重新终审。

## 共享文件独占

阶段 1 至 6 的领域执行者不得修改：

- `main.py`；
- `core/__init__.py`；
- initializer/plugin initializer 的平台聚合部分；
- `core/page_api.py`；
- `tests/conftest.py`；
- Dashboard 根路由、Provider、启动入口、bridge 注册和公共测试配置。

AST-15/AST-16 在阶段 7 串行拥有这些路径。前序领域 PR 提交接入清单，不抢写共享
文件。

## 阶段失败

任一屏障失败时：保持后续任务 `backlog`；停止新的领域 PR；revert 可疑领域提交；
运行相同验证确认恢复；在 AST-5 记录触发、影响、责任人和重新进入条件。CI 在外部
运行时只做一次状态快照，除非验收明确要求 CI 最终结果，不使用后台轮询延长任务。
