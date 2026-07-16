[根目录](../../CLAUDE.md) > [core](../) > **handlers**

## 模块职责

`core/handlers/` 是 Memora 的 LLM 请求/响应事件处理器模块，负责在 LLM 调用前后执行记忆召回注入与反思存储。3 个核心文件 + `__init__.py`。

## 入口与启动

- **对外导出**: `RecallHandler`, `ReflectionHandler`
- **调用方**: `core/event_handler.py` 在 AstrBot 的 `on_llm_request` / `on_llm_response` 钩子中调用本模块

## 对外接口

### RecallHandler

| 方法 | 职责 |
|------|------|
| `handle_memory_recall(event, req)` | LLM 请求前检索并注入长期记忆 |
| `_maybe_spontaneous_recall()` | 6% 概率触发自发回忆（宽泛查询，模拟人类"突然想起"） |
| `_maybe_prospective_recall()` | 扫描 24h 内到期的 PLANNED 原子主动注入（前瞻记忆） |
| `_build_cognitive_context()` | 组装黑话解释 + 表达模式 + 好感度状态 |
| `_build_fallback_query()` | 空消息（纯 @mention）时从历史构建回退查询 |
| `_finalize_recall_candidates()` | 去重 + source 优先级排序 + inject budget 截断 |

**自适应注入策略**：

- 路由模式：`manual`、`auto`、`hybrid`。
- 内置预设：`tool_first`、`low_cost`、`balanced`、`quality`。
- 前置路由按当前请求的 ToolSet、Provider 能力和上下文余量决定是否跳过被动检索；最终路由再结合归一化候选信号。
- `InjectionExecutor` 统一执行硬预算、分层格式化、提示词保护和原子请求变更。
- 默认传输为临时 `extra_user_content`；Provider 兼容层可选择消息前后或伪工具传输，但动态记忆永不进入 System Prompt。
- 旧 `recall_engine.injection_method` 已删除且无兼容迁移。

**召回流程**：
```
1. 清理历史注入片段 (InjectionCleaner)
2. 提取用户消息 (MessageContentExtractor)
3. 查询改写 (QueryRewriter → R1 语义展开)
4. 构建当前请求能力/上下文信号并执行前置路由
5. 未被 `tool_first` 短路时执行会话/人格过滤、上下文扩展与多路检索
6. 自发回忆与前瞻记忆补充
7. 归一化候选信号并执行唯一一次最终路由
8. 认知上下文组装 (黑话 + 表达 + 好感度)
9. InjectionExecutor 按全局硬预算构建、保护、校验并原子注入
10. 异步提交脱敏决策记录并记录可观测性指标
```

### ReflectionHandler

| 方法 | 职责 |
|------|------|
| `handle_memory_reflection(event, resp)` | LLM 响应后检查是否需要反思与记忆存储 |
| `_storage_task()` | 后台异步执行记忆总结、LLM 抽取、并行写入 |
| `_prepare_message_batches()` | 委托 TopicBatchPreparer 按话题切分消息批次 |
| `_feed_cognitive_components()` | 投喂助手回复给认知模块（表达学习、好感度、黑话挖掘） |
| `try_begin_summary_window()` | 获取会话总结窗口（防并发） |
| `finish_summary_window()` | 释放会话总结窗口 |
| `shutdown()` | 优雅关闭，等待所有存储任务完成 |

**反思触发条件**：
- 必须是 assistant 角色响应
- 不能是工具调用响应（`tools_call_name` 非空则跳过）
- 不能是工具循环总结（`tools_call_extra_content` 非空则跳过）
- 响应内容经过安全清洗（`PromptProtectionService.sanitize_response`）
- 未总结轮数 >= `reflection_engine.summary_trigger_rounds`（默认 10 轮）
- 消息数 >= 2（至少一轮对话）

**失败重试机制**：
- 失败时将范围记录到 `pending_summary` 元数据
- 下次触发时合并待处理范围
- 最多重试 3 次，超过则放弃该范围

**幂等写入**：
- 每条记忆生成 `idempotency_key`（SHA256: session_id + 范围 + 批次索引 + 内容哈希）
- 完成写入的 key 保存到 `pending_summary.completed_idempotency_keys`
- 重试时已完成的记忆不重复写入

### TopicBatchPreparer

| 方法 | 职责 |
|------|------|
| `prepare_batches(history_messages, is_group_chat)` | 按话题策略切分消息为批次 |
| `_prepare_strategy_c()` | 策略 C：基于嵌入的话题边界检测预切分 |
| `_prepare_strategy_d()` | 策略 D：两阶段 LLM 话题识别 + 切分 |

策略 A/B（hybrid）直接返回单一批次；策略 C/D 将消息按话题切分为多个批次，并行调用 LLM。

## 关键依赖与配置

- **内部依赖**: `ConfigManager`, `MemoryEngine`, `MemoryProcessor`, `ConversationManager`, `InjectionAdapter`, `InjectionCleaner`, `MessageContentExtractor`, `QueryRewriter`, `TopicChunkingStrategy`, `TwoStageLLMStrategy`, `PromptProtectionService`
- **可选认知模块**: `JargonQueryService`, `ExpressionLearner`, `AffectionManager`, `RelationManager`
- **关键配置**: `recall_engine.*`（top_k, injection_routing_mode, injection_manual_preset, injection_hybrid_*, injection_delivery_override, query_rewrite, spontaneous_recall, prospective_recall）, `reflection_engine.summary_trigger_rounds`, `security.*`（sanitize_llm_response, prompt_protection）

## 数据模型

无独立数据模型。使用 `HybridResult` (来自 `core/retrieval/rrf_fusion.py`) 作为召回结果的载体。

## 测试与质量

- 对应测试文件: `tests/test_handlers.py`
- 核心函数使用 `@monitored` 装饰器提供性能追踪（调试模式）
- Prometheus 指标: `RECALL_DURATION` (stage=total), `RECALL_REQUESTS`
- 性能样本记录: `PerfTracker` 环形缓冲区

## 常见问题 (FAQ)

**Q: 为什么记忆没有注入到对话中？**
A: 检查 `recall_engine.top_k > 0`，确保有 1 条以上记忆被检索到。查看日志确认 "检索到 N 条记忆"。

**Q: 为什么 LLM 不自动记忆对话？**
A: 检查 `reflection_engine.summary_trigger_rounds` 配置（默认每 10 轮触发一次），查看日志确认 "未总结轮数达到 N 轮"。

**Q: 伪造工具调用无效？**
A: 某些 Provider（如 Gemini）不支持 fake_tool_call，`InjectionAdapter` 会自动降级到 `user_message_before`。

## 相关文件清单

- `recall_handler.py` -- 记忆召回处理器（764 行）
- `reflection_handler.py` -- 反思存储处理器（780 行）
- `topic_batch_preparer.py` -- 话题批次准备器（175 行）
- `__init__.py` -- 公共导出

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取 3 个源文件，生成模块级 CLAUDE.md |
