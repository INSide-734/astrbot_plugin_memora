[根目录](../../CLAUDE.md) > [core](../) > **initializer**

## 模块职责

`core/initializer/` 是 Memora 插件的初始化编排模块，负责在插件启动时按顺序组装所有子系统：Provider 加载与等待、FAISS 运行时兼容性检查、数据库初始化、旧文件迁移、组件构造工厂。5 个文件 + `__init__.py`。

## 入口与启动

- **对外接口**: `core/initializer/__init__.py` 导出 5 个核心类：`ComponentFactory`, `DatabaseSetup`, `FaissChecker`, `ProviderLoader`, `ProviderWaiter`
- **调用方**: `core/plugin_initializer.py` 在 `initialize()` 方法中按顺序调用本模块各组件

### 初始化流程

```
FaissChecker.check_runtime()  → 验证 FAISS 运行时可用
FaissChecker.load_vec_db_class()  → 动态加载 FaissVecDB
ProviderWaiter.wait_non_blocking()  → 非阻塞等待 Provider 就绪（最多 5 秒）
  └─ ProviderLoader.initialize_providers()  → 加载 Embedding + LLM Provider
ProviderWaiter.start_retry_if_needed()  → 未就绪时后台指数退避重试（最多 60 次）
ComponentFactory.build_all()  → 组装所有核心组件
  ├─ 旧文件 v1→v2 迁移（livingmemory → memora）
  ├─ FaissChecker.check_and_fix_dimension_mismatch()  → 维度检查
  ├─ FaissVecDB.initialize()  → 主 DB + 图 DB 并行初始化
  ├─ MemoryEngine  → 搜索引擎构建（含 BM25 / 向量 / 图）
  ├─ ConversationManager  → 会话管理器
  ├─ MemoryProcessor  → 记忆处理器
  ├─ DatabaseSetup.auto_rebuild_index_if_needed()  → 索引一致性检查 + 自动重建
  └─ DecayScheduler  → 衰减调度器启动
```

## 对外接口

| 类 | 方法 | 职责 |
|----|------|------|
| `ComponentFactory` | `build_all()` | 创建并初始化所有核心组件，返回组件字典 |
| `DatabaseSetup` | `auto_rebuild_index_if_needed()` | 检查索引一致性，不一致时自动触发重建 |
| `DatabaseSetup` | `repair_message_counts()` | 修复会话 message_count 偏差 |
| `FaissChecker` | `check_runtime()` | FAISS 运行时兼容性检查（子进程探测） |
| `FaissChecker` | `load_vec_db_class()` | 动态加载 AstrBot 的 FaissVecDB 类 |
| `FaissChecker` | `check_and_fix_dimension_mismatch()` | 检测索引维度不匹配，自动删除/隔离旧文件 |
| `ProviderLoader` | `initialize_providers()` | 从配置或默认 Provider 列表中加载 Embedding + LLM |
| `ProviderLoader` | `get_provider_by_id()` | 按 ID 获取 Provider 实例 |
| `ProviderWaiter` | `wait_non_blocking()` | 非阻塞等待 Provider 就绪（最多 5 秒） |
| `ProviderWaiter` | `start_retry_if_needed()` | 启动后台指数退避重试（2s 起，最大 30s，系数 1.5x） |

## 关键依赖与配置

- **AstrBot 框架**: `astrbot.api.Star`, `astrbot.core.provider.Provider`, `astrbot.core.provider.EmbeddingProvider`, `astrbot.core.db.vec_db.faiss_impl.vec_db.FaissVecDB`
- **内部依赖**: `core.base.exceptions`（`ProviderNotReadyError`, `InitializationError`）, `core.managers`, `core.processors`, `core.schedulers`, `core.storage`, `core.validators`

## 数据模型

无独立数据模型。`ComponentFactory._build_engine_config()` 从 `ConfigManager` 读取 30+ 个配置项组装引擎配置字典。

## 测试与质量

- 初始化流程通过 `tests/` 目录下的集成测试覆盖
- `database is locked` 重试逻辑确保并发安全
- 索引维度不匹配时自动删除旧索引文件（避免静默错误）
- 不可读索引文件自动隔离到 `*.corrupt_{timestamp}` 备用文件

## 常见问题 (FAQ)

**Q: FAISS 初始化失败（Illegal instruction）？**
A: CPU 不支持 AVX2 指令集。安装 `faiss-cpu` 兼容版本或更换运行环境。

**Q: Provider 一直未就绪？**
A: 检查 AstrBot 是否已正确配置 LLM 和 Embedding Provider。插件会后台重试最多 60 次。

**Q: 索引维度不匹配？**
A: 通常因为切换了 Embedding 模型。旧索引文件会被自动删除，系统会在初始化后自动重建索引。

## 相关文件清单

- `component_factory.py` -- 组件构造工厂（262 行）
- `db_setup.py` -- 数据库迁移 + 索引修复（49 行）
- `faiss_checker.py` -- FAISS 运行时检查 + 维度修复（121 行）
- `provider_loader.py` -- Provider 加载器（94 行）
- `provider_waiter.py` -- 非阻塞等待 + 指数退避重试（133 行）
- `__init__.py` -- 公共导出

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取 5 个源文件，生成模块级 CLAUDE.md |
