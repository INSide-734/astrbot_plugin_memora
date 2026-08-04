# Package-by-Feature Test and Acceptance Gates

> 状态：阶段 1 验证基线，待 AST-6 架构评审
>
> 验证日期：2026-08-04
>
> 事实提交：`f1ad662cd56766f090b6699ea81ee164783b78b6`

本文定义 Package-by-Feature 迁移的可执行验收入口。当前结果证明阶段 1 文档基线
对应的仓库在迁移前通过现有门禁，不证明尚未实施的目标包结构已经可用。后续每个
迁移 PR 都必须在自己的 HEAD 上重新取证，不能继承本页的一次性通过结果。

## 基线环境

| 项目 | 实际值 |
|---|---|
| 平台 | Windows NT `10.0.26200.0` |
| Python | CPython `3.12.10`，由 uv 锁定环境运行 |
| uv | `0.11.32` |
| Node.js | `v22.22.0` |
| npm | `10.9.4` |
| 源码基线 | `f1ad662cd56766f090b6699ea81ee164783b78b6` |
| 变更范围 | 仅本架构文档集；生产代码、schema、配置和 Dashboard 源码未迁移 |

依赖准备使用 `uv sync --locked --dev` 和 Dashboard 目录下的 `npm ci`。前者安装
164 个包，后者安装 580 个包。`npm ci` 同时报告 1 个 moderate 和 2 个 high 审计项；
阶段 1 不修改依赖或锁文件，也不使用可能产生无关版本变化的 `npm audit fix`。

## 2026-08-04 基线结果

| 命令 | 实际结果 |
|---|---|
| `uv run --locked ruff check main.py core tests scripts` | 通过，`All checks passed!` |
| `uv run --locked python -m pytest tests/test_plugin_package_imports.py -q --basetemp .tmp-ast21-namespace` | `1 passed`；另有 3 条第三方 `jieba` `SyntaxWarning` |
| `uv run --locked python -m pytest tests -q --basetemp .tmp-ast21-pytest` | `5746 passed in 168.93s` |
| `uv run --locked python scripts/check_all.py` | 通过，耗时 `393.35s` |

统一门禁的内部结果为：后端 5,746 个测试通过，5 组 integration smoke 通过，
Dashboard production build 与 artifact check 通过，79 个前端测试文件中的 919 个
测试通过，runtime smoke 与 Chrome browser smoke 通过。浏览器 smoke 生成的 50 张
截图已通过 4 张 contact sheet 人工检查；桌面、移动、宽屏、暗色和三语言状态均未见
空白画布、缺失资源、非预期遮挡、裁切或文本重叠。浏览器在成功后输出的两条
`Not implemented: navigation to another Document` 为测试 DOM 的非致命提示。

## 每个迁移 PR 的共同门禁

1. 只移动 [迁移矩阵](MODULE_MIGRATION_MATRIX.md) 分配给当前任务的行；源路径和
   目标路径必须一一对应，不能顺带改变算法、SQL、schema、配置或返回契约。
2. 先运行本页对应 `v-*` 的聚焦测试，再运行 Ruff、命名空间/兼容测试和 `v-full`。
   测试文件也随领域移动时，命令应使用新路径，但覆盖的契约不能减少。
3. 包外导入只通过批准的领域门面或 `contracts`。AST-7 建立静态依赖门禁后，所有
   阶段都必须运行它；在此之前，Reviewer 逐项核对 diff 中的新跨包 import。
4. 对 `54` 个生产模块的动态 import/patch 字符串重新扫描。测试 patch 字符串随实现
   更新；只有 AST-6 批准的外部导入契约可以保留纯 re-export 兼容层。
5. Graph、Memory、Backup、Conversation 必须额外核对数据权威、事务、身份、取消、
   顺序和资源关闭不变量，详见 [数据安全](SECURITY_AND_DATA_SAFETY.md)。
6. 统一门禁必须从仓库根目录执行且返回 0；随后人工检查 browser smoke 截图、
   `git diff --check`、相对 Markdown 链接和最终 diff。任何失败都阻止合并。
7. 回滚单位是当前领域的结构迁移提交或 PR。结构迁移不得要求数据库回滚；若出现
   数据或行为变化，说明范围已越界，应先回退并拆成独立设计和迁移任务。

以下命令是领域的最低聚焦集合，不取代矩阵每行列出的专用验证和统一门禁。

## v-platform

覆盖 21 行平台入口、初始化、命令注册与 API 装配；必须保持 AstrBot 模块身份和
`core/command_endpoints.py` 对 handler `__module__` 的绑定语义。

```powershell
uv run --locked python -m pytest tests/test_plugin_init.py tests/test_plugin_package_imports.py tests/test_command_endpoints.py tests/test_command_handler.py tests/test_commands.py -q
```

## v-shared

覆盖 35 行无业务语义的共享能力。除聚焦测试外，Reviewer 必须证明能力至少被两个
领域稳定复用，且目标名称不是新的 `utils`、`base` 或 `common` 杂项目录。

```powershell
uv run --locked python -m pytest tests/test_storage_base.py tests/test_storage_builder.py tests/test_config_persistence_concurrency.py tests/test_cache_manager.py -q
```

## v-affection

覆盖 7 行情感状态与 API，保持衰减、更新和序列化语义。

```powershell
uv run --locked python -m pytest tests/test_affection_manager.py tests/test_api_affection.py -q
```

## v-expression

覆盖 6 行表达模式学习、存储与 API。

```powershell
uv run --locked python -m pytest tests/test_expression_pattern_learner.py tests/test_api_expression.py -q
```

## v-jargon

覆盖 9 行术语挖掘、调度、过滤、存储与管理 API。

