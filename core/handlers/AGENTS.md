[根级 AGENTS.md](../../AGENTS.md) > [core](../) > **handlers**

# 消息主链处理器

**最后更新：** 2026-07-17  
**入口：** `RecallHandler.handle_memory_recall()`、`ReflectionHandler.handle_memory_reflection()`  
**公开导出：** `RecallHandler`、`ReflectionHandler`

## 职责边界

`core/handlers/` 位于 AstrBot 的 LLM 请求/响应钩子与记忆基础设施之间，负责两条互补链路：

- 请求前：清理旧注入、确定查询、检索长期记忆、路由并原子注入当前 `ProviderRequest`。
- 响应后：先清洗可见回复，再记录 assistant 消息；达到滑动窗口阈值时后台抽取并写入长期记忆。
- `TopicBatchPreparer` 只负责为反思链准备消息批次；A/B 策略保持单批次，C/D 才在 LLM 抽取前预切分。

不属于本模块：AstrBot 装饰器注册和群聊全量捕获在 `main.py` / `core/event_handler.py`；实际检索在 `core/retrieval/` 与 `MemoryEngine`；结构化抽取在 [`../processors/AGENTS.md`](../processors/AGENTS.md)；注入路由/执行在 `core/injection/`；持久化由 manager/store 完成。

## 主链与数据流

```mermaid
flowchart TD
    A["AstrBot on_llm_request"] --> B["EventHandler.handle_memory_recall"]
    B --> C["RecallHandler: 清理旧注入并取得查询"]
    C --> D["QueryRewriter + preflight 路由"]
    D -->|"跳过被动召回"| E["前瞻记忆"]
    D -->|"继续"| F["MemoryEngine.search_memories"]
    F --> G["自发/前瞻补充、去重排序"]
    E --> H["InjectionExecutor 原子修改请求"]
    G --> H
    H --> I["Provider/LLM 正常处理"]
    I --> J["AstrBot on_llm_response"]
    J --> K["ReflectionHandler: 响应清洗与消息记录"]
    K --> L{"未总结轮数达到阈值?"}
    L -->|"否"| M["结束"]
    L -->|"是"| N["TopicBatchPreparer"]
    N --> O["MemoryProcessor 结构化抽取"]
    O --> P["MemoryEngine.add_memory"]
    P --> Q["提交 last_summarized_index / 清除 pending_summary"]
```

聊天主链路必须保持：`main.py` 的 `@filter.on_llm_request()` / `@filter.on_llm_response()` 只在插件就绪且 `EventHandler` 存在时委托；处理器不能自行注册钩子，也不能把动态记忆写进长期 System Prompt。群聊用户消息由全量捕获钩子写入；私聊用户消息在召回链写入；assistant 消息在反思链写入。

## 关键接口与协议

### `RecallHandler`

- `handle_memory_recall(event, req) -> None`：主入口。请求没有 `prompt` 且没有额外用户内容时直接跳过。
- 清理：配置 `recall_engine.auto_remove_injected` 开启时调用 [`../cleaners/AGENTS.md`](../cleaners/AGENTS.md) 中两个清理入口。
- 查询：优先 `event.get_message_str()`；纯 At 等空消息从最近历史构建回退查询。私聊写入请求消息时按 `req.prompt` → 组件提取 → 原始事件文本回退。
- 过滤：`filtering_settings` 决定 session/persona 范围；`get_persona_id()` 解析当前人格。
- 路由：根据 `manual` / `auto` / `hybrid` 配置和 Provider、工具集、上下文余量执行 preflight；只有未短路时才调用 `MemoryEngine.search_memories()`，最终路由只执行一次。
- 候选：主召回可加自发回忆；按稳定 `doc_id` 或来源+内容哈希去重，来源优先级为 prospective > main > spontaneous，再按分数排序并截断 `top_k`。前瞻记忆使用独立辅助预算，不混入普通候选列表。
- 注入：`InjectionExecutor` 负责硬字符预算、保护、Provider 传输适配与请求回滚；结果和脱敏计数交给 recorder/metrics。`system_prompt` 必须保持不变。

### `ReflectionHandler`

