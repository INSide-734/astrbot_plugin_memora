[根目录](../../CLAUDE.md) > [core](../) > **validators**

## 模块职责

`core/validators/` 负责索引一致性验证、BM25/向量索引的批量安全重建、Embedding 重试逻辑以及持久化健康检查。7 个源文件 + `__init__.py`。

## 入口与启动

- **对外导出**: `IndexValidator`, `PersistenceHealthValidator`
- **调用方**: `DatabaseSetup.auto_rebuild_index_if_needed()`, `ComponentFactory.build_all()`, 手动 `/lmem rebuild-index` 命令

## 对外接口

### IndexValidator (`index_validator.py` + Mixin 链)

通过多重继承 (`IndexRebuilderMixin → Bm25RebuilderMixin + EmbeddingRetryMixin + VectorRebuilderMixin`) 组合完整的索引验证与重建能力。

| 方法 | 职责 |
|------|------|
| `check_consistency()` | 检查 documents 表与 BM25/向量索引的一致性 |
| `rebuild_indexes(memory_engine, progress_callback)` | 分批安全重建 BM25 + 向量索引 |
| `_get_document_count()` | 获取 documents 表文档总数 |
| `_get_document_ids()` | 获取所有文档 ID 集合 |
| `_iter_document_batches(batch_size, document_ids)` | 分批迭代文档（支持游标分页和 ID 分批） |
| `_get_vector_count()` | 获取 FAISS 索引向量总数 |
| `_get_vector_ids()` | 获取 FAISS IndexIDMap 中的所有 ID |

**一致性检查逻辑**：
1. 读取 `documents` 表的所有 ID 集合
2. 读取 `memora_memories_fts` 表的 BM25 索引 ID 集合
3. 读取 FAISS 向量索引的 ID 集合
4. 计算差异：`missing_in_bm25`, `missing_in_vector`
5. 判断是否需要重建：缺失 > 0 或 BM25 有冗余数据
6. 向量索引冗余（FAISS ntotal 含逻辑删除槽位）不触发重建

**IndexStatus**（返回的数据类）:
- `is_consistent`: bool
- `documents_count`, `bm25_count`, `vector_count`
- `missing_in_bm25`, `missing_in_vector`
- `needs_rebuild`: bool
- `reason`: str

### PersistenceHealthValidator (`persistence_health_validator.py`)

只读持久化健康检查器，检查 documents、Atom、Graph、Note、BM25、Vector 之间的不变量：

| 方法 | 职责 |
|------|------|
| `check()` | 主入口：返回 {ok, needs_repair, counts, issues} |
| `_check_bm25()` | 检查 BM25 FTS 孤儿 doc_id |
| `_check_atoms()` | 检查 Atom 孤儿 parent_memory_id |
| `_check_graph()` | 检查 Graph 孤儿 source_memory_id + 节点引用完整性 |
| `_check_notes()` | 检查 Note 版本孤儿 + 重复版本 |
| `_check_main_vectors()` | 检查主向量索引孤儿 ID |
| `_check_graph_vectors()` | 检查图向量索引孤儿 ID |

**检查项目**：
- BM25: FTS 表中引用了 documents 中不存在的 doc_id
- Atom: `parent_memory_id` 指向不存在的 document
- Graph entries: `source_memory_id` 指向不存在的 document
- Graph nodes: entry_nodes 引用不存在的 entry_id 或 node_id
- Notes: `note_versions` 引用不存在的 `note_id`，或同一版本重复
- Vectors: 主向量索引和图向量索引中引用了不存在的 document/entry

### Bm25RebuilderMixin (`bm25_rebuilder.py`)

| 方法 | 职责 |
|------|------|
| `_rebuild_bm25_index(memory_engine, total, options, progress_callback)` | 从 documents 表重建 FTS 索引 |

**重建流程**：
1. 清空 FTS 表（带重试）
2. 分批读取 documents
3. 每批调用 `TextProcessor.preprocess_for_bm25()` 分词
4. 批量 INSERT 到 FTS 表
5. 批量失败时回退为逐条写入
6. 失败率超过 `max_failure_ratio` 时停止

### EmbeddingRetryMixin (`embedding_retry.py`)

| 方法 | 职责 |
|------|------|
| `_embed_batch_with_retry(provider, contents, options)` | 分批调用 Embedding API，支持指数退避 |
| `_embed_request_with_retry(provider, contents, max_retries, retry_base_delay)` | 单次请求的重试逻辑 |

