[根目录](../../CLAUDE.md) > [core](../) > **cleaners**

## 模块职责

`core/cleaners/` 负责在 LLM 请求前后清理上下文中的历史记忆注入片段和伪造工具调用消息，防止重复注入导致上下文膨胀。1 个源文件 + `__init__.py`。

## 入口与启动

- **对外导出**: `InjectionCleaner`
- **调用方**: `RecallHandler.handle_memory_recall()` 在每次召回前调用

## 对外接口

### InjectionCleaner

| 方法 | 职责 |
|------|------|
| `remove_injected_memories_from_context(req, session_id)` | 从 `system_prompt`, `prompt`, `extra_user_content_parts`, `contexts` 中删除历史注入片段 |
| `cleanup_injected_memories_from_db(connection, write_lock, session_id, dry_run)` | 批量清理数据库 `messages` 表中含注入标记的历史消息 |
| `remove_fake_tool_call_from_context(req, session_id)` | 删除以 `FAKE_TOOL_CALL_ID_PREFIX` 开头的伪造工具调用消息对 |

**清理范围**：
1. `req.system_prompt` -- 匹配 MEMORY_INJECTION_HEADER...FOOTER 包围的内容
2. `req.extra_user_content_parts` -- 删除含注入标记的 TextPart
3. `req.prompt` -- 同上
4. `req.contexts` -- 支持 `str`, `dict(content=str)`, `dict(content=list[{type:"text", text:...}])` 三种格式

**清理策略**：
- 正则模式: `re.escape(HEADER) + ".*?" + re.escape(FOOTER) + DOTALL`
- 清理后压缩连续换行（3+ → 2）
- 清理后内容为空的消息直接删除
- 伪造工具调用：按 `FAKE_TOOL_CALL_ID_PREFIX` 匹配 assistant tool_calls + 对应 tool 消息，成对删除

**数据库清理** (`cleanup_injected_memories_from_db`)：
- 扫描 `messages` 表中包含 `MEMORY_INJECTION_HEADER` 的消息
- 可选 `session_id` 过滤
- 支持 `dry_run` 模式（只统计不修改）
- 清理后内容为空则 DELETE，否则 UPDATE

## 关键依赖与配置

- **内部依赖**: `core.base.constants`（`MEMORY_INJECTION_HEADER`, `MEMORY_INJECTION_FOOTER`, `FAKE_TOOL_CALL_ID_PREFIX`）
- **外部依赖**: `astrbot.api.logger`, `re`, `asyncio`

## 数据模型

无独立数据模型。操作 `ProviderRequest` 和数据库 `messages` 表。

## 测试与质量

- 对应测试文件: `tests/test_cleaners.py`
- 所有方法为静态方法，无状态副作用
- 异常捕获确保清理失败不影响主流程

## 常见问题 (FAQ)

**Q: 为什么需要清理历史注入片段？**
A: LLM 上下文有长度限制。如果每次请求都在系统提示词/上下文历史中注入记忆，之前的注入片段会堆积导致 token 浪费和语义干扰。清理后保证每次注入都是"新鲜"的。

**Q: 伪造工具调用消息为什么不自动清理？**
A: 已通过 `remove_fake_tool_call_from_context()` 实现自动清理。在每次召回前检测并删除以 `FAKE_TOOL_CALL_ID_PREFIX` 开头的调用对。

## 相关文件清单

- `injection_cleaner.py` -- 注入清理器（338 行）
- `__init__.py` -- 公共导出

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取源文件，生成模块级 CLAUDE.md |
