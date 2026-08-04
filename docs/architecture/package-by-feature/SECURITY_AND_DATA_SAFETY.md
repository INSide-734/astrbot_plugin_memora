# Security and Data Safety

> 状态：结构迁移不可破坏的不变量

## 总原则

Package-by-Feature 迁移只移动代码，不迁移数据。所有阶段保持最小权限、输入校验、
SQL 参数绑定、取消传播、有限资源、敏感信息隔离和可回滚提交。不得以“目录重构”
为理由改变事务、异常、重试、超时、日志、schema 或数据目录。

## 数据权威

- SQLite canonical memory 记录及整数 ID 是长期记忆唯一权威。
- FTS、FAISS、Graph、relation/projection 是派生面，必须可以从 canonical/authoritative
  Store 重建。
- Projection 只能作为有 source/revision 证据的读时注解，不能拥有独立 canonical
  `doc_id`、正文或排序身份。
- identity、conversation、profile、knowledge、notes、review、evaluation、diagnostics、
  injection decision 等表继续由原 schema 和 migration 管理；代码归属变化不移动表。
- 多步写使用现有 write coordinator 或等价 store-local transaction，结构迁移不扩大
  transaction scope，也不把跨域 partial write 隐藏为成功。

## 敏感数据

以下内容不得写入文档示例、日志、指标、trace、PR 证据或测试产物：真实 query、
prompt、记忆正文、用户/会话/群/persona/canonical ID、Provider key/header/endpoint、
source mapping/revision、内部 job ID 和原始异常堆栈。

Injection/recall/evolution/identity 的模型可见 metadata 继续使用现有 allowlist。动态
memory 不进入 System Prompt；身份只暴露当前名称、必要历史名称和稳定标签；内部
查询候选、歧义过程和目录 ID 不进入模型上下文。

## 异步与故障隔离

- `asyncio.CancelledError` 必须传播。
- 所有 background task 有所有者、可观察、有限队列并在关闭时收束。
- Provider、可选 cognition、观测和派生面失败按现有路径降级，不阻断已定义的聊天
  主链；不能把 canonical 写失败伪装成降级成功。
- 初始化失败按逆依赖顺序清理已创建资源；关闭先停止生产者，再关闭 Store/DB。
- 外部调用保持明确 timeout 和当前有限 retry；结构 PR 不扩大 timeout 或增加无限 retry。

## Graph 高风险边界

Graph 迁移涉及 `GraphStore`、CRUD/query/delete/subgraph/canvas、图抽取、Graph manager、
Graph retriever 和实体 hierarchy。

必须保持：

- SQLite transaction 和 atomic replace 语义；
- graph entry/node/edge 与 `source_memory_id` 的映射；
- 删除 canonical source 后的派生清理；
- direct/matched node 距离 0、最多 0..2 hop、层级路径未知距离等现有语义；
- graph vector/keyword 结果回到 canonical memory ID，不产生第二 ID 空间；
- rebuild 失败只降级派生面，不删除 canonical 数据。

验证至少覆盖 graph CRUD/delete/query/subgraph/atomic replace/canvas、pipeline graph 和
Graph/Memory source identity。失败时 revert Graph PR，无数据库 migration rollback。

## Memory 高风险边界

Memory 是阶段 7 最后迁移的中心领域，包含 MemoryEngine、atom、CRUD/batch/lifecycle、
write coordinator/journal/repair/serialization、processor 和 reflection 写入。

必须保持：

- canonical 整数 ID、soft delete、revision/CAS、origin/source provenance；
- SQLite commit 后才更新/重建 FTS、FAISS、Graph 和 Evolution；
- write journal、repair 与序列化可以恢复原行为，不吞 partial failure；
- `MemoryEngine` 三层候选导入按 AST-6 决策兼容；
- recall 不通过 infrastructure 私有路径绕过 scope/privacy/role/filter；
- Memory Evolution 调度失败不回滚已提交 canonical 写，但取消继续传播。

阶段 7 PR 若改变 schema、ID 或数据格式，必须拒绝并拆分，不得用备份恢复作为正常
结构迁移手段。

## Backup 高风险边界

Backup 迁移涉及版本、catalog、snapshot、restore plan、pending restore、热重载、
write guard 和 Page API。

必须保持：

- 所有路径限制在批准的数据/备份目录，拒绝 traversal 和符号链接逃逸；
- manifest/SHA-256、SQLite quick check、版本状态和脱敏摘要；
- `staged -> applying -> succeeded/failed/rolled_back/cancelled` 的现有状态与 journal；
- 恢复在 runtime 发布前应用，完整初始化成功后才标记 succeeded；
- 失败时 rollback/cancel 和原始错误优先级；
- pending restore 的 write guard 覆盖 Page、event、command、Agent 写入；
- 热重载不可用时保持可手动重启状态，不伪造成功。

结构 PR 不读写生产数据库或执行真实恢复。测试只使用隔离临时目录和测试 SQLite。

## Conversation 高风险边界

Conversation 迁移横跨 models、manager mixin、message/session operations、Store/queries、
identity sync、dedup、extractor、formatter 和 topic segmentation。

必须保持：

- `Message`、`Session`、`MemoryEvent` JSON 字段与反序列化兼容；
- 消息顺序、session range/metadata、reset/new 和 close 语义；
- identity protocol/namespace/stable ID 的 scope，名称只作辅助数据；
- 群消息自消息过滤、内容提取、会话去重和有所有者的清理任务；
- conversation 写与 canonical memory 写的边界，不把两者合并为隐式跨域 transaction；
- legacy alias 只读增强拒绝歧义且不修改 canonical candidate。

验证覆盖 Store/queries/manager/session/sender/range、identity pipeline、event pipeline 和
并发写。失败时停止阶段 5，不进入 Graph/Memory 后续阶段。

## 其他关键领域

| 领域 | 不变量 |
|---|---|
| Identity | OneBot11 canonical QQ；QQ Official 按平台实例隔离 OpenID；`union_openid` 不替换主键 |
| Evolution | enabled/mode、job idempotency、source revision、validity/scope/privacy/role 校验 |
| Injection | budget、route、atomic request mutation、prompt protection、telemetry allowlist |
| Profile/Knowledge/Notes | provenance、revision、stale pagination、manual boundary 和 SQL binding |
| Diagnostics/Monitoring | bounded labels/queue；不记录 payload、ID 列表、凭据或 raw exception |
| Update | 下载/安装边界、版本校验、文件操作和失败清理不变 |

## SQL 与文件安全

- SQL 值继续参数绑定；动态 identifier 只能来自固定 allowlist。
- 不把 table 名或字段名从 Page/query 参数直接拼接。
- 文件操作先 resolve 并验证 containment；临时文件在成功/失败/取消路径清理。
- 写替换保持原 atomic/backup 语义；不在结构迁移中改变 fsync/rename 顺序。
- 不在仓库、日志或 metadata 写入 token、key、真实数据库路径或用户数据。

## 发布与回滚

每个结构 PR 可独立 revert，不需要数据 migration rollback。出现一致性失败时先停止
阶段推进并回滚代码；不要自动“修复”或删除 canonical 数据。只有用户明确授权且有
备份/目标环境/回滚计划时，后续独立任务才可执行数据库或生产操作。

## 安全验收阻塞项

以下任一项阻塞合并：schema/ID/SQL 变化混入结构 PR；取消被吞；无限任务/队列；
Graph/Memory 权威倒置；Backup path/hash/rollback 弱化；Conversation/Identity scope
变化；敏感数据进入日志/trace/prompt；为通过测试禁用安全校验或放宽 allowlist。
