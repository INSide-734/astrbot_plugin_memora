# Current Backend Baseline

> 状态：已从事实提交采集
>
> 采集日期：2026-08-04
>
> 提交：`f1ad662cd56766f090b6699ea81ee164783b78b6`

## 扫描边界

生产后端定义为 `main.py` 和 `core/**/*.py`。测试定义为 `tests/**/*.py`，脚本定义
为 `scripts/**/*.py`。扫描跳过 `node_modules`、构建产物、缓存、虚拟环境、运行时
数据库、Dashboard 生成物和 Multica 工作区文件。资源目录 `core/prompts`、
`core/i18n` 被记录为运行时资源，但不伪装成 Python 包。

## 总量

| 范围 | 文件 | 物理行 | 说明 |
|---|---:|---:|---|
| 生产 Python | 329 | 81,033 | `main.py` + `core/**/*.py` |
| Python 测试 | 287 | 88,521 | 单元、契约、integration、evaluation、stress |
| Python 脚本 | 9 | 2,347 | 门禁、smoke、打包、发布和 benchmark |
| 架构/开发 Markdown | 27 | 未汇总 | `docs` 与 `website/docs` 当前文件 |

## 当前技术目录规模

| 当前目录 | Python 文件 | 物理行 | 目标处理 |
|---|---:|---:|---|
| `core/` 根文件 | 11 | 4,452 | platform/shared/operations 拆分，聚合入口阶段 7 独占 |
| `api` | 31 | 11,307 | 按领域进入 `interfaces/page` |
| `managers` | 52 | 11,141 | 按领域进入 `application`，横向门面阶段 8 删除 |
| `storage` | 30 | 8,299 | 按数据所有者进入 `infrastructure/persistence/sqlite` |
| `retrieval` | 35 | 7,194 | retrieval 为主，graph/knowledge/evolution 归还所有者 |
| `processors` | 23 | 4,413 | 按 ingestion、graph、knowledge、profile 等归属 |
| `models` | 14 | 2,431 | 进入各领域 `domain`；跨域 provenance/temporal 进入 shared kernel |
| `utils` | 14 | 2,973 | 逐项归还领域；少量 cache/runtime/text/kernel 进入具名 shared 包 |
| `monitoring` | 7 | 2,133 | 通用观测进入 shared；业务质量/召回时序归领域 |
| `handlers` | 7 | 3,102 | recall、reflection、conversation 编排归领域 |
| `evaluation` | 9 | 3,324 | 保持独立 evaluation 领域并深化分层 |
| `jargon` | 7 | 2,149 | cognition/jargon |
| `base` | 11 | 1,712 | shared config/kernel 与领域策略拆分 |
| `injection` | 8 | 1,775 | 保持 injection 领域并深化分层 |
| `affection` | 4 | 1,571 | cognition/affection |
| `identity` | 9 | 1,507 | 保持 identity 领域并分离协议适配器 |
| `tools` | 10 | 1,642 | 进入各领域 `interfaces/tools` |
| `validators` | 7 | 1,292 | retrieval reliability 或 operations/diagnostics |
| `social` | 4 | 1,231 | cognition/social |
| `initializer` | 7 | 1,203 | platform/bootstrap、shared setup、领域 reliability |
| 其余 10 个目录 | 34 | 6,782 | 按矩阵逐文件迁移 |

## 超过 800 行的生产热点

| 排名 | 文件 | 行数 | 主要风险 |
|---:|---|---:|---|
| 1 | `core/page_api.py` | 1,434 | 跨域 Page API 聚合与响应契约 |
| 2 | `core/handlers/recall_handler.py` | 1,256 | 检索、注入、身份和取消传播 |
| 3 | `core/handlers/reflection_handler.py` | 1,231 | canonical 写入、反思和 evolution 调度 |
| 4 | `main.py` | 1,007 | AstrBot hooks、命令、工具和插件生命周期 |
| 5 | `core/managers/retrieval_optimizer.py` | 967 | 多路召回、排序和降级 |
| 6 | `core/api/jargon_api.py` | 958 | Page API、并发状态与存储 |
| 7 | `core/api/profile_api.py` | 939 | 画像写入、revision 和 provenance |
| 8 | `core/retrieval/dual_route_retriever.py` | 917 | direct/graph 合并与派生元数据 |
| 9 | `core/managers/backup_manager.py` | 889 | 恢复事务、路径、hash、rollback 和 cancel |
| 10 | `core/affection/affection_manager.py` | 812 | 情绪状态、并发写和关闭顺序 |

文件行数是迁移风险信号，不是领域切分依据。`memory_engine_crud.py` 当前 790 行，
仍应按 command/query 职责评审，但不再被错误记录为超过硬上限。

## 运行时入口与装配

```text
AstrBot
  -> main.py / MemoraPlugin
     -> PluginInitializer
        -> ProviderLoader / ProviderWaiter
        -> ComponentFactory.build_all
        -> stores, MemoryEngine, ConversationManager, identity runtime,
           schedulers, validators, injection recorder, evolution worker
     -> EventHandler -> RecallHandler / ReflectionHandler
     -> CommandHandler -> CommandEndpointsMixin / commands
     -> PluginPageApi -> api mixins
```