```powershell
uv run --locked python -m pytest tests -q -k "jargon"
```

## v-cognition

覆盖 2 行 trait evolution 与 style analysis。AST-6 必须先确认
`cognition/personality` 与 AST-22 Dashboard 词汇一致。

```powershell
uv run --locked python -m pytest tests/test_managers_trait.py tests/test_style_analyzer.py -q
```

## v-social

覆盖 7 行社会关系领域、持久化与 API。

```powershell
uv run --locked python -m pytest tests/test_social_relation.py tests/test_api_social.py -q
```

## v-conversation

覆盖 20 行会话、session 与 handler 协作。重点验证 identity scope、消息顺序、取消
传播、反思触发、部分初始化清理和幂等 shutdown。

```powershell
uv run --locked python -m pytest tests -q -k "conversation or session or recall_handler or reflection_handler"
```

## v-evaluation

覆盖 10 行质量评估、指标与 API，指标标签必须保持有界且不包含敏感内容。

```powershell
uv run --locked python -m pytest tests/test_api_evaluation.py tests/test_quality_monitor_wiring.py -q
```

## v-evolution

覆盖 10 行记忆演化、gate、hook、模型、store 与 consolidation。

```powershell
uv run --locked python -m pytest tests -q -k "memory_evolution or memory_consolidator"
```

## v-graph

覆盖 16 行 Graph 权威投影、抽取、检索、CRUD 与 API。SQLite canonical memory 仍是
权威数据，Graph 必须可重建；replace/delete 必须维持原子性和 identity 投影。

```powershell
uv run --locked python -m pytest tests -q -k "graph"
```

## v-identity

覆盖 10 行身份解析、协议适配、store 与投递模式，保持 persona/session/group scope
和匿名化边界。

```powershell
uv run --locked python -m pytest tests -q -k "identity"
```

## v-injection

覆盖 14 行注入模型、路由、预算、执行、保护、decision store/recorder 与 API。

```powershell
uv run --locked python -m pytest tests -q -k "injection"
```

## v-knowledge

覆盖 7 行知识抽取、provenance、store、retriever、manager 与 API。

```powershell
uv run --locked python -m pytest tests -q -k "knowledge"
```

## v-memory

覆盖 49 行 canonical memory 模型、CRUD、批处理、生命周期、store 与工具。SQLite
仍是权威面；FTS、FAISS、Graph 和 relation/projection 必须保持可重建。

```powershell
uv run --locked python -m pytest tests -q -k "memory or atom"
```

## v-notes

覆盖 6 行 note 生成、manager、store、工具与 API，保持 source/provenance 完整性。

```powershell
uv run --locked python -m pytest tests -q -k "note"
```

## v-backup

覆盖 4 行备份 manager、snapshot 与 API。迁移不得改变备份一致性窗口、路径校验、
恢复顺序或失败清理；必须使用临时数据库测试，不能接触真实用户数据。

```powershell
uv run --locked python -m pytest tests/test_managers_backup.py tests/test_managers_backup_snapshot.py tests/test_api_backup.py -q
```

## v-diagnostics

覆盖 10 行 health、metrics、debug report、instrumentation 与 diagnostics API/命令。

```powershell
uv run --locked python -m pytest tests -q -k "diagnostic or monitoring or observability"
```

## v-maintenance

覆盖 3 行维护编排、调度和 API，保持任务有界、关闭顺序和取消传播。

```powershell
uv run --locked python -m pytest tests/test_maintenance_api.py tests/test_task_scheduler.py tests/test_decay_scheduler.py tests/test_backfill_scheduler.py -q
```

## v-update

覆盖 5 行版本检查、安装、manager 与 API；测试不得执行真实网络更新或覆盖安装。

```powershell
uv run --locked python -m pytest tests/test_update_api.py tests/test_update_installer.py tests/test_update_manager.py -q
```

## v-profile

覆盖 8 行 profile 抽取、provenance、store、授权、manager、工具与 API。

```powershell
uv run --locked python -m pytest tests -q -k "profile"
```

## v-retrieval

覆盖 50 行 BM25、vector、hybrid、graph、dual-route、ranking、deadline 和 recall
协作，保持过滤、预算、fallback、取消、deadline 与确定性排序。

```powershell
uv run --locked python -m pytest tests -q -k "retriev or recall or rank"
```

## v-review

覆盖 5 行 review detection、反思审核与 API。

```powershell
uv run --locked python -m pytest tests -q -k "review or ambient_reflection"
```

## v-full

覆盖 15 行 composition root、全局 façade 和跨领域装配，并作为每个迁移阶段的最终
门禁。运行后必须人工检查新生成的 browser smoke 截图。

```powershell
uv run --locked ruff check main.py core tests scripts
uv run --locked python -m pytest tests/test_plugin_package_imports.py -q
uv run --locked python scripts/check_all.py
```

## AST-6 必须关闭的评审项

- 决定 `core.managers.*`、`core.storage.*` 等具体路径是否存在仓库外消费者；没有
  消费证据的实现路径不应成为永久公共 API。
- 确认需要一个版本纯 re-export 的候选入口、删除版本和对应导入测试；禁止用
  `sys.modules` alias 或复制实现维持双份模块状态。
- 与 AST-22 对齐 `cognition/personality` 及所有领域词汇，随后更新唯一迁移矩阵，
  不另建第二份映射。
- 批准 stage 7 对 `main.py`、`core/__init__.py`、initializer、PluginPageApi 和全局
  fixtures 的单一所有权；领域任务不得提前修改这些共享热点。
- 确认 AST-7 的门禁实现覆盖所有批准门面、禁止的逆向依赖、54 个动态引用以及
  command handler `__module__` 契约。