- `handle_memory_reflection(event, resp) -> None`：只处理 `assistant`；但在 tools、空会话、写阻塞等早退之前，先完成请求关联的可见回复安全清洗。
- 工具调用响应 `tools_call_name` 和工具循环总结 `tools_call_extra_content` 不进入普通对话存储。
- 清洗后为空或命中常见 API/限流/连接错误文本时不记录。
- assistant 消息成功写入会话后计算 `unsummarized_rounds = (total_messages - last_summarized_index) // 2`；阈值来自 `reflection_engine.summary_trigger_rounds`。
- `try_begin_summary_window()` / `finish_summary_window()` 是反思与手动提交共享的会话级并发门；同一 session 同时只允许一个总结任务。
- `_storage_task()` 先由 `TopicBatchPreparer` 生成批次，再调用 `MemoryProcessor.process_conversation()`；多批次并行抽取，写入由 3 槽 semaphore 限流。
- `shutdown()` 停止新窗口并等待已登记存储任务，不主动取消正在落库的反思任务。

### `TopicBatchPreparer`

- `prepare_batches(history_messages, is_group_chat) -> list[list]`。
- 非 C/D 或少于 3 条消息：复制为单批次。
- C：调用 `MemoryEngine.embed_texts` 的相邻向量边界策略；失败回退单批次。
- D：`balanced` / `low_cost` 默认禁用额外 LLM 阶段；仅 `quality` 或显式 `cost_control.allow_llm_topic_strategy_d` 允许。识别失败、零/单话题或无有效行范围均回退单批次。

## 失败、取消与一致性

- **`asyncio.CancelledError` 必须向上传播。** 召回主入口、Provider 获取/能力探测、执行器、决策记录、自发/前瞻检索和反思主入口均有显式重抛路径；不得把关闭取消误记为普通业务失败或保守回退。测试已锁定执行器和 Provider getter 的真实取消传播。
- 普通召回异常在钩子边界记录后降级为“不注入”，并始终记录总耗时；可选认知上下文、指标和 recorder 失败彼此隔离。
- 响应保护采用 fail-closed：要求保护却没有有效 scope、scope 查询失败、清洗异常或二次验证失败时，将 `resp.completion_text` 置空并清理事件标记。
- 反思 LLM 任一批次失败时不提交窗口，写 `pending_summary`；部分落库记录成功的幂等键，重试跳过已完成项。
- 幂等键由 session、窗口范围、批次/记忆索引和内容 SHA-256 组成。连续失败最多 3 次；达到上限后清除 pending 并推进窗口，明确放弃该范围。
- 所有记忆成功后先推进 `last_summarized_index` 再清 pending；元数据提交失败会再尝试一次，仍失败时存在重复总结风险并记录错误。

## 依赖方向与安全边界

- 上游：`main.py` → `core/event_handler.py` → 本模块。
- 下游：`cleaners`、`extractors`、`processors`、`retrieval`、`injection`、`security`、manager/store。
- 检索结果视为不可信内容；保护 scope 必须与单个请求关联，不得写入决策记录、日志或跨请求复用。
- recorder 只记录路由、预算、耗时和计数；不得记录查询文本、记忆正文、ID 集合或保护 token。
- 写入维护状态检查失败时必须按“写入被阻止”处理，不能冒险写入。
- 不要在处理器中复制 Provider 适配、清洗标记识别、持久化事务或抽取 schema；调用对应边界。

## 文件导航

| 文件 | 作用 |
|---|---|
| `recall_handler.py` | 请求前召回、路由、注入、安全关联和可观测性 |
| `reflection_handler.py` | 响应清洗、会话记录、窗口控制、后台抽取与幂等写入 |
| `topic_batch_preparer.py` | 反思前 C/D 话题预切分及成本回退 |
| `__init__.py` | 仅导出 `RecallHandler`、`ReflectionHandler` |

## 测试定位与验证

核心行为在 `tests/test_handlers.py`；跨模块委托、群聊捕获和关闭语义在 `tests/test_event_handler.py`；注入路由/保护协定在 `tests/test_injection_router.py`、`tests/test_prompt_sanitizer.py`；真实召回热路径成本契约在 `tests/test_recall_cost_benchmark.py`。

只改本模块时的精确验证命令：

```bash
python -m pytest tests/test_handlers.py tests/test_event_handler.py tests/test_injection_router.py tests/test_prompt_sanitizer.py tests/test_recall_cost_benchmark.py -q
```

重点回归：请求无候选不得变更请求；System Prompt 保持相等；取消向上传播；响应保护失败关闭输出；同 session 总结窗口互斥；部分写入保留幂等重试状态；关闭等待存储任务。