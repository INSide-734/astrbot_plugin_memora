# 记忆生命周期

Memora 把长期记忆视为有来源、有作用域、可更新并可重建派生视图的持久数据，而不是一段无约束文本。

## 形成

- 捕获 AstrBot 消息并提取规范化文本。
- 对消息去重，按话题组织上下文。
- 结构化抽取产出候选事实；只有通过质量门并带用户来源证据的候选才写入长期记忆。
- 使用重要度、有效期和参与者来源证据决定长期保留内容。

## 持久化

SQLite 中的 canonical memory（`documents` 行 + 整数 ID）是唯一权威记录，也是唯一权威事实正文。正文更新采用两阶段替换：新内容先落为不可召回的暂存记录，再在单个事务内切换可见性并删除旧记录，因此任一时刻最多只有一条可召回记录。

成功提交后，系统从这条记录派生全文与向量索引、图条目、记忆原子信号、主题目录以及画像、知识、笔记等派生对象，并调度可选的记忆演化任务。派生阶段失败不得回滚或删除已经成功提交的 canonical 记录；系统应报告降级原因，并在后续维护或统一重建中修复。

记忆原子（Atom）只是排序、前瞻提醒与图事实抽取所用的派生信号：它的状态与过期只影响该信号是否继续参与候选，不改变 canonical 事实的存在、可见性与召回；模型可见的正文始终取自当前 canonical 事实原文。事实元数据（`key_facts` 及逐事实来源证据）必须与正文保持一致，正文改写后不再一致的元数据会被清除，或按 `fact_evidence_mismatch` 拒绝写入。

## 使用与更新

召回会根据访问上下文校验 scope、privacy、validity、role 和 revision。读取路径同样补齐了 canonical 状态门：缓存命中、记忆列表、召回测试与聚焦图查询都会按当前 canonical 重新校验来源状态与证据，失效候选被剔除并计数，而不是回显旧正文或旧派生行。

语义 metadata 更新仍通过 canonical 提交边界，并在提交后重新加载 source 再调度派生处理。

## 衰减、归档与遗忘

- 普通记忆可以根据重要度、访问状态和配置参与衰减。
- 高情绪强度（`emotional_intensity` 达到 `flashbulb.intensity_threshold`）的闪光灯记忆具有额外保护。
- 归档和清理通过显式生命周期服务执行；状态批量变更提交后会统一失效关系/投影、回收图残留并重派生原子信号，失败只降级计数。
- `/memora forget <doc_id>` 删除指定 canonical 记忆，并清理图记忆、MemoryAtom 与向量/文档派生条目。该命令要求 AstrBot 管理员权限，维护写保护期间会被拒绝，操作不可逆；`doc_id` 必须是存在且可访问的非负整数。canonical 与向量/文档删除成功即回复成功；图记忆或 Atom 清理失败只独立记为 `needs_repair` 待修复，因此成功回复不代表所有派生数据都已清理。详见[管理命令](/reference/commands)。

## 派生重建

```mermaid
flowchart LR
    Canonical["确认 canonical 数据"] --> Indexes["重建 FTS5 / FAISS"]
    Indexes --> Catalog["重建主题目录"]
    Catalog --> Atoms["重派生原子信号"]
    Atoms --> Graph["重建图索引"]
    Graph --> Evolution["重建 Relation / Projection"]
    Evolution --> Compression["重建语义压缩摘要"]
    Compression --> Notes["重建自动笔记"]
```

统一重建按上述固定顺序执行，canonical 可读性先校验。任一阶段失败只报告对应层降级并保留其余阶段结果，不删除 canonical 数据；原子阶段的部分失败记为 `atoms_rebuild_partial_failed`，并回收父记录已不存在的残留信号。管理员可以使用 `/memora rebuild-index` 和 `/memora rebuild-graph` 执行维护，它们与控制台维护入口都经过同一阶段调度，只请求各自对应的阶段。

## 相关页面

- [检索与注入](/concepts/retrieval-injection)
- [备份与恢复](/operations/backup-recovery)
- [管理命令](/reference/commands)