**重试策略**：
- 指数退避: `retry_base_delay * 2^attempt`
- 速率限制检测: 429/rate limit/tpm limit → 最小延迟 30s
- 兼容三种 Embedding API: `get_embeddings(list)`, `get_embeddings_batch()`, 逐条 `get_embedding()`

### VectorRebuilderMixin (`vector_rebuilder.py`)

| 方法 | 职责 |
|------|------|
| `_rebuild_or_repair_vector_index(memory_engine, total, options, progress_callback)` | 智能选择：增量补写或全量重建 |
| `_repair_missing_vectors(memory_engine, missing_ids, options, progress_callback)` | 增量补写缺失向量 |
| `_rebuild_vector_index_full(memory_engine, total, options, progress_callback)` | 全量重建（临时 FAISS 索引 → 原子替换） |

**智能策略决策**：
1. 能读取向量 ID → 计算缺失集合 → 增量补写
2. 不能读取向量 ID 但计数 >= documents 数 → 跳过
3. 否则 → 全量安全重建（临时索引 → 原子替换）

**安全措施**：
- 全量重建时先构建临时 FAISS 索引，成功后才原子替换原文件
- 失败率超过阈值时不切换新索引，保留原数据
- `_try_restore_from_backup()`: 重建失败且 documents 为空时从备份表恢复

## 关键依赖与配置

- **外部库**: `faiss`, `numpy`, `aiosqlite`
- **内部依赖**: `core.storage.base.apply_perf_pragmas`, `core.managers.memory_engine.MemoryEngine`, `astrbot.api.logger`

### 重建配置项

| 配置键 | 默认值 | 约束 | 含义 |
|--------|--------|------|------|
| `index_rebuild_batch_size` | 50 | 1-500 | 每批文档数 |
| `index_rebuild_embedding_batch_size` | 8 | 1-256 | Embedding API 批大小 |
| `index_rebuild_tasks_limit` | 1 | 1-8 | 并发任务数 |
| `index_rebuild_max_retries` | 5 | 1-8 | 最大重试次数 |
| `index_rebuild_retry_base_delay` | 30.0 | 0-60s | 重试基础延迟 |
| `index_rebuild_batch_delay` | 5.0 | 0-10s | 批次间延迟 |
| `index_rebuild_request_delay` | 5.0 | 0-60s | Embedding 请求间延迟 |
| `index_rebuild_max_failure_ratio` | 0.02 | 0-1 | 最大失败率阈值 |

## 数据模型

| 数据类 | 文件 | 字段 |
|--------|------|------|
| `IndexStatus` | `index_validator.py` | is_consistent, documents_count, bm25_count, vector_count, missing_in_bm25, missing_in_vector, needs_rebuild, reason |

## 测试与质量

- 对应测试文件: `tests/test_validator*.py`
- FTS 表名白名单: 只允许 `memora_memories_fts`
- SQL 注入防护: FTS 表名通过 `_validate_fts_table_name()` 白名单校验
- 数据库锁重试: `database is locked` 时自动重试（线性递增 0.2s/次）
- 索引文件隔离: 不可读索引文件移到 `*.corrupt_{timestamp}` 备用

## 常见问题 (FAQ)

**Q: 索引什么时候需要重建？**
A: 系统启动时 `DatabaseSetup.auto_rebuild_index_if_needed()` 会自动检查。也可手动执行 `/lmem rebuild-index`。

**Q: 重建会丢失数据吗？**
A: 不会。`documents` 表始终作为唯一数据源，只读不删。全量重建使用临时索引 + 原子替换策略。

**Q: 重建失败怎么办？**
A: `_try_restore_from_backup()` 会从 `_documents_rebuild_backup` 表恢复 documents 数据。BM25/向量索引需要手动重建。

## 相关文件清单

- `__init__.py` -- 公共导出
- `index_validator.py` -- 索引一致性验证器（375 行）
- `index_rebuilder.py` -- 索引重建编排器（Mixin 组合，196 行）
- `bm25_rebuilder.py` -- BM25 全文索引重建（100 行）
- `vector_rebuilder.py` -- 向量索引重建/修复（255 行）
- `embedding_retry.py` -- Embedding 重试逻辑（92 行）
- `persistence_health_validator.py` -- 持久化健康检查器（268 行）

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取 7 个源文件，生成模块级 CLAUDE.md |
