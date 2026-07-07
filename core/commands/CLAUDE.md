[根目录](../../CLAUDE.md) > [core](../) > **commands**

## 模块职责

`core/commands/` 是 Memora 插件命令处理器的 Mixin 实现层，提供 `/lmem` 命令组的具体处理方法。通过 Mixin 模式拆分维护类命令和查询类命令，由 `core/command_handler.py` 中的 `CommandHandler` 类组合使用。共 3 个文件。

## 入口与启动

- **组合入口**: `core/command_handler.py` -- `CommandHandler(QueryCommandMixin, MaintenanceCommandMixin)` 继承两个 Mixin，注册到 `core/command_endpoints.py` 的 `/lmem` 命令路由
- **模块导出**: `core/commands/__init__.py` -- 空文件（Mixin 由 command_handler.py 直接导入）

## 对外接口

### /lmem 命令组

所有命令通过 AstrBot 命令系统注册，前缀 `/lmem`：

| 子命令 | Mixin 来源 | 方法 | 描述 |
|--------|-----------|------|------|
| `status` | QueryCommandMixin | `handle_status()` | 查看记忆系统运行状态（总记忆数、会话数、最后更新时间、DB 大小） |
| `search <query>` | QueryCommandMixin | `handle_search()` | 管理员搜索记忆（k 限制 1-100，显示 score + 四路评分明细） |
| `forget <doc_id>` | QueryCommandMixin | `handle_forget()` | 按 ID 删除记忆（需写保护检查） |
| `webui` | QueryCommandMixin | `handle_webui()` | 显示 WebUI 使用指引 |
| `rebuild-index` | MaintenanceCommandMixin | `handle_rebuild_index()` | 检查索引一致性并触发重建（显示文档/B25/向量比对，部分重建提示） |
| `rebuild-graph` | MaintenanceCommandMixin | `handle_rebuild_graph()` | 重建图记忆索引 |
| `reset` | MaintenanceCommandMixin | `handle_reset()` | 根据 session_id 清除当前会话上下文 |
| `cleanup [dry_run]` | MaintenanceCommandMixin | `handle_cleanup()` | 清理 AstrBot 历史消息中的记忆注入片段（支持预览模式） |
| `summarize` | CommandHandler | `handle_summarize()` | 手动触发记忆总结（通过滑动窗口锁防止并发） |

### 写保护机制

所有写操作（`rebuild-index`、`rebuild-graph`、`reset`、`cleanup`、`forget`、`summarize`）在执行前调用 `_maintenance_write_guard_message()`。当 `_write_guard_cb` 返回 `True` 时阻止写入并返回提示："备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"

### 错误处理模式

所有命令方法使用统一的错误处理模式：
- `_component_not_ready_message(component, command)` -- 构建一致的组件未就绪响应
- `_format_error_message(action, error, suggestions)` -- 格式化面向用户的错误消息，包含可操作提示（通过 i18n 后端翻译）
- 命令方法返回 `AsyncGenerator[MessageEventResult, None]`，通过 `yield event.plain_result(...)` 流式输出

## 核心组件详解

### QueryCommandMixin (`query_commands.py`, 202 lines)

查询类命令处理：

**handle_status()**: 调用 `memory_engine.get_statistics()` 获取统计数据，包括总记忆数、活跃会话数、最新记忆更新时间、数据库文件大小。

**handle_search(query, k=5)**: 调用 `memory_engine.search_memories(query, k, session_id)` 执行搜索, 使用 `event.unified_msg_origin` 作为 `session_id`。结果格式化显示 final_score 和四路评分明细（document_keyword_score, document_vector_score, graph_keyword_score, graph_vector_score）。

**handle_forget(doc_id)**: 调用 `memory_engine.delete_memory(doc_id)` 删除指定记忆。对 doc_id 做输入验证（必须 >= 0）。

**handle_webui()**: 静态方法，返回 WebUI 使用指引消息。

### MaintenanceCommandMixin (`maintenance_commands.py`, 303 lines)

维护类命令处理：

