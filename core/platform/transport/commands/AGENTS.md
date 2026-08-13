[根级 AGENTS.md](../../../../../../AGENTS.md) / core / platform / transport / commands

# `/memora` 管理命令实现

**最后核对：** 2026-08-02
**组合入口：** `core/command_handler.py::CommandHandler`  
**路由入口：** `core/command_endpoints.py::CommandEndpointsMixin`

## 职责边界

本目录实现查询、维护与只读诊断三个命令 mixin。AstrBot 装饰器、管理员权限和就绪门控在 `CommandEndpointsMixin`；依赖注入、手动总结与真实写保护覆盖在 `CommandHandler`；本目录方法负责业务调用、i18n 文本和 `AsyncGenerator[MessageEventResult, None]` 输出。

```mermaid
flowchart LR
    ADMIN[管理员 /memora] --> EP[CommandEndpointsMixin]
    EP --> READY[_ensure_plugin_ready]
    READY --> CH[CommandHandler]
    CH --> Q[QueryCommandMixin]
    CH --> M[MaintenanceCommandMixin]
    CH --> D[DiagnosticCommandMixin]
    Q --> ENGINE[MemoryEngine]
    M --> STORE[ConversationManager / IndexValidator]
    D --> PROVIDERS[Diagnostics / Metrics / Trace 窄提供器]
    CH --> PROC[MemoryProcessor / SummaryWindowLocker]
```

## 权限与端点契约

所有 `/memora` 子命令都在 `core/command_endpoints.py` 上使用 `@permission_type(PermissionType.ADMIN)`；不要仅在 handler 文档或提示文本中声称管理员权限。`status`、`health`、`diagnostics`、`webui`、`help` 使用 `wait=False` 的非阻塞就绪快照，其余命令等待初始化。

| 子命令 | 实现 | 行为 |
|---|---|---|
| `status` | `handle_status` | 记忆总数、session 数、最新更新时间、主 DB 大小 |
| `health` | `handle_health` | 健康分、本地化等级、异常领域和固定排障建议；不显示原始错误文本 |
| `diagnostics` | `handle_diagnostics` | Provider、召回、任务、索引、写入和 Prometheus 的实时 allowlist 标量 |
| `search <query> [k]` | `handle_search` | 以当前 `unified_msg_origin` 检索；`k` 钳制 1–100；显示最终分数和四路评分明细 |
| `trace <query> [k]` | `handle_trace` | 以当前 session/chat type 执行可解释召回；`k` 钳制 1–20；聊天只显示 trace 关联码、排名、评分、阶段和路由 |
| `forget <doc_id>` | `handle_forget` | 写保护后删除记忆；负 ID 拒绝 |
| `webui` | `handle_webui` | 返回 i18n WebUI 指引 |
| `rebuild-index` | `handle_rebuild_index` | 写保护、检查一致性、必要时重建并报告 partial/vector mode/switched |
| `rebuild-graph` | `handle_rebuild_graph` | 写保护后重建图索引，报告 rebuilt/skipped |
| `reset` | `handle_reset` | 写保护后清除当前 session 的 Memora 会话上下文 |
| `cleanup [preview|exec]` | `handle_cleanup` | 默认 preview 映射 `dry_run=True`；只有大小写不敏感的 `exec` 真正写 AstrBot 历史 |
| `summarize` | `CommandHandler.handle_summarize` | 写保护与会话窗口锁后处理未总结消息，分别反馈 canonical 写入与 quarantine 数量 |
| `help` | `CommandHandler.handle_help` | 返回 i18n 帮助文本 |

`core/commands/__init__.py` 是空包标记；mixin 由 `CommandHandler` 直接导入，不存在包级公共重导出契约。

## 数据流与持久化语义

### 查询

`status` 和 `search` 要先检查 `memory_engine`。搜索 query 去空白，使用当前 session；展示内容截断到 100 字符，但真实检索结果不在命令层改写。错误通过 `_format_error_message()` 与 i18n suggestions 转为用户消息。

### 只读诊断

`health` 与 `diagnostics` 通过 `main.py` 注入的窄异步提供器复用现有 Page API 计算结果；命令模块不导入 Page API，也不返回 envelope 中的自由文本。`diagnostics` 只格式化固定状态、布尔值、计数和有限浮点数，忽略 Provider 错误、失败任务消息、索引 reason、metric 名称等非 allowlist 字段。