`main.py`、`core/__init__.py`、initializer、顶层 Page API 和全局测试 fixture 在阶段
1 至 6 是共享热点，只允许阶段 7 的 AST-15 串行修改。领域任务必须提交接入清单，
不能提前修改这些文件。

## 数据权威与派生面

- SQLite canonical memory 和整数 ID 是长期记忆唯一权威。
- FTS、FAISS、Graph、relation/projection 是可重建派生面；它们不能成为第二份
  canonical 数据，也不能改变 canonical `doc_id`。
- Memory Evolution 在 canonical 写入提交并重读 source 后调度；派生失败不能回滚
  已成功的 canonical 写入。
- Conversation、identity、profile、knowledge、notes 和 injection decisions 各有
  SQLite 数据；垂直迁移只能移动代码，不能移动表或改变 schema。
- Backup 在运行时发布前应用 pending restore，恢复成功状态必须等待组件重建完成。

## 静态依赖证据

Python AST 对 329 个生产模块解析成功，无语法解析错误。生产模块间得到 823 条
显式 import 边。高扇入模块包括：

| 模块 | 已知生产/测试导入方总数 | 迁移含义 |
|---|---:|---|
| `core.retrieval.rrf_fusion` | 45 | 需要 retrieval 门面或 contracts，不能散落导入私有实现 |
| `core.base.list_sorting` | 32 | 只有确认无业务语义后才进入 shared kernel |
| `core.api.response_utils` | 30 | 应归 platform/page，领域 API 不复制 response helper |
| `core.injection.models` | 30 | injection 稳定 DTO 候选 |
| `core.models.memory_evolution` | 28 | evolution 稳定 contracts 候选 |
| `core.storage.base` | 28 | shared SQLite primitive 候选，不导出业务 Store |
| `core.base.entity_editing` | 25 | 跨域编辑 helper，需以最小协议进入 shared |
| `core.models.memory_atom` | 22 | memory domain 的核心公共类型 |
| `core.models.conversation_models` | 21 | conversation domain 类型及 JSON 兼容 |
| `core.models.domain_provenance` | 20 | 多领域共用 provenance，不得复制 |
| `core.adapter_capabilities` | 19 | platform provider contract |
| `core.managers.memory_engine` | 19 | 三层导入兼容的明确候选 |

完整逐文件 importer 计数、示例和动态引用见
[MODULE_MIGRATION_MATRIX.md](MODULE_MIGRATION_MATRIX.md)。目标域依赖图与复现方法见
[DEPENDENCY_GRAPH.md](DEPENDENCY_GRAPH.md)。

## 动态、patch 与模块身份证据

- 54 个生产模块被测试中的字符串 `patch`、`monkeypatch.setattr` 或
  `importlib.import_module` 指向。路径迁移必须在同一提交更新这些字符串。
- `tests/test_plugin_package_imports.py` 通过
  `data.plugins.astrbot_plugin_memora` 真实命名空间动态导入 3 个模块：
  `core.api.recall_trace_api`、`core.injection.recorder`、
  `core.monitoring.perf_tracker`。
- `tests/test_plugin_init.py` 使用 `spec_from_file_location` 加载插件，并依据
  `MemoraPlugin.__module__` 查找模块对象。
- `core/command_endpoints.py` 在装饰器注册前把命令 handler 的 `__module__` 改为
  插件入口，同时在 import 时清理热重载遗留 handler。它不是普通纯模块。
- `core/monitoring/instrumentation.py`、`core/utils/cache_manager.py` 和
  `core/utils/task_scheduler.py` 使用 `func.__module__` 构造指标、缓存或任务标识。
- 生产代码和测试均未发现 `pickle`、`dill` 或 `cloudpickle` import；当前没有已证实
  的 pickle 模块路径持久化契约，但后续新增不能据此跳过模块身份测试。
- `core/version_check.py` 使用 `importlib.metadata` 和 `__import__` 探测 AstrBot 版本；
  目标路径迁移必须保持 import-time 兼容提示和异常降级。

## 测试与脚本现状

- pytest 入口为 `pytest.ini`，测试根为 `tests`。
- `tests/conftest.py` 必须先把 AstrBot 最小 mock 安装进 `sys.modules`，之后才能
  导入依赖 `astrbot.*` 的生产模块。
- `scripts/run_smoke.py` 固定执行 ingest、event、retrieval、graph、lifecycle 五条
  integration 管线。
- `scripts/check_all.py` 当前依次执行全量 pytest、五条 smoke、Dashboard build、
  artifact check、frontend tests、runtime smoke 和 browser smoke；当前仓库没有
  `scripts/validate_conf_schema.py`，因此 schema 步骤被条件跳过。
- Ruff 0.16.0 由 `uv.lock`、`pyproject.toml` 和 pre-commit 一致锁定。

实际命令结果记录在 [TEST_AND_ACCEPTANCE.md](TEST_AND_ACCEPTANCE.md)，不能从本节
的入口说明推断门禁已经通过。