**handle_rebuild_index()**: 三步流程：
1. `index_validator.check_consistency()` -- 检查索引一致性
2. 显示 IndexStatus（文档数、BM25 数、向量数、原因）
3. `index_validator.rebuild_indexes(memory_engine)` -- 执行重建
返回处理成功/失败/错误数，以及向量模式（增量补写 vs 全量重建）和是否切换索引。

**handle_rebuild_graph()**: 调用 `memory_engine.rebuild_graph_index()`，返回重建和跳过的节点数。

**handle_reset()**: 调用 `conversation_manager.clear_session(session_id)` 清除当前会话的所有记忆上下文。

**handle_cleanup(dry_run=False)**: 完整的历史注入清理管道：
1. 获取 AstrBot 对话的 conversation history（JSON 字符串格式）
2. 编译正则 `re.escape(MEMORY_INJECTION_HEADER)` + `.*?` + `re.escape(MEMORY_INJECTION_FOOTER)` (DOTALL)
3. 匹配并清理每条消息中的注入片段
4. 清理后为空的消息跳过（标记删除）
5. 清理后内容变化的消息保留清理版本
6. 非空内容无变化的消息保持原样
7. 连续空行归一化（`\n{3,}` -> `\n\n`）
8. `dry_run=True` 时只统计不写入；否则调用 `conversation_manager.update_conversation()` 更新数据库

### CommandHandler (`core/command_handler.py`, 100+ lines)

Mixin 组合器，继承 `QueryCommandMixin` 和 `MaintenanceCommandMixin`：
- 构造函数注入 `context`、`config_manager`、`memory_engine`、`conversation_manager`、`index_validator`、`memory_processor`、`initialization_status_callback`、`summary_window_locker`、`write_guard_cb`
- `handle_summarize()` -- 使用 `summary_window_locker.try_begin_summary_window(session_id)` 防止并发总结，调用 `memory_processor.summarize_and_store()` 执行总结
- `_maintenance_write_guard_message()` -- 检查 `_write_guard_cb`，返回写保护提示或 None

## 关键依赖与配置

- **astrbot.api**: `AstrMessageEvent`（消息事件基类）、`MessageEventResult`（结果封装）、`logger`
- **core/base/constants**: `MEMORY_INJECTION_HEADER`、`MEMORY_INJECTION_FOOTER`（注入标记）
- **core/i18n_backend**: `t()`、`t_list()`（国际化翻译函数）
- **core/managers**: `MemoryEngine`（记忆引擎）、`ConversationManager`（会话管理）
- **core/validators**: `IndexValidator`（索引一致性检查与重建）

## 数据模型

本模块不定义独立数据模型。命令输入通过 AstrBot 命令参数解析，输出为 `MessageEventResult` 纯文本结果。

## 测试与质量

相关测试文件：
- `tests/test_commands.py` -- 命令处理器单元测试

## 常见问题 (FAQ)

**Q: 为什么命令处理用 Mixin 模式？**
A: 将维护类命令和查询类命令拆分为独立 Mixin，避免单个类过大，也便于测试时可以 mock 部分命令。

**Q: cleanup 的 dry_run 模式有什么用？**
A: 可以先预览哪些消息包含注入片段、有多少会被清理/删除，再决定是否实际执行。

**Q: summarize 的滑动窗口锁是什么？**
A: `summary_window_locker.try_begin_summary_window(session_id)` 确保同一会话不会同时执行多个总结任务，返回 False 时提示用户稍后再试。

## 相关文件清单

- `core/commands/__init__.py` -- 模块标记
- `core/commands/query_commands.py` -- 查询类命令 Mixin（status, search, forget, webui）
- `core/commands/maintenance_commands.py` -- 维护类命令 Mixin（rebuild-index, rebuild-graph, reset, cleanup）
- `core/command_handler.py` -- CommandHandler 组合入口
- `core/command_endpoints.py` -- 命令路由注册到 AstrBot

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取 3 文件 + command_handler.py，生成 core/commands/CLAUDE.md |