`trace` 传入去空白 query、钳制后的 `k`、当前 `unified_msg_origin` 和消息类型推导的 `chat_type`。群聊必须使用 `group` 以保留机密记忆过滤；命令只预览路由，不执行注入或写 canonical memory。聊天和 `RecallTraceStore` 都不保留 query、正文预览、canonical memory ID、候选 ID、身份或任意 metadata；命令结果只从安全 DTO 提取 trace 关联码、耗时、阶段计数、排名、分数与固定路由字段。

### 维护与清理

`rebuild-index` 仅在 `IndexStatus` 不一致或 `needs_rebuild` 时调用重建。`reset` 清 Memora `ConversationManager` session，不等同于删除 AstrBot 原始对话。

`cleanup` 读取 AstrBot 当前 conversation 的 JSON history，只匹配 `MEMORY_INJECTION_HEADER ... MEMORY_INJECTION_FOOTER`（当前常量为旧 `<RAG-Faiss-Memory>` 边界），删除纯注入消息或保留清理后的内容，并把三行以上空行压成两行。preview 只统计；exec 才调用 `update_conversation()`。

重要限制：自适应执行器使用 `<memora-untrusted-memory>` 与临时用户侧载体，且永不写 System Prompt。此命令当前只清理旧常量边界，不能宣称清理所有现代注入载体或伪工具上下文；通用运行时清理由 `core/cleaners` 负责。

### 手动总结

`handle_summarize()`：

1. 写保护后按 session 尝试 `SummaryWindowLocker`；同 session 已有任务则退出。
2. 从实际消息数与 `last_summarized_index` 计算窗口，少于 2 条不处理。
3. 读取窗口、解析 persona/group，调用 `MemoryProcessor.process_conversation()`。
4. 每条记忆写入 `source_window`（含 `triggered_by=manual`）。
5. quarantine 只计为已安全处理，不计入 canonical 写入、重要性或主题；canonical 写入失败时记录 `pending_summary` 并不推进窗口。
6. 全部候选安全处理后更新 `last_summarized_index`、清空 pending，并分别反馈 canonical 与 quarantine 数量；反馈进度使用真实消息索引。
7. `finally` 释放窗口锁。

这保证“部分写入”不会假装整个窗口已提交，隔离候选也不会伪装成长期记忆；但已成功写入的前缀可能存在，后续补偿逻辑必须尊重 `pending_summary`。

## 写保护与安全边界

- mixin 自带的 `_maintenance_write_guard_message()` 只为独立测试提供无保护默认；生产 `CommandHandler` 必须覆盖它并使用 `_write_guard_cb`。
- 待恢复备份存在时，`forget`、重建、`reset`、`cleanup exec`、`summarize` 均拒绝写入。`cleanup preview` 是只读扫描，允许执行。
- 写保护检查异常采用 fail-closed，向管理员返回维护检查失败；不要回退为允许写入。
- 权限必须保留在装饰器层；输入验证不能代替管理员鉴权。
- 日志只记录固定阶段和错误类别，不得记录 session、用户/群组/消息/记忆 ID、正文、历史 JSON、凭据、Provider 配置、异常消息或堆栈。
- `health`、`diagnostics` 和 `trace` 的聊天输出必须从固定 allowlist 组装；不得透传 Page API 错误 message、任务错误、Provider 错误、trace metadata 或记忆正文。
- 清理历史时只处理 `content` 为字符串的消息，非字符串结构原样保留；不要用宽泛正则跨消息删除。

## 依赖方向

`command_endpoints` → `CommandHandler` → 本目录 mixin → engine/manager/validator/context 或窄异步提供器。`main.py` 负责从现有 Page API 对象提取健康、指标和 trace callable；命令模块不应导入 Page API，也不应直接操作 SQLite。动态注入规则见 [注入模块 AGENTS.md](../../../injection/AGENTS.md)。

## 测试定位与精确验证

```powershell
python -m pytest tests/test_command_endpoints.py -q
python -m pytest tests/test_command_handler.py tests/test_commands.py tests/test_diagnostic_commands.py -q
```

重点覆盖：管理员端点委托、阻塞/非阻塞就绪检查、诊断标量 allowlist、trace scope/隐私输出、取消传播、cleanup mode 映射、写保护、索引结果、历史 preview/exec、部分总结写入与窗口锁释放。

## 相关上下文

- [根级 AGENTS.md](../../../../AGENTS.md)
- [注入模块 AGENTS.md](../../../injection/AGENTS.md)
- `core/command_endpoints.py`
- `core/command_handler.py`
- `core/cleaners/`
